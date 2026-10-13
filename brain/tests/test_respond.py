"""The conversation agent (Respond): the schema it decides first in, the prompt's opening and tail
(the world, TASKS, WORK UNDER WAY, what is new, then the line or the report), its call's usage row,
and what code does after the call: the guard on a reply that promises with no hand-off, the DONE
check on a hand-off's reply, the cut of a reply too long for one chat line, and a report answer's
fixes (what is left, the record's name). Its own promises(), `busy` included."""

import pytest
from vfg_llm import LlmError

from brain.respond import (PROMPT, REPLY_CUT, ReportAnswer, ReportEvent, Respond, Response, claims_done, cut,
                           promises)
from brain.tasks import Tasks
from brain.world import Seen

from conftest import FakeAsk, catalog, rows

SEEN = Seen(players="PLAYERS ONLINE:\n- Steve at 10 64 -20 in overworld: 14.5/20 health, survival",
            clock="TIME: day 3, 12:00. WEATHER: clear.", running="RUNNING NOW: none", places="",
            incantations="INCANTATIONS: none")
TASKS = "TASKS: none yet"
UNDER = "WORK UNDER WAY:\n- Steve: “build me a bridge” — being done now (12 s)"
FROST = {"name": "frost_bow_alex", "description": "A frost bow for Alex: its arrows freeze what they hit.",
         "player": "Alex", "status": "open", "line": "make me a bow that freezes things",
         "brief": "THE PLAYER'S WORDS\nmake me a bow that freezes things\n\nDONE WHEN\n- an arrow freezes a mob\n"
                  "- an arrow freezes a player",
         "created_at": "2026-10-10T18:20:00.000Z", "updated_at": "2026-10-10T18:20:00.000Z"}


def line(seq: int = 7, who: str = "Steve", text: str = "lol make it rain", *before: str):
    """A context holding `before` and the line, and the line's row."""
    ctx = rows(*before, f"LINE#{seq} 18:29:59 {who} voice conversational 0.9 - | {text}")
    return ctx, ctx.rows[-1]


def agent(**answers) -> tuple[Respond, FakeAsk]:
    ask = FakeAsk(**answers)
    return Respond(catalog(), ask, owner="KrownJM"), ask


async def said(answer: dict, text: str = "lol make it rain", *, tasks: str = TASKS, busy=(), who: str = "Steve"):
    """The Response the agent gives `text` when the model answers `answer`."""
    r, _ = agent(Response=answer)
    ctx, row = line(7, who, text)
    return (await r.answer(row, ctx, SEEN, tasks, UNDER, [], busy=busy)).value


# ------------------------------------------------------------------ the schema

def test_it_decides_first_and_always_writes_its_reason():
    schema = Response.model_json_schema()                  # what the model is shown, and writes in this order
    assert list(schema["properties"]) == ["reason", "hand_off", "reply"]
    # every property required, its default kept: a hand_off left out would hand off the talk beside it
    assert schema["required"] == ["reason", "hand_off", "reply"] and schema["properties"]["hand_off"]["default"] is True
    assert schema["properties"]["reply"]["default"] == ""
    report = ReportAnswer.model_json_schema()
    assert list(report["properties"]) == ["reason", "hand_off", "left", "status", "reply", "record"]
    assert report["required"] == ["reason", "hand_off", "left", "status", "reply", "record"]
    assert report["properties"]["hand_off"]["default"] is False
    assert report["properties"]["status"]["enum"] == ["done", "failed"]


# ------------------------------------------------------------------ a line

async def test_a_line_is_one_call_its_usage_row_respond_and_its_seq_its_prompt_the_opening_then_the_tail():
    r, ask = agent(Response={"reason": "a greeting", "hand_off": False, "reply": "hey Steve!"})
    ctx, row = line(7, "Steve", "hi AI", "GAME#5 18:29:40 join Steve | Steve joined the game")
    new = [ctx.rows[0]]
    got = await r.answer(row, ctx, SEEN, TASKS, UNDER, new)
    assert (got.value.hand_off, got.value.reply) == (False, "hey Steve!")
    call, = ask.calls
    assert (call["output"], call["component"], call["call_id"]) == ("Response", "respond", "#7")
    assert call["cached"] == ctx.cached(r.opening(), 5)     # the opening marked, then the conversation's blocks
    opening, marked = call["cached"][0]
    assert marked and "THE OWNER: KrownJM." in opening and "{owner}" not in opening
    assert "WHAT THE AI CAN DO" in opening and "\n- teleport" in opening
    assert opening.endswith("THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                            "player's line Jev's class):\n")
    tail = call["prompt"]
    heads = ["THE WORLD NOW:", "TASKS: none yet", "WORK UNDER WAY:\n- Steve: “build me a bridge”",
             "NEW SINCE THE LAST READ", "Steve joined the game", "THE LINE #7"]
    assert [tail.index(h) for h in heads] == sorted(tail.index(h) for h in heads)
    assert "14.5/20 health" in tail and tail.endswith("THE LINE #7, 18:29:59: Steve said: hi AI\n")


async def test_work_under_way_is_the_cores_text_its_heading_put_on_when_it_has_none():
    r, ask = agent(Response={"reason": "talk", "hand_off": False, "reply": ""})
    ctx, row = line()
    for under, shown in (("", "WORK UNDER WAY: nothing"),
                         ("WORK UNDER WAY: no brain loops", "WORK UNDER WAY: no brain loops"),
                         ("- Alex: “a stack of torches” — being done now (3 s)",
                          "WORK UNDER WAY (every ask being done now, or done a moment ago; every player's):\n"
                          "- Alex: “a stack of torches” — being done now (3 s)")):
        await r.answer(row, ctx, SEEN, TASKS, under, [])
        assert f"{TASKS}\n\n{shown}\n\nNEW SINCE THE LAST READ" in ask.calls[-1]["prompt"]


async def test_a_hand_off_may_carry_its_acknowledgement_and_silence_is_an_empty_reply():
    assert (await said({"reason": "rain now", "hand_off": True, "reply": "On it!"})).reply == "On it!"
    got = await said({"reason": "rain now"})                   # the defaults: a hand-off, nothing said
    assert (got.hand_off, got.reply) == (True, "")
    got = await said({"reason": "thanks", "hand_off": False, "reply": "  "}, "thanks AI")
    assert (got.hand_off, got.reply) == (False, "")
    # with a hand-off a promise is the acknowledgement: no guard
    promise = "I'll make it rain!"
    assert (await said({"reason": "rain", "hand_off": True, "reply": promise})).reply == promise


async def test_a_failed_call_is_the_callers_to_handle():
    r, _ = agent(Response=LlmError("no answer within 20 s"))
    ctx, row = line()
    with pytest.raises(LlmError):
        await r.answer(row, ctx, SEEN, TASKS, UNDER, [])


# ------------------------------------------------------------------ the guard

async def test_a_reply_with_no_hand_off_that_promises_is_handed_off_with_it_and_one_that_offers_without(capsys):
    got = await said({"reason": "a question", "hand_off": False, "reply": "Want me to make it rain?"})
    assert (got.hand_off, got.reply) == (True, "")                    # a question waiting for a yes: dropped
    assert got.reason == "a question · handed off in code: an offer"
    assert ('respond guard #7 an offer, handed off, dropped: an offer: "Want me to make it rain?"'
            in capsys.readouterr().out)                                # the log alone
    got = await said({"reason": "rain", "hand_off": False, "reply": "Sure, I'll make it rain!"})
    assert (got.hand_off, got.reply, got.reason) == (True, "Sure, I'll make it rain!",
                                                     "rain · handed off in code: a promise")
    assert ('respond guard #7 a promise, handed off, kept as the acknowledgement: "Sure, I\'ll make it rain!"'
            in capsys.readouterr().out)
    long = "Sure thing, I'll make it rain on the whole base!"                  # over the prompt's 40: kept whole
    assert (await said({"reason": "rain", "hand_off": False, "reply": long})).reply == long
    got = await said({"reason": "rain", "hand_off": False, "reply": "Done! Enjoy the rain."})  # says it is done
    assert (got.hand_off, got.reply) == (True, "")
    assert 'a promise, handed off, dropped: it says it is done: "Done! Enjoy the rain."' in capsys.readouterr().out
    ack = "Got it, I'll ask you before giving Alex anything."  # a rule for later: said
    got = await said({"reason": "a rule for later", "hand_off": False, "reply": ack}, "ask me first next time")
    assert (got.hand_off, got.reply) == (False, ack)


async def test_a_status_passes_for_a_player_with_work_under_way_a_loop_or_a_task_and_is_a_promise_else():
    status = {"reason": "asks after it", "hand_off": False, "reply": "Hang tight, it's coming."}
    assert (await said(status, "is it done yet", busy={"Steve"})).hand_off is False          # a loop thinking
    tasks = Tasks(rows=[{**FROST, "player": "Steve"}]).show(["frost_bow_alex"])               # an implementer
    assert (await said(status, "is it done yet", tasks=tasks)).hand_off is False
    for busy in ((), ("Alex",)):                       # nothing of his: a promise, kept as the acknowledgement
        got = await said(status, "is it done yet", busy=busy)
        assert (got.hand_off, got.reply) == (True, "Hang tight, it's coming.")


def test_promises_takes_the_players_with_work_under_way_and_nothing_else_changes():
    status = "Hang tight, it's coming."
    assert [promises(status, TASKS, "Steve", busy=b) for b in ((), ("Alex",), ("Steve",), ["Alex", "Steve"])] == \
        ["a promise", "a promise", "", ""]
    # offers and promises are never a status, whoever is busy; offers=False as the brain's check calls it
    assert promises("Want me to make it rain?", TASKS, "Steve", busy=("Steve",)) == "an offer"
    assert promises("I'll make it rain!", TASKS, "Steve", busy=("Steve",)) == "a promise"
    assert promises("Want me to make it rain?", TASKS, "Steve", offers=False, busy=("Steve",)) == ""
    assert promises("Still building it.", TASKS, "Steve") == ""          # the answer to "is it done yet?"
    # "tun": tune, tuning; slipped through on the night's bench, never a tunnel or a tuna
    assert promises("I'm just tuning it so it hits harder.", TASKS, "Steve") == "a promise"
    assert promises("I'll tune the damage up for you.", TASKS, "Steve") == "a promise"
    assert promises("Nice tunnel! Tuna is not a Minecraft fish though.", TASKS, "Steve") == ""
    # crank, dial, bump, tweak, and "I can <verb> it up/down/back": offers that slipped through on the bench
    assert [promises(r, TASKS, "Steve") for r in (
        "I can crank it up or down whenever.", "I can dial that back if it's too much.", "I'm bumping the damage up.",
        "I'll tweak the range for you.", "I can turn them down.", "I can hear it, loud and clear.")] == \
        ["an offer", "an offer", "a promise", "a promise", "an offer", ""]
    # "Still on it" alone: a status for a player with work under way, a promise for one with none
    assert [promises("Still on it.", TASKS, "Steve", busy=b) for b in ((), ("Alex",), ("Steve",))] == \
        ["a promise", "a promise", ""]
    working = Tasks(rows=[{**FROST, "player": "Steve"}]).show(["frost_bow_alex"])
    assert promises("Still on it.", working, "Steve") == ""
    # bind and unbind; "want to <verb> one?" as the last sentence; and "nothing I can do" is no offer
    assert [promises(r, TASKS, "Steve") for r in (
        "I'll bind it to the word zap.", "I'm binding it now.", "I'll unbind it.", "Want to make one?",
        "You want to build a tower? It takes a minute.", "Do you want to spawn some cows?", "Want to hear a joke?",
        "Nothing I can do from here though.", "Not much I can do about lag, sorry.", "There's little I can do there.",
        "Nothing I can turn it down to, it's at the minimum.", "I can do that.")] == \
        ["a promise", "a promise", "a promise", "an offer", "", "an offer", "", "", "", "", "", "an offer"]
    assert promises("Done a moment ago, have a look.", TASKS, "Steve") == ""


# ------------------------------------------------------------------ the DONE check

DONE = {  # a hand-off's reply -> whether it says the thing is done
    "Done!": True, "All set, enjoy!": True, "There you go.": True, "Here you go, one sword.": True,
    "Fixed it!": True, "It's ready, try it out.": True, "You're all set.": True, "Teleported you to Alex!": True,
    "I gave you a stack of torches.": True, "Good to go!": True, "No problem, done!": True,
    "Consider it done.": True, "I just made it rain.": True,
    "On it!": False, "Coming up!": False, "Still on it.": False, "Teleporting you now!": False,
    "Not done yet, still building it.": False, "It'll be ready in a minute.": False, "All set?": False,
    "I'll tell you when it's done.": False, "Almost ready!": False, "Get ready, here it comes!": False,
    "Steak, well-done!": False, "On it, getting it fixed now.": False, "": False, "Sure thing!": False,
    # the night's bench: "gone" and "is unbound" claimed it, and "fixed up next" claims nothing
    "On it! Cooldowns gone.": True, "On it! Cooldowns gone on all your spells.": True,
    "On it — freeze gone for good.": True, "Freeze is unbound.": True, "The rain's off.": True,
    "Lava's removed now.": True, "Got it — ice aura fixed up next.": False, "It'll be gone in a sec.": False,
    "Sorry, fixing it.": False, "Got it.": False, "On it, turning it off.": False,
    # a past tense opening a clause, as "Got it, unbound." beside a hand-off on the third bench round
    "Got it, unbound.": True, "Removed!": True, "Stopped, all of them.": True, "Cleared.": True,
    "Killed them all.": True, "Killed by a creeper again?": False,
    # looking is no claim, and someone off to a place is not a thing turned off (the code review)
    "On it — checking where Stirka's off to.": False, "Looking — your freeze is unbound, checking…": False,
    "Alex's off to the nether.": False, "On it, seeing what broke.": False, "Finding it now.": False,
    "Done, looking good!": True, "The music's off.": True,
}


def test_claims_done_reads_a_clause_at_a_time_and_a_hedge_or_a_question_claims_nothing():
    assert {reply: claims_done(reply) for reply in DONE} == DONE


async def test_a_hand_offs_reply_that_says_it_is_done_or_offers_is_dropped_and_a_replys_alone_is_not_checked(capsys):
    got = await said({"reason": "rain now", "hand_off": True, "reply": "Done! Enjoy the rain."})
    assert (got.hand_off, got.reply) == (True, "")
    assert 'respond guard #7 beside a hand-off, dropped: it says it is done: "Done! Enjoy the rain."' in \
        capsys.readouterr().out
    got = await said({"reason": "bigger", "hand_off": True, "reply": "If it's not big enough, I can make it bigger."})
    assert (got.hand_off, got.reply) == (True, "")                     # an offer: the work is handed off already
    assert 'respond guard #7 beside a hand-off, dropped: an offer: "If it' in capsys.readouterr().out
    for ack in ("On it!", "Coming up!", "Still on it.", "Sorry, fixing it.", "Got it.",
                "Got it — ice aura fixed up next."):
        assert (await said({"reason": "rain now", "hand_off": True, "reply": ack})).reply == ack
    got = await said({"reason": "asks after it", "hand_off": False, "reply": "All set a moment ago, have a look!"},
                     "is my bridge done", busy={"Steve"})
    assert (got.hand_off, got.reply) == (False, "All set a moment ago, have a look!")


# ------------------------------------------------------------------ the cut

def test_cut_makes_one_line_and_cuts_at_a_word():
    assert cut("one\nline  here") == "one line here"
    long = " ".join(["word"] * 80)                                       # 399 characters
    assert len(cut(long)) <= REPLY_CUT and cut(long).endswith("word…") and cut(long, 20) == "word word word word…"
    assert cut("words cut through here", 15) == "words cut…"


async def test_a_reply_past_300_characters_is_cut_at_a_word_to_200_and_one_just_over_200_goes_out(capsys):
    long = "Beacons need a pyramid of iron, gold, emerald, diamond or netherite blocks under them, " * 4
    got = await said({"reason": "a question", "hand_off": False, "reply": long}, "what's a beacon for")
    assert len(long.strip()) > 300 and len(got.reply) <= 200 and got.reply.endswith("…")
    assert long.startswith(got.reply[:-1]) and not got.reply[:-1].endswith(" ")
    assert f"respond #7 reply of {len(long.strip())} characters cut to 200" in capsys.readouterr().out
    fine = "x" * 250
    assert (await said({"reason": "a question", "hand_off": False, "reply": fine})).reply == fine


# ------------------------------------------------------------------ a report

async def report(answer: dict, event: ReportEvent | None = None):
    r, ask = agent(ReportAnswer=answer)
    ctx, _ = line(9, "Alex", "nice")
    got = await r.report(event or ReportEvent(FROST, "done", "Done: the frost bow works."), ctx, SEEN, TASKS, UNDER, [])
    return got.value, ask


async def test_a_report_is_one_call_its_usage_row_the_tasks_name_its_event_the_task_and_the_report_whole():
    whole = "Done: arrows freeze mobs.\nThe gap: players were not tested, nobody else was online."
    got, ask = await report({"reason": "mobs freeze; players untested", "hand_off": False, "status": "done",
                             "reply": "Your frost bow's ready: shoot a mob to freeze it.",
                             "record": "frost_bow_alex done: arrows freeze mobs"},
                            ReportEvent(FROST, "done", whole, following=True))
    call, = ask.calls
    assert (call["output"], call["component"], call["call_id"]) == ("ReportAnswer", "respond", "frost_bow_alex")
    opening, marked = call["cached"][0]
    assert marked and "A REPORT" in opening                     # the same opening as a line's: one cache
    tail = call["prompt"]
    heads = ["THE WORLD NOW:", "TASKS: none yet", "WORK UNDER WAY:", "NEW SINCE THE LAST READ",
             "THE REPORT: the implementer of frost_bow_alex ended, done."]
    assert [tail.index(h) for h in heads] == sorted(tail.index(h) for h in heads)
    report_ = tail.split("THE REPORT:")[1]
    for part in ("frost_bow_alex is for Alex, who said: make me a bow that freezes things",
                 "WHAT IT IS: A frost bow for Alex", "ITS LATEST BRIEF:\nTHE PLAYER'S WORDS",
                 "- an arrow freezes a player",
                 f"ITS REPORT:\n{whole}", "A FOLLOW-UP to frost_bow_alex is already under way"):
        assert part in report_
    assert got.record == "frost_bow_alex done: arrows freeze mobs" and got.left == ""
    assert ReportEvent(FROST, "done", "x").show().count("FOLLOW-UP") == 0
    assert ReportEvent({}, "failed", "x").ref() == "report"


async def test_a_reports_answer_is_fixed_in_code_what_is_left_and_the_records_name():
    got, _ = await report({"reason": "players were never frozen", "hand_off": True, "status": "done",
                           "reply": "Your bow freezes mobs now; players are next.", "record": "done: freezes mobs"})
    assert got.left == "players were never frozen"              # a hand-off with no left: its reason
    assert got.record == "frost_bow_alex done: freezes mobs"   # the status there, the name put in front
    got, _ = await report({"reason": "all landed", "hand_off": False, "left": "nothing", "status": "done",
                           "reply": "Your frost bow is ready.", "record": "a frost bow for Alex"})
    assert got.left == ""                                       # left with no hand-off: dropped
    assert got.record == "frost_bow_alex done: a frost bow for Alex"
    got, _ = await report({"reason": "it broke", "hand_off": True, "left": " an arrow\nfreezes a player ",
                           "status": "failed", "reply": "", "record": ""})
    assert (got.left, got.reply, got.record) == ("an arrow freezes a player", "", "")  # the caller writes them
    got, _ = await report({"reason": "x", "status": "done", "reply": "y " * 200, "record": "frost_bow_alex done: x"})
    assert len(got.reply) <= 200 and got.reply.endswith("…")


async def test_a_reports_failed_call_is_the_callers_to_handle():
    r, _ = agent(ReportAnswer=LlmError("no answer within 20 s"))
    with pytest.raises(LlmError):
        await r.report(ReportEvent(FROST, "done", "Done."), rows(), SEEN, TASKS, UNDER, [])


# ------------------------------------------------------------------ the rules

def test_the_rules_say_its_new_job_and_name_no_class_of_jevs():
    rules = PROMPT.read_text(encoding="utf-8")
    for part in ("DECIDE FIRST", "`reason` first, then `hand_off`, then `reply`", "HAND OFF, always",
                 "THE REPLY WITH A HAND-OFF", "WORK UNDER WAY", "is it done yet?", "Still on it.", "A REPORT",
                 "Work under way is never a reason to keep a line", "A correction of what the AI just did",
                 "A clarification of, or an addition to, an ask being done", "the past tense",
                 "Players cannot bind, unbind or change", "unless the conversation shows the AI already did it",
                 "however it is put", "\"I meant a tall one\"", "never Jev or any filter",
                 "A need in the game said as a complaint", "Venting that the AI's own work is slow",
                 "`left`", "`record`", "{owner}"):
        assert part in rules
    for label in ("action_oriented", "conversational", "to_ai", "hand_up", "hand up"):  # Jev's classes are changing
        assert label not in rules


# ------------------------------------------------------------------ promises(), on the lines it was tuned on
# (moved from test_loop.py: they test respond.promises, which both the agent and the brain's step check call)

GUARD = {  # reply -> what promises() says, no task open (the reviewer's probes among them)
    "Sure thing, on it!": "a promise", "Aye, on it, captain!": "a promise", "Will do!": "a promise",
    "Consider it done.": "a promise", "Leave it to me!": "a promise", "I'm gonna make it rain!": "a promise",
    "I'm going to build that for you.": "a promise", "Okay, making it rain now!": "a promise",
    "I'll take care of it.": "a promise", "I'll make sure you get a netherite sword.": "a promise",
    "Aye, I'll be makin' it rain, matey!": "a promise", "One storm, coming right at ya!": "a promise",
    "Done! Enjoy the sword.": "a promise", "Sure thing, spawning some cows now.": "a promise",
    "I can do that.": "an offer", "Let me do that for you.": "an offer", "Want it stopped?": "an offer",
    "Yeah, I can add theme music. Want distinct tracks for each?": "an offer",
    "Put a sign on it and click on it.": "", "Aye aye! I could get used to this pirate life.": "",
    "Let me put it this way: smite only beats sharpness on undead.": "", "Will check with you first, promise.": "",
    "Want the recipe? Three glass, five obsidian and a nether star.": "",
    "You don't want me to spoil the ending, trust me.": "", "Good luck in the nether! Watch your back for ghasts.": "",
    "Hang on, you lost everything in lava? Rip.": "", "Night's coming up, find a bed soon.": "",
    "Grab some food on the way back.": "", "The sun will be up soon, hold out.": "",
    "I mean, that was a pretty good one, I'll give you that.": "", "Aye aye, Captain Riley.": "",
    # small talk and the AI's own talk: said, not handed up
    "I'm doing great, thanks for asking!": "", "We're doing great as a team.": "",
    "Getting dark now, better find shelter.": "", "Haha, I'm making no promises about the creepers.": "",
    "Done already? That was fast.": "", "Right away? Patience, my friend.": "",
    "Okay, I'll stop talking now.": "", "Sure, I'll keep quiet for a bit.": "", "Arr, I'll turn the chatter down.": "",
    "You're on the way to the nether, nice.": "", "Not right away, it takes a minute to learn.": "",
    "Steak, well-done!": "", "I can get why you'd think that.": "", "Your stuff is on the way back to spawn.": "",
    "Sure - on it.": "a promise", "Your sword's on the way.": "a promise", "I can get you one.": "an offer",
    # the brain's loop on the 10-11 replay: a yes, then the work; work promised for later
    "Got it, making sure it explodes right on impact.": "a promise",
    "Got it, making sure it reliably detonates right when it hits.": "a promise",
    "Yeah, 600ms latency feels noticeable in real-time play. We can look into smaller or streaming ASR models to "
    "trim that down.": "a promise",
    "Sure, adding a moat round your castle.": "a promise", "I'll dig into why it broke.": "a promise",
    "Yeah, doing great!": "", "Okay, getting dark out there, grab a bed.": "", "Got it, good to know.": "",
    "Fire Aspect's max is 2 on /enchant, but I can set 5": "an offer",
    # respond_bench's offers of a thing (7DZGBF), and live #45's banter about lava, which is not one
    "Glad you like it — the obsidian one's got your name on it, right?": "an offer",
    "That obsidian upgrade has your name on it.": "an offer",
    "It is, right? Don't get too comfy, lava's got your name on it next.": "",
}


def test_promises_catches_an_offer_or_a_promise_and_lets_talk_and_a_speakers_own_status_through():
    assert {reply: promises(reply, "TASKS: none yet") for reply in GUARD} == GUARD
    status = "castle's coming up, give it a sec"   # a status only while the speaker has a task open
    castle = {"name": "castle_steve", "description": "a castle", "player": "Steve", "status": "open",
              "line": "build me a castle", "created_at": "2026-10-08T18:22:31.000Z",
              "updated_at": "2026-10-08T18:22:31.000Z"}
    opened = Tasks(rows=[castle])                         # the new format: a named row, open
    closed = Tasks(rows=[{**castle, "status": "done", "closed_at": "2026-10-08T18:25:00.000Z"}])
    assert opened.show().endswith("\n- castle_steve for Steve, open: a castle")
    assert [bool(promises(status, tasks, who)) for tasks, who in (
        (opened.show(), "Steve"), (opened.show(["castle_steve"]), "Steve"), (opened.show(), "Alex"),
        (closed.show(), "Steve"),                         # all closed: a status is a promise
        ("TASKS: none yet", "Steve"))] == [False, False, True, True, True]


def test_the_brains_check_is_for_promises_alone_an_offer_is_a_question_it_may_ask():
    assert promises("Fire Aspect's max is 2 on /enchant, but I can set 5", offers=False) == ""
    assert promises("Want me to make it rain? Got it, making sure it rains", offers=False) == "a promise"


# ------------------------------------------------------------------ Jev unnamed

async def test_a_sentence_that_names_jev_or_the_filter_is_dropped_from_a_line_reply_and_a_reports(capsys):
    got = await said({"reason": "talk", "hand_off": False, "reply": "Ha, nice try. Ask Jev for the receipts."}, "hi AI")
    assert (got.hand_off, got.reply) == (False, "Ha, nice try.")
    assert 'respond guard #7 names Jev, sentence dropped: "Ask Jev for the receipts."' in capsys.readouterr().out
    got = await said({"reason": "talk", "hand_off": False, "reply": "That's the filter's call, not mine"}, "hi AI")
    assert got.reply == ""                                            # nothing left: silence
    for fine in ("Jeval's a funny name.", "Filtered water beats a potion of water breathing."):
        assert (await said({"reason": "talk", "hand_off": False, "reply": fine}, "hi AI")).reply == fine
    got, _ = await report({"reason": "all landed", "hand_off": False, "status": "done",
                           "reply": "Your frost bow is ready. JEV let it through!", "record": "frost_bow_alex done: x"})
    assert got.reply == "Your frost bow is ready."
