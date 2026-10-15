"""python -m brain from start to stop, every part faked: the start's steps in order, the three
agents wired with their own settings, the history it never acts on, the tasks it loads from the
box, the old tasks file set aside, the feed followed, and a stop that fails what it interrupts;
and the settings: DeepSeek V4.1 Flash for every agent, the step timeout by the reasoning, the
loop's budget, no hold whatever the router's reasoning, and VFG_LOOP_SECONDS gone."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from brain import __main__ as main
from brain import config
from brain.__main__ import GREETING, NoImplementers, serve
from brain.vfg import VfgResult
from brain.world import World

from conftest import FakeAsk, FakeImplementers, FakeRefs, FakeVfg, act, answer, catalog, cmd, overview, route, step


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

    ask = FakeAsk(Step=step(act("engine_start", "give a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                            reply="there you go"), Response=answer(), Route=route())
    run = asyncio.create_task(serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                                    respond_ask=ask, router_ask=ask,
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
    assert "engine_start — Start one of the engine's scenarios" in vfg.of("jev", "menu", "set")[0][3]
    assert vfg.of("context", "--since") == [["context", "--since", "-6h", "--json"]] and seen_after == [12]
    assert ["context", "mark", "10"] in vfg.calls  # history: marked read, never acted on
    steps = ask.of("Step")
    assert len(steps) == 2 and "give me bread" not in steps[0]["cached"][-1][0].split("THE EVENT")[1]
    assert [c["component"] for c in ask.calls].count("router") == 2  # each line: the agent, the router, a loop
    for c in steps + ask.of("Response") + ask.of("Route"):
        assert "THE OWNER: KrownJM" in c["cached"][0][0]  # each prompt names the owner
    assert [GREETING] in vfg.said() and impl.closed
    # one engine read at the start, which the start never waits for: the first router call does, so it shows the engine
    first = ask.of("Route")[0]["prompt"]
    assert "ENGINE (every class the game engine has" in first and "fire_staff" in first
    assert "RUNNING NOW: nothing (no scenario runs" in first and "ENGINE: unknown" not in first
    assert len(vfg.of("engine", "overview")) >= 1 and vfg.of("engine", "overview")[0] == ["engine", "overview", "--json"]
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
    # the usage queue sent at the start, after the calls and at the stop; never a row of its own, and never two
    # flushes running at once (calls that end meanwhile ask for one more)
    flushes = vfg.of("usage")
    assert 3 <= len(flushes) <= 8 and flushes == [["usage", "flush"]] * len(flushes)
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

    ask = FakeAsk(Step=step(reply="never"), Response=answer(), Route=route())
    assert await serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                       respond_ask=ask, router_ask=ask, make_implementers=lambda *_: FakeImplementers()) == 0
    assert len(vfg.of("context", "--since")) == 3 and seen_after == [902] and ask.calls == []


async def test_an_engine_down_or_silent_at_the_start_is_said_in_words_and_never_holds_the_start(tmp_path, capsys):
    down = World(FakeVfg().on("engine", "overview", answer=VfgResult(6, "", "vfg: the engine is not running")))
    await main.read_engine_first(down, wait=1)
    assert "start the engine is not running (" in capsys.readouterr().out
    gate = asyncio.Event()

    async def silent(argv):
        await gate.wait()
        return overview()

    late = World(FakeVfg().on("engine", "overview", answer=silent))
    began = time.monotonic()
    await main.read_engine_first(late, wait=0.05)
    assert time.monotonic() - began < 1
    assert "the engine did not answer within 0.05 s of the start; ENGINE and RUNNING NOW are unknown" in \
        capsys.readouterr().out
    assert (await late.glance()).engine == "ENGINE: unknown"
    gate.set()                                            # the read goes on alone, and the next glance has it
    for _ in range(20):
        await asyncio.sleep(0)
    assert (await late.glance()).engine.startswith("ENGINE (every class")
    await late.close()

    # a whole start with the engine hung: the start never waits for it (only a router call does, 2 s at most)
    assert main.ENGINE_FIRST_S == 2.0
    cfg = config.load({"VFG_HOME": str(tmp_path / "home")})
    hung = asyncio.Event()

    async def never(argv):
        await hung.wait()
        return overview()

    vfg = FakeVfg().on("context", "--since", answer=VfgResult(0, json.dumps({"last_seq": 3}) + "\n", ""))
    vfg.on("engine", "overview", answer=never)
    stop, seen_after = asyncio.Event(), []

    async def watch(after: int):
        seen_after.append(after)
        stop.set()
        await asyncio.Event().wait()
        yield ""

    ask = FakeAsk(Step=step(reply="never"), Response=answer(), Route=route())
    began = time.monotonic()
    assert await asyncio.wait_for(serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                                        respond_ask=ask, router_ask=ask,
                                        make_implementers=lambda *_: FakeImplementers()), 5) == 0
    assert time.monotonic() - began < 1 and seen_after == [3] and [GREETING] in vfg.said()


async def test_with_no_implementers_a_new_task_reports_its_failure_with_five_arguments():
    got = []

    async def on_report(*args):
        got.append(args)

    none = NoImplementers(on_report, "No module named claude_agent_sdk")
    await none.start("galick_gun_styrk", "a Galick Gun")
    await none.follow_up("castle_steve", "a moat", session="sess-1", brief="a castle")
    assert got == [("galick_gun_styrk", "failed", "Failed: no implementers here (No module named claude_agent_sdk).",
                    "", None),        # nothing known of what it made: the row keeps its own
                   ("castle_steve", "failed", "Failed: no implementers here (No module named claude_agent_sdk).",
                    "", None)]


async def test_each_agent_is_wired_with_its_own_settings_and_the_start_line_says_them(tmp_path, monkeypatch, capsys):
    cfg = config.load({"VFG_HOME": str(tmp_path / "home")})
    made: list[tuple] = []

    def asker(model, reasoning, timeout, providers=()):
        made.append((model, reasoning, timeout, providers))
        return FakeAsk()

    monkeypatch.setattr(main, "asker", asker)
    vfg = FakeVfg().on("context", "--since", answer=VfgResult(0, json.dumps({"last_seq": 1}) + "\n", ""))
    stop = asyncio.Event()

    async def watch(after: int):
        stop.set()
        await asyncio.Event().wait()
        yield ""

    assert await serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(),
                       make_implementers=lambda *_: FakeImplementers()) == 0
    hosts = ("together", "coreweave", "parasail")
    assert made == [("deepseek/deepseek-v4.1-flash", "low", 120.0, hosts),   # the brain's steps
                    ("deepseek/deepseek-v4.1-flash", "off", 20.0, hosts),    # the conversation agent
                    ("deepseek/deepseek-v4.1-flash", "off", 30.0, hosts)]    # the router
    out = capsys.readouterr().out
    assert ("respond deepseek/deepseek-v4.1-flash off 20 s; router deepseek/deepseek-v4.1-flash off 30 s; "
            "brain deepseek/deepseek-v4.1-flash low 120 s a step, 10 steps a line up to 20, taken up within "
            "120 s;") in out


def test_every_agent_runs_deepseek_v41_flash_and_a_steps_timeout_follows_its_reasoning():
    cfg = config.load({})
    hosts = ("together", "coreweave", "parasail")
    assert (config.V41, config.V41_HOSTS) == ("deepseek/deepseek-v4.1-flash", hosts)
    # the brain at low, the loop replay's choice (integration/replay/loop/report-deepseek-v4.1-flash-low.md)
    assert (cfg.model, cfg.reasoning, cfg.providers, cfg.timeout_s) == (config.V41, "low", hosts, 120.0)
    assert (cfg.respond_model, cfg.respond_reasoning, cfg.respond_providers, cfg.respond_timeout_s) == \
        (config.V41, "off", hosts, 20.0)
    assert (cfg.router_model, cfg.router_reasoning, cfg.router_providers, cfg.router_timeout_s) == \
        (config.V41, "off", hosts, 30.0)
    assert config.load({"VFG_BRAIN_REASONING": "off"}).timeout_s == 60.0     # a step that does not think: 1 minute
    assert config.load({"VFG_BRAIN_REASONING": "low", "VFG_BRAIN_TIMEOUT_S": "45"}).timeout_s == 45.0  # set: it wins
    assert config.load({"VFG_ROUTER_REASONING": "low"}).router_timeout_s == 60.0
    assert config.load({"VFG_ROUTER_REASONING": "low", "VFG_ROUTER_TIMEOUT_S": "10"}).router_timeout_s == 10.0
    assert config.load({"VFG_BRAIN_PROVIDERS": "none"}).providers == ()        # OpenRouter's choice
    # the gate bench's model and hosts are still exported, though the agent no longer defaults to them
    assert config.CHEAP == "~deepseek/deepseek-v4-flash-latest" and config.FAST[0] == "parasail"
    assert config.Config.effort_of("off") is False and config.Config.effort_of("low") == "low"


def test_there_is_no_hold_whatever_the_routers_reasoning():
    """VFG_ROUTER_REASONING stays, and keeps its timeout rule (above), but no longer changes what
    is said: there is no hold, at off or at low."""
    for reasoning in ("off", "low"):
        cfg = config.load({"VFG_ROUTER_REASONING": reasoning})
        assert not hasattr(cfg, "hold")
    assert not hasattr(config.load({}), "hold")


def test_the_loops_budget_and_the_turns_settings_and_the_removed_ones_ignored(tmp_path, capsys):
    cfg = config.load({})
    assert (cfg.loop_steps, cfg.loop_cap, cfg.loop_take_up_s, cfg.turn_waiting, cfg.turn_settle_s) == \
        (10, 20, 120.0, 3, 3.0)
    cfg = config.load({"VFG_BRAIN_MODEL": "openai/gpt-6.1-sol", "VFG_BRAIN_REASONING": "minimal",
                       "VFG_BRAIN_PROVIDERS": "openai, azure", "VFG_LOOP_STEPS": "4", "VFG_LOOP_CAP": "8",
                       "VFG_LOOP_TAKE_UP_S": "30", "VFG_TURN_WAITING": "1", "VFG_TURN_SETTLE_S": "0"})
    assert (cfg.model, cfg.reasoning, cfg.providers) == ("openai/gpt-6.1-sol", "minimal", ("openai", "azure"))
    assert (cfg.loop_steps, cfg.loop_cap, cfg.loop_take_up_s, cfg.turn_waiting, cfg.turn_settle_s) == \
        (4, 8, 30.0, 1, 0.0)
    assert config.load({"VFG_LOOP_STEPS": "0"}).loop_steps == 10  # under 1: the default
    capsys.readouterr()
    # a loop has no time limit now: VFG_LOOP_SECONDS, left in the environment or ~/.vfg/config, is said once and
    # never read
    cfg = config.load({"VFG_LOOP_SECONDS": "45"})
    assert not hasattr(cfg, "loop_seconds")
    out = capsys.readouterr().out
    assert out.count("VFG_LOOP_SECONDS='45' is no longer read: a loop has no time limit now") == 1
    # the orchestrator's and the planner's keys, left in ~/.vfg/config, are ignored as every dead key is
    home = tmp_path / "home"
    home.mkdir()
    (home / "config").write_text("VFG_ORCHESTRATOR_MODEL=x\nVFG_PLAN_MODEL=y\nVFG_PLAN_CHECK=on\nVFG_LOOP_SECONDS=90\n")
    cfg = config.load({"VFG_HOME": str(home)})
    assert not any(hasattr(cfg, k) for k in ("orchestrator_model", "plan_model", "plan_check", "tasks_file"))
    assert "VFG_LOOP_SECONDS='90' is no longer read" in capsys.readouterr().out
