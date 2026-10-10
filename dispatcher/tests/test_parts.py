"""The parts on their own: the batch window, reply's retries, the executor, the inbox, the
config, the actions, the running copies and the replay."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
import time

import pytest

from conftest import FakeLLM, FakeVfg, catalog, fail, line as json_line, select
from dispatcher import actions, config, running
from dispatcher.config import now_iso
from dispatcher.batch import Batch
from dispatcher.executor import Ran, argv, read
from dispatcher.inbox import Inbox, State
from dispatcher.replay import read_tasks, replay
from dispatcher.reply import Reply, clean
from dispatcher.selector import Selector
from dispatcher.vfg import VfgResult


def line(tid: str, task: str = "build a castle", at: str = "") -> dict:
    return {"id": tid, "at": at or now_iso(), "task": task, "context": ""}


# ---------------------------------------------------------------- the batch

async def test_the_window_hands_everything_it_collected_in_one_write(tmp_path):
    state, vfg = State(tmp_path / "state.json"), FakeVfg()
    batch = Batch(state, vfg, window_s=0.2, retry_s=(0.01,))
    t0 = time.monotonic()
    batch.add(line("T1"))
    await asyncio.sleep(0.05)
    batch.add(line("T2"))
    batch.add(line("T3"))
    assert vfg.hands() == []  # the window is still open
    while state.batch:
        await asyncio.sleep(0.01)
    assert time.monotonic() - t0 >= 0.2
    assert [[x["id"] for x in hand] for hand in vfg.hands()] == [["T1", "T2", "T3"]]
    assert json.loads((tmp_path / "state.json").read_text())["batch"] == []


async def test_a_failed_hand_is_retried_until_it_succeeds_and_kept_in_state_meanwhile(tmp_path):
    state, vfg = State(tmp_path / "state.json"), FakeVfg()
    vfg.on("orch", "hand", answer=[fail(1, "flock timeout"), fail(1, "flock timeout"),
                                   VfgResult(0, "handed T1\n", "")])
    batch = Batch(state, vfg, window_s=0.01, retry_s=(0.01,))
    batch.add(line("T1"))
    state.save()
    await asyncio.sleep(0.03)
    assert json.loads((tmp_path / "state.json").read_text())["batch"][0]["id"] == "T1"
    while state.batch:
        await asyncio.sleep(0.01)
    assert len(vfg.hands()) == 3


async def test_a_line_vfg_orch_hand_refuses_is_told_to_the_conversation_and_the_rest_handed(tmp_path):
    state, vfg = State(tmp_path / "state.json"), FakeVfg()

    def hand(argv):
        lines = json.loads(argv[2])
        if any(x["id"] == "T2" for x in lines):
            return VfgResult(2, "", "T2: the task is empty\n")
        return VfgResult(0, "".join(f"handed {x['id']}\n" for x in lines), "")

    vfg.on("orch", "hand", answer=hand)
    refused = []
    batch = Batch(state, vfg, window_s=0.01, on_refused=lambda ln, why: refused.append((ln["id"], why)),
                  retry_s=(0.01,))
    for t in ("T1", "T2", "T3"):
        batch.add(line(t))
    while state.batch:
        await asyncio.sleep(0.01)
    assert refused == [("T2", "T2: the task is empty")]
    assert [[x["id"] for x in h] for h in vfg.hands()] == [["T1", "T2", "T3"], ["T1"], ["T2"], ["T3"]]


# ---------------------------------------------------------------- reply

async def test_reply_retries_until_sent_keeping_each_tasks_order(tmp_path):
    state, vfg = State(tmp_path / "state.json"), FakeVfg()
    attempts = {"n": 0}

    def flaky(argv):
        if argv[2] == "T1":
            attempts["n"] += 1
            if attempts["n"] <= 2:
                return fail(1, "ssh: connection reset")
        return VfgResult(0, "5\n", "")

    vfg.on("orch", "reply", answer=flaky)
    reply = Reply(state, vfg, retry_s=(0.02,))
    reply.send("T1", "progress", "first")
    reply.send("T1", "progress", "second")
    reply.send("T2", "done", "other task")
    reply.send("T1", "done", "third")
    state.save()
    assert len(json.loads((tmp_path / "state.json").read_text())["outbox"]) == 4  # on disk before it is sent
    assert await reply.drain(2)

    t1 = [(s, t) for _, s, t in vfg.replies("T1")]
    assert t1 == [("progress", "first"), ("progress", "first"), ("progress", "first"),
                  ("progress", "second"), ("done", "third")]
    # T2 did not wait behind T1's retries
    assert vfg.replies().index(("T2", "done", "other task")) < 3
    assert json.loads((tmp_path / "state.json").read_text())["outbox"] == []


async def test_replies_left_unsent_by_a_stop_are_sent_at_the_next_start_in_order(tmp_path):
    state = State(tmp_path / "state.json", {"outbox": [
        {"id": "T4", "status": "progress", "text": "a"}, {"id": "T4", "status": "done", "text": "b"}]})
    vfg = FakeVfg()
    reply = Reply(state, vfg, retry_s=(0.01,))
    reply.start()
    assert await reply.drain(1)
    assert vfg.replies() == [("T4", "progress", "a"), ("T4", "done", "b")]


def test_reply_text_is_one_line_never_read_as_an_option():
    assert clean("--hi\nthere  x") == "hi there x"
    assert clean("-") == "(no text)"
    assert len(clean("x" * 5000)) == 1000


# ---------------------------------------------------------------- executor

def test_the_executor_says_which_kind_of_outcome_each_exit_is():
    said = read(VfgResult(0, "noise\n" + json_line(True, "teleported Steve to 1 70 2", copy="teleport",
                                                     action="teleport", pos=[1, 70, 2]), ""))
    assert said == Ran(True, "teleported Steve to 1 70 2", {"pos": [1, 70, 2]}, "ok", "teleport", False)
    # vfg's notes go with the summary; a copy that keeps running says so
    said = read(VfgResult(0, json_line(True, "running as galick_gun_2", copy="galick_gun_2", running=True,
                                       notes=["cooldown=500 is over its max, clamped to 60"]), ""))
    assert (said.summary, said.copy, said.running) == \
        ("running as galick_gun_2 (cooldown=500 is over its max, clamped to 60)", "galick_gun_2", True)
    # ok false: Minecraft or the script said no, whatever the exit
    for code in (0, 1):
        assert read(VfgResult(code, json_line(False, "No player was found", failed="give Bob bread"), "")) == \
            Ran(False, "No player was found", {"failed": "give Bob bread"}, "refused")
    assert read(VfgResult(1, json_line(False, "", error="galick_gun_3 is not running"), "")).summary == \
        "galick_gun_3 is not running"
    # it cannot be run at all: exits 2 to 5 (with a JSON line or none), no JSON line, vfg missing
    for code, err in ((2, "vfg: teleport has no value speed"), (3, "vfg: the scripts are not deployed"),
                      (4, "vfg: cannot reach EC2 (ec2-user@1.2.3.4): ssh exited 255"),
                      (5, "vfg: Minecraft is not answering RCON"), (127, "cannot run vfg: no such file"),
                      (1, "vfg: no action tp")):
        assert read(VfgResult(code, "", err + "\n")) == Ran(False, f"could not run it: {err}", kind="not_run")
    assert read(VfgResult(2, json_line(False, "", error="fire is fixed at click"), "")) == \
        Ran(False, "could not run it: fire is fixed at click", kind="not_run")
    partial = read(VfgResult(1, json_line(False, "script error: ValueError: x", partial=True,
                                          ran=["time set day"]), ""))
    assert partial.kind == "partial" and partial.summary == (
        "it may have partly happened (script error: ValueError: x); these ran and took effect: time set day "
        "— check before redoing it")
    assert read(VfgResult(124, "", "vfg: timed out\n"), timeout=20) == \
        Ran(False, "timed out after 20 s; it may or may not have happened — check before redoing it", kind="timeout")


def test_the_executor_runs_vfg_action_run_with_key_value_words():
    assert argv("railgun", {"player": "Steve,Alex", "cooldown": 0.0, "through_walls": False, "range": 24},
                "T7") == ["action", "run", "railgun", "player=Steve,Alex", "cooldown=0", "through_walls=false",
                          "range=24", "--task", "T7"]
    assert argv("command", {"commands": ["give Steve minecraft:bread", 'say "hi"']}, "T8") == \
        ["action", "run", "command", 'commands=["give Steve minecraft:bread","say \\"hi\\""]', "--task", "T8"]
    assert argv("edit", {"copy": "galick_gun_2", "damage_spare": ["Steve"], "particle": "flame"}, "T9") == \
        ["action", "run", "edit", "copy=galick_gun_2", 'damage_spare=["Steve"]', "particle=flame", "--task", "T9"]
    assert argv("railgun", {"player": "Steve"}, "T1", as_name="steve_gun") == \
        ["action", "run", "railgun", "player=Steve", "as=steve_gun", "--task", "T1"]
    assert argv("save_place", {"name": "home", "at": "1 64 -3 the_nether"}, "T2") == \
        ["action", "run", "save_place", "name=home", "at=1 64 -3 the_nether", "--task", "T2"]


# ---------------------------------------------------------------- inbox

def test_the_inbox_reads_whole_new_lines_once_and_saves_the_offset_with_them(tmp_path):
    path = tmp_path / "inbox.jsonl"
    state = State(tmp_path / "state.json")
    inbox = Inbox(path, state)
    assert inbox.poll() == []
    path.write_text(json.dumps(line("T1", "give me bread")) + "\n" + '{"id": "T2", "task": "half')
    assert [x["id"] for x in inbox.poll()] == ["T1"]
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "T1" in saved["tasks"] and saved["offset"] == len(json.dumps(line("T1", "give me bread"))) + 1
    with open(path, "a") as f:
        f.write(' written"}\nnot json\n' + json.dumps(line("T1", "again")) + "\n" + '{"task": "no id"}\n')
    got = inbox.poll()
    assert [(x["id"], x["task"]) for x in got] == [("T2", "half written")]
    assert got[0]["at"]  # a line without a time gets one
    assert inbox.poll() == []


def test_the_inbox_expires_a_line_older_than_the_limit_before_its_save(tmp_path):
    path = tmp_path / "inbox.jsonl"
    state = State(tmp_path / "state.json")
    expired = []

    def on_expired(ln, minutes):
        expired.append((ln["id"], minutes))
        state.outbox.append({"id": ln["id"], "status": "failed", "text": "x"})  # saved with the offset

    inbox = Inbox(path, state, stale_minutes=10, on_expired=on_expired)
    path.write_text(json.dumps(line("T1", at="2026-01-01T00:00:00Z")) + "\n"
                    + json.dumps(line("T2", at=now_iso())) + "\n"
                    + json.dumps(line("T3", at="not a time")) + "\n")
    assert [x["id"] for x in inbox.poll()] == ["T2", "T3"]
    assert [i for i, _ in expired] == ["T1"] and expired[0][1] > 10
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "T1" not in saved["tasks"] and saved["outbox"][0]["id"] == "T1" and "T1" in saved["recent"]


def test_a_task_is_never_run_twice_after_the_inbox_is_replaced(tmp_path):
    path = tmp_path / "inbox.jsonl"
    state = State(tmp_path / "state.json")
    inbox = Inbox(path, state)
    path.write_text(json.dumps(line("T1")) + "\n" + json.dumps(line("T2")) + "\n")
    assert len(inbox.poll()) == 2
    state.tasks.clear()  # both finished
    path.write_text(json.dumps(line("T2")) + "\n")  # smaller than the offset: read again from 0
    assert inbox.poll() == []


def test_an_unreadable_state_file_is_kept_aside_and_the_inbox_read_from_its_end(tmp_path):
    (tmp_path / "inbox.jsonl").write_text(json.dumps(line("T1")) + "\n")
    (tmp_path / "state.json").write_text("{not json")
    state = State.load(tmp_path / "state.json", tmp_path / "inbox.jsonl")
    assert state.offset == (tmp_path / "inbox.jsonl").stat().st_size
    assert list(tmp_path.glob("state.json.bad-*"))


# ---------------------------------------------------------------- config

def test_config_comes_from_the_environment_then_the_config_file_then_defaults(tmp_path):
    home = tmp_path / "vh"
    home.mkdir()
    (home / "config").write_text("VFG_DISPATCH_WINDOW_S=3\nVFG_DISPATCH_MODEL='file/model'\nVFG_DISPATCH_RETRIES=x\n")
    cfg = config.load({"VFG_HOME": str(home), "VFG_DISPATCH_MODEL": "env/model"})
    assert cfg.model == "env/model" and cfg.window_s == 3.0 and cfg.retries == 1
    assert cfg.dispatch_dir == home / "dispatch" and cfg.orch_dir == home / "orch"
    assert cfg.inbox == home / "dispatch" / "inbox.jsonl" and cfg.action_timeout_s == 20.0
    assert cfg.stale_minutes == 10
    assert cfg.model_timeout_s == 10.0
    assert config.load({"VFG_HOME": str(home), "VFG_DISPATCH_MODEL_TIMEOUT_S": "4.5"}).model_timeout_s == 4.5
    assert config.load({"VFG_HOME": str(home), "VFG_ORCH_STALE_MINUTES": "3"}).stale_minutes == 3
    cfg = config.load({"VFG_HOME": str(tmp_path / "none"), "VFG_DISPATCH_DIR": str(tmp_path / "d")})
    assert cfg.dispatch_dir == tmp_path / "d" and cfg.model == "~deepseek/deepseek-v4-flash-latest"
    assert cfg.repo == config.REPO and (config.REPO / "dispatcher" / "run").exists()
    assert (cfg.server, cfg.running_ttl_s, cfg.running_file) == ("main", 30.0, tmp_path / "none" / "running.json")
    cfg = config.load({"VFG_HOME": str(home), "VFG_SERVER": "lobby", "VFG_RUNNING_TTL_S": "5"})
    assert (cfg.server, cfg.running_ttl_s) == ("lobby", 5.0)


async def test_every_model_call_carries_the_providers_of_the_config(tmp_path, monkeypatch):
    home = tmp_path / "vh"
    home.mkdir()
    assert config.load({"VFG_HOME": str(home)}).providers == ("parasail", "wafer", "phala", "together", "venice")
    (home / "config").write_text("VFG_DISPATCH_PROVIDERS= wafer , phala,\n")
    assert config.load({"VFG_HOME": str(home)}).providers == ("wafer", "phala")
    assert config.load({"VFG_HOME": str(home), "VFG_DISPATCH_PROVIDERS": "none"}).providers == ()

    import vfg_llm
    from dispatcher.__main__ import asker
    calls = []

    async def fake(prompt, output, **kw):
        calls.append(kw)

    monkeypatch.setattr(vfg_llm, "structured", fake)
    await asker(config.load({"VFG_HOME": str(home)}))("p", object, system="s", model="m")
    await asker(config.load({"VFG_HOME": str(home), "VFG_DISPATCH_PROVIDERS": "none"}))("p", object, model="m")
    assert calls == [{"system": "s", "model": "m", "reasoning": False, "providers": ["wafer", "phala"], "timeout": 10.0},
                     {"system": None, "model": "m", "reasoning": False, "providers": None, "timeout": 10.0}]


def test_a_log_line_is_iso_time_area_message(capsys):
    config.log("select", "T7 812 ms\nsecond line")
    out = capsys.readouterr().out
    stamp, area, rest = out.split(" ", 2)
    assert stamp.endswith("Z") and "T" in stamp and area == "select" and rest == "T7 812 ms second line\n"


# ---------------------------------------------------------------- actions

def test_the_repos_actions_all_load_and_build_the_selector():
    cat = catalog()
    assert cat.errors() == {}
    assert {"command", "teleport", "summon_around", "save_place", "stop", "edit", "railgun",
            "gatling_gun"} <= set(cat.names())
    assert "run_script" not in cat.names() and "stop_script" not in cat.names()
    sel = Selector(cat, None, "m")  # the menu and the Literal of names build
    for name in cat.names():
        assert f"\n- {name} — " in sel.rules
    assert actions.command_fields(cat.get("command")) == ["commands"]
    assert actions.command_fields(cat.get("teleport")) == []


def test_a_copy_is_named_with_its_underscores_or_with_spaces():
    names = ["galick_gun", "galick_gun_2", "rain"]
    assert actions.named("stop galick_gun_2 now", names) == ["galick_gun_2"]
    assert actions.named("stop Galick Gun", names) == ["galick_gun"]
    assert actions.named("stop the galick gun 2", names) == ["galick_gun", "galick_gun_2"]  # unsure: the planner picks
    assert actions.named("make it rain harder", names) == ["rain"] and actions.named("raining", names) == []


def test_a_value_on_the_command_line():
    assert [actions.word(v) for v in (True, False, 2.0, 2.5, 3, "a b", [1, "x"], {"k": 1})] == \
        ["true", "false", "2", "2.5", "3", "a b", '[1,"x"]', '{"k":1}']


# ---------------------------------------------------------------- running copies

def test_running_json_is_read_as_vfg_writes_it_and_a_bad_file_is_none(tmp_path):
    path = tmp_path / "running.json"
    assert running.read(path) == {}
    path.write_text("{not json")
    assert running.read(path) == {}
    path.write_text(json.dumps({"fetched_at": 1, "copies": {"rain": {"action": "lava_rain"}, "bad": 3}}))
    assert running.read(path) == {"rain": {"action": "lava_rain"}}


def test_a_copys_line_is_its_name_its_action_the_values_that_differ_and_its_task():
    cat = catalog()
    rail = {"action": "railgun", "task": "T12", "values": {
        "player": "Alex", "cooldown": 0, "range": 100, "fire": "click", "damage_amount": 30, "damage_spare": ["Bob"]}}
    assert running.line("galick_gun_2", rail, cat) == \
        'galick_gun_2: railgun, player=Alex cooldown=0 damage_spare=["Bob"], started by T12'
    # a fixed value is a default; a copy at its defaults says so
    gat = {"action": "gatling_gun", "task": "", "values": {"player": "@a", "fire": "hold", "cooldown": 0.1}}
    assert running.line("gatling_gun", gat, cat) == "gatling_gun: gatling_gun, its defaults"
    # its action not loaded: every value
    assert running.line("zap", {"action": "zap", "values": {"player": "Steve"}, "task": "T3"}, cat) == \
        "zap: zap, player=Steve, started by T3"
    long = {"action": "railgun", "values": {"player": ",".join(["Steve"] * 100)}}
    assert running.line("r", long, cat).endswith("…") and len(running.line("r", long, cat)) < 330
    assert running.block({}, cat) == "RUNNING NOW: none"
    assert running.block({"b": gat, "a": rail}, cat).splitlines()[1:] == [
        "- " + running.line("a", rail, cat), "- " + running.line("b", gat, cat)]


async def test_the_running_copies_are_fetched_in_the_background_every_ttl_and_failures_logged_once(capsys):
    vfg = FakeVfg().on("script", "list", answer=[fail(255, "ssh: connect to host"), fail(255, "ssh: connect to host"),
                                                 VfgResult(0, "{}\n", "")])
    r = running.Refresher(vfg, every=0.05)
    r.start()
    await asyncio.sleep(0.18)
    await r.stop()
    fetches = [a for a in vfg.calls if a[:2] == ["script", "list"]]
    assert len(fetches) >= 3 and all(a == ["script", "list", "--json", "--fresh"] for a in fetches)
    out = capsys.readouterr().out
    assert out.count("vfg script list --fresh failed (ssh: connect to host)") == 1
    assert "vfg script list --fresh works again" in out
    off = running.Refresher(vfg, every=0)
    off.start()
    assert off.task is None  # VFG_RUNNING_TTL_S=0: never


async def test_an_ask_never_waits_on_the_background_fetch(tmp_path):
    from conftest import Harness, planned, tp

    h = Harness(tmp_path)
    slow = asyncio.Event()

    async def hung(argv):
        await slow.wait()
        return VfgResult(0, "{}\n", "")

    h.vfg.on("script", "list", answer=hung)
    fetching = running.Refresher(h.vfg, every=30)
    fetching.start()
    await asyncio.sleep(0)
    h.running(railgun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}})
    h.llm.add("Selection", select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))
    t0 = time.monotonic()
    tid = await asyncio.wait_for(h.run("take me to a village"), 2)
    assert time.monotonic() - t0 < 1 and h.vfg.replies(tid)[0][1] == "done"
    assert "- railgun: railgun, player=Steve, started by T3" in h.llm.prompts("Selection")[0]  # read from the file
    assert not fetching.task.done()  # still hung
    slow.set()
    await fetching.stop()


# ---------------------------------------------------------------- replay

async def test_replay_runs_only_the_selector_and_prints_each_selection_with_its_cost(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text("\n".join(json.dumps(x) for x in (
        line("T1", "give me speed"), line("T1", "give me speed"), {"id": "T2", "task": "a castle, please", "context": "c"},
        {"oops": 1}, line("T3", "a third"))) + "\n")
    before = inbox.read_bytes()
    tasks = read_tasks(inbox)
    assert [t["id"] for t in tasks] == ["T1", "T2", "T3"]
    assert [t["id"] for t in read_tasks(inbox, limit=2)] == ["T1", "T2"]
    assert [t["id"] for t in read_tasks(inbox, only={"T3", "T1"})] == ["T1", "T3"]

    from vfg_llm import LlmError
    answers = {"give me speed": select(("command", "give Alex speed")), "a castle, please": select(),
               "a third": LlmError("down")}
    llm = FakeLLM(Selection=[lambda prompt: next(v for k, v in answers.items() if f"TASK: {k}\n" in prompt)] * 3)
    out = io.StringIO()
    summary = await replay(tasks, Selector(catalog(), llm, "some/model"), out)
    text = out.getvalue()
    assert "T1  7 ms  $0.000010  give me speed\n    command: give Alex speed" in text
    assert "T2  7 ms  $0.000010  a castle, please\n    no picks" in text
    assert "T3  FAILED" in text
    assert summary == {"tasks": 3, "failed": 1, "cost": pytest.approx(0.00002), "median_ms": 7, "p90_ms": 7,
                       "calls": 2, "calls_median_ms": 7, "calls_p90_ms": 7}
    assert all("SAVED PLACES: none" in p and "RUNNING NOW: none" in p for p in llm.prompts())
    assert inbox.read_bytes() == before


# ---------------------------------------------------------------- selector checks

def parts(*xs: tuple[str, str]) -> dict:
    return {"parts": [{"part": i or "it", "to": to, "instruction": i} for to, i in xs]}


@pytest.mark.parametrize("answer,picks,orchestrator", [
    # adjacent command parts are one pick, in order; at most three of them
    (parts(("command", "give Steve 64 firework rockets"), ("command", "give Steve Speed II")),
     [("command", "give Steve 64 firework rockets; give Steve Speed II")], ""),
    (parts(("command", "a"), ("command", "b"), ("command", "c"), ("command", "d")),
     [("command", "a; b; c"), ("command", "d")], ""),
    # a part between them keeps them apart, and the order
    (parts(("command", "give Steve bread"), ("teleport", "teleport Steve home"), ("command", "make it day")),
     [("command", "give Steve bread"), ("teleport", "teleport Steve home"), ("command", "make it day")], ""),
    # the orchestrator's parts, joined; an empty instruction is the part's own words
    (parts(("command", "give Steve speed"), ("orchestrator", "build Steve a tower"), ("orchestrator", "")),
     [("command", "give Steve speed")], "build Steve a tower; it"),
    # a repeated pick once
    (parts(("teleport", "Teleport Steve home"), ("command", "x"), ("teleport", "teleport  steve home.")),
     [("teleport", "Teleport Steve home"), ("command", "x")], ""),
    # every part the orchestrator's, or more than four picks: the whole task
    (parts(("orchestrator", ""), ("orchestrator", "build it")), [], ""),
    (parts(*[(a, f"{a} {i}") for i, a in enumerate(["command", "teleport"] * 3)]), [], ""),
])
async def test_the_parts_become_picks_and_the_orchestrator_text(answer, picks, orchestrator):
    got = await Selector(catalog(), FakeLLM(Selection=[answer]), "m").select("t", "", "SAVED PLACES: none")
    assert [(p.action, p.instruction) for p in got.value.picks] == picks
    assert got.value.orchestrator == orchestrator


@pytest.mark.parametrize("part", [("command", "nothing"), ("command", "N/A."), ("command", " "), ("orchestrator", "none"),
                                  ("teleport", "teleport Steve to his current position"),
                                  ("teleport", "teleport Steve to where he stands")])
def test_a_part_that_does_nothing_is_refused(part):
    from pydantic import ValidationError
    Selection = Selector(catalog(), None, "m").Selection
    with pytest.raises(ValidationError, match="leave the part out|has no instruction"):
        Selection.model_validate(parts(part))
    Selection.model_validate(parts(("teleport", "teleport Steve to the place home"), ("orchestrator", "")))
    with pytest.raises(ValidationError):
        Selection.model_validate({"parts": []})  # a task has at least one part


def test_a_part_names_an_action_on_the_menu_or_the_orchestrator():
    from pydantic import ValidationError
    Selection = Selector(catalog(), None, "m").Selection
    Selection.model_validate(parts(("stop", "stop galick_gun_2"), ("edit", "set rain's size to 3")))
    for gone in ("run_script", "stop_script", "zap"):
        with pytest.raises(ValidationError):
            Selection.model_validate(parts((gone, "do it")))


async def test_a_task_that_builds_with_picks_and_no_orchestrator_part_keeps_its_picks():
    llm = FakeLLM(Selection=[select(("command", "give Steve speed"))])
    sel = Selector(catalog(), llm, "m")
    got = await sel.select("Give Steve speed and build a small stone tower at his position", "", "")
    assert [p.action for p in got.value.picks] == ["command"]  # what is routed is what runs: no regex hands it on


async def test_replay_scores_routes_and_lost_work_against_labels():
    tasks = [{"id": f"T{i}", "task": t, "context": ""} for i, t in enumerate(
        ["give me speed", "a castle, please", "speed and a castle", "save this spot for the castle",
         "stop the gun", "make a frost bow", "maybe", "Fix T2: taller"], 1)]
    answers = {
        "give me speed": select(("command", "give Alex speed")),                       # agrees
        "a castle, please": select(),                                                       # agrees
        "speed and a castle": select(("command", "give Alex speed"), orchestrator="a castle, please"),  # agrees
        "save this spot for the castle": select(("save_place", "save the castle spot")),  # lost and misrouted
        "stop the gun": select(("stop", "stop galick_gun")),                              # agrees
        "make a frost bow": select(("railgun", "give Alex a railgun")),                    # misrouted
        "maybe": select(("command", "do it")),                                            # either: not scored
    }
    labels = {"T1": {"accept": {"command"}, "either": False, "parts": ["speed", "rockets?"]},  # rockets dropped
              "T2": {"accept": {"orchestrator"}, "either": False},
              "T3": {"accept": {"command+orchestrator"}, "either": False, "parts": ["speed", "castle"]},
              "T4": {"accept": {"orchestrator"}, "either": False},
              "T5": {"accept": {"stop"}, "either": False},
              "T6": {"accept": {"orchestrator"}, "either": False},
              "T7": {"accept": {"command", "orchestrator"}, "either": True},
              "T8": {"accept": {"orchestrator"}, "either": False}}
    llm = FakeLLM(Selection=[lambda prompt: next(v for k, v in answers.items() if f"TASK: {k}\n" in prompt)] * 7)
    out = io.StringIO()
    summary = await replay(tasks, Selector(catalog(), llm, "m"), out, labels=labels, jobs=3)
    assert len(llm.calls) == 7  # "Fix T2: taller" needed no model call
    assert {k: summary[k] for k in ("scored", "agree", "lost", "calls", "multipart", "covered")} == \
        {"scored": 7, "agree": 5, "lost": 3, "calls": 7, "multipart": 2, "covered": 1}
    text = out.getvalue()
    assert "[LOST]" in text and "MISROUTE save_place, expected orchestrator" in text
    assert "MISROUTE railgun, expected orchestrator" in text
    assert "T8  0 ms  $0.000000  Fix T2: taller\n    no picks" in text
    assert "give me speed  [DROPPED rockets?]  [LOST]" in text
    assert "route agreement 5/7 (71.4%), 3 lost, 1 either, 0 unlabelled; every part named in 1/2 multi-part task(s)" in text


# ---------------------------------------------------------------- vfg

async def test_vfg_runs_the_binary_with_an_argv_and_no_shell_and_times_out_with_124(tmp_path, monkeypatch):
    from dispatcher.vfg import vfg, why
    fake = tmp_path / "bin" / "vfg"
    fake.parent.mkdir()
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, time
        if sys.argv[1] == "sleep":
            time.sleep(30)
        if sys.argv[1] == "fail":
            print("partial"); print("vfg: refused: stop", file=sys.stderr); sys.exit(3)
        print(repr(sys.argv[1:]))
    """))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    r = await vfg("orch", "reply", "T1", "done", "a; b $(x) 'c'")
    assert (r.code, r.stdout.strip()) == (0, repr(["orch", "reply", "T1", "done", "a; b $(x) 'c'"]))
    r = await vfg("fail")
    assert r.code == 3 and why(r) == "vfg: refused: stop"
    t0 = time.monotonic()
    r = await vfg("sleep", timeout=0.3)
    assert r.code == 124 and time.monotonic() - t0 < 5 and "timed out" in why(r)


def test_replay_passes_the_model_timeout_to_every_structured_call(tmp_path, monkeypatch, capsys):
    import vfg_llm
    from vfg_llm import Answer

    from dispatcher import replay as replay_module

    home = tmp_path / "vh"
    (home / "orch").mkdir(parents=True)
    (home / "orch" / "inbox.jsonl").write_text(json.dumps(line("T1", "give me bread")) + "\n")
    monkeypatch.setenv("VFG_HOME", str(home))
    monkeypatch.setenv("VFG_DISPATCH_MODEL_TIMEOUT_S", "3")
    seen = []

    async def fake_structured(prompt, output, **kw):
        seen.append(kw)
        return Answer[output](value=output.model_validate({"parts": [{"part": "x", "to": "orchestrator"}]}), cost=0.0, ms=1)

    monkeypatch.setattr(vfg_llm, "structured", fake_structured)
    assert replay_module.main(["--limit", "1"]) == 0
    assert seen and all(kw["timeout"] == 3.0 and kw["reasoning"] is False for kw in seen)


async def test_a_line_is_handed_only_once_its_tasks_replies_are_out(tmp_path):
    """The conversation hears "handed on" before the orchestrator can answer: a failing reply holds its line."""
    state, vfg = State(tmp_path / "state.json"), FakeVfg()
    vfg.on("orch", "reply", answer=[fail(1, "ssh: connection reset"), fail(1, "ssh: connection reset"),
                                    VfgResult(0, "7\n", "")])
    reply = Reply(state, vfg, retry_s=(0.1,))
    batch = Batch(state, vfg, window_s=0.02, retry_s=(0.01,), held_s=0.02)
    batch.add(line("T1"))
    reply.send("T1", "progress", "handed on to the orchestrator: build a castle")
    batch.add(line("T2"))  # nothing to say for T2: it does not wait for T1
    state.save()
    while state.batch:
        await asyncio.sleep(0.01)
    hands = [i for i, a in enumerate(vfg.calls) if a[:2] == ["orch", "hand"]]
    sent = max(i for i, a in enumerate(vfg.calls) if a[:2] == ["orch", "reply"])
    assert [[x["id"] for x in h] for h in vfg.hands()] == [["T2"], ["T1"]]
    assert hands[0] < sent < hands[1]  # T1 went only after its reply got through, on the third try
    assert len(vfg.replies("T1")) == 3


async def test_a_held_line_never_keeps_a_stop_past_its_deadline(tmp_path):
    state, vfg = State(tmp_path / "state.json"), FakeVfg()
    state.outbox.append({"id": "T1", "status": "progress", "text": "never sent"})
    batch = Batch(state, vfg, window_s=10, held_s=0.05)
    batch.add(line("T1"))
    t0 = time.monotonic()
    assert await batch.flush(0.2) is False
    assert time.monotonic() - t0 < 0.5 and vfg.hands() == [] and [x["id"] for x in state.batch] == ["T1"]
