"""The loop: each row routed by its class, Respond's hand-up, history, two lines at once, a
report, and the batch timer on a clock moved by hand."""

import asyncio

from vfg_llm import LlmError

from brain.timer import Timer

from conftest import cmd, decision, part, rows

ASK = "LINE#{seq} 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"
TALK = "LINE#{seq} 18:29:59 Steve voice conversational 0.88 - | haha thanks AI"
GIVE = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                reply="there you go")


async def test_each_row_goes_where_its_class_says_and_a_read_marks_what_it_took(kit):
    k = kit(rows("LINE#1 18:29:40 Alex voice context 0.8 - | I'm by the lava", "AI#2 18:29:41 say - | hi"),
            Decision=GIVE, Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    for line in ("GAME#3 18:29:50 death Alex | Alex tried to swim in lava", "SYS#0 18:29:51 | vfg watch: reconnected",
                 "LINE#4 18:29:52 Alex voice context 0.7 - | ouch", ASK.format(seq=5), TALK.format(seq=6)):
        k.brain.feed(line)
        await asyncio.sleep(0)  # as `vfg watch` gives them: one line at a time
    await k.settle()
    assert sorted(c["output"] for c in k.ask.calls) == ["Decision", "Response"]
    assert "Alex tried to swim in lava" in k.ask.of("Decision")[0]["prompt"]  # the live read took in what waited
    assert sorted(k.vfg.said()) == [["anytime!"], ["there you go", "--task", "T1"]]
    assert k.vfg.of("context", "mark") == [["context", "mark", "1", "3", "4", "5"], ["context", "mark", "6"]]
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


async def test_history_is_context_and_never_acted_on(kit):
    k = kit(Decision=GIVE)
    k.brain.feed("LINE#9 18:20:00 Steve voice action_oriented 0.9 - | give me a diamond pickaxe")
    await k.settle()
    assert k.ask.calls == [] and k.vfg.of("context", "mark") == [["context", "mark", "9"]]
    assert not k.ctx.unprocessed() and k.ctx.last_seq == 9


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
    k = kit(Decision=decision(reply="Steve, your ice trail is on: just walk", record="T1 done: ice trail as ice_trail",
                              status="done"))
    tid = k.tasks.open("Steve", "make me an ice trail", "an ice trail behind Steve")
    k.impl.session = lambda tid: "sess-1"
    await k.brain.on_report(tid, "done", "Done: an ice trail follows Steve, running as ice_trail.")
    await k.settle()
    assert "ITS REPORT:\nDone: an ice trail follows Steve" in k.ask.calls[0]["prompt"]
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


class Finished:
    """A batch call that is over at once."""

    def done(self) -> bool:
        return True


async def test_the_batch_fires_after_a_quiet_minute_with_something_unread():
    now, unread, fired = [1000.0], [False], []
    timer = Timer(60, lambda: unread[0], lambda: fired.append(now[0]) or Finished(), clock=lambda: now[0])
    now[0] += 90
    assert not timer.check()          # a quiet spell with nothing unread: nothing to catch up
    unread[0] = True
    assert timer.check() and fired == [1090.0]  # something arrives after the minute: at once
    now[0] += 30
    assert not timer.check()          # the batch was a read: the minute starts again
    now[0] += 20
    timer.read()                      # a live read pushes it back
    now[0] += 59
    assert not timer.check()
    now[0] += 1
    assert timer.check() and fired == [1090.0, 1200.0]
