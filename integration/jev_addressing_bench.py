#!/usr/bin/env python3
"""Jev's question, "is this player talking to the AI, or to a friend?" (Prototype_Addressing_The_AI's
_Test_): every line Jev judged in a real session, through a candidate wording of the routing call,
with the REAL Jev API, each line with the history as the filter would have built it at that moment.

This file holds no session data. It reads a session's `vfg.db` (the box's, taken after it), its
`lookout.md` and `menu.md`, and a hand-labels file, all from paths given on the command line, and
writes its rows next to the labels (keep real players' speech out of the repo).

THE STATE IS PRODUCTION'S. `prompt.build_state` (jev/prompt.py) on the candidate's rules, examples
and lookout, the session's menu, and the history the filter's store would have read when the line
was judged then: every line not dropped (the AI's included) STORED by that moment (a line's id is a
ULID, whose time is when the bridge stored it), said within `VFG_JEV_HISTORY_HOURS` (3 h), and the
brain's records (`AI` feed rows of kind action, task and result, as `store.ai_records` reads them)
posted by then. The budget is the session's `VFG_JEV_STATE_TOKENS` (8,000; BENCH_STATE_TOKENS sets another). A refused call is asked once more with
half the history, then passed as `context`, as `Filter._judge_one` does; a call that fails
(`JevDown`) is tried again, a second apart, five times.

THE LINES are those Jev judged in the session (a verdict and not dropped: a cast dropped by the
bridge never reaches the routing call), in the order said. Each row records the candidate's class,
every answer as Jev gave it ([choice, confidence, {option: p}]), ms, input tokens, the state's
estimate, how many history lines it kept, and the line's hand label.

    PY=python3; B=BUILD_DIR; N=$B/night/vfg
    $PY integration/jev_addressing_bench.py run prod --db $N/vfg.db --labels $B/jev-bench/labels.json \\
        --lookout $N/lookout.md --menu $N/menu.md --env ~/Documents/EverHear/Internal/.env
    $PY integration/jev_addressing_bench.py run prod … --only 4,5,34,111      # some lines, by LINE#
    $PY integration/jev_addressing_bench.py score --labels $B/jev-bench/labels.json prod d2
    $PY integration/jev_addressing_bench.py probe prod --labels $B/jev-bench/labels.json --env …

`run NAME` writes LABELS_DIR/NAME/rows.jsonl (a run into a file with rows goes on after them).
`score` reads each NAME's rows and prints, per hand label, how many lines went to the AI, every line
that breaks the pass bar, the cost and the latency; with `--write`, into LABELS_DIR/score.md.
`probe NAME` asks invented lines (a misheard name, a line about the AI, an answer to a question the
AI asked of another player, an ask between players) in an invented history: PROBES below; into
LABELS_DIR/NAME/probes.jsonl. `d2` is the routing call the session ran (jev/prompt.py at 94e517e,
two questions walked onto four classes), for the before. `NAME@2` asks NAME again, into its own folder:
Jev is not deterministic, and a line near 0.5 flips from run to run.

THE CANDIDATES below are every wording tried, in rounds, each round's comment saying what the one
before it got wrong. What ships is SHIPPED (`b4`), word for word what jev/prompt.py asks: the module
asserts it as it loads, and jev/tests/test_drift.py checks it.

THE LABELS file: `{"named": [LINE#…], "answer": […], "lost": […], "stt": […], "F": […], "U": […],
"other_ask": […]}`, by the feed's LINE numbers; a judged line in none is `other`. The pass bar: no
F, U or other line goes to the AI; every named and answer line does; lost, stt and other_ask are
reported.

The TypeSafe key as the filter reads it (`TYPESAFE_API_KEY`, else `--env FILE`), never printed.
"""

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "jev"))

import prompt  # noqa: E402  (jev/prompt.py)
from jev_api import PRICE_PER_INPUT_TOKEN, Jev, JevDown, JevRefused  # noqa: E402
from jev_clock import iso, parse_iso  # noqa: E402
from jev_filter import read_key  # noqa: E402
from store import _AI_ROW, RECORDS, named  # noqa: E402

STATE_TOKENS = int(os.environ.get("BENCH_STATE_TOKENS", "8000"))   # the session's VFG_JEV_STATE_TOKENS (8,000)
HISTORY_H = 3.0          # VFG_JEV_HISTORY_HOURS's default
TRIES = 5
BEFORE = "94e517e"       # main when the session ran: its jev/prompt.py is `d2`
TO_AI = "to_ai"
OLD_TO_AI = ("action_oriented", "conversational")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LINE = re.compile(r"^LINE#(\d+) \S+ (\S+) (\S+) ")

# ------------------------------------------------------------------ the candidates

# Every candidate shares the setting; what changes is how the AI's names and the rule are said, the
# examples and the question. Examples are invented, never a session's lines or close paraphrases.

SETTING = (
    "You read what players say on a Minecraft server, for an AI that plays alongside them. The AI hears every "
    "line: speech turned into text by speech-to-text, so words can be wrong or missing, and typed chat. It answers "
    "in chat and changes the game for the players: it gives items and effects, builds, spawns, teleports, sets the "
    "time and the weather, makes new weapons and powers, and runs events. ")

NAMES = (
    "Its names are \"AI\" and \"brain\". A player talks to it by saying its name to it, or by answering a "
    "question it asked; speech-to-text often mishears the name, as \"I\", \"eye\", \"A\" or another short word "
    "that sounds like it, or runs it into the word next to it. Most of what players say is to each other, and "
    "talk about the AI is not talk to it.")


# How to read the state: production's, with the HISTORY line as rounds one to three had it (round four
# says when a voice line began, as production does now).
WHEN_1 = "- HISTORY is everything said, oldest first, times in UTC."
WHEN_4 = ("- HISTORY is everything said, oldest first, times in UTC; a voice line's time is when the player began "
          "saying it.")
READ = prompt.RULES.split("\n\n", 1)[1]
assert WHEN_4 in READ or WHEN_1 in READ


def rules(names, when=WHEN_1):
    """The setting and `names`, then "How to read the state" with the HISTORY line `when`, and its last line."""
    return SETTING + names + "\n\n" + READ.replace(WHEN_4, WHEN_1).replace(WHEN_1, when)


EXAMPLES_A = """\
Invented lines, not from this game, under what should become of each:
- Said to the AI by name: "AI, can you give me a diamond sword", "brain, make it stop raining", "build me a tower over there AI", "what does a beacon do, brain?", "thanks AI", "spawn me a horse, I" (speech-to-text's "AI").
- An answer to the AI: after the AI asked Steve "stone or wood for the wall?", Steve's "wood" or Alex's "stone, definitely".
- Not said to the AI, but remembered: "my base is past the big oak", "we're going to the nether next", "Alex, this sword the AI made is way too weak", "the AI keeps getting my bow wrong".
- Not said to the AI, and dropped: "can you pass me some arrows", "come help me with this creeper", "bro run", "nice", "wait what", "who took my bed lol", and garbled speech."""

A = {
    "to_the_ai_by_name": "To the AI, by name: the speaker says the AI's name, \"AI\" or \"brain\" (or speech-to-text's "
                         "mishearing of it), and says the line to the AI: an ask, a question, thanks or a complaint.",
    "answers_the_ai": "To the AI, answering it: the AI asked a question, of this player or of another, and this line "
                      "answers it.",
    "remember_it": "Not to the AI, but remember it: said to another player, to no one, or about the AI, it tells "
                   "something lasting: a plan or a rule the players make, a fact about a player or the world, or a "
                   "problem with what the AI made.",
    "ignore_it": "Not to the AI, and drop it: said to another player or to no one, and only of this moment, however "
                 "much it sounds like an ask: talk, a reaction, filler or garbled speech.",
}
A_ASK = "Is the speaker talking to the AI or to a friend, and what should become of line [1]?"
A_TO = ("to_the_ai_by_name", "answers_the_ai")
A_CLS = {"remember_it": "context", "ignore_it": "not_relevant"}


def one_question(name, ask, criteria, to_ai, cls):
    """A candidate of one `choice` question: an option in `to_ai` is to_ai, the rest by `cls`."""
    def questions(row):
        who = "AI" if row["source"] == "ai" else row["player_name"]
        head = "Line [1], %s at %s: “%s”. " % (who, prompt.when(row["at"]), prompt.quote(row))
        return {name: {"type": "choice", "instructions": head + ask, "criteria": dict(criteria)}}

    def route(answers):
        got, conf = answers[name]
        return (TO_AI if got in to_ai else cls[got]), conf

    return questions, route


B_TO = {
    "yes_by_name": "Yes, by name: the speaker says the AI's name, \"AI\" or \"brain\" (or speech-to-text's mishearing "
                   "of it), and says the line to the AI: an ask, a question, thanks or a complaint.",
    "yes_answering_it": "Yes, answering it: the AI asked a question, of this player or of another, and this line "
                        "answers it.",
    "no": "No: the speaker is talking to another player, to no one, or about the AI, however much the line sounds "
          "like an ask.",
}
B_KEEP = {
    "remember_it": "Remember it: it tells something lasting: a plan or a rule the players make, a fact about a player "
                   "or the world, or a problem with what the AI made.",
    "ignore_it": "Ignore it: it matters only this moment: talk, a reaction, filler or garbled speech.",
}


def two_questions(to_ask, to_criteria, keep_ask, keep_criteria):
    """`to_the_ai` (yes by name, yes answering, no) then `keep` (remember, ignore) in one call. A yes is to_ai with
    its confidence; a no is `keep`'s class and confidence, or context with the no's when `keep` is missing."""
    def questions(row):
        who = "AI" if row["source"] == "ai" else row["player_name"]
        head = "Line [1], %s at %s: “%s”. " % (who, prompt.when(row["at"]), prompt.quote(row))
        return {"to_the_ai": {"type": "choice", "instructions": head + to_ask, "criteria": dict(to_criteria)},
                "keep": {"type": "choice", "instructions": head + keep_ask, "criteria": dict(keep_criteria)}}

    def route(answers):
        got, conf = answers["to_the_ai"]
        if got != "no":
            return TO_AI, conf
        keep = answers.get("keep")
        if keep is None:
            return "context", conf
        return ("context" if keep[0] == "remember_it" else "not_relevant"), keep[1]

    return questions, route


def d2():
    """The routing call the session ran: jev/prompt.py at BEFORE, loaded from git."""
    src = subprocess.run(["git", "-C", REPO, "show", "%s:jev/prompt.py" % BEFORE], capture_output=True, text=True,
                         check=True).stdout
    path = os.path.join(tempfile.mkdtemp(prefix="jev-d2-"), "prompt_d2.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("prompt_d2", path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)

    def route(answers):
        cls, conf = old.route(answers)
        return (TO_AI if cls in OLD_TO_AI else cls), conf, cls

    return {"what": "the session's routing call (D2, %s), its classes read as the new ones" % BEFORE,
            "rules": old.RULES, "examples": old.EXAMPLES, "lookout": None, "questions": old.questions,
            "route": route, "required": "d_what", "module": old}


def candidate(name):
    """name -> {what, rules, examples, lookout (None: the session's file), questions(row), route(answers),
    required}. `name@2` is `name` asked again, into its own folder, for Jev's run-to-run noise."""
    name = name.split("@")[0]
    if name == "d2":
        return d2()
    if name == "prod":
        return {"what": "jev/prompt.py as it is", "rules": prompt.RULES, "examples": prompt.EXAMPLES,
                "lookout": prompt.DEFAULT_LOOKOUT, "questions": prompt.questions, "route": prompt.route,
                "required": prompt.QUESTION}
    if name == "prod_night_lookout":
        c = candidate("prod")
        c.update(what="jev/prompt.py with the session's own lookout.md", lookout=None)
        return c
    if name in CANDIDATES:
        what, make = CANDIDATES[name]
        return dict(make(), what=what)
    raise SystemExit("no candidate %r: d2, prod, prod_night_lookout, %s" % (name, ", ".join(CANDIDATES)))


LOOKOUT_NEW = """\
- Anything said to the AI by name ("AI" or "brain", or a mishearing of it): asks, questions, praise, complaints.
- A player answering a question the AI asked, whoever it asked.
- What players say to each other about what the AI built, gave, said or did.
- Names of places, bases, builds and coordinates, and whose they are.
- Plans the group is making: where they are going, what they want to build, fight or find next, and rules they set.
- A player in trouble: lost, low on health, stuck, trapped, or asking for help.
- What a player likes or hates about the game right now, if the AI could change it."""


def _a1():
    q, r = one_question("to_whom", A_ASK, A, A_TO, A_CLS)
    return {"rules": rules(NAMES), "examples": EXAMPLES_A, "lookout": LOOKOUT_NEW, "questions": q, "route": r,
            "required": "to_whom"}


def _b1():
    q, r = two_questions("Is the speaker talking to the AI in line [1]?", B_TO,
                         "If line [1] is not said to the AI, should it be remembered?", B_KEEP)
    return {"rules": rules(NAMES), "examples": EXAMPLES_A, "lookout": LOOKOUT_NEW, "questions": q, "route": r,
            "required": "to_the_ai"}


# Round two. Round one's Jev took asks that name no AI as said to the AI by name
# (a1: 12 of the 15 asks that never name it), and took a player going on with his own talk after the AI
# asked something as an answer. The rule goes into the rules; "by name" says the name is IN the line;
# an answer gives what the AI asked for; and (a2, a3) an ask without the name gets an option of its own.
NAMES_2 = (
    "Its names are \"AI\" and \"brain\". A line is said to the AI only when it says the AI's name to it, or "
    "answers a question the AI asked; speech-to-text often mishears the name, as \"I\", \"eye\", \"A\" or another "
    "short word that sounds like it, or runs it into the word next to it. Everything else is said to a friend or "
    "to no one, however much it sounds like an ask, and talk about the AI is not talk to it.")
BY_NAME_2 = ("To the AI, by name: line [1] itself says the AI's name, \"AI\" or \"brain\", or a word speech-to-text "
             "made of it, and is said to the AI: an ask, a question, thanks or a complaint.")
ANSWERS_2 = ("To the AI, answering it: the AI's latest message asked a question, of this player or of another, and "
             "line [1] replies to it: yes or no, one of the choices the AI offered, or what it asked for.")
UNNAMED_2 = ("Not to the AI: an ask, an order or a question that does not say the AI's name, said to a friend or to "
             "no one, however much it sounds meant for the AI.")
REMEMBER_2 = ("Not to the AI, but remember it: said to a friend, to no one, or about the AI, it tells something "
              "lasting: a plan or a rule the players make, a fact about a player or the world, or a problem with "
              "what the AI made.")
IGNORE_2 = ("Not to the AI, and drop it: said to a friend or to no one, talk of this moment: a reaction, filler or "
            "garbled speech.")
A2 = {"to_the_ai_by_name": BY_NAME_2, "answers_the_ai": ANSWERS_2, "ask_without_its_name": UNNAMED_2,
      "remember_it": REMEMBER_2, "ignore_it": IGNORE_2}
A2_CLS = {"ask_without_its_name": "not_relevant", "remember_it": "context", "ignore_it": "not_relevant"}
A3_ASK = ("Does line [1] say the AI's name to it, or answer a question the AI asked? If neither, it is said to a "
          "friend or to no one: what should become of it?")
B2_TO = {
    "says_its_name": BY_NAME_2.replace("To the AI, by name: ", "Yes, by name: "),
    "answers_its_question": ANSWERS_2.replace("To the AI, answering it: ", "Yes, answering it: "),
    "neither": "No: line [1] neither says the AI's name nor answers a question the AI asked: it is said to a friend, "
               "to no one, or about the AI, however much it sounds like an ask.",
}
EXAMPLES_2 = """\
Invented lines, not from this game, under what should become of each:
- Said to the AI by name: "AI, can you give me a diamond sword", "brain, make it stop raining", "build me a tower over there AI", "what does a beacon do, brain?", "thanks AI", "spawn me a horse, I" (speech-to-text's "AI").
- An answer to the AI: after the AI asked Steve "stone or wood for the wall?", Steve's "wood" or Alex's "stone, definitely".
- An ask without the AI's name, said to a friend: "can you pass me some arrows", "give me your pickaxe", "can you make it night", "where are you?".
- Not said to the AI, but remembered: "my base is past the big oak", "we're going to the nether next", "Alex, this sword the AI made is way too weak", "the AI keeps getting my bow wrong".
- Not said to the AI, and dropped: "bro run", "nice", "wait what", "who took my bed lol", and garbled speech."""


def _a2(ask=A_ASK):
    q, r = one_question("to_whom", ask, A2, A_TO, A2_CLS)
    return {"rules": rules(NAMES_2), "examples": EXAMPLES_2, "lookout": LOOKOUT_NEW, "questions": q, "route": r,
            "required": "to_whom"}


def _b2():
    q, r = two_questions("Is the speaker talking to the AI in line [1]?", B2_TO,
                         "If line [1] is not said to the AI, should it be remembered?", B_KEEP)

    def route(answers):   # B2's yes options have their own names
        got, conf = answers["to_the_ai"]
        if got != "neither":
            return TO_AI, conf
        keep = answers.get("keep")
        if keep is None:
            return "context", conf
        return ("context" if keep[0] == "remember_it" else "not_relevant"), keep[1]

    return {"rules": rules(NAMES_2), "examples": EXAMPLES_2.replace("An ask without the AI's name, said to a friend",
                                                                   "Not said to the AI, an ask without its name"),
            "lookout": LOOKOUT_NEW, "questions": q, "route": route, "required": "to_the_ai"}


# Round three. What still went to the AI in round two was nearly all "answers": a player going on with
# his own talk, about the AI, after the AI asked him something, and the one real answer missed was an
# order about what the AI had asked, given by the other player. And no misheard name was read as one. So: talk that calls the AI
# "it" is about it; an answer is said to the AI and gives what it asked for, or says what to do about
# it; and (x3) a misheard name gets an option of its own.
NAMES_3 = (
    "Its names are \"AI\" and \"brain\". A line is said to the AI only when it says the AI's name to it, or "
    "answers a question the AI asked; speech-to-text often mishears the name, as \"I\" where \"I\" makes no "
    "sense, \"eye\", \"A\" or another short word that sounds like it, or runs it into the word next to it. "
    "Everything else is said to a friend or to no one, however much it sounds like an ask. Talk about the AI is "
    "not talk to it: a line that calls the AI \"it\" or \"the AI\" talks about it.")
ANSWERS_3 = ("To the AI, answering it: the AI's latest message asked a question, of this player or of another, and "
             "line [1] is said back to the AI with what it asked for: yes or no, which one, a detail it asked about, "
             "or what to do about what it asked. Going on with one's own talk after the AI spoke is no answer.")
MISHEARD_3 = ("To the AI, its name misheard: line [1] is said to the AI, and speech-to-text wrote its name, \"AI\" or "
              "\"brain\", as another word that sounds like it, such as \"I\" where \"I\" makes no sense, \"eye\" or "
              "\"A\".")
BY_NAME_3 = ("To the AI, by name: line [1] itself says \"AI\" or \"brain\" to the AI: an ask, a question, thanks or "
             "a complaint.")
EXAMPLES_3 = """\
Invented lines, not from this game, under what should become of each:
- Said to the AI by name: "AI, can you give me a diamond sword", "brain, make it stop raining", "build me a tower over there AI", "what does a beacon do, brain?", "thanks AI", and with the name misheard, "spawn me a horse, I", "eye can you make it night".
- An answer to the AI: after the AI asked Steve "stone or wood for the wall?", Steve's "wood" or Alex's "stone, definitely" or "make it stone and go higher".
- An ask without the AI's name, said to a friend: "can you pass me some arrows", "give me your pickaxe", "can you make it night", "where are you?".
- Not said to the AI, but remembered: "my base is past the big oak", "we're going to the nether next", "Alex, this sword the AI made is way too weak", "it keeps getting my bow wrong", and, after the AI asked Steve "stone or wood?", Steve going on to Alex, "so I'm thinking a moat round it too".
- Not said to the AI, and dropped: "bro run", "nice", "wait what", "who took my bed lol", and garbled speech."""


def _a4(misheard=False):
    opts = {"to_the_ai_by_name": BY_NAME_3 if misheard else BY_NAME_2}
    if misheard:
        opts["to_the_ai_misheard"] = MISHEARD_3
    opts.update({"answers_the_ai": ANSWERS_3, "ask_without_its_name": UNNAMED_2, "remember_it": REMEMBER_2,
                 "ignore_it": IGNORE_2})
    q, r = one_question("to_whom", A3_ASK, opts, A_TO + ("to_the_ai_misheard",), A2_CLS)
    return {"rules": rules(NAMES_3), "examples": EXAMPLES_3, "lookout": LOOKOUT_NEW, "questions": q, "route": r,
            "required": "to_whom"}


def _b3(misheard=False):
    to = {"says_its_name": (BY_NAME_3 if misheard else BY_NAME_2).replace("To the AI, by name: ", "Yes, by name: ")}
    if misheard:
        to["says_its_name_misheard"] = MISHEARD_3.replace("To the AI, its name misheard: ", "Yes, its name misheard: ")
    to.update({"answers_its_question": ANSWERS_3.replace("To the AI, answering it: ", "Yes, answering it: "),
               "neither": B2_TO["neither"]})
    q, _ = two_questions("Is the speaker talking to the AI in line [1]?", to,
                         "If line [1] is not said to the AI, should it be remembered?", B_KEEP)
    return {"rules": rules(NAMES_3), "examples": EXAMPLES_3.replace("An ask without the AI's name, said to a friend",
                                                                   "Not said to the AI, an ask without its name"),
            "lookout": LOOKOUT_NEW, "questions": q, "route": _b2()["route"], "required": "to_the_ai"}


# Round four. Two of round three's four false answers began before the AI's question was said (a voice
# line's time is when the player began it: one began 7 s before the AI's question, which came while he
# spoke), and Jev cannot know that. The rules say so, and an answer comes after the question.
ANSWERS_4 = ("To the AI, answering it: the AI asked a question, of this player or of another, before line [1] began, "
             "and line [1] is said back to the AI with what it asked for: yes or no, which one, a detail it asked "
             "about, or what to do about what it asked. Going on with one's own talk after the AI spoke is no "
             "answer.")


def rules4(names):
    return rules(names, WHEN_4)


def _b5():
    c = _b3(True)
    to = {"says_its_name": BY_NAME_3.replace("To the AI, by name: ", "Yes, by name: "),
          "says_its_name_misheard": MISHEARD_3.replace("To the AI, its name misheard: ", "Yes, its name misheard: "),
          "answers_its_question": ANSWERS_4.replace("To the AI, answering it: ", "Yes, answering it: "),
          "neither": B2_TO["neither"]}
    q, _ = two_questions("Is the speaker talking to the AI in line [1]?", to,
                         "If line [1] is not said to the AI, should it be remembered?", B_KEEP)
    c.update(rules=rules4(NAMES_3), questions=q)
    return c


def _a6():
    opts = {"to_the_ai_by_name": BY_NAME_3, "to_the_ai_misheard": MISHEARD_3, "answers_the_ai": ANSWERS_4,
            "ask_without_its_name": UNNAMED_2, "remember_it": REMEMBER_2, "ignore_it": IGNORE_2}
    q, r = one_question("to_whom", A3_ASK, opts, A_TO + ("to_the_ai_misheard",), A2_CLS)
    return {"rules": rules4(NAMES_3), "examples": EXAMPLES_3, "lookout": LOOKOUT_NEW, "questions": q, "route": r,
            "required": "to_whom"}


# Round five. The one real answer every wording missed is an order given after the player the AI asked
# had answered it (the AI asks one player which of two things is wrong; he answers; the other player
# then tells it what to do with hers). The spec counts it: an answer goes "whichever player the AI asked".
ANSWERS_5 = ("To the AI, answering it: the AI asked a question, of this player or of another, before line [1] began, "
             "and line [1] replies to it, said to the AI: yes or no, which one, a detail it asked about, or what it "
             "should do about it, even after another player has answered. A player going on with their own talk "
             "after the AI spoke does not answer it.")
EXAMPLES_5 = EXAMPLES_3.replace(
    "Steve's \"wood\" or Alex's \"stone, definitely\" or \"make it stone and go higher\".",
    "Steve's \"wood\" or Alex's \"stone, definitely\", or, after Steve's \"wood\", Alex's \"no, make it stone and go "
    "higher\".")
assert EXAMPLES_5 != EXAMPLES_3


def _b6():
    c = _b5()
    to = {"says_its_name": BY_NAME_3.replace("To the AI, by name: ", "Yes, by name: "),
          "says_its_name_misheard": MISHEARD_3.replace("To the AI, its name misheard: ", "Yes, its name misheard: "),
          "answers_its_question": ANSWERS_5.replace("To the AI, answering it: ", "Yes, answering it: "),
          "neither": B2_TO["neither"]}
    q, _ = two_questions("Is the speaker talking to the AI in line [1]?", to,
                         "If line [1] is not said to the AI, should it be remembered?", B_KEEP)
    c.update(questions=q, examples=EXAMPLES_5.replace("An ask without the AI's name, said to a friend",
                                                      "Not said to the AI, an ask without its name"))
    return c


def _a7():
    c = _a6()
    opts = {"to_the_ai_by_name": BY_NAME_3, "to_the_ai_misheard": MISHEARD_3, "answers_the_ai": ANSWERS_5,
            "ask_without_its_name": UNNAMED_2, "remember_it": REMEMBER_2, "ignore_it": IGNORE_2}
    q, r = one_question("to_whom", A3_ASK, opts, A_TO + ("to_the_ai_misheard",), A2_CLS)
    c.update(questions=q, route=r, examples=EXAMPLES_5)
    return c


# Round six. Jesse's own word for an answer is "explicitly responding to something AI asked"; and the
# question in the spec's words, "is this player talking to the AI, or to a friend?".
ANSWERS_6 = ("To the AI, answering it: line [1] explicitly replies to a question the AI asked before it began, of "
             "this player or of another: yes or no, which one, a detail it asked for, or what it should do about "
             "it. A player going on with their own talk after the AI spoke does not answer it.")
NEITHER_6 = ("A friend, or no one: line [1] neither says the AI's name nor explicitly replies to a question the AI "
             "asked: it is said to a friend, to no one, or about the AI, however much it sounds like an ask.")
TO_ASK_6 = "Is the speaker talking to the AI or to a friend in line [1]?"


def _b7(question=TO_ASK_6):
    c = _b5()
    to = {"says_its_name": BY_NAME_3.replace("To the AI, by name: ", "The AI, by name: "),
          "says_its_name_misheard": MISHEARD_3.replace("To the AI, its name misheard: ", "The AI, its name misheard: "),
          "answers_its_question": ANSWERS_6.replace("To the AI, answering it: ", "The AI, answering it: "),
          "neither": NEITHER_6}
    q, _ = two_questions(question, to, "If line [1] is not said to the AI, should it be remembered?", B_KEEP)
    c.update(questions=q)
    return c


# Round seven: lean. Jev's state is a fixed 8K tokens, so every byte the rules, lookout and examples add
# comes out of the menu and the history (b4 added 837 bytes to them: at line #111 the menu kept 70 of
# 79 lines where it kept 73). b4_lean says the misheard forms once (in the option, not the rules too),
# keeps fewer examples, and the old lookout, which the bench found changes nothing (b7 and prod with it).
NAMES_LEAN = (
    "Its names are \"AI\" and \"brain\". A line is said to the AI only when it says the AI's name to it, a "
    "misheard one included, or answers a question the AI asked; everything else is said to a friend or to no one, "
    "however much it sounds like an ask. Talk about the AI is not talk to it: a line that calls the AI \"it\" or "
    "\"the AI\" talks about it.")
EXAMPLES_LEAN = """\
Invented lines, not from this game, under what should become of each:
- Said to the AI by name: "AI, can you give me a diamond sword", "brain, make it stop raining", "thanks AI", and with the name misheard, "spawn me a horse, I", "eye can you make it night".
- An answer to the AI: after the AI asked Steve "stone or wood for the wall?", Steve's "wood" or Alex's "stone, definitely".
- Not said to the AI, an ask without its name: "can you pass me some arrows", "can you make it night", "where are you?".
- Not said to the AI, but remembered: "my base is past the big oak", "we're going to the nether next", "Alex, this sword the AI made is way too weak".
- Not said to the AI, and dropped: "bro run", "nice", "wait what", "who took my bed lol", and garbled speech."""


# b4_lean2 (b4's rules, lean examples) sent an unnamed "can you give me …" ask to the AI in all three runs and
# held the real answer #267 lower: the examples matter, not the rules. b4_lean3 gives back the one unnamed
# "give me …" ask the lean examples dropped (24 bytes).
EXAMPLES_LEAN3 = EXAMPLES_LEAN.replace('"can you pass me some arrows", "can you make it night"',
                                       '"can you pass me some arrows", "give me your pickaxe", "can you make it night"')
assert EXAMPLES_LEAN3 != EXAMPLES_LEAN


def _b4_lean(names=NAMES_LEAN, examples=EXAMPLES_LEAN):
    c = _b3(True)
    c.update(rules=rules(names), examples=examples, lookout=None)
    return c


CANDIDATES = {
    "a1": ("one question, four options: to the AI by name, answering it, remember, drop", _a1),
    "b1": ("two questions in one call: to the AI (yes by name, yes answering it, no), then remember or ignore", _b1),
    "a2": ("one question, five options (an ask without the name its own), the rule in the rules", _a2),
    "a3": ("a2, its question asking the rule", lambda: _a2(A3_ASK)),
    "b2": ("two questions, the rule in the rules, `neither` named by the rule", _b2),
    "a4": ("a3, with round three's rules, answer and examples", _a4),
    "a5": ("a4, and a misheard name its own option", lambda: _a4(True)),
    "b3": ("b2, with round three's rules, answer and examples", _b3),
    "b4": ("b3, and a misheard name its own option", lambda: _b3(True)),
    "a6": ("a5, with round four's rules (when a voice line began) and answer (after the question)", _a6),
    "b5": ("b4, with round four's rules (when a voice line began) and answer (after the question)", _b5),
    "a7": ("a6, an answer even after another player answered (round five)", _a7),
    "b6": ("b5, an answer even after another player answered (round five)", _b6),
    "b7": ("b5, an answer explicitly replies (Jesse's word), the spec's question (round six)", _b7),
    "b7_night_lookout": ("b7 with the session's own lookout.md (the old default)", lambda: dict(_b7(), lookout=None)),
    "b4_lean": ("b4, lean: the misheard forms said once, fewer examples, the old lookout (round seven)", _b4_lean),
    "b4_lean2": ("b4_lean with b4's rules (the misheard forms in the rules too)", lambda: _b4_lean(NAMES_3)),
    "b4_lean3": ("b4_lean, the unnamed \"give me …\" ask back in the examples", lambda: _b4_lean(examples=EXAMPLES_LEAN3)),
    "b4_lean4": ("b4 with b4_lean's rules and the old lookout, b4's examples whole",
                 lambda: _b4_lean(examples=_b3(True)["examples"])),
    # Every lean run had the old lookout, and with it #128, an ask said again with no name, went to the AI
    # (0.51 to 0.60; b4 with the old lookout too, 0.55): the old lookout's "saying an earlier ask again"
    # draws it. With the new lookout b4 kept it out in five runs (0.40 to 0.52). So: lean, the new lookout.
    "b4_lean5": ("b4_lean with the new lookout", lambda: dict(_b4_lean(), lookout=LOOKOUT_NEW)),
}


# What ships (2026-10-13): b4. Over four runs it broke the pass bar on 3 to 5 lines and never sent a line said
# between players or an unclear one to the AI; b7, worded closer to the spec, broke it on 4 in each of three runs,
# one of them a line said between players every time, and held a real answer (said after the answer to the AI's
# question) at 0.51 to 0.55 against b4's 0.62 to 0.68. See the bench's score.md.
SHIPPED = "b4"


def _shipped_is_production():
    """SHIPPED is what jev/prompt.py ships: its rules, examples, lookout and questions, word for word. (Its route
    stores `keep`'s confidence for a line not to the AI; production stores `to_the_ai`'s, the classes the same.)"""
    c = candidate(SHIPPED)
    row = {"id": "x", "at": "2026-10-13T03:22:12.000Z", "player_name": "Steve", "source": "voice", "text": "hi",
           "peak_dbfs": None}
    return (c["rules"], c["examples"], c["lookout"], c["questions"](row), c["required"]) == (
        prompt.RULES, prompt.EXAMPLES, prompt.DEFAULT_LOOKOUT, prompt.questions(row), prompt.QUESTION)


assert _shipped_is_production(), "jev/prompt.py is no longer %s: bench it as a new candidate" % SHIPPED

# ------------------------------------------------------------------ the session


def ulid_time(line_id):
    """When the bridge stored the line: its ULID's first 10 characters, ms since the epoch, in seconds."""
    n = 0
    for ch in line_id[:10].upper():
        n = n * 32 + CROCKFORD.index(ch)
    return n / 1000.0


class Session:
    """A session's vfg.db, read only: its lines (the stored time from the id), the brain's records from the feed,
    and the feed's LINE numbers."""

    def __init__(self, db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        conn.row_factory = sqlite3.Row
        cols = {c[1] for c in conn.execute("PRAGMA table_info(lines)")}
        dropped = " WHERE dropped IS NULL" if "dropped" in cols else ""
        self.lines = [dict(r) for r in conn.execute("SELECT * FROM lines%s ORDER BY at, id" % dropped)]
        for r in self.lines:
            r["stored"] = ulid_time(r["id"])
        self.records = []
        feed = conn.execute("SELECT seq, at, line FROM feed ORDER BY seq").fetchall()
        for seq, at, line in feed:
            m = _AI_ROW.match(line)
            if m and m.group(1) in RECORDS:
                self.records.append({"id": "AI#%d" % seq, "at": at, "player_name": "AI", "source": m.group(1),
                                     "text": named(m.group(2), m.group(3)), "peak_dbfs": None,
                                     "stored": parse_iso(at)})
        # the feed's LINE number of each kept line: same time, player and text (a group ends with its ask)
        by_at = {}
        for r in self.lines:
            by_at.setdefault((r["at"], "_".join(r["player_name"].split())), []).append(r)
        self.number = {}
        for seq, at, line in feed:
            m = _LINE.match(line)
            if not m:
                continue
            text = line.split(" | ", 1)[1]
            hits = [r for r in by_at.get((at, m.group(2)), []) if text == prompt.one_line(r["text"])[:500]
                    or text.endswith(prompt.one_line(r["text"])[-200:])]
            if len(hits) == 1:
                self.number[hits[0]["id"]] = int(m.group(1))
        conn.close()

    def judged(self):
        return [r for r in self.lines if r["source"] != "ai" and r["jev_class"]]

    def history(self, line, at):
        """What the store would have read at `at`, newest first, without `line`."""
        since = iso(at - HISTORY_H * 3600)
        rows = [r for r in self.lines + self.records if r["id"] != line["id"] and r["stored"] <= at
                and r["at"] >= since]
        return sorted(rows, key=lambda r: (r["at"], r["id"]), reverse=True)


def read_labels(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    labels = {}
    for kind in ("named", "answer", "lost", "stt", "F", "U", "other_ask"):
        for n in raw.get(kind, []):
            labels[int(n)] = kind
    return labels, raw.get("notes", {})


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def raw_answers(body, names):
    """{name: [choice, confidence, {option: p}]} as Jev sent them."""
    try:
        got = json.loads((body or b"").decode("utf-8")).get("answers") or {}
    except (ValueError, AttributeError):
        return {}
    out = {}
    for name in names:
        a = got.get(name)
        if isinstance(a, dict):
            out[name] = [a.get("choice"), a.get("confidence"), a.get("probabilities")]
    return out


# ------------------------------------------------------------------ one call


class Asker:
    """The routing call for one line, as the filter makes it (refused: once more with half the history, then
    context), the candidate's wording on production's build_state."""

    def __init__(self, cand, key, lookout, menu):
        self.cand, self.key, self.lookout, self.menu = cand, key, lookout, menu
        self.lock = threading.Lock()

    def state(self, row, others, share):
        with self.lock:   # build_state reads prompt.EXAMPLES; one candidate a run, but be safe
            saved, prompt.EXAMPLES = prompt.EXAMPLES, self.cand["examples"]
            try:
                return prompt.build_state(self.lookout, [(1, row)], others, STATE_TOKENS, share, self.menu,
                                          self.cand["rules"])
            finally:
                prompt.EXAMPLES = saved

    def ask(self, row, others):
        """{"class", "confidence", "calls": [{answers, ms, tokens, estimate, kept, tries}]}."""
        out = {"calls": []}
        for share in (1.0, 0.5):
            state, kept = self.state(row, others, share)
            qs = self.cand["questions"](row)
            call = {"estimate": prompt.estimate(state), "kept": kept, "share": share, "tries": []}
            out["calls"].append(call)
            for _ in range(TRIES):
                jev = Jev(self.key, timeout=10.0)
                try:
                    res = jev.ask(state, qs, required=self.cand["required"])
                except JevRefused as e:
                    call["refused"] = str(e)
                    break
                except JevDown as e:
                    call["tries"].append(str(e))
                    time.sleep(1.0)
                    continue
                call.update(answers=raw_answers(jev.last_body, qs), ms=res.ms, tokens=res.input_tokens or 0)
                routed = self.cand["route"](res.answers)
                out["class"], out["confidence"] = routed[0], round(routed[1], 3)
                if len(routed) > 2:
                    out["old_class"] = routed[2]
                return out
            if "refused" not in call:
                out["class"], out["failed"] = "context", True
                return out
        out["class"], out["confidence"] = "context", None   # refused twice: passed as context
        return out


def run(args):
    cand = candidate(args.name)
    session = Session(args.db)
    labels, _ = read_labels(args.labels)
    key, where = read_key(args.env or "", name="TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("no TYPESAFE_API_KEY in the environment or %s" % where)
    lookout = cand["lookout"] if cand["lookout"] is not None else prompt.read_lookout(args.lookout)[0]
    menu = prompt.read_menu(args.menu)[0]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(args.labels)), args.name)
    os.makedirs(out_dir, exist_ok=True)
    rows_path = os.path.join(out_dir, "rows.jsonl")
    done = {r["id"] for r in read_rows(rows_path)}
    lines = session.judged()
    if args.only:
        want = {int(x) for x in args.only.split(",")}
        lines = [r for r in lines if session.number.get(r["id"]) in want]
    todo = [r for r in lines if r["id"] not in done]
    print("%s: %s · %d lines judged in the session, %d to ask · key from %s" % (
        args.name, cand["what"], len(session.judged()), len(todo), where), flush=True)
    asker = Asker(cand, key, lookout, menu)

    def one(r):
        at = parse_iso(r["jev_judged_at"])
        got = asker.ask(r, session.history(r, at))
        n = session.number.get(r["id"])
        return dict({"id": r["id"], "n": n, "at": r["at"], "who": r["player_name"], "source": r["source"],
                     "text": r["text"], "night": [r["jev_class"], r["jev_confidence"]],
                     "label": labels.get(n, "other")}, **got)

    with open(rows_path, "a", encoding="utf-8") as f, \
            concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(one, todo):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            c = row["calls"][-1]
            mark = "TO_AI" if row["class"] == TO_AI else ""
            print("#%-4s %-5s %-6s %-12s %-5s %4s ms %5s tok %s | %s" % (
                row["n"] or "-", row["who"][:5], row["label"], row["class"], mark, c.get("ms", "-"),
                c.get("tokens", "-"), row.get("confidence"), prompt.cut(prompt.one_line(row["text"]), 70)),
                flush=True)


# ------------------------------------------------------------------ probes: invented lines

T = 1791900000.0   # 2026-10-13T13:20:00Z, an invented afternoon


def _r(i, dt, who, text, source="voice"):
    return {"id": "P%03d" % i, "at": iso(T + dt), "player_name": "AI" if source == "ai" else who, "source": source,
            "text": text, "peak_dbfs": -20.0 if source == "voice" else None}


PROBE_HISTORY = [
    _r(1, 0, "Steve", "ok we need wood before it gets dark"),
    _r(2, 6, "Alex", "I'll get the oak by the river"),
    _r(3, 15, "Steve", "AI, give me a stone axe"),
    _r(4, 18, "AI", "Here's a stone axe, Steve.", "ai"),
    _r(5, 30, "Alex", "nice, that was fast"),
    _r(6, 44, "Steve", "brain, can you give me a speed potion"),
    _r(7, 47, "AI", "Sure, Steve: should it last one minute or five?", "ai"),
]
# (text, who, what should happen, why); each asked as the line after PROBE_HISTORY. None is, or is
# close to, a line of jev/prompt.py's EXAMPLES: a probe that is one tests nothing.
PROBES = [
    ("hey I, can you put a fence round the farm", "Alex", TO_AI, "misheard name: 'hey I,' for 'hey AI,'"),
    ("aye give me a shield", "Steve", TO_AI, "misheard name: 'aye', a spelling the prompt never names"),
    ("hey A, light up the path to the village", "Alex", TO_AI, "misheard name: 'A' for 'AI'"),
    ("brane, build a wall round the farm", "Steve", TO_AI, "misheard name: 'brane' for 'brain'"),
    ("bring, give me a ladder", "Alex", TO_AI, "misheard name: 'bring' for 'brain'"),
    ("I'm saying today, give me a bow", "Alex", TO_AI, "misheard name: 'saying to AI' run into 'today'"),
    ("five", "Alex", TO_AI, "answers the AI's question, asked of Steve"),
    ("one minute is fine", "Steve", TO_AI, "answers the AI's question, asked of him"),
    ("Alex can you pass me some arrows", "Steve", "not", "an ask between players"),
    ("can you come help me with this creeper", "Alex", "not", "an ask between players, no name"),
    ("Steve come look at this cave", "Alex", "not", "talk between players right after the AI's question"),
    ("the AI made my axe out of stone lol", "Steve", "not", "about the AI"),
    ("it never hears me when I talk to the brain", "Alex", "not", "about the AI"),
    ("I think the brain is lagging tonight", "Steve", "not", "about the AI, naming it"),
    ("give me some of that wood", "Steve", "not", "an ask, no name: to Alex"),
    ("thanks", "Steve", "not", "no name, after the AI's question"),
]


def probe(args):
    cand = candidate(args.name)
    key, where = read_key(args.env or "", name="TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("no TYPESAFE_API_KEY in the environment or %s" % where)
    lookout = cand["lookout"] if cand["lookout"] is not None else prompt.DEFAULT_LOOKOUT
    menu = prompt.read_menu(args.menu)[0] if args.menu else ""
    asker = Asker(cand, key, lookout, menu)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(args.labels)), args.name)
    os.makedirs(out_dir, exist_ok=True)
    good = 0
    with open(os.path.join(out_dir, "probes.jsonl"), "w", encoding="utf-8") as f:
        for i, (text, who, want, why) in enumerate(PROBES):
            row = _r(100 + i, 52, who, text)
            got = asker.ask(row, sorted(PROBE_HISTORY, key=lambda r: (r["at"], r["id"]), reverse=True))
            ok = (got["class"] == TO_AI) == (want == TO_AI)
            good += ok
            f.write(json.dumps(dict({"text": text, "who": who, "want": want, "why": why, "ok": ok}, **got)) + "\n")
            ans = got["calls"][-1].get("answers", {})
            print("%s %-12s %-5s %-55s %s" % ("ok  " if ok else "MISS", got["class"], want[:5], repr(text)[:55],
                                              {k: v[:2] for k, v in ans.items()}), flush=True)
    print("%s: %d of %d probes right" % (args.name, good, len(PROBES)))


# ------------------------------------------------------------------ score

MUST_GO = ("named", "answer")
MUST_NOT = ("F", "U", "other")
REPORT = ("lost", "stt", "other_ask")
ORDER = ("named", "answer", "F", "U", "other", "lost", "stt", "other_ask")


def pctl(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0
    k = (len(xs) - 1) * p
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def score(args):
    base = os.path.dirname(os.path.abspath(args.labels))
    out = []
    for name in args.names:
        rows = read_rows(os.path.join(base, name, "rows.jsonl"))
        if not rows:
            out.append("## %s\n\nno rows\n" % name)
            continue
        by = {}
        for r in rows:
            by.setdefault(r["label"], []).append(r)
        calls = [c for r in rows for c in r["calls"] if "ms" in c]
        tokens = sum(c.get("tokens", 0) for c in calls)
        ms = [c["ms"] for c in calls]
        broke = [r for r in rows if (r["label"] in MUST_GO) != (r["class"] == TO_AI) and r["label"] in MUST_GO + MUST_NOT]
        out.append("## %s\n" % name)
        out.append("%d lines · pass bar: %s (%d line%s break it) · %d calls, $%.4f of Jev (%.0f tokens a call), "
                   "median %d ms, p90 %d ms%s\n" % (
                       len(rows), "PASS" if not broke else "FAIL", len(broke), "" if len(broke) == 1 else "s",
                       len(calls), tokens * PRICE_PER_INPUT_TOKEN, tokens / max(1, len(calls)),
                       statistics.median(ms) if ms else 0, pctl(ms, 0.9),
                       " · %d failed" % sum(1 for r in rows if r.get("failed")) if any(r.get("failed") for r in rows)
                       else ""))
        out.append("| label | lines | to the AI | context | not relevant |\n|---|---|---|---|---|")
        for lab in ORDER:
            rs = by.get(lab, [])
            if not rs:
                continue
            n = lambda c: sum(1 for r in rs if r["class"] == c)  # noqa: E731
            out.append("| %s | %d | %d | %d | %d |" % (lab, len(rs), n(TO_AI), n("context"), n("not_relevant")))
        out.append("")
        for title, keep in (("Breaking the pass bar", lambda r: r in broke),
                            ("Real asks the rule loses or keeps (lost, stt, other_ask)",
                             lambda r: r["label"] in REPORT)):
            rs = [r for r in rows if keep(r)]
            if not rs:
                continue
            out.append("**%s**\n" % title)
            for r in sorted(rs, key=lambda r: (r["n"] or 0, r["at"])):
                ans = r["calls"][-1].get("answers", {})
                short = "; ".join("%s %s %s" % (k, v[0], "-" if v[1] is None else "%.2f" % v[1])
                                  for k, v in ans.items())
                out.append("- #%s %s %s → **%s** (%s) · %s" % (
                    r["n"] or "-", r["who"], r["label"], r["class"], short,
                    prompt.cut(prompt.one_line(r["text"]), 90)))
            out.append("")
    text = "\n".join(out)
    print(text)
    if args.write:
        with open(os.path.join(base, "score.md"), "w", encoding="utf-8") as f:
            f.write(text + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    for p in (r,):
        p.add_argument("--db", required=True)
        p.add_argument("--lookout", required=True)
    r.add_argument("--menu", required=True)
    r.add_argument("--labels", required=True)
    r.add_argument("--env")
    r.add_argument("--only", help="LINE numbers, comma-separated")
    r.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("probe")
    p.add_argument("name")
    p.add_argument("--labels", required=True)
    p.add_argument("--menu")
    p.add_argument("--env")
    s = sub.add_parser("score")
    s.add_argument("names", nargs="+")
    s.add_argument("--labels", required=True)
    s.add_argument("--write", action="store_true")
    args = ap.parse_args()
    {"run": run, "probe": probe, "score": score}[args.cmd](args)


if __name__ == "__main__":
    main()
