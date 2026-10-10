"""The parts on their own: the batch window, reply's retries, the executor, the inbox, the
config, the action loader and the replay."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
import time

import pytest

from conftest import CATALOG, FakeLLM, FakeVfg, fail, select
from dispatcher import actions, config
from dispatcher.config import now_iso
from dispatcher.batch import Batch
from dispatcher.executor import Ran, read
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
    assert read(VfgResult(0, 'noise\n{"ok": true, "summary": "teleported", "pos": [1, 2, 3]}\n', "")) == \
        Ran(True, "teleported", {"pos": [1, 2, 3]}, "ok")
    assert read(VfgResult(0, '{"ok": false, "summary": "No player was found", "failed": "give Bob bread"}\n', "")) == \
        Ran(False, "No player was found", {"failed": "give Bob bread"}, "refused")
    assert read(VfgResult(0, "saved home at -368 70 912 in overworld\n", ""), action_run=False) == \
        Ran(True, "saved home at -368 70 912 in overworld", kind="ok")
    # exit 1 is Minecraft's or the toolkit's refusal for a command printing no JSON...
    assert read(VfgResult(1, "", "vfg: zeus is not running\n"), action_run=False) == \
        Ran(False, "vfg: zeus is not running", kind="refused")
    # ...but from vfg action run it means the action cannot be run at all
    assert read(VfgResult(1, "", "vfg: no actions/tp/ in ~/vfg\n")) == \
        Ran(False, "could not run it: vfg: no actions/tp/ in ~/vfg", kind="not_run")
    for code, err in ((2, "vfg: --args is not JSON"), (3, "vfg: mc.py is not deployed"),
                      (4, "vfg: cannot reach EC2 (ec2-user@1.2.3.4): ssh exited 255"),
                      (5, "vfg: Minecraft is not answering RCON"), (127, "cannot run vfg: no such file")):
        for action_run in (True, False):
            assert read(VfgResult(code, "", err + "\n"), action_run=action_run) == \
                Ran(False, f"could not run it: {err}", kind="not_run")
    partial = read(VfgResult(0, '{"ok": false, "summary": "handler error: ValueError: x", "partial": true, '
                                '"ran": ["time set day"]}\n', ""))
    assert partial.kind == "partial" and partial.summary == (
        "it may have partly happened (handler error: ValueError: x); these ran and took effect: time set day "
        "— check before redoing it")
    assert read(VfgResult(124, "", "vfg: timed out: give did not finish within 20 s\n"), timeout=20) == \
        Ran(False, "timed out after 20 s; it may or may not have happened — check before redoing it", kind="timeout")


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


async def test_the_planner_has_its_own_model_providers_and_reasoning(tmp_path, monkeypatch):
    home = tmp_path / "vh"
    home.mkdir()
    same = config.load({"VFG_HOME": str(home), "VFG_DISPATCH_MODEL": "openai/gpt-6.1-sol",
                        "VFG_DISPATCH_REASONING": "low", "VFG_DISPATCH_PROVIDERS": "none"})
    assert (same.plan_model, same.plan_providers, same.plan_effort) == ("openai/gpt-6.1-sol", (), "low")
    (home / "config").write_text("VFG_DISPATCH_MODEL=openai/gpt-6.1-sol\nVFG_DISPATCH_REASONING=low\n"
                                 "VFG_DISPATCH_PROVIDERS=none\nVFG_DISPATCH_PLAN_MODEL=~deepseek/deepseek-v4-flash-latest\n"
                                 "VFG_DISPATCH_PLAN_REASONING=off\nVFG_DISPATCH_PLAN_PROVIDERS=parasail,wafer\n")
    cfg = config.load({"VFG_HOME": str(home)})
    assert (cfg.model, cfg.providers, cfg.effort) == ("openai/gpt-6.1-sol", (), "low")
    assert (cfg.plan_model, cfg.plan_providers, cfg.plan_effort) == ("~deepseek/deepseek-v4-flash-latest", ("parasail", "wafer"), False)

    import vfg_llm
    from dispatcher.__main__ import asker
    calls = []

    async def fake(prompt, output, **kw):
        calls.append((kw["providers"], kw["reasoning"]))

    monkeypatch.setattr(vfg_llm, "structured", fake)
    await asker(cfg)("p", object, model="m")
    await asker(cfg, plan=True)("p", object, model="m")
    assert calls == [(None, "low"), (["parasail", "wafer"], False)]


def test_a_log_line_is_iso_time_area_message(capsys):
    config.log("select", "T7 812 ms\nsecond line")
    out = capsys.readouterr().out
    stamp, area, rest = out.split(" ", 2)
    assert stamp.endswith("Z") and "T" in stamp and area == "select" and rest == "T7 812 ms second line\n"


# ---------------------------------------------------------------- actions

@pytest.fixture
def fresh_actions_package():
    """The loader imports the package `actions`; give each test its own and put sys back after."""
    saved_path, saved = list(sys.path), {k: v for k, v in sys.modules.items() if k == "actions" or k.startswith("actions.")}
    for k in saved:
        del sys.modules[k]
    yield
    for k in [k for k in sys.modules if k == "actions" or k.startswith("actions.")]:
        del sys.modules[k]
    sys.modules.update(saved)
    sys.path[:] = saved_path


def write_action(root, name: str, body: str) -> None:
    folder = root / "actions" / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    (folder / "action.py").write_text(textwrap.dedent(body))


def test_every_action_class_loads_with_its_menu_line(tmp_path, fresh_actions_package):
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "__init__.py").write_text("")
    (tmp_path / "actions" / "_types.py").write_text(textwrap.dedent('''
        from pydantic import BaseModel, ConfigDict
        class Action(BaseModel):
            model_config = ConfigDict(extra="forbid")
    '''))
    write_action(tmp_path, "wave", '''
        from typing import Literal
        from pydantic import Field
        from actions._types import Action
        class Wave(Action):
            """Make a player wave.

            The rest is for the planner."""
            action: Literal["wave"]
            player: str = Field(description="who waves")
    ''')
    write_action(tmp_path, "nod", '''
        from typing import Literal
        from actions._types import Action
        class Helper(Action):
            action: Literal["other"]
        class Nod(Action):
            """Make a player nod."""
            action: Literal["nod"]
    ''')
    write_action(tmp_path, "broken", "import nothing_here_at_all\n")
    (tmp_path / "actions" / "empty").mkdir()  # no action.py: not an action

    catalog = actions.load(tmp_path)
    assert list(catalog) == ["nod", "wave"]
    assert actions.menu_line(catalog["wave"]) == "Make a player wave."
    assert actions.doc(catalog["wave"]).endswith("The rest is for the planner.")
    assert actions.action_name(catalog["nod"]) == "nod"


def test_the_repos_own_actions_all_load(fresh_actions_package):
    root = config.REPO / "actions"
    folders = sorted(p.name for p in root.iterdir() if (p / "action.py").is_file()) if root.is_dir() else []
    if not folders:
        pytest.skip("actions/ has no action yet")
    catalog = actions.load(config.REPO)
    assert sorted(catalog) == folders
    for name, cls in catalog.items():
        assert actions.menu_line(cls), f"{name} has no docstring"
        assert hasattr(cls, "vfg_argv")
        for field, info in cls.model_fields.items():
            assert field == "action" or info.description, f"{name}.{field} has no description"
    Selector(catalog, None, "m")  # the menu and the Literal of names build


def script(root, name: str, main: str, readme: str | None = None) -> None:
    (root / "scripts" / name).mkdir(parents=True)
    (root / "scripts" / name / "main.py").write_text(main)
    if readme is not None:
        (root / "scripts" / name / "README.md").write_text(readme)


def test_a_new_format_script_is_its_docstrings_first_line_and_one_line_per_value(tmp_path):
    values = textwrap.dedent("""\
        VALUES = {
            "player":  {"type": "player", "default": "@a"},
            "strikes": {"type": "int", "min": 1, "max": 10, "default": 1, "live": True},
            "speed":   {"type": "number", "min": 0.5, "default": 2.0},
            "mode":    {"type": "enum", "of": ["one", "ring"], "default": "one", "live": True},
            "spare":   {"type": "list", "of": "player", "default": []},
            "mobs":    {"type": "list", "of": ["zombie", "husk"], "default": ["zombie"]},
            "loud":    {"type": "bool", "default": False},
            "label":   {"type": "str", "default": ""},
        }
    """)
    script(tmp_path, "zeus", '"""Zeus: lightning where tridents land.\n\nMore words."""\nASKS = ["zeus"]\n'
           "from kit import values\nimport mc\n\n" + values + "v = values(VALUES)\nraise SystemExit('never run')\n",
           "README words that lose.\nx\ny\n")
    script(tmp_path, "calm", '"""Calm: nothing at all."""\nVALUES = {}\n')
    assert actions.scripts(tmp_path) == {
        "calm": "Calm: nothing at all.\n  (takes no values)",
        "zeus": "Zeus: lightning where tridents land.\n"
                "  player (player, default @a)\n"
                "  strikes (int, default 1, 1..10)\n"
                "  speed (number, default 2, 0.5..)\n"
                "  mode (enum, default one, one of one, ring)\n"
                "  spare (list of player, default [])\n"
                "  mobs (list, default [\"zombie\"], each one of zombie, husk)\n"
                "  loud (bool, default false)\n"
                "  label (str, default \"\")",
    }


def test_a_script_with_no_values_is_its_readmes_first_three_lines(tmp_path):
    script(tmp_path, "tnt_rain", "import mc\n", "Drops TNT.\nUsage: tnt_rain [every_s=20]\nEnds: when stopped\n\nmore")
    script(tmp_path, "zeus", '"""\nLightning where tridents land.\n\nUsage: zeus <player>\nEnds: when stopped\nMore.\n"""\n'
           "import mc\n", "Lightning, from the README.\nUsage: zeus <player>\n\nmore")
    script(tmp_path, "computed", '"""Computed."""\nVALUES = dict(a={"type": "int", "default": 1})\n', "Old words.\n")
    script(tmp_path, "docs_only", '"""\nDoes a thing.\nUsage: docs_only\nEnds: at once\nMore.\n"""\n')
    script(tmp_path, "broken", "def (:\n", "Old seed script.\n")
    script(tmp_path, "bare", "import mc\n")
    script(tmp_path, "_lib", '"""not a script"""\nVALUES = {}\n')
    assert actions.scripts(tmp_path) == {
        "bare": "(no description)",
        "broken": "Old seed script.",
        "computed": "Old words.",  # VALUES that is not a literal: as if it had none
        "docs_only": "Does a thing.\n  Usage: docs_only\n  Ends: at once",
        "tnt_rain": "Drops TNT.\n  Usage: tnt_rain [every_s=20]\n  Ends: when stopped",
        "zeus": "Lightning, from the README.\n  Usage: zeus <player>",
    }


def test_the_repos_scripts_reach_the_selectors_menu_and_the_planner_with_every_value(fresh_actions_package):
    """ScriptName, the selector's menu and the planner's view all list the same scripts, and
    each new-format one shows its docstring's first line and one line per VALUES entry; the
    names come from the scripts themselves, never from here."""
    from dispatcher.action_planner import Planner

    if not (config.REPO / "actions" / "run_script" / "action.py").is_file():
        pytest.skip("actions/ has no run_script yet")
    catalog = actions.load(config.REPO)
    found = actions.scripts(config.REPO)
    from actions import _types
    assert list(found) == list(_types.SCRIPT_NAMES) and found
    run_line = next(x for x in Selector(catalog, None, "m", scripts=found).rules.splitlines()
                    if x.startswith("- run_script:"))
    assert run_line.endswith(" The only scripts: %s." % ", ".join(sorted(found)))
    planner = Planner(None, "m", None, found)
    for name in found:
        tree = actions.parse(config.REPO / "scripts" / name / "main.py")
        values = actions.script_values(tree)
        if values is None:
            continue
        first = actions.docstring_of(tree).strip().splitlines()[0]
        block = "\n".join(["- %s: %s" % (name, first), *("  " + actions.value_line(k, e) for k, e in values.items())])
        assert block + "\n" in planner.describe(catalog["run_script"], "start %s for Steve" % name), name


RUN_SCRIPT_PROMPT_MAX = 4_000  # bytes, for an instruction naming one script


def test_the_planner_sees_every_value_of_the_script_named_and_the_others_by_name(fresh_actions_package):
    """"start tnt_rain on Steve": tnt_rain's first line and values, none of zeus's, every other
    script by name on one line, and the whole prompt small. The value names come from the scripts."""
    from dispatcher.action_planner import Pick, Planner

    found = actions.scripts(config.REPO)
    values = {n: actions.script_values(actions.parse(config.REPO / "scripts" / n / "main.py")) for n in ("tnt_rain", "zeus")
              if n in found}
    if len(values) < 2 or not all(values.values()) or not (config.REPO / "actions" / "run_script").is_dir():
        pytest.skip("needs scripts/tnt_rain and scripts/zeus with VALUES, and actions/run_script")
    catalog = actions.load(config.REPO)
    pick = Pick(task_id="T1", index=0, instruction="start tnt_rain on Steve", context="Steve asked",
                places="SAVED PLACES: none", reports=[])
    prompt = Planner(None, "m", None, found).base(catalog["run_script"], pick)
    assert "\n- tnt_rain: %s\n" % found["tnt_rain"] in prompt  # its first line and every value
    assert "\nOTHER SCRIPTS: %s\n" % ", ".join(n for n in found if n != "tnt_rain") in prompt
    assert found["zeus"].splitlines()[0] not in prompt  # zeus by name alone
    for key in set(values["zeus"]) - set(values["tnt_rain"]):
        assert "  %s (" % key not in prompt, key
    size = len(prompt.encode("utf-8"))
    print("run_script planner prompt for 'start tnt_rain on Steve': %d bytes" % size)
    assert size < RUN_SCRIPT_PROMPT_MAX


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
    summary = await replay(tasks, Selector(CATALOG, llm, "some/model"), out)
    text = out.getvalue()
    assert "T1  7 ms  $0.000010  give me speed\n    command: give Alex speed" in text
    assert "T2  7 ms  $0.000010  a castle, please\n    no picks" in text
    assert "T3  FAILED" in text
    assert summary == {"tasks": 3, "failed": 1, "cost": pytest.approx(0.00002), "median_ms": 7, "p90_ms": 7,
                       "calls": 2, "calls_median_ms": 7, "calls_p90_ms": 7, "invented": 0, "caught": 0}
    assert all("SAVED PLACES: none" in p for p in llm.prompts())
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
    got = await Selector(CATALOG, FakeLLM(Selection=[answer]), "m").select("t", "", "SAVED PLACES: none")
    assert [(p.action, p.instruction) for p in got.value.picks] == picks
    assert got.value.orchestrator == orchestrator


@pytest.mark.parametrize("part", [("command", "nothing"), ("command", "N/A."), ("command", " "), ("orchestrator", "none"),
                                  ("teleport", "teleport Steve to his current position"),
                                  ("teleport", "teleport Steve to where he stands")])
def test_a_part_that_does_nothing_is_refused(part):
    from pydantic import ValidationError
    Selection = Selector(CATALOG, None, "m").Selection
    with pytest.raises(ValidationError, match="leave the part out|has no instruction"):
        Selection.model_validate(parts(part))
    Selection.model_validate(parts(("teleport", "teleport Steve to the place home"), ("orchestrator", "")))
    with pytest.raises(ValidationError):
        Selection.model_validate({"parts": []})  # a task has at least one part


async def test_a_task_that_builds_with_picks_and_no_orchestrator_part_is_handed_whole():
    llm = FakeLLM(Selection=[select(("command", "give Steve speed")),
                             select(("command", "give Steve speed"), orchestrator="build Steve a stone tower"),
                             select(("command", "take the armor off Steve"))])
    sel = Selector(CATALOG, llm, "m")
    whole = await sel.select("Give Steve speed and build a small stone tower at his position", "", "")
    assert whole.value.picks == [] and whole.value.orchestrator == ""
    split = await sel.select("Give Steve speed and build a small stone tower at his position", "", "")
    assert [p.action for p in split.value.picks] == ["command"] and split.value.orchestrator
    plain = await sel.select("Remove all armor from Steve", "", "")  # armor, not blocks
    assert [p.action for p in plain.value.picks] == ["command"]


async def test_replay_scores_routes_lost_work_and_unknown_scripts_against_labels():
    tasks = [{"id": f"T{i}", "task": t, "context": ""} for i, t in enumerate(
        ["give me speed", "a castle, please", "speed and a castle", "save this spot for the castle",
         "start the lightning script", "make a frost bow", "maybe", "Change to T2: taller"], 1)]
    answers = {
        "give me speed": select(("command", "give Alex speed")),                       # agrees
        "a castle, please": select(),                                                       # agrees
        "speed and a castle": select(("command", "give Alex speed"), orchestrator="a castle, please"),  # agrees
        "save this spot for the castle": select(("save_place", "save the castle spot")),  # lost and misrouted
        "start the lightning script": select(("run_script", "start zeus for Alex")),  # agrees
        "make a frost bow": select(("run_script", "run frost_bow.py for Alex"),
                                          orchestrator="make the bow"),                 # caught: handed on whole
        "maybe": select(("command", "do it")),                                            # either: not scored
    }
    labels = {"T1": {"accept": {"command"}, "either": False, "parts": ["speed", "rockets?"]},  # rockets dropped
              "T2": {"accept": {"orchestrator"}, "either": False},
              "T3": {"accept": {"command+orchestrator"}, "either": False, "parts": ["speed", "castle"]},
              "T4": {"accept": {"orchestrator"}, "either": False},
              "T5": {"accept": {"run_script"}, "either": False},
              "T6": {"accept": {"orchestrator"}, "either": False},
              "T7": {"accept": {"command", "orchestrator"}, "either": True},
              "T8": {"accept": {"orchestrator"}, "either": False}}
    llm = FakeLLM(Selection=[lambda prompt: next(v for k, v in answers.items() if f"TASK: {k}\n" in prompt)] * 7)
    out = io.StringIO()
    catalog = dict(CATALOG, save_place=CATALOG["command"])
    summary = await replay(tasks, Selector(catalog, llm, "m", scripts=["zeus", "tnt_rain"]), out, labels=labels,
                           scripts={"zeus", "tnt_rain"}, jobs=3)
    assert len(llm.calls) == 7  # "Change to T2: taller" needed no model call
    assert {k: summary[k] for k in ("scored", "agree", "lost", "invented", "caught", "calls", "multipart", "covered")} == \
        {"scored": 7, "agree": 6, "lost": 2, "invented": 0, "caught": 1, "calls": 7, "multipart": 2, "covered": 1}
    text = out.getvalue()
    assert "[LOST]" in text and "MISROUTE save_place, expected orchestrator" in text
    assert "T6  7 ms  $0.000010  make a frost bow  [handed on whole for run_script: run frost_bow.py for Alex]" \
        "\n    no picks" in text
    assert "T8  0 ms  $0.000000  Change to T2: taller\n    no picks" in text
    assert "give me speed  [DROPPED rockets?]  [LOST]" in text
    assert "route agreement 6/7 (85.7%), 2 lost, 1 either, 0 unlabelled; every part named in 1/2 multi-part task(s)" in text


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


def test_a_listing_names_each_item_by_its_slot_and_marks_the_one_held():
    from dispatcher.listing import readable
    inv = ('Steve has the following entity data: [{count: 1, Slot: 0b, id: "minecraft:stick"}, '
           '{count: 1, Slot: 4b, id: "minecraft:iron_sword"}, {components: {"minecraft:enchantments": '
           '{"minecraft:sharpness": 5}}, count: 1, Slot: 14b, id: "minecraft:diamond_sword"}, '
           '{components: {"minecraft:custom_name": "Excalibur", "minecraft:damage": 200}, count: 1, Slot: 21b, '
           'id: "minecraft:iron_pickaxe"}, {count: 64, Slot: 1b, id: "minecraft:cooked_beef"}]')
    worn = 'Steve has the following entity data: {feet: {count: 1, id: "minecraft:diamond_boots"}}'
    assert readable([inv, worn, "Steve has the following entity data: 4"]) == (
        "Steve's items, by slot: hotbar.0 stick; hotbar.4 iron_sword (held); inventory.5 diamond_sword "
        '{sharpness 5}; inventory.12 iron_pickaxe {named "Excalibur", damaged 200}; hotbar.1 64 cooked_beef; '
        "armor.feet diamond_boots (worn)")
    assert readable(["Steve has the following entity data: []", "Steve has the following entity data: 0"]) == (
        "Steve carries and wears nothing")
    assert readable(["Gave 1 [Diamond Sword] to Steve"]) is None
    assert readable(['Steve has the following entity data: {count: 1, id: "minecraft:stick"}']) is None
