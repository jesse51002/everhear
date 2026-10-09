import asyncio
import os
import re
from collections import deque
from types import SimpleNamespace

import pytest
from helpers import config_in, new_store, temp_dir, tick, until, use_fake_vfg

from orchestrator.config import iso, now_ms
from orchestrator.guard import GuardContext
from orchestrator.reply import Outbox
from orchestrator.session import (MSG, Delays, FatalError, Prompts, Session, build_options, rules,
                                  scrub_environ, session_env)
from orchestrator.usage import UsageMeter
from orchestrator.vfg import vfg

_END = object()


class FakeQuery:
    """A stand-in for the SDK's query: it records the prompts it pulls, and emits what a test tells it to."""

    def __init__(self, prompt, options, info=None):
        self.received: list[str] = []
        self.stop_tasks: list[str] = []
        self.interrupts = 0
        self.ended = False
        self.options = options
        self.info = info or {"apiKeySource": "none", "subscriptionType": "max"}
        self._queue: deque = deque()
        self._waiter: asyncio.Future | None = None
        self._consumer = asyncio.get_running_loop().create_task(self._consume(prompt))

    async def _consume(self, prompt):
        async for m in prompt:
            self.received.append(m["message"]["content"])

    def _deliver(self, item) -> bool:
        w, self._waiter = self._waiter, None
        if w is None or w.done():
            return False
        w.set_result(item)
        return True

    def emit(self, **m) -> None:
        msg = {"session_id": "S1", **m}
        if not self._deliver(msg):
            self._queue.append(msg)

    def end(self) -> None:
        self.ended = True
        self._deliver(_END)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._queue:
            return self._queue.popleft()
        if self.ended:
            raise StopAsyncIteration
        self._waiter = asyncio.get_running_loop().create_future()
        m = await self._waiter
        if m is _END:
            raise StopAsyncIteration
        return m

    async def account_info(self):
        return self.info

    async def stop_task(self, task_id):
        self.stop_tasks.append(task_id)

    async def interrupt(self):
        self.interrupts += 1

    def close(self):
        self.end()


def result(**over):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 1000, "result": "ok",
            "modelUsage": {}, "permission_denials": [], "queued_turn_count": 0, **over}


def started(task_id, description, tool_use_id=""):
    return {"type": "system", "subtype": "task_started", "task_id": task_id, "tool_use_id": tool_use_id,
            "description": description, "task_type": "local_agent"}


def notified(task_id, status="completed"):
    return {"type": "system", "subtype": "task_notification", "task_id": task_id, "status": status, "summary": "done", "output_file": ""}


def setup(info=None, mode="implementers", extra=None):
    d = temp_dir()
    fake = use_fake_vfg(d)
    cfg = config_in(d, {"VFG_ORCH_MODE": mode, **(extra or {})})
    store = new_store(d)
    outbox = Outbox(store, vfg, [5], 5)
    usage = UsageMeter(store, vfg, mode)
    queries: list[FakeQuery] = []
    clock = [1_000_000]

    def query_fn(prompt, options):
        q = FakeQuery(prompt, options, info)
        queries.append(q)
        return q

    session = Session(cfg=cfg, store=store, outbox=outbox, usage=usage,
                      prompts=Prompts(orchestrator="orch %REPO% %MAX_IMPLEMENTERS%", implementer="impl"),
                      query_fn=query_fn, now=lambda: clock[0], delays=Delays(first=10, max=40, healthy=600_000))

    def open_(tid):
        return store.add(tid, at=iso(now_ms()), task="do " + tid, context="")

    def advance(ms):
        clock[0] += ms

    return SimpleNamespace(dir=d, fake=fake, cfg=cfg, store=store, session=session, queries=queries, open=open_,
                           advance=advance, q=lambda: queries[-1])


async def stop_keeper(s, keeper):
    s.session.stopping = True
    s.session.close_query()
    await keeper


# ====================================================== session, implementers mode (the default)

async def test_the_session_id_of_the_first_message_is_saved():
    s = setup()
    await s.session.open()
    s.q().emit(type="system", subtype="init", session_id="abc", model="opus", permissionMode="dontAsk", tools=[])
    await until(lambda: s.store.state.session_id == "abc")
    assert s.store.state.session_id == "abc"


async def test_idle_with_a_task_open_nudged_once_closed_at_the_next_idle_moment():
    s = setup()
    s.open("T1")
    await s.session.open()
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 1)
    assert s.q().received[0] == MSG.nudge(["T1"])
    assert s.store.get("T1").nudged is True
    s.session.tick()  # the nudge was taken: a turn is coming, so not idle
    assert len(s.q().received) == 1
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == MSG.closed(["T1"])
    t = s.store.get("T1")
    assert t.status == "failed"
    assert [r.text for r in t.replies] == [MSG.no_answer]


async def test_an_error_result_the_task_is_closed_with_the_error():
    s = setup()
    s.open("T2")
    await s.session.open()
    err = {"subtype": "error_during_execution", "is_error": True, "errors": ["API Error: 429 rate_limit_error " + "x" * 200]}
    s.q().emit(**result(**err))
    await until(lambda: len(s.q().received) == 1)
    s.q().emit(**result(**err))
    await until(lambda: s.store.get("T2").status == "failed")
    text = s.store.get("T2").replies[0].text
    assert text.startswith("The orchestrator could not run: API Error: 429 rate_limit_error")
    assert len(text) == len("The orchestrator could not run: ") + 120


async def test_a_replied_task_is_not_nudged():
    s = setup()
    s.open("T1")
    await s.session.open()
    s.store.reply("T1", "done", "day")
    s.q().emit(**result())
    await tick()
    assert s.q().received == []


async def test_a_running_implementer_keeps_it_from_idling_its_start_clears_nudged():
    s = setup()
    s.open("T1")
    s.store.get("T1").nudged = True
    await s.session.open()
    s.session.guard_context().on_agent_allowed("T1", "u1")
    assert s.session.guard_context().active_implementers() == 1
    s.q().emit(**started("k1", "T1 make it day", "u1"))
    await until(lambda: len(s.session.running) == 1)
    assert len(s.session.pending) == 0
    assert s.store.get("T1").nudged is False
    s.q().emit(**result())
    await tick()
    assert s.q().received == []  # running: not idle
    s.q().emit(**notified("k1"))
    await until(lambda: len(s.session.running) == 0)
    s.session.tick()
    assert s.q().received == []  # its report wakes a turn: wait for it
    s.advance(61_000)
    s.session.tick()
    await until(lambda: len(s.q().received) == 1)
    assert s.q().received[0] == MSG.nudge(["T1"])


async def test_background_tasks_changed_replaces_the_running_set():
    s = setup()
    s.open("T1")
    await s.session.open()
    s.session.guard_context().on_agent_allowed("T1", "u1")
    s.q().emit(type="system", subtype="background_tasks_changed", tasks=[
        {"task_id": "k1", "task_type": "local_agent", "subagent_type": "implementer", "description": "T1 day"},
        {"task_id": "b1", "task_type": "local_bash", "description": "sleep 5"},
    ])
    await until(lambda: len(s.session.running) == 1)
    assert len(s.session.pending) == 0
    s.q().emit(**result())
    await tick()
    assert s.q().received == []
    s.q().emit(type="system", subtype="background_tasks_changed", tasks=[])
    await until(lambda: len(s.session.running) == 0)


async def test_allowed_implementers_that_never_start_stop_counting_after_a_minute():
    s = setup()
    await s.session.open()
    s.session.guard_context().on_agent_allowed("T1", "u9")
    s.advance(61_000)
    s.session.tick()
    assert len(s.session.pending) == 0


async def test_an_implementer_over_its_time_limit_is_stopped():
    s = setup()
    s.open("T1")
    await s.session.open()
    s.q().emit(**started("k1", "T1 castle"))
    await until(lambda: len(s.session.running) == 1)
    s.advance(9 * 60_000)
    s.session.tick()
    assert s.q().stop_tasks == []
    s.advance(2 * 60_000)
    s.session.tick()
    s.session.tick()
    await tick()
    assert s.q().stop_tasks == ["k1"]


async def test_an_api_key_is_fatal():
    s = setup({"apiKeySource": "ANTHROPIC_API_KEY"})
    with pytest.raises(FatalError):
        await s.session.open()


async def test_usage_rows_land_after_a_result():
    s = setup()
    s.open("T1")
    await s.session.open()
    s.q().emit(**result(modelUsage={
        "claude-opus-4-8": {"inputTokens": 10, "outputTokens": 2, "cacheReadInputTokens": 5, "cacheCreationInputTokens": 0, "costUSD": 0.01},
        "claude-sonnet-4-6": {"inputTokens": 3, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "costUSD": 0.001},
    }))
    await until(lambda: len([c for c in s.fake.calls() if c[0] == "usage"]) == 2)
    await s.session.settled()
    rows = [" ".join(c[2:4]) for c in s.fake.calls() if c[0] == "usage"]
    assert sorted(rows) == ["--component implementer", "--component orchestrator"]
    assert "T1" in next(c for c in s.fake.calls() if len(c) > 3 and c[3] == "orchestrator")


async def test_a_session_that_ends_open_tasks_failed_as_interrupted_resumed_told():
    s = setup()
    s.store.state.session_id = "S1"
    await s.session.open("S1")
    keeper = asyncio.create_task(s.session.keep_alive(0))
    s.q().emit(**result())
    await tick()
    s.open("T3")
    s.advance(60_000)
    s.q().end()
    await until(lambda: len(s.queries) == 2)
    assert s.store.get("T3").status == "failed"
    assert s.store.get("T3").replies[0].text == MSG.interrupted
    assert s.queries[1].options.resume == "S1"
    await until(lambda: len(s.queries[1].received) == 1)
    assert re.match(r"^RUNNER: restarted at \d\d:\d\d:\d\d UTC\. Closed as failed: T3\. Implementers from before are gone",
                    s.queries[1].received[0])
    await stop_keeper(s, keeper)


async def test_a_resume_that_fails_before_any_turn_a_new_session():
    s = setup()
    s.store.state.session_id = "gone"
    s.store.state.usage = {"m": {"in": 1, "out": 1, "cost": 1}}
    await s.session.open("gone")
    keeper = asyncio.create_task(s.session.keep_alive(1_000_000))
    s.q().end()  # the CLI exits: no conversation with that id
    await until(lambda: len(s.queries) == 2)
    assert s.queries[1].options.resume is None
    assert s.store.state.session_id is None
    assert s.store.state.usage == {}
    await stop_keeper(s, keeper)


async def test_restarts_back_off_from_5_s_doubling():
    s = setup()
    await s.session.open()
    keeper = asyncio.create_task(s.session.keep_alive(1_000_000))
    s.q().emit(**result())
    await tick()
    for i in range(4):
        s.q().emit(**result())
        await tick()
        s.q().end()
        await until(lambda: len(s.queries) == i + 2, 2000)
    await stop_keeper(s, keeper)
    assert len(s.queries) == 5


async def test_stop_fails_every_open_task_as_stopped():
    s = setup()
    s.open("T1")
    s.open("T2")
    await s.session.open()
    assert await s.session.stop() == ["T1", "T2"]
    assert s.store.get("T2").replies[0].text == MSG.stopped
    s.session.close_query()


# ====================================================== session, direct mode (VFG_ORCH_MODE=direct)

def task(tid):
    return "TASK %s at 00:00:00 UTC\ndo %s" % (tid, tid)


async def test_direct_tasks_go_to_the_session_one_at_a_time():
    s = setup(None, "direct")
    await s.session.open()
    s.open("T1")
    s.open("T2")
    s.session.push_task("T1", task("T1"))
    s.session.push_task("T2", task("T2"))
    await until(lambda: len(s.q().received) == 1)
    await tick()
    assert s.q().received == [task("T1")]
    assert [w["id"] for w in s.session.waiting] == ["T2"]
    s.store.reply("T1", "done", "ok")
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == task("T2")
    s.store.reply("T2", "done", "ok")
    s.q().emit(**result())
    await tick()
    assert len(s.q().received) == 2


async def test_direct_a_waiting_task_is_never_nudged_delivered_ones_are_after_the_queue_is_through():
    s = setup(None, "direct")
    await s.session.open()
    s.open("T1")
    s.open("T2")
    s.session.push_task("T1", task("T1"))
    s.session.push_task("T2", task("T2"))
    await until(lambda: len(s.q().received) == 1)
    s.q().emit(**result())  # T1's turn ended without a reply: T2 goes in, nobody is nudged yet
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == task("T2")
    assert s.store.get("T1").nudged is False
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 3)
    assert s.q().received[2] == MSG.nudge(["T1", "T2"], "direct")
    assert "do what it needs" in MSG.nudge(["T1"], "direct")


async def test_direct_a_task_closed_while_waiting_is_skipped():
    s = setup(None, "direct")
    await s.session.open()
    for tid in ("T1", "T2", "T3"):
        s.open(tid)
    for tid in ("T1", "T2", "T3"):
        s.session.push_task(tid, task(tid))
    await until(lambda: len(s.q().received) == 1)
    s.store.reply("T2", "failed", "cancelled")
    s.store.reply("T1", "done", "ok")
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == task("T3")


async def test_direct_a_session_restart_fails_the_task_in_hand_and_keeps_the_waiting_ones():
    s = setup(None, "direct")
    await s.session.open()
    keeper = asyncio.create_task(s.session.keep_alive(0))
    s.open("T1")
    s.open("T2")
    s.session.push_task("T1", task("T1"))
    s.session.push_task("T2", task("T2"))
    await until(lambda: len(s.q().received) == 1)
    s.advance(60_000)
    s.q().end()
    await until(lambda: len(s.queries) == 2)
    assert s.store.get("T1").status == "failed"
    assert s.store.get("T1").replies[0].text == MSG.interrupted
    assert s.store.get("T2").status == "open"
    await until(lambda: len(s.queries[1].received) == 1)
    assert "Work in progress was cut off" in s.queries[1].received[0]
    s.queries[1].emit(**result())  # the note's turn ends: T2 goes in
    await until(lambda: len(s.queries[1].received) == 2)
    assert s.queries[1].received[1] == task("T2")
    await stop_keeper(s, keeper)


async def test_direct_a_turn_over_the_time_limit_is_interrupted_and_told_to_reply_failed():
    s = setup(None, "direct")
    await s.session.open()
    s.open("T1")
    s.session.push_task("T1", task("T1"))
    await until(lambda: len(s.q().received) == 1)
    s.advance(9 * 60_000)
    s.session.tick()
    assert s.q().interrupts == 0
    s.advance(2 * 60_000)
    s.session.tick()
    s.session.tick()
    await tick()
    assert s.q().interrupts == 1
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == MSG.overtime(10)


async def test_direct_stop_fails_waiting_tasks_too():
    s = setup(None, "direct")
    await s.session.open()
    s.open("T1")
    s.open("T2")
    s.session.push_task("T1", task("T1"))
    s.session.push_task("T2", task("T2"))
    assert await s.session.stop() == ["T1", "T2"]
    assert s.session.waiting == []
    s.session.close_query()


async def test_direct_usage_rows_are_the_orchestrators_whatever_the_model():
    s = setup(None, "direct")
    await s.session.open()
    s.q().emit(**result(modelUsage={"claude-haiku-4-6": {"inputTokens": 3, "outputTokens": 1, "cacheReadInputTokens": 0,
                                                         "cacheCreationInputTokens": 0, "costUSD": 0.001}}))
    await until(lambda: any(c[0] == "usage" for c in s.fake.calls()))
    await s.session.settled()
    assert "orchestrator" in next(c for c in s.fake.calls() if c[0] == "usage")


# ====================================================== session, hybrid mode (the default)

async def test_hybrid_a_quick_task_goes_in_while_an_implementer_works_and_waits_only_for_the_sessions_own_turn():
    s = setup(None, "hybrid")
    await s.session.open()
    s.open("T3")
    s.session.push_task("T3", task("T3"))
    await until(lambda: len(s.q().received) == 1)
    s.q().emit(**started("k1", "T3 build a house", "u1"))  # the house goes to an implementer
    s.q().emit(**result())
    await until(lambda: len(s.session.running) == 1 and not s.session.busy)
    s.open("T4")
    s.session.push_task("T4", task("T4"))  # a quick one, while the house is built
    await until(lambda: len(s.q().received) == 2)
    assert s.q().received[1] == task("T4")
    s.open("T5")
    s.session.push_task("T5", task("T5"))  # the session is busy with T4: T5 waits
    await tick()
    assert len(s.q().received) == 2
    s.store.reply("T4", "done", "rain")
    s.q().emit(**result())
    await until(lambda: len(s.q().received) == 3)
    assert s.q().received[2] == task("T5")


async def test_hybrid_an_implementer_running_keeps_the_session_from_being_idle():
    s = setup(None, "hybrid")
    await s.session.open()
    s.open("T3")
    s.session.push_task("T3", task("T3"))
    await until(lambda: len(s.q().received) == 1)
    s.q().emit(**started("k1", "T3 build a house", "u1"))
    s.q().emit(**result())
    await until(lambda: len(s.session.running) == 1)
    s.session.tick()
    await tick()
    assert len(s.q().received) == 1  # no nudge


# ====================================================== context rollover (VFG_ORCH_MAX_CONTEXT, default 70000)

def big(n):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [], "usage": {"input_tokens": 1000, "cache_read_input_tokens": n - 1500, "cache_creation_input_tokens": 500}}}


def opus(inp, cost):
    return {"claude-opus-4-8": {"inputTokens": inp, "outputTokens": 10, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "costUSD": cost}}


def history(s):
    s.store.add("T1", at="2026-10-08T06:00:00.000Z", task="make it day", context="")
    s.store.reply("T1", "done", "It's day now: time set to 1000.")
    s.store.add("T2", at="2026-10-08T06:01:00.000Z", task="rain TNT", context="")
    s.store.reply("T2", "done", "TNT rain is running on everyone; stop it with vfg script stop tnt_rain.")
    s.store.add("T3", at="2026-10-08T06:02:00.000Z", task="build a hut next to Steve", context="Steve is at 120 64 -40.")
    s.store.reply("T3", "progress", "Working on the hut.")


async def test_over_budget_at_an_idle_point_a_fresh_session_with_the_handoff_the_open_task_survives_and_is_answered_once():
    s = setup(None, "hybrid")
    history(s)
    s.store.state.session_id = "S1"
    await s.session.open("S1")
    keeper = asyncio.create_task(s.session.keep_alive(0))
    s.q().emit(**big(71_000))
    s.q().emit(**result(modelUsage=opus(100, 0.5)))
    await until(lambda: len(s.queries) == 2)
    assert s.queries[0].ended is True
    assert s.queries[1].options.resume is None
    assert s.session.rollovers == 1
    assert s.store.get("T3").status == "open"
    await until(lambda: len(s.queries[1].received) == 1)
    h = s.queries[1].received[0]
    assert h.startswith("RUNNER: this is a fresh session: the last one had grown to 71000 tokens of context.")
    assert ("Open tasks, each still needing exactly one done or failed reply:\n- T3 (sent 06:02:00 UTC): build a hut next to Steve "
            "Context: Steve is at 120 64 -40. Said so far: Working on the hut.") in h
    assert "Implementers running: none" in h
    assert "- T2 done: TNT rain is running on everyone; stop it with vfg script stop tnt_rain." in h
    assert "- T1 done: It's day now" in h
    assert h.index("T1 done") < h.index("T2 done")
    # the new session answers the open task, exactly once
    s.queries[1].emit(type="system", subtype="init", session_id="S2", model="opus", permissionMode="dontAsk", tools=[])
    await until(lambda: s.store.state.session_id == "S2")
    assert s.store.reply("T3", "done", "The hut is built.").ok is True
    assert s.store.reply("T3", "done", "again").ok is False
    # usage: the old session's row went out before its snapshot was dropped; the new one counts from zero
    s.queries[1].emit(**result(modelUsage=opus(30, 0.1)))
    await until(lambda: len([c for c in s.fake.calls() if c[0] == "usage"]) == 2)
    await s.session.settled()
    ins = [c[c.index("--in") + 1] for c in s.fake.calls() if c[0] == "usage"]
    assert ins == ["100", "30"]
    await stop_keeper(s, keeper)


async def test_not_while_an_implementer_runs_it_rolls_over_once_the_implementer_has_reported():
    s = setup(None, "hybrid")
    history(s)
    await s.session.open()
    keeper = asyncio.create_task(s.session.keep_alive(0))
    s.q().emit(**started("k1", "T3 hut", "u1"))
    s.q().emit(**big(90_000))
    s.q().emit(**result())
    await until(lambda: len(s.session.running) == 1 and not s.session.busy)
    s.session.tick()
    await tick()
    assert len(s.queries) == 1
    s.q().emit(**notified("k1"))
    await until(lambda: len(s.session.running) == 0)
    s.advance(61_000)
    s.session.tick()
    await until(lambda: len(s.queries) == 2)
    assert s.store.get("T3").status == "open"
    await stop_keeper(s, keeper)


async def test_under_the_budget_or_with_the_budget_off_no_rollover():
    for extra, n in (({}, 69_000), ({"VFG_ORCH_MAX_CONTEXT": "0"}, 500_000)):
        s = setup(None, "hybrid", extra)
        await s.session.open()
        s.q().emit(**big(n))
        s.q().emit(**result())
        await tick()
        s.session.tick()
        await tick()
        assert len(s.queries) == 1
        assert s.session.context_tokens == n
        s.session.close_query()


async def test_a_subagents_large_context_does_not_count_only_the_main_threads():
    s = setup(None, "hybrid")
    await s.session.open()
    s.q().emit(**{**big(200_000), "parent_tool_use_id": "toolu_1"})
    s.q().emit(**result())
    await tick()
    assert s.session.context_tokens == 0
    assert len(s.queries) == 1
    s.session.close_query()


async def test_a_task_waiting_during_the_rollover_goes_in_after_the_handoff():
    s = setup(None, "hybrid")
    await s.session.open()
    keeper = asyncio.create_task(s.session.keep_alive(0))
    s.open("T4")
    s.open("T5")
    s.session.push_task("T4", task("T4"))
    await until(lambda: len(s.q().received) == 1)
    s.session.push_task("T5", task("T5"))  # waits: the session is busy with T4
    s.store.reply("T4", "done", "rain")
    s.q().emit(**big(80_000))
    s.q().emit(**result())
    await until(lambda: len(s.queries) == 2)
    await until(lambda: len(s.queries[1].received) == 1)
    assert "1 more task(s) are waiting" in s.queries[1].received[0]
    assert "Open tasks: none." in s.queries[1].received[0]
    s.queries[1].emit(**result())  # the handoff's turn
    await until(lambda: len(s.queries[1].received) == 2)
    assert s.queries[1].received[1] == task("T5")
    await stop_keeper(s, keeper)


# ====================================================== options

def guard(cfg, **over):
    fields = dict(repo=cfg.repo, max_implementers=4, implementer_model="haiku", main_thread_rule=False,
                  open_task_ids=lambda: [], active_implementers=lambda: 0, on_agent_allowed=lambda t, u: None)
    fields.update(over)
    return GuardContext(**fields)


def test_direct_mode_no_agent_no_implementer_the_implementers_tools_under_the_guard_low_effort():
    d = temp_dir()
    cfg = config_in(d, {"VFG_ORCH_MODE": "direct"})
    assert cfg.mode == "direct"
    assert cfg.model == "opus"
    assert cfg.effort == "low"
    o = build_options(cfg, Prompts(orchestrator="direct %REPO%", implementer=""), new_store(d), guard(cfg))
    assert o.model == "opus"
    assert o.effort == "low"
    assert o.tools == ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
    assert o.agents is None
    assert o.allowed_tools == rules(cfg.repo, "direct")
    assert "Agent" not in o.allowed_tools
    assert "TaskStop" not in o.allowed_tools
    assert "mcp__orch__reply" in o.allowed_tools
    assert "Bash(vfg cmd *)" in o.allowed_tools
    assert "Write(/%s/scripts/**)" % cfg.repo in o.allowed_tools
    assert o.system_prompt == "direct " + cfg.repo
    assert o.permission_mode == "dontAsk"
    over = config_in(d, {"VFG_ORCH_MODE": "direct", "VFG_ORCH_MODEL": "haiku", "VFG_ORCH_EFFORT": "medium"})
    assert [over.model, over.effort] == ["haiku", "medium"]


def test_defaults_hybrid_mode_opus_low_sonnet_implementers_at_low_all_overridable():
    d = temp_dir()
    c = config_in(d)
    assert [c.mode, c.model, c.effort, c.implementer_model, c.implementer_effort] == ["hybrid", "opus", "low", "sonnet", "low"]
    assert config_in(d, {"VFG_ORCH_MODE": "implementers"}).mode == "implementers"
    assert config_in(d, {"VFG_ORCH_MODE": "direct"}).mode == "direct"
    assert config_in(d, {"VFG_ORCH_MODE": "nonsense"}).mode == "hybrid"
    o = config_in(d, {"VFG_IMPL_MODEL": "haiku", "VFG_IMPL_EFFORT": "medium", "VFG_ORCH_EFFORT": "high"})
    assert [o.implementer_model, o.implementer_effort, o.effort] == ["haiku", "medium", "high"]
    assert config_in(d, {"VFG_IMPLEMENTER_MODEL": "haiku"}).implementer_model == "haiku"  # the older name still works


def test_hybrid_mode_agent_and_bash_no_write_or_edit_for_the_session_implementers_as_before():
    d = temp_dir()
    cfg = config_in(d)
    o = build_options(cfg, Prompts(orchestrator="hybrid %REPO% %MAX_IMPLEMENTERS%", implementer="impl"), new_store(d),
                      guard(cfg, implementer_model="sonnet", main_thread_rule=True, hybrid=True))
    assert [o.model, o.effort] == ["opus", "low"]
    assert "Agent" in o.tools
    assert "Bash" in o.tools
    assert o.allowed_tools == rules(cfg.repo, "hybrid")
    assert "Agent" in o.allowed_tools
    assert "Bash(vfg cmd *)" in o.allowed_tools
    assert o.agents["implementer"].model == "sonnet"
    assert o.agents["implementer"].effort == "low"
    assert o.system_prompt == "hybrid %s 4" % cfg.repo


def test_the_sessions_guard_holds_the_direct_main_thread_to_the_implementers_rules():
    s = setup(None, "direct")
    assert s.session.guard_context().main_thread_rule is False
    s2 = setup(None, "implementers")
    assert s2.session.guard_context().main_thread_rule is True
    assert s2.session.guard_context().hybrid is False
    s3 = setup(None, "hybrid")
    assert [s3.session.guard_context().main_thread_rule, s3.session.guard_context().hybrid] == [True, True]


def test_the_clis_environment_leaves_out_keys_and_the_parent_session():
    env = session_env({
        "PATH": "/bin", "HOME": "/h", "VFG_REPO": "/r", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t",
        "ANTHROPIC_BASE_URL": "http://proxy", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_SESSION_ID": "s",
        "CLAUDE_CODE_SSE_PORT": "1", "CLAUDE_PID": "9", "CLAUDE_CODE_OAUTH_TOKEN": "subscription-token", "CLAUDE_EFFORT": "low",
    })
    assert sorted(env) == ["CLAUDE_CODE_OAUTH_TOKEN", "HOME", "PATH", "VFG_REPO"]


def test_implementers_mode_the_sessions_options():
    d = temp_dir()
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
    cfg = config_in(d, {"VFG_ORCH_MODE": "implementers", "VFG_ORCH_MODEL": "haiku", "VFG_ORCH_MAX_IMPLEMENTERS": "3"})
    o = build_options(cfg, Prompts(orchestrator="repo %REPO% max %MAX_IMPLEMENTERS%", implementer="impl %REPO%"),
                      new_store(d), guard(cfg, max_implementers=3, main_thread_rule=True), resume="S9")
    del os.environ["ANTHROPIC_API_KEY"]
    assert o.model == "haiku"  # VFG_ORCH_MODEL wins
    assert o.effort == "low"  # the orchestrator's default
    assert o.system_prompt == "repo %s max 3" % cfg.repo
    assert o.cwd == cfg.repo
    assert o.setting_sources == []
    assert o.skills == []
    assert o.strict_mcp_config is True
    assert list(o.mcp_servers) == ["orch"]
    assert o.tools == ["Agent", "TaskStop", "Bash", "Read", "Write", "Edit", "Glob", "Grep"]
    assert o.permission_mode == "dontAsk"
    assert o.resume == "S9"
    assert o.env.get("ANTHROPIC_API_KEY") is None
    assert "ANTHROPIC_API_KEY" not in o.env
    assert o.env["CLAUDE_AGENT_SDK_CLIENT_APP"] == "vfg-orchestrator/0.1"
    impl = o.agents["implementer"]
    assert impl.model == "sonnet"  # VFG_IMPL_MODEL's default
    assert impl.effort == "low"  # VFG_IMPL_EFFORT's default, set on the agent definition
    assert impl.tools == ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
    assert impl.background is True
    assert impl.maxTurns == 40
    assert impl.prompt == "impl " + cfg.repo
    assert o.allowed_tools == rules(cfg.repo)
    assert "Read(/%s/**)" % cfg.repo in o.allowed_tools
    assert "Write(/%s/scripts/**)" % cfg.repo in o.allowed_tools
    assert ("Read(/%s/**)" % cfg.repo).startswith("Read(//")
    assert not any("vfg say" in r for r in o.allowed_tools)


# Not in the TypeScript tests: what the Python SDK needs done differently.

def test_the_probes_options_run_the_implementer_as_the_main_thread_through_the_agent_flag():
    d = temp_dir()
    cfg = config_in(d, {"VFG_ORCH_MODE": "implementers"})
    prompts = Prompts(orchestrator="o", implementer="i")
    assert build_options(cfg, prompts, new_store(d), guard(cfg)).extra_args == {}
    o = build_options(cfg, prompts, new_store(d), guard(cfg), agent="implementer")
    assert o.extra_args == {"agent": "implementer"}  # --agent implementer, as the TypeScript SDK passed it
    assert o.mcp_servers["orch"]["alwaysLoad"] is True


def test_the_runners_own_environment_loses_what_the_cli_must_not_see():
    os.environ.update({"ANTHROPIC_API_KEY": "k", "CLAUDECODE": "1", "CLAUDE_CODE_SSE_PORT": "1",
                       "CLAUDE_CODE_OAUTH_TOKEN": "subscription-token", "VFG_REPO": "/r"})
    scrub_environ()
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "CLAUDECODE" not in os.environ
    assert "CLAUDE_CODE_SSE_PORT" not in os.environ
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "subscription-token"
    assert os.environ["VFG_REPO"] == "/r"
