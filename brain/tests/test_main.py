"""python -m brain from start to stop, every part faked: the start's steps in order, the
history it never acts on, the tasks it loads from the box, the old tasks file set aside, the
feed followed, and a stop that fails what it interrupts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from brain import __main__ as main
from brain import config
from brain.__main__ import GREETING, NoImplementers, serve
from brain.vfg import VfgResult

from conftest import HAND_UP, FakeAsk, FakeImplementers, FakeRefs, FakeVfg, act, catalog, cmd, step


def feed(seq: int, at: datetime, rest: str, processed: bool = False) -> str:
    stamp = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return json.dumps({"seq": seq, "at": stamp, "line": f"LINE#{seq} {stamp[11:19]} {rest}",
                       "processed_at": stamp if processed else None})


def row(name: str, status: str, at: str, **more) -> dict:
    return {"name": name, "description": f"what {name} is", "player": "Alex", "status": status, "line": "…",
            "brief": f"make {name}", "made": None, "variables": None, "session": None, "result": None,
            "created_at": at, "updated_at": at, "closed_at": None, **more}


async def test_a_start_answers_only_what_is_new_follows_the_feed_and_a_stop_fails_what_it_cut(tmp_path):
    cfg = config.load({"VFG_HOME": str(tmp_path / "home"), "VFG_OWNER": "KrownJM", "VFG_BRAIN_HISTORY_HOURS": "6"})
    cfg.brain_dir.mkdir(parents=True)
    old = {"next": 2, "tasks": {"old_castle": {"player": "Alex", "task": "a castle, by the old brain",
                                               "status": "open"}}}
    (cfg.brain_dir / "tasks.json").write_text(json.dumps(old))  # the brain before named tasks: set aside
    now = datetime.now(UTC)
    tasks = [row("castle_alex", "open", "2026-10-11T10:00:00.000Z"),  # open when the last run died
             row("ice_trail_steve", "done", "2026-10-11T09:00:00.000Z", closed_at="2026-10-11T09:02:00.000Z")]
    vfg = FakeVfg().on("context", "--since", answer=VfgResult(0, "\n".join([
        feed(10, now - timedelta(minutes=30), "Steve voice to_ai 0.9 - | give me bread"),  # history
        feed(11, now - timedelta(minutes=29), "Alex voice context 0.9 - | so hungry", processed=True),
        feed(12, now - timedelta(seconds=20), "Steve voice to_ai 0.9 - | give me a diamond pickaxe"),
        json.dumps({"tasks": tasks}),
        json.dumps({"last_seq": 12}),
    ]) + "\n", ""))
    impl, stop, seen_after = FakeImplementers(), asyncio.Event(), []

    async def watch(after: int):
        seen_after.append(after)
        yield f"LINE#13 {now:%H:%M:%S} Alex voice to_ai 0.9 - | a diamond pickaxe for me too"
        await stop.wait()

    ask = FakeAsk(Step=step(act("command", "give a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                            reply="there you go"), Response=HAND_UP)
    run = asyncio.create_task(serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                                    make_implementers=lambda on_report, on_session: impl))
    for _ in range(200):
        if len([s for s in vfg.said() if s[0] == "there you go"]) == 2:
            break
        await asyncio.sleep(0.001)
    stop.set()
    assert await run == 0

    firsts = [c[:2] for c in vfg.calls]
    assert firsts[0] == ["up"] and {"watchers", "lookout", "jev", "context"} <= {c[0] for c in vfg.calls[1:6]}
    assert vfg.of("watchers")[0][2] == "join,leave,death,advancement,server"
    assert "Names of places, bases, builds and coordinates" in vfg.of("lookout")[0][2]
    assert "command — Run vanilla commands" in vfg.of("jev", "menu", "set")[0][3]
    assert vfg.of("context", "--since") == [["context", "--since", "-6h", "--json"]] and seen_after == [12]
    assert ["context", "mark", "10"] in vfg.calls  # history: marked read, never acted on
    steps = ask.of("Step")
    assert len(steps) == 2 and "give me bread" not in steps[0]["cached"][-1][0].split("THE EVENT")[1]
    assert "THE OWNER: KrownJM." in steps[0]["cached"][0][0]
    assert [GREETING] in vfg.said() and impl.closed
    # the tasks from the box: the open one failed by name, written through, its AI result row
    record = "castle_alex failed: interrupted, the brain restarted while it ran"
    assert vfg.rows("result") == [("result castle_alex", record)]
    [written] = [json.loads(a[2]) for a in vfg.of("task", "set")]
    assert (written["name"], written["status"], written["result"]) == ("castle_alex", "failed", record)
    assert written["closed_at"] and written["description"] == "what castle_alex is"
    # the old file: renamed, never read, and nothing in it carried over
    assert not (cfg.brain_dir / "tasks.json").exists()
    [retired] = cfg.brain_dir.glob("tasks.json.retired-*")
    assert json.loads(retired.read_text()) == old
    assert not any(w in json.dumps(vfg.calls) for w in ("old_castle", "by the old brain"))
    assert all("--task" not in c for c in vfg.calls)    # the lines' actions and replies carry no task
    # the usage queue sent at the start, after the calls (each line's Respond and step) and at the stop; never a
    # row of its own. The calls asked at once: one flush for them, or one and the one more after it, never two at once
    flushes = vfg.of("usage")
    assert 3 <= len(flushes) <= 6 and flushes == [["usage", "flush"]] * len(flushes)
    assert vfg.calls[-1] == ["usage", "flush"] and vfg.calls.index(flushes[0]) < vfg.calls.index(vfg.of("say")[0])


async def test_with_no_rows_that_recent_it_follows_from_the_feeds_last_seq_and_a_failed_read_is_tried_again(
        tmp_path, monkeypatch):
    """Yesterday's session ended at #902: followed from 0, `vfg watch` would replay its last rows as new."""
    monkeypatch.setattr(main, "CONTEXT_RETRY_S", 0.001)
    cfg = config.load({"VFG_HOME": str(tmp_path / "home")})
    vfg = FakeVfg().on("context", "--since", answer=[
        VfgResult(1, "", "vfg: the feed table has no processed_at column yet — restart vfg-bridge, which adds it"),
        VfgResult(0, "", ""),                          # nothing at all: not an empty feed, a failed read
        VfgResult(0, json.dumps({"last_seq": 902}) + "\n", "")])
    stop, seen_after = asyncio.Event(), []

    async def watch(after: int):
        seen_after.append(after)
        stop.set()
        await asyncio.Event().wait()
        yield ""

    ask = FakeAsk(Step=step(reply="never"))
    assert await serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                       make_implementers=lambda *_: FakeImplementers()) == 0
    assert len(vfg.of("context", "--since")) == 3 and seen_after == [902] and ask.calls == []


async def test_with_no_implementers_a_new_task_reports_its_failure_with_five_arguments():
    got = []

    async def on_report(*args):
        got.append(args)

    none = NoImplementers(on_report, "No module named claude_agent_sdk")
    await none.start("galick_gun_styrk", "a Galick Gun")
    await none.follow_up("castle_steve", "a moat", session="sess-1", brief="a castle")
    empty = {"scripts": [], "actions": [], "presets": [], "copies": []}
    assert got == [("galick_gun_styrk", "failed", "Failed: no implementers here (No module named claude_agent_sdk).",
                    "", empty),
                   ("castle_steve", "failed", "Failed: no implementers here (No module named claude_agent_sdk).",
                    "", empty)]


def test_the_loops_settings_and_the_removed_ones_ignored(tmp_path):
    cfg = config.load({})
    assert (cfg.model, cfg.reasoning, cfg.providers) == ("google/gemini-3.8-flash", "low", ())
    assert (cfg.loop_steps, cfg.loop_seconds, cfg.turn_waiting, cfg.turn_settle_s) == (10, 120.0, 3, 3.0)
    assert cfg.timeout_s == 45.0 and config.load({"VFG_BRAIN_TIMEOUT_S": "30"}).timeout_s == 30.0
    assert cfg.respond_timeout_s == 20.0 and config.load({"VFG_RESPOND_TIMEOUT_S": "8"}).respond_timeout_s == 8.0
    cfg = config.load({"VFG_BRAIN_MODEL": "openai/gpt-6.1-sol", "VFG_BRAIN_REASONING": "off",
                       "VFG_BRAIN_PROVIDERS": "google-vertex, google-ai-studio", "VFG_LOOP_STEPS": "4",
                       "VFG_LOOP_SECONDS": "45", "VFG_TURN_WAITING": "1", "VFG_TURN_SETTLE_S": "0"})
    assert (cfg.model, cfg.reasoning, cfg.providers) == ("openai/gpt-6.1-sol", "off",
                                                         ("google-vertex", "google-ai-studio"))
    assert (cfg.loop_steps, cfg.loop_seconds, cfg.turn_waiting, cfg.turn_settle_s) == (4, 45.0, 1, 0.0)
    assert config.load({"VFG_LOOP_STEPS": "0"}).loop_steps == 10  # under 1: the default
    # the orchestrator's and the planner's keys, left in ~/.vfg/config, are ignored as every dead key is
    home = tmp_path / "home"
    home.mkdir()
    (home / "config").write_text("VFG_ORCHESTRATOR_MODEL=x\nVFG_PLAN_MODEL=y\nVFG_PLAN_CHECK=on\n")
    cfg = config.load({"VFG_HOME": str(home)})
    assert not any(hasattr(cfg, k) for k in ("orchestrator_model", "plan_model", "plan_check", "tasks_file"))
