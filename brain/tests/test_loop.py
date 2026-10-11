"""The loop: each row routed by its class, a context line no model is shown, Respond's
hand-up and the order it answers in, history, two lines at once, a report, and no batch."""

import asyncio
import importlib.util
from pathlib import Path

from vfg_llm import LlmError

from brain import config, loop
from brain.act import BROKE
from brain.call import PROMPT
from brain.respond import Response, promises
from brain.tasks import Tasks

from conftest import cmd, decision, part, rows

ASK = "LINE#{seq} 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"
TALK = "LINE#{seq} 18:29:59 Steve voice conversational 0.88 - | haha thanks AI"
GIVE = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                reply="there you go")


async def test_each_row_goes_where_its_class_says_and_a_read_marks_what_it_took(kit):
    k = kit(rows("GAME#1 18:29:40 join Alex | Alex joined the game", "AI#2 18:29:41 say - | hi"),
            Decision=GIVE, Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    for line in ("GAME#3 18:29:50 death Alex | Alex tried to swim in lava", "SYS#0 18:29:51 | vfg watch: reconnected",
                 "LINE#4 18:29:52 Alex voice context 0.7 - | ouch, I'm by the lava", ASK.format(seq=5),
                 TALK.format(seq=6)):
        k.brain.feed(line)
        await asyncio.sleep(0)  # as `vfg watch` gives them: one line at a time
    await k.settle()
    assert sorted(c["output"] for c in k.ask.calls) == ["Decision", "Response"]
    tail = k.ask.of("Decision")[0]["prompt"].split("NEW SINCE")[1]
    assert "Alex joined the game" in tail and "Alex tried to swim in lava" in tail  # every game event since
    for c in k.ask.calls:                  # the context line: in no prompt, cached or not, of any call
        assert "by the lava" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert sorted(k.vfg.said()) == [["anytime!"], ["there you go", "--task", "T1"]]
    # the context line marked as it came, nobody waiting; the line's read took the game events and the line
    assert k.vfg.of("context", "mark") == [["context", "mark", "4"], ["context", "mark", "1", "3", "5"],
                                           ["context", "mark", "6"]]
    usage = k.vfg.of("usage", "add")
    assert ["usage", "add", "--component", "brain", "--model", "brain-model", "--in", "1000", "--out", "40",
            "--cost", "0.002000", "--cache-read", "900", "--ref", "#5"] in usage and any("respond" in u for u in usage)
    assert k.ctx.unprocessed() == [] and k.ctx.last_seq == 6


async def test_respond_replies_or_hands_up_never_both(kit):
    k = kit(Decision=GIVE, Response={"reply": "sure thing!", "hand_up": True, "reason": "asks for rain"})
    k.brain.feed("LINE#7 18:29:59 Steve voice conversational 0.9 - | lol make it rain")
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Decision"]
    assert "handed this line up to you: asks for rain" in k.ask.calls[1]["prompt"]
    assert k.vfg.said() == [["there you go", "--task", "T1"]]  # the brain's reply alone, never Respond's

    k = kit(Decision=GIVE, Response=LlmError("no answer"))  # unsure, failing: handed up all the same
    k.brain.feed(TALK.format(seq=8))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Decision"]


async def test_respond_never_says_an_offer_or_a_promise_it_hands_the_line_up_instead(kit, capsys):
    k = kit(Decision=GIVE, Response={"reason": "a question", "hand_up": False, "reply": "Want me to make it rain?"})
    k.brain.feed(TALK.format(seq=7))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Decision"]
    assert "handed this line up to you: a question · handed up in code: an offer" in k.ask.calls[1]["prompt"]
    assert k.vfg.said() == [["there you go", "--task", "T1"]]  # the brain's line, never the offer
    assert 'respond guard #7 an offer: "Want me to make it rain?"' in capsys.readouterr().out  # the log alone

    ack = "Got it, I'll ask you before giving Alex anything."  # a rule for later: said
    k = kit(Response={"reason": "a rule for later", "hand_up": False, "reply": ack})
    k.brain.feed(TALK.format(seq=8))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response"] and k.vfg.said() == [[ack]]


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
}


def test_promises_catches_an_offer_or_a_promise_and_lets_talk_and_a_speakers_own_status_through():
    assert {reply: promises(reply, "OPEN TASKS:\nnone") for reply in GUARD} == GUARD
    status = "castle's coming up, give it a sec"   # a status only while the speaker has a task open
    opened = Tasks(Path("/nonexistent/tasks.json"))
    opened.tasks["T1"] = {"id": "T1", "at": "2026-10-08T18:22:31.000Z", "player": "Steve", "task": "a castle",
                          "status": "open"}
    assert [bool(promises(status, tasks, who)) for tasks, who in (
        (opened.show(), "Steve"), (opened.show(["T1"]), "Steve"), (opened.show(), "Alex"),
        ("OPEN TASKS:\nnone", "Steve"),
        ("TASKS (…):\n- castle_steve for Steve, done: a castle", "Steve"),   # named tasks list closed ones too
        ("TASKS (…):\n- castle_steve for Steve, open: a castle", "Steve"))] == [False, False, True, True, True, False]


def test_respond_decides_first_and_always_writes_its_reason():
    schema = Response.model_json_schema()               # what the model is shown, and writes in this order
    assert list(schema["properties"]) == ["reason", "hand_up", "reply"]
    assert schema["required"] == ["reason"] and schema["properties"]["hand_up"]["default"] is True


async def test_history_is_the_old_feed_at_the_start_and_a_line_on_the_watch_is_live_whatever_its_time(kit):
    k = kit(rows("LINE#8 18:29:50 Alex voice context 0.9 - | so hungry",
                 "LINE#9 18:20:00 Steve voice action_oriented 0.9 - | give me a diamond pickaxe"), Decision=GIVE)
    k.brain.start = 9
    assert [r.seq for r in k.brain.catch_up()] == [9]      # ten minutes old as the brain starts: history
    await k.settle()                                      # and a context line unread, whatever its age, read
    assert k.ask.calls == [] and k.vfg.of("context", "mark") == [["context", "mark", "8", "9"]]
    # the box's clock 90 s ahead of the PC's: a line said now reads as yesterday's 18:31:30, and is live
    k.brain.feed("LINE#10 18:31:30 Steve voice action_oriented 0.9 - | give me a diamond pickaxe")
    await k.settle()
    assert len(k.ask.calls) == 1 and "LINE #10" in k.ask.calls[0]["prompt"].split("THE EVENT")[1]
    # after a drop, the backlog the watch catches up with is judged by its age, as a start's is
    k.brain.feed("SYS#0 18:30:00 | reconnected")
    k.brain.feed("LINE#11 18:21:00 Steve voice action_oriented 0.9 - | give me a diamond axe")
    await k.settle()
    assert len(k.ask.calls) == 1


async def test_two_lines_at_once_neither_waits_for_the_other(kit):
    both = asyncio.Event()
    started = []

    async def slow(prompt):
        started.append(prompt)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)  # the first call ends only once the second has begun
        return GIVE

    k = kit(Decision=slow)
    k.brain.feed(ASK.format(seq=10))
    k.brain.feed("LINE#11 18:29:58 Alex voice action_oriented 0.9 - | give me a diamond pickaxe too")
    await k.settle()
    assert len(started) == 2 and len(k.vfg.said()) == 2
    assert {t["player"] for t in k.tasks.tasks.values()} == {"Steve", "Alex"}


async def test_a_report_closes_its_task_with_one_record_and_one_chat_line(kit):
    k = kit(rows("GAME#1 18:29:50 death Steve | Steve fell from a high place"),
            Decision=decision(reply="Steve, your ice trail is on: just walk", record="T1 done: ice trail as ice_trail",
                              status="done"))
    tid = k.tasks.open("Steve", "make me an ice trail", "an ice trail behind Steve")
    k.impl.session = lambda tid: "sess-1"
    await k.brain.on_report(tid, "done", "Done: an ice trail follows Steve, running as ice_trail.")
    await k.settle()
    assert "ITS REPORT:\nDone: an ice trail follows Steve" in k.ask.calls[0]["prompt"]
    assert "Steve fell from a high place" in k.ask.calls[0]["prompt"].split("NEW SINCE")[1]  # a report reads NEW
    assert k.vfg.of("context", "mark") == [["context", "mark", "1"]]
    t = k.tasks.get("T1")
    assert (t["status"], t["result"], t["session"]) == ("done", "T1 done: ice trail as ice_trail", "sess-1")
    assert t["closed_at"]
    assert k.vfg.rows("result") == [("result T1", "T1 done: ice trail as ice_trail")]
    assert k.vfg.said() == [["Steve, your ice trail is on: just walk", "--task", "T1"]]


async def test_a_report_while_a_follow_up_to_its_task_runs_keeps_the_task_open(kit):
    k = kit(Decision=decision(reply="the trail's on; making it wider now", record="T1 ice trail on, wider next",
                              status="done"))
    tid = k.tasks.open("Steve", "make me an ice trail", "an ice trail behind Steve")
    k.impl.busy = [tid]  # the follow-up queued behind this run has started
    await k.brain.on_report(tid, "done", "Done: an ice trail follows Steve, running as ice_trail.")
    await k.settle()
    assert "A FOLLOW-UP to T1 is already under way" in k.ask.calls[0]["prompt"]
    t = k.tasks.get("T1")
    assert (t["status"], t["result"], t["closed_at"]) == ("open", "T1 ice trail on, wider next", None)
    assert k.vfg.rows("result") == [("result T1", "T1 ice trail on, wider next")]
    assert "an implementer is on it" in k.brain.open_tasks()


async def test_a_report_while_the_brain_stops_closes_its_task_in_code(kit):
    gate = asyncio.Event()

    async def held(prompt):
        await gate.wait()
        return GIVE

    k = kit(Decision=held)
    tid = k.tasks.open("Steve", "build a castle", "a castle at spawn, 40 by 40")
    k.brain.feed(ASK.format(seq=5))                       # a call in flight as the stop begins
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await asyncio.sleep(0)
    await k.brain.on_report(tid, "done", "Done: the castle is up at spawn, with a moat I added.")  # meanwhile
    gate.set()
    await stopping
    await k.settle()
    assert len(k.ask.calls) == 1                          # the line's call; the report's needs no model
    t = k.tasks.get(tid)
    assert (t["status"], t["result"]) == ("done", "T1 done: a castle at spawn, 40 by 40")
    assert k.vfg.rows("result") == [("result T1", "T1 done: a castle at spawn, 40 by 40")]


async def test_a_report_gets_a_record_and_a_chat_line_written_in_code_when_the_call_gives_none(kit):
    k = kit(Decision=decision())                          # an answer with no record and no reply
    trail = k.tasks.open("Steve", "make me an ice trail", "an ice trail behind Steve")
    await k.brain.on_report(trail, "failed", "Failed: vfg script check said line 40 of scripts/ice/main.py …")
    await k.settle()
    k.ask.answers["Decision"] = LlmError("no answer")     # and a call that fails
    beam = k.tasks.open("Alex", "an ice beam", "an ice beam for Alex")
    await k.brain.on_report(beam, "done", "Done: ice_beam running as ice_beam_alex; I also cleaned up …")
    await k.settle()
    assert k.vfg.rows("result") == [("result T1", "T1 failed: an ice trail behind Steve"),
                                    ("result T2", "T2 done: an ice beam for Alex")]  # never the implementer's words
    assert [t["result"] for t in k.tasks.tasks.values()] == ["T1 failed: an ice trail behind Steve",
                                                             "T2 done: an ice beam for Alex"]
    assert k.vfg.said() == [["sorry, that one failed: an ice trail behind Steve", "--task", "T1"],
                            ["done: an ice beam for Alex", "--task", "T2"]]


async def test_an_unexpected_error_never_leaves_a_task_open_and_the_player_hears_so(kit):
    async def broken(argv):
        raise RuntimeError("the runner broke")

    async def blind():
        raise RuntimeError("no state")

    k = kit(Decision=GIVE)
    k.vfg.on("action", "run", answer=broken)
    k.brain.feed(ASK.format(seq=5))                       # T1: its action's run raises
    await k.settle()
    trail = k.tasks.open("Steve", "make me an ice trail", "an ice trail behind Steve")
    k.world.seen = blind                                  # T2's report: the world cannot be read
    await k.brain.on_report(trail, "done", "Done: an ice trail follows Steve.")
    await k.settle()
    assert [(t["status"], t["result"]) for t in k.tasks.tasks.values()] == [
        ("failed", "T1 failed: it broke (RuntimeError: the runner broke)"), ("done", "T2 done: an ice trail behind Steve")]
    assert k.vfg.said() == [[BROKE, "--task", "T1"], ["done: an ice trail behind Steve", "--task", "T2"]]


async def test_after_the_feed_restarts_the_context_is_read_again_and_the_new_feed_heard(kit, monkeypatch):
    monkeypatch.setattr(loop, "WATCH_AGAIN_S", 0.001)
    k = kit(rows("LINE#500 18:20:00 Alex voice context 0.9 - | on the old box", processed=True), Decision=GIVE)
    k.brain.start = 500
    fresh = rows("SYS#1 18:29:00 | a new box", "GAME#2 18:29:30 join Steve | Steve joined the game")

    async def load():
        return fresh, 2

    k.brain.load, seen = load, []

    async def watch(after: int):
        seen.append(after)
        if after == 500:
            yield "SYS#0 18:29:40 | vfg watch: feed restarted — cursor reset"
            yield "SYS#1 18:29:00 | a new box"
        else:
            yield ASK.format(seq=3)
        await asyncio.Event().wait()

    following = asyncio.create_task(k.brain.follow(watch))
    for _ in range(200):
        if k.vfg.said():
            break
        await asyncio.sleep(0.001)
    following.cancel()
    await k.settle()
    assert seen == [500, 2] and [r.seq for r in k.brain.ctx.rows] == [1, 2, 3]
    assert k.vfg.said() == [["there you go", "--task", "T1"]]


async def test_there_is_no_batch_whatever_waits_unread_waits_for_the_next_call(kit):
    assert importlib.util.find_spec("brain.timer") is None
    assert not hasattr(config.load({"VFG_BRAIN_BATCH_S": "60"}), "batch_s")
    assert "batch" not in PROMPT.read_text().lower()
    k = kit(Decision=GIVE)
    k.brain.feed("GAME#1 18:29:50 death Alex | Alex tried to swim in lava")
    k.brain.feed("LINE#2 18:29:52 Alex voice context 0.7 - | ouch")
    await k.settle()
    assert k.ask.calls == [] and not hasattr(k.brain, "timer") and not hasattr(k.brain, "batch")
    assert [r.seq for r in k.ctx.unprocessed()] == [1]    # the game event waits for the next call; the context line not
    k.brain.feed(ASK.format(seq=3))
    await k.settle()
    assert "Alex tried to swim in lava" in k.ask.calls[0]["prompt"].split("NEW SINCE")[1]


async def test_respond_sees_each_players_place_and_state_but_no_items(kit):
    k = kit(Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    await k.world.fresh_state()                           # Respond never waits: the last state there is
    k.brain.feed(TALK.format(seq=1))
    await k.settle()
    prompt = k.ask.of("Response")[0]["prompt"]
    assert ("- Steve at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, survival; "
            "effects: speed II 0:30, night vision ∞\n") in prompt
    assert "items, by slot" not in prompt
