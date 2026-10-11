"""The decision tree's questions, every wording version scored, and how each answer maps onto an
outcome: read by tree_bench.py (which asks them) and tree_score.py (which walks them). Standard
library only.

THE TREE, one Jev call per line, all nine questions sharing the filter's state:

- Level 1, who the line is said to. The AI: level 3. Anyone or anything else: level 2.
- Level 2, a line not said to the AI: is anything in it worth keeping? Keep: `context`, fed to the
  brain's context, no model call. Else `dropped`.
- Level 3, a line said to the AI: what does the speaker want? Something to change in the game:
  `brain`. A reply: `respond`. Nothing to answer: `noreply`, kept as context with no reply.

Each level has three members. A is the tree's own question, the only one the no-council variant
reads. B and C frame the same decision differently; the council weighs all three by confidence.

A version is the nine questions' wording. The option names (and so the outcome maps) never change
between versions; only the question and the options' descriptions do.
"""

from __future__ import annotations

LEVELS = ("l1", "l2", "l3")
MEMBERS = ("a", "b", "c")
NAMES = tuple(f"{lv}_{m}" for lv in LEVELS for m in MEMBERS)   # l1_a, l1_b, … l3_c
A_ONLY = tuple(f"{lv}_a" for lv in LEVELS)

# Each level's outcomes, and the cheaper mistake a clash goes to.
OUTCOMES = {"l1": ("ai", "not_ai"), "l2": ("keep", "drop"), "l3": ("brain", "respond", "noreply")}
CHEAPER = {"l1": "ai", "l2": "keep", "l3": "respond"}

# option -> the level's outcome, for every member
MAP = {
    "l1_a": {"the_ai": "ai", "a_player": "not_ai", "themselves": "not_ai", "no_one": "not_ai"},
    "l1_b": {"the_ai": "ai", "another_player": "not_ai", "nobody": "not_ai"},
    "l1_c": {"request_or_question_to_the_ai": "ai", "talk_between_players": "not_ai",
             "thinking_aloud": "not_ai", "only_a_reaction": "not_ai"},
    "l2_a": {"ai_work_bad": "keep", "plan_or_rule": "keep", "player_or_world_context": "keep",
             "the_moment": "drop", "reaction": "drop", "garbled": "drop"},
    "l2_b": {"yes_lasting": "keep", "no_this_moment": "drop"},
    "l2_c": {"information": "keep", "chatter": "drop", "unclear_speech": "drop"},
    "l3_a": {"a_change": "brain", "a_fix": "brain", "an_answer": "respond", "just_chatting": "respond",
             "only_thanks": "noreply"},
    "l3_b": {"no_something_must_change": "brain", "yes_a_reply_is_enough": "respond",
             "nothing_needs_answering": "noreply"},
    "l3_c": {"a_request": "brain", "a_complaint_about_the_ais_work": "brain", "a_question": "respond",
             "small_talk": "respond", "only_thanks": "noreply"},
}
# At level 2, member A's `ai_work_bad` keeps the line whatever the others say.
KEEP_ALONE = ("l2_a", "ai_work_bad")


# ------------------------------------------------------------------ v1: the drafts, as given

V1 = {
    "l1_a": ("Who is the speaker talking to?", {
        "the_ai": "Said to the AI. It names the AI (\"Claude\", \"AI\", or a name speech-to-text misheard), "
                  "asks or tells the AI something, or answers or follows up something the AI said or did.",
        "a_player": "Said to another player. Talk between players, even when it is about the AI or the game.",
        "themselves": "Said to themselves: thinking aloud, or narrating what they are doing, to no one in "
                      "particular.",
        "no_one": "Not really said to anyone: a reaction, an exclamation, filler, or speech too garbled to tell.",
    }),
    "l1_b": ("Who does the speaker expect to answer?", {
        "the_ai": "The AI. The speaker expects the AI to answer or to act: they named it, asked or told it "
                  "something, or are replying to what it said or did.",
        "another_player": "Another player. The speaker expects a player to answer, react or go along, even "
                          "when the AI or the game is the topic.",
        "nobody": "Nobody. No answer is expected: thinking aloud, narrating, a reaction, filler or garbled "
                  "speech.",
    }),
    "l1_c": ("What kind of line is it?", {
        "request_or_question_to_the_ai": "A request or a question to the AI, or a reply to it: it asks the AI "
                                         "to do or answer something, or answers or follows up what the AI said "
                                         "or did.",
        "talk_between_players": "Talk between players: said to another player, including talk about the AI "
                                "or the game.",
        "thinking_aloud": "Thinking aloud: the speaker narrates what they are doing, or muses to no one in "
                          "particular.",
        "only_a_reaction": "Only a reaction: an exclamation, a laugh, swearing, filler, or speech too garbled "
                           "to tell.",
    }),
    "l2_a": ("What in this line is worth keeping for the AI?", {
        "ai_work_bad": "Something the AI made or did isn't working, is too weak or too strong, is in the wrong "
                       "place, or is gone.",
        "plan_or_rule": "A plan, a decision or a standing rule for later: what the players will do, what is "
                        "allowed or banned.",
        "player_or_world_context": "Information about a player or the world that will still be true and useful "
                                   "in five minutes: where a base is, what someone likes or hates, who is on "
                                   "whose team, what someone is trying to do, what is where in the world.",
        "the_moment": "Talk about this moment only: what is happening right now, chatter, jokes, banter that "
                      "won't matter in five minutes.",
        "reaction": "Nothing but a reaction: an exclamation, swearing, \"nice\", \"oh my god\", \"wow\".",
        "garbled": "Speech-to-text garbage: words that don't make sense together.",
    }),
    "l2_b": ("Will this line still matter in five minutes?", {
        "yes_lasting": "Yes, it says something lasting: a plan, a rule, a fact about a player or the world, or "
                       "a problem with something the AI made, that will still be true and useful in five "
                       "minutes.",
        "no_this_moment": "No, it is about this moment only: what is happening right now, chatter, a joke, a "
                          "reaction, or words that make no sense.",
    }),
    "l2_c": ("What kind of line is it?", {
        "information": "Information: it tells something about a player, the world, a plan or a rule, or "
                       "something the AI made, that someone could use later.",
        "chatter": "Chatter: banter, jokes, reactions, or running commentary on what is happening right now.",
        "unclear_speech": "Unclear speech: speech-to-text garbage, or fragments whose meaning can't be made "
                          "out.",
    }),
    "l3_a": ("What does the speaker want from the AI?", {
        "a_change": "Something to happen in the game: make, give, build, spawn, teleport, change or remove "
                    "something.",
        "a_fix": "A change to something the AI already made or did: fix it, undo it, stop it, or make it "
                 "stronger, weaker or bigger.",
        "an_answer": "An answer: a question about the game, Minecraft, the players or the AI itself, which a "
                     "chat reply settles.",
        "just_chatting": "Conversation with the AI: a greeting, a joke, an opinion, a comment on what's going "
                         "on, with nothing to do in the game.",
        "only_thanks": "Nothing to answer: only thanks, \"ok\", \"cool\", \"wow\" or another acknowledgement.",
    }),
    "l3_b": ("If the AI only replied in chat and changed nothing in the game, would the speaker be satisfied?", {
        "no_something_must_change": "No, something must change in the game: the speaker wants something made, "
                                    "given, moved, fixed, undone or stopped.",
        "yes_a_reply_is_enough": "Yes, a reply is enough: the speaker wants an answer, an opinion or a bit of "
                                 "conversation.",
        "nothing_needs_answering": "Nothing needs answering: the line is only thanks, an acknowledgement or a "
                                   "reaction.",
    }),
    "l3_c": ("What kind of line is it?", {
        "a_request": "A request: it asks the AI to do, make, give, change or remove something in the game.",
        "a_complaint_about_the_ais_work": "A complaint about the AI's work: something the AI made or did is "
                                          "wrong, broken, too weak or too strong, or gone.",
        "a_question": "A question: it asks the AI about the game, Minecraft, the players or the AI itself.",
        "small_talk": "Small talk: a greeting, a joke, an opinion or a comment, with nothing to do in the game.",
        "only_thanks": "Only thanks: thanks, \"ok\", \"cool\" or another acknowledgement, with nothing to "
                       "answer.",
    }),
}

# ------------------------------------------------------------------ v2: v1, three general changes
# 1. Level 3: a request counts however casually or half-heard it is said, and a yes, a no or a
#    choice answering what the AI asked before doing something is part of the ask (Jev's own rule);
#    a complaint said to the AI about its work is a fix; an answer is one with nothing to change.
# 2. Level 2, member A: a kept fact is a lasting one, and what someone is doing or seeing this
#    second is the moment, so the two options split on time alone.
# 3. Level 2, members B and C: a plan, a decision or a rule, and a problem with something the AI
#    made, count as lasting information, as they do for A.

V2 = {k: (q, dict(opts)) for k, (q, opts) in V1.items()}
V2["l3_a"][1].update({
    "a_change": "Something to happen in the game, however casually or half-heard it is said: make, give, build, "
                "spawn, teleport, change or remove something, or a yes, a no or a choice answering what the AI "
                "asked before doing it.",
    "a_fix": "A change to something the AI already made or did: fix it, undo it, stop it, or make it stronger, "
             "weaker or bigger, or a complaint to the AI that it isn't working, is too weak or too strong, or is "
             "gone.",
    "an_answer": "An answer: a question about the game, Minecraft, the players or the AI itself, which a chat "
                 "reply settles, with nothing to change in the game.",
})
V2["l3_b"][1].update({
    "no_something_must_change": "No, something must change in the game: the speaker wants something made, given, "
                                "moved, fixed, undone or stopped, or is answering what the AI asked before doing "
                                "it.",
})
V2["l3_c"][1].update({
    "a_request": "A request, however casually or half-heard it is said: it asks the AI to do, make, give, change or "
                 "remove something in the game, or answers what the AI asked before doing it.",
})
V2["l2_a"][1].update({
    "player_or_world_context": "A lasting fact about a player or the world, still true and useful in five minutes: "
                               "where a base is, what someone likes or hates, who is on whose team, what someone "
                               "is trying to do, what is where in the world.",
    "the_moment": "Talk about this moment only: what is happening right now, what someone is doing or seeing this "
                  "second, chatter, jokes, banter that won't matter in five minutes.",
})
V2["l2_b"][1].update({
    "yes_lasting": "Yes, it says something lasting: a plan, a decision or a rule for what the players will do, a "
                   "fact about a player or the world, or a problem with something the AI made, that will still be "
                   "true and useful in five minutes.",
})
V2["l2_c"][1].update({
    "information": "Information: a plan, a decision or a rule, a fact about a player or the world, or a problem "
                   "with something the AI made: something the AI could use later.",
})

VERSIONS = {"v1": V1, "v2": V2}


def check() -> None:
    """Every version names the same options as MAP, so a version only changes wording."""
    for v, qs in VERSIONS.items():
        assert tuple(qs) == NAMES, (v, tuple(qs))
        for k, (_, opts) in qs.items():
            assert set(opts) == set(MAP[k]), (v, k, set(opts) ^ set(MAP[k]))


check()
