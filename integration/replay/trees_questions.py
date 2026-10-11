"""Five ways to build Jev's routing tree: the questions, every wording version scored, and the
code that walks each approach's answers to an outcome. Read by trees_bench.py (which asks them)
and trees_score.py (which walks them). Standard library only.

Every approach is ONE Jev call per line: all its questions are asked at once, on the filter's
state, and code walks the answers. The outcomes are the same four for all of them:

- `brain`: a brain call (GPT-6.1 Sol, about $0.0085).
- `respond`: a Respond call ($0.0015), which may hand the line up to the brain.
- `context`: stored, for a memory system later; no model reads it now. $0.
- `dropped`: $0.

THE APPROACHES

- A. Who, then what (tree_questions.py's v2, member A only): who the line is said to; then, said
  to the AI, what the speaker wants (brain / Respond / no reply), else whether it is worth keeping.
- B. Kind first: one six-way question, "what kind of line is it?", each kind with one home; talk
  to a player or to themselves goes on to one question, lasting or this moment.
- C. Cheapest first, an elimination ladder: four rungs asked at once, code takes the first that
  fires: noise (dropped), an explicit request to the AI (brain), said to the AI (Respond),
  something lasting (context), else dropped.
- D. Consequence first, two keys for the brain: "what should happen with this line?" and "if the
  AI only replied, how would the speaker feel?". The brain only when both say something must
  change; act with the second key disagreeing goes to Respond.
- E. Observable signals, the tree in code: who the line addresses, its form, its topic and how
  long it stays true; `route_e` combines them.

A version is an approach's wording. Its option names (and so the walk) never change between
versions of one approach, except E's rules, which are versioned in code beside the wording.
"""

from __future__ import annotations

OUTS = ("brain", "respond", "context", "dropped")

# ------------------------------------------------------------------ A: who, then what (v2, member A)
# Reused from tree_questions.py; not asked again. Walked here with A's three questions alone.

A_MAP = {
    "l1_a": {"the_ai": "ai", "a_player": "not_ai", "themselves": "not_ai", "no_one": "not_ai"},
    "l2_a": {"ai_work_bad": "context", "plan_or_rule": "context", "player_or_world_context": "context",
             "the_moment": "dropped", "reaction": "dropped", "garbled": "dropped"},
    "l3_a": {"a_change": "brain", "a_fix": "brain", "an_answer": "respond", "just_chatting": "respond",
             "only_thanks": "context"},
}


def route_a(a: dict, version: str = "v2") -> dict:
    l1 = a.get("l1_a")
    if l1 and A_MAP["l1_a"][l1[0]] == "ai":
        l3 = a.get("l3_a")
        return {"out": A_MAP["l3_a"][l3[0]] if l3 else "respond", "path": f"the_ai > {l3[0] if l3 else '-'}"}
    l2 = a.get("l2_a")
    return {"out": A_MAP["l2_a"][l2[0]] if l2 else "context",
            "path": f"{l1[0] if l1 else '-'} > {l2[0] if l2 else '-'}"}


# ------------------------------------------------------------------ B: kind first

B_MAP = {
    "b_kind": {"request_to_the_ai": "brain", "question_or_talk_to_the_ai": "respond",
               "comment_on_the_ais_work": "context", "talk_to_a_player": "level2",
               "talk_to_themselves": "level2", "noise_or_reaction": "dropped"},
    "b_keep": {"a_plan_or_rule": "context", "lasting_info": "context", "only_this_moment": "dropped"},
}

B1 = {
    "b_kind": ("What kind of line is it?", {
        "request_to_the_ai": "An explicit request to the AI to do something in the game: it tells or asks the AI to "
                             "make, give, build, spawn, teleport, change, fix, undo or stop something, however "
                             "casually it is said, or follows up an earlier request with what to change.",
        "question_or_talk_to_the_ai": "A question to the AI, or conversation with it: it asks the AI something a "
                                      "chat reply settles, greets it, jokes with it or tells it an opinion, with "
                                      "nothing to do in the game.",
        "comment_on_the_ais_work": "A comment on something the AI made or did, that tells it to do nothing: praise, "
                                   "a complaint, or a remark that it works, doesn't work, is too strong, too weak "
                                   "or gone.",
        "talk_to_a_player": "Talk to another player: said to someone else in the game, even when it is about the AI "
                            "or the game.",
        "talk_to_themselves": "Talk to themselves: thinking aloud, or narrating what they are doing, to no one in "
                              "particular.",
        "noise_or_reaction": "Noise, filler or a reaction: an exclamation, a laugh, swearing, \"ok\", \"yeah\", "
                             "\"wow\", thanks, or speech too garbled to make out.",
    }),
    "b_keep": ("What does the line tell that could still be used later?", {
        "a_plan_or_rule": "A plan, a decision or a rule: what the players will do, build, fight or find next, or "
                          "what is allowed or banned.",
        "lasting_info": "A lasting fact about a player or the world, still true in five minutes: where a base or a "
                        "build is, who is on whose team, what someone likes, hates or is trying to do.",
        "only_this_moment": "Only this moment: what is happening or being seen right now, a reaction, a joke, "
                            "banter or running commentary that won't matter in five minutes.",
    }),
}


# B2: B1 with two general changes, from patterns in the first half's answers.
# 1. A yes, a no or a choice answering what the AI asked before doing something is part of the
#    request (Jev's own rule); B1 read such answers as comments.
# 2. A complaint said TO the AI about its work is a request to fix it; a comment on the AI's work
#    is one made to a player or to no one, or praise. B1 put every complaint in context.
B2 = {k: (q, dict(opts)) for k, (q, opts) in B1.items()}
B2["b_kind"][1].update({
    "request_to_the_ai": "An explicit request to the AI to do something in the game: it tells or asks the AI to "
                         "make, give, build, spawn, teleport, change, fix, undo or stop something, however casually "
                         "it is said; follows up an earlier request with what to change; tells the AI that "
                         "something it made is broken, too weak or too strong; or answers yes, no or a choice to "
                         "what the AI asked before doing something.",
    "comment_on_the_ais_work": "A comment on something the AI made or did, said to a player or to no one, or "
                               "praise for it: it works, it's cool, it doesn't work, it's too strong, too weak or "
                               "gone, without asking the AI for anything.",
})


def route_b(a: dict, version: str = "B1") -> dict:
    k = a.get("b_kind")
    if not k:
        return {"out": "context", "path": "-"}
    out = B_MAP["b_kind"][k[0]]
    if out != "level2":
        return {"out": out, "path": k[0]}
    keep = a.get("b_keep")
    return {"out": B_MAP["b_keep"][keep[0]] if keep else "context", "path": f"{k[0]} > {keep[0] if keep else '-'}"}


# ------------------------------------------------------------------ C: cheapest first, an elimination ladder

C_RUNGS = (   # (question, the options that fire it, its outcome), in the ladder's order
    ("c1_noise", ("only_filler_or_a_reaction", "garbled_speech"), "dropped"),
    ("c2_request", ("an_explicit_request_to_the_ai",), "brain"),
    ("c3_to_ai", ("a_question_to_the_ai", "talk_with_the_ai"), "respond"),
    ("c4_lasting", ("a_plan_or_rule", "lasting_player_or_world_info", "a_problem_with_the_ais_work"), "context"),
)
C_OTHERWISE = "dropped"

C1 = {
    "c1_noise": ("How much does the line say?", {
        "only_filler_or_a_reaction": "Only filler or a reaction: \"so\", \"um\", \"yeah\", \"okay\", a laugh, "
                                     "swearing, or an exclamation like \"wow\", \"nice\" or \"oh my god\", and "
                                     "nothing more.",
        "garbled_speech": "Garbled speech: speech-to-text words that don't make sense together, whose meaning "
                          "can't be made out.",
        "something_with_meaning": "Something with meaning: a request, a question, an idea, a fact, a plan or a "
                                  "comment, even short, mixed with filler or partly garbled.",
    }),
    "c2_request": ("What, if anything, does the line ask to be done?", {
        "an_explicit_request_to_the_ai": "An explicit request to the AI: it tells or asks the AI to make, give, "
                                         "build, spawn, teleport, change, fix, undo or stop something in the game, "
                                         "however casually, or answers what the AI asked before doing it.",
        "only_implied": "Something wanted but only implied: a wish, a complaint or a remark that something should "
                        "be different, without telling anyone to do it.",
        "a_request_to_a_player": "A request to another player: it asks or tells a player, not the AI, to do "
                                 "something.",
        "no_request": "No request at all: a question, a comment, a story, a joke or a reaction, asking nothing to "
                      "be done.",
    }),
    "c3_to_ai": ("Who is the line said to?", {
        "a_question_to_the_ai": "A question to the AI: it asks the AI about the game, Minecraft, the players or "
                                "the AI itself.",
        "talk_with_the_ai": "Talk with the AI: a greeting, a joke, an opinion or a reply to what the AI said, "
                            "said to the AI.",
        "said_to_a_player": "Said to another player: talk between players, even when it is about the AI or the "
                            "game.",
        "said_to_no_one": "Said to no one: thinking aloud, narrating, or a remark to the room.",
    }),
    "c4_lasting": ("What could the AI still use from this line in five minutes?", {
        "a_plan_or_rule": "A plan, a decision or a rule: what the players will do, build, fight or find next, or "
                          "what is allowed or banned.",
        "lasting_player_or_world_info": "A lasting fact about a player or the world: where a base or a build is, "
                                        "who is on whose team, what someone likes, hates or is trying to do.",
        "a_problem_with_the_ais_work": "A problem with something the AI made or did: it doesn't work, is too "
                                       "strong or too weak, is in the wrong place, or is gone.",
        "nothing_beyond_this_moment": "Nothing beyond this moment: what is happening right now, chatter, a joke "
                                      "or a reaction that won't matter in five minutes.",
    }),
}


def route_c(a: dict, version: str = "C1") -> dict:
    for q, fires, out in C_RUNGS:
        got = a.get(q)
        if got and got[0] in fires:
            return {"out": out, "path": f"{q}={got[0]}"}
    return {"out": C_OTHERWISE, "path": "otherwise"}


# ------------------------------------------------------------------ D: consequence first, two keys

D_MAP = {
    "d_what": {"the_ai_must_act": "act", "the_ai_should_answer": "respond", "remember_it": "context",
               "ignore_it": "dropped"},
    "d_feel": {"fine_a_reply_is_all": "reply", "let_down_wanted_something_done": "act",
               "not_talking_to_the_ai": "not_ai"},
}

D1 = {
    "d_what": ("What should happen with this line?", {
        "the_ai_must_act": "The AI must act in the game: the speaker asked it to make, give, build, move, change, "
                           "fix, undo or stop something.",
        "the_ai_should_answer": "The AI should answer in chat: the speaker asked it a question or talked to it, "
                                "and a chat reply is all that's needed.",
        "remember_it": "Remember it for later: no one needs an answer now, but it tells something lasting: a plan, "
                       "a rule, a fact about a player or the world, or a problem with what the AI made.",
        "ignore_it": "Ignore it: talk that matters only this moment, said between players or to no one, a "
                     "reaction, filler or garbled speech.",
    }),
    "d_feel": ("If the AI only replied in chat and nothing changed in the game, how would the speaker feel?", {
        "fine_a_reply_is_all": "Fine: a reply is all they wanted from the AI, an answer, an opinion or a bit of "
                               "conversation.",
        "let_down_wanted_something_done": "Let down: they wanted the AI to get something done in the game, made, "
                                          "given, moved, fixed or stopped.",
        "not_talking_to_the_ai": "They wouldn't notice: they weren't talking to the AI, but to a player or to no "
                                 "one.",
    }),
}


# D2: D1 with two general changes, from patterns in the first half's answers.
# 1. Acting includes a standing instruction for how the AI should act from now on, and a yes, a no
#    or a choice answering what the AI asked before doing something (Jev's own rule).
# 2. What to remember is a plan or a rule the players make among themselves, not a rule given to
#    the AI: D1's "a rule" took standing instructions to the AI.
D2 = {k: (q, dict(opts)) for k, (q, opts) in D1.items()}
D2["d_what"][1].update({
    "the_ai_must_act": "The AI must act in the game: the speaker asked it to make, give, build, move, change, fix, "
                       "undo or stop something, told it how to act from now on, or answered yes, no or a choice to "
                       "what it asked before doing something.",
    "remember_it": "Remember it for later: no one needs an answer now, but it tells something lasting: a plan or a "
                   "rule the players make among themselves, a fact about a player or the world, or a problem with "
                   "what the AI made.",
})


def route_d(a: dict, version: str = "D1") -> dict:
    w = a.get("d_what")
    if not w:
        return {"out": "context", "path": "-"}
    what = D_MAP["d_what"][w[0]]
    if what != "act":
        return {"out": what, "path": w[0]}
    f = a.get("d_feel")
    if f and D_MAP["d_feel"][f[0]] == "act":
        return {"out": "brain", "path": f"{w[0]} + {f[0]}"}
    return {"out": "respond", "path": f"{w[0]} + {f[0] if f else '-'}"}


# ------------------------------------------------------------------ E: observable signals, the tree in code

E1 = {
    "e_addressed": ("Who does the line address?", {
        "names_the_ai": "It names the AI: \"Claude\", \"AI\", \"bot\", or a name speech-to-text misheard, like "
                        "\"Cloud\" or \"Clod\".",
        "you_with_no_name": "It speaks to \"you\", or gives an order or asks a question, without naming anyone.",
        "names_a_player": "It names another player, or speaks to the group: \"you guys\", \"bro\", a player's "
                          "name.",
        "no_one": "It addresses no one: a remark about things, thinking aloud, narrating, or a reaction.",
    }),
    "e_form": ("What form does the line take?", {
        "an_instruction_or_request": "An instruction or request: somewhere in it, someone is told or asked to do "
                                     "something (\"give me\", \"can you make\", \"teleport me\", \"stop\").",
        "a_question": "A question: it asks something, and asks nothing to be done.",
        "a_statement": "A statement: it tells or describes something, and asks nothing.",
        "only_an_exclamation": "Only an exclamation: a reaction, a laugh, swearing, filler, or words too garbled "
                               "to read.",
    }),
    "e_topic": ("What is the line about?", {
        "something_the_ai_made_or_did": "Something the AI made, gave, changed or said: an item, a power, a build, "
                                        "an effect, or the AI itself.",
        "a_player": "A player: what someone is doing, has, feels, or where they are.",
        "the_world_or_the_game": "The world or the game: places, mobs, blocks, the server, or how Minecraft works.",
        "nothing_in_particular": "Nothing in particular: filler, a reaction, or chatter with no clear subject.",
    }),
    "e_time": ("How long will what the line says stay true or useful?", {
        "still_true_in_five_minutes": "Still true in five minutes: a plan, a rule, a fact about a player or the "
                                      "world, or a problem with something that stays.",
        "only_this_moment": "Only this moment: what is happening or being seen right now, or a reaction to it.",
    }),
}


def _e(a: dict, q: str) -> str:
    got = a.get(q)
    return got[0] if got else "-"


def route_e1(a: dict) -> dict:
    """E's rules, r1: written before any answers were read.

    1. Only an exclamation: dropped.
    2. Names the AI: an instruction or request goes to the brain; anything else to Respond.
    3. "You" with no name: an instruction or request about something the AI made or the world goes to the
       brain; a question about the AI's work or the world to Respond.
    4. The rest: still true in five minutes, or about something the AI made, is context; else dropped.
    """
    who, form, topic, time = (_e(a, q) for q in ("e_addressed", "e_form", "e_topic", "e_time"))
    if form == "only_an_exclamation":
        return {"out": "dropped", "path": "exclamation"}
    if who == "names_the_ai":
        return {"out": "brain" if form == "an_instruction_or_request" else "respond", "path": f"ai, {form}"}
    if who == "you_with_no_name" and topic in ("something_the_ai_made_or_did", "the_world_or_the_game"):
        if form == "an_instruction_or_request":
            return {"out": "brain", "path": f"you, {form}, {topic}"}
        if form == "a_question":
            return {"out": "respond", "path": f"you, {form}, {topic}"}
    lasting = time == "still_true_in_five_minutes" or topic == "something_the_ai_made_or_did"
    return {"out": "context" if lasting else "dropped", "path": f"{who}, {topic}, {time}"}


def route_e2(a: dict) -> dict:
    """E's rules, r2: r1 with two general changes, set on the first half's answers.

    1. Only an exclamation: dropped.
    2. An instruction or request that names the AI, or speaks to "you" with no name, goes to the brain,
       whatever its topic (r1 asked the topic of "you", and lost "can you teleport me" to `a_player`).
    3. An instruction or request that names a player goes to Respond, which hands it up if it was meant
       for the AI.
    4. Any other line that names the AI goes to Respond; a question to "you" about something the AI made
       or the world goes to Respond.
    5. The rest: still true in five minutes, or about something the AI made, is context; else dropped.
    """
    who, form, topic, time = (_e(a, q) for q in ("e_addressed", "e_form", "e_topic", "e_time"))
    if form == "only_an_exclamation":
        return {"out": "dropped", "path": "exclamation"}
    if form == "an_instruction_or_request":
        if who in ("names_the_ai", "you_with_no_name"):
            return {"out": "brain", "path": f"{who}, instruction"}
        if who == "names_a_player":
            return {"out": "respond", "path": f"{who}, instruction"}
    if who == "names_the_ai":
        return {"out": "respond", "path": f"ai, {form}"}
    if who == "you_with_no_name" and form == "a_question" and topic in ("something_the_ai_made_or_did",
                                                                         "the_world_or_the_game"):
        return {"out": "respond", "path": f"you, {form}, {topic}"}
    lasting = time == "still_true_in_five_minutes" or topic == "something_the_ai_made_or_did"
    return {"out": "context" if lasting else "dropped", "path": f"{who}, {topic}, {time}"}


def route_e3(a: dict) -> dict:
    """E's rules, r3: r2 without its rule 3; an instruction that names a player is context or dropped
    by rule 5, like any other line not for the AI."""
    got = route_e2(a)
    if got["path"] == "names_a_player, instruction":
        topic, time = _e(a, "e_topic"), _e(a, "e_time")
        lasting = time == "still_true_in_five_minutes" or topic == "something_the_ai_made_or_did"
        return {"out": "context" if lasting else "dropped", "path": f"names_a_player, {topic}, {time}"}
    return got


E_RULES = {"r1": route_e1, "r2": route_e2, "r3": route_e3}


def route_e(a: dict, version: str = "E1/r1") -> dict:
    rules = version.split("/")[1] if "/" in version else "r1"
    return E_RULES[rules](a)


# ------------------------------------------------------------------ the registry

# approach -> {wording version: questions}
VERSIONS = {
    "B": {"B1": B1, "B2": B2},
    "C": {"C1": C1},
    "D": {"D1": D1, "D2": D2},
    "E": {"E1": E1},
}
ROUTES = {"A": route_a, "B": route_b, "C": route_c, "D": route_d, "E": route_e}
NAMES = {"A": "A. Who, then what", "B": "B. Kind first", "C": "C. Cheapest first, a ladder",
         "D": "D. Consequence first, two keys", "E": "E. Observable signals, the tree in code"}
MAPS = {"B": B_MAP, "D": D_MAP}


def questions_of(version: str) -> dict:
    for approach, vs in VERSIONS.items():
        if version in vs:
            return vs[version]
    raise KeyError(version)


def approach_of(version: str) -> str:
    return version[0]


def check() -> None:
    """Every version of an approach names the same questions and options as its first."""
    for approach, vs in VERSIONS.items():
        first = next(iter(vs.values()))
        for v, qs in vs.items():
            assert v.startswith(approach), v
            assert tuple(qs) == tuple(first), (v, tuple(qs))
            for k, (_, opts) in qs.items():
                assert tuple(opts) == tuple(first[k][1]), (v, k)
    for k, m in B_MAP.items():
        assert set(m) == set(B1[k][1]), k
    for k, m in D_MAP.items():
        assert set(m) == set(D1[k][1]), k
    for q, fires, _ in C_RUNGS:
        assert set(fires) <= set(C1[q][1]), q


check()
