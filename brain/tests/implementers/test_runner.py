"""The runner at its edge: start, follow_up, running, session and close, with a fake SDK client
and a fake vfg; the report with its DESCRIPTION line taken off, and what the task made, recorded by
the guard as the fake client puts each tool call through the hook. The brief's EXAMPLES block is
test_examples.py's; here it is one line."""
import asyncio
import json
import os

import pytest

from brain.implementers import examples, runner
from brain.implementers.guard import DENY
from brain.implementers.config import ImplementerConfig
from brain.implementers.runner import Implementers, follow_up_prompt, session_env, split_description

from .fakes import Clients, FakeVfg, bad, bash, haiku, init, ok, result, tool

BRIEF = "Give Steve an ice beam that freezes what it hits."
EMPTY = {"scripts": [], "actions": [], "presets": [], "copies": []}


@pytest.fixture
def cfg(tmp_path) -> ImplementerConfig:
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "vfg-home")})


@pytest.fixture(autouse=True)
def _examples(monkeypatch):
    async def build(cfg, job, task, run):
        return "EXAMPLES for %s." % task

    monkeypatch.setattr(examples, "build", build)


def system(options) -> str:
    """The system prompt the CLI reads, from the file it is given (--system-prompt-file)."""
    assert options.system_prompt["type"] == "file"
    with open(options.system_prompt["path"], encoding="utf-8") as f:
        return f.read()


class Run:
    """An Implementers with fakes, and every report it made."""

    def __init__(self, cfg, vfg=None):
        self.clients = Clients()
        self.vfg = vfg or (FakeVfg().on("prefix", answer=ok("THE KIT\n"))
                           .on("place", "list", answer=ok("castle 1 2 3\n")))
        self.reports: list[tuple[str, str, str]] = []
        self.described: list[tuple[str, str, dict]] = []  # (task, its description, what it made), by report
        self.running: list[list[str]] = []  # running() as each report arrives
        self.sessions: list[tuple[str, str, int]] = []  # (task, session, how many reports had come)

        async def on_report(task, status, text, description, made):
            self.reports.append((task, status, text))
            self.described.append((task, description, made))
            self.running.append(self.imps.running())

        def on_session(task, session):
            self.sessions.append((task, session, len(self.reports)))

        self.imps = Implementers(cfg, on_report, on_session=on_session, client_factory=self.clients, vfg=self.vfg)

    async def settle(self):
        """Until every run, and every usage row after it, is done."""
        while True:
            busy = [t.worker for t in self.imps._tasks.values() if t.worker] + list(self.imps._background)
            if not busy:
                return
            await asyncio.wait(busy)


async def test_a_start_returns_at_once_and_the_implementer_reports_when_it_ends(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-never")
    r = Run(cfg)
    r.clients.add(init("s1"), result("Done: Steve has an ice beam, ice_beam running as ice_beam_steve.",
                                     model_usage=haiku(1200, 300, 0.0123, read=1000, write=100)))
    await r.imps.start("ice_beam_steve", BRIEF)
    assert r.imps.running() == ["ice_beam_steve"] and r.reports == []
    await r.imps.start("pickaxe_alex", "A pickaxe for Alex.")
    await r.settle()
    assert r.reports == [("ice_beam_steve", "done", "Done: Steve has an ice beam, ice_beam running as ice_beam_steve."),
                         ("pickaxe_alex", "done", "Done: it is built.")]
    assert r.described == [("ice_beam_steve", "", EMPTY), ("pickaxe_alex", "", EMPTY)]
    assert r.imps.running() == [] and r.imps.session("ice_beam_steve") == "s1"
    c = r.clients.made[0]
    assert c.prompt == (BRIEF + "\n\nYOUR TASK: ice_beam_steve. Name what you make ice_beam_steve: vfg script new "
                        "ice_beam_steve, its action ice_beam_steve, and run it as=ice_beam_steve. Every vfg action run "
                        "you send takes --task ice_beam_steve, and a vfg place save --by ice_beam_steve.\n"
                        "SAVED PLACES: castle 1 2 3\n\nEXAMPLES for ice_beam_steve.")
    assert c.disconnected
    o = c.options
    assert (o.model, o.effort, o.max_turns, o.permission_mode, o.cwd, o.resume) == (
        "claude-haiku-5-5", "low", 40, "dontAsk", cfg.repo, None)
    assert o.tools == ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
    assert o.allowed_tools == runner.rules(cfg.repo, "main")
    assert not [x for x in o.allowed_tools if x.startswith(("Agent", "TaskStop", "mcp__"))]
    assert "Edit(/%s/made/main/**)" % cfg.repo in o.allowed_tools
    assert json.loads(o.settings) == {"promptCacheTtl": "5m"}
    assert o.hooks["PreToolUse"][0].hooks
    assert "ANTHROPIC_API_KEY" not in o.env and o.env["PYTHONPYCACHEPREFIX"] == cfg.brain_dir + "/pycache"
    # its rules with this server filled in, then vfg prefix: read once, the same bytes for every implementer
    # (in a file of the brain's folder: with the kit, far longer than one argument may be)
    assert system(o).startswith("You are an implementer") and "made/main/actions/" in system(o)
    assert "%SERVER%" not in system(o) and system(o).endswith("\n\nTHE KIT\n")
    assert o.system_prompt["path"].startswith(cfg.brain_dir + "/")
    assert r.clients.made[1].options.system_prompt == o.system_prompt
    assert len(r.vfg.made("prefix")) == 1
    # one usage row a run, after its report: "in" counts the cache's tokens too
    assert r.vfg.made("usage") == [
        ["usage", "add", "--component", "implementer", "--model", "claude-haiku-5-5", "--in", "2300", "--out", "300",
         "--cost", "0.012300", "--cache-read", "1000", "--cache-write", "100", "--ref", "ice_beam_steve"]]


async def test_how_a_run_ends_gives_its_status(cfg):
    cfg.time_limit_s = 0.01
    r = Run(cfg)
    cases = {
        "bow_steve": ((init(), result("**Failed** — Steve has no bow.")), {}, "failed",
                      "**Failed** — Steve has no bow."),
        "castle_steve": ((init(), result("Built the castle at 1 2 3.")), {}, "done", "Built the castle at 1 2 3."),
        "overloaded": ((init(), result("API Error: 529 overloaded", is_error=True)), {}, "failed",
                       "Failed: API Error: 529 overloaded"),
        "out_of_turns": ((init(), result(None, subtype="error_max_turns", is_error=True)), {}, "failed",
                         "Failed: the implementer used its 40 turns."),
        "boom": ((init(), result(None, subtype="error_during_execution", is_error=True, errors=["boom"])), {},
                 "failed", "Failed: boom"),
        "no_claude": ((), {"raises": RuntimeError("no claude")}, "failed",
                      "Failed: it crashed: RuntimeError: no claude."),
        "no_report": ((init(),), {}, "failed", "Failed: the implementer ended without a report."),
        "api_key": ((init(key="ANTHROPIC_API_KEY"), result()), {}, "failed",
                    "Failed: the CLI would use an API key (ANTHROPIC_API_KEY), not the subscription, so it was stopped."),
        "too_slow": ((init(), result()), {"gate": asyncio.Event()}, "failed",
                     "Failed: it ran over its time limit, 0.01 s, and was stopped."),
    }
    for name, (messages, kw, _, _) in cases.items():
        r.clients.add(*messages, **kw) if messages else r.clients.add(init(), **kw)
        await r.imps.start(name, BRIEF)
    await r.settle()
    assert sorted(r.reports) == sorted((name, status, text) for name, (_, _, status, text) in cases.items())
    assert r.vfg.made("usage") == []  # no model_usage in these results
    assert all(c.disconnected for c in r.clients.made)


async def test_a_follow_up_resumes_the_ended_session_and_writes_only_its_own_usage(cfg):
    r = Run(cfg)
    r.clients.add(init("s1"), result(model_usage=haiku(1000, 200, 0.01)))
    r.clients.add(init("s1"), result("Done: bigger now.", model_usage=haiku(1500, 260, 0.015)))
    await r.imps.start("castle_steve", BRIEF)
    await r.settle()
    await r.imps.follow_up("castle_steve", "make it bigger")
    await r.settle()
    first, second = r.clients.made
    assert second.options.resume == "s1" and second.prompt == follow_up_prompt("castle_steve", "make it bigger")
    assert second.prompt == ("FOLLOW-UP to castle_steve, from the brain: make it bigger\nDo it as before, check it "
                             "once, and end with a new report, its last line DESCRIPTION: what castle_steve is now.")
    assert second.options.system_prompt == first.options.system_prompt
    assert r.reports[-1] == ("castle_steve", "done", "Done: bigger now.")
    assert [row[row.index("--in") + 1:row.index("--cost") + 2] for row in r.vfg.made("usage")] == [
        ["1000", "--out", "200", "--cost", "0.010000"], ["500", "--out", "60", "--cost", "0.005000"]]
    assert len(r.vfg.made("place", "list")) == 1  # a follow-up's brief is its words alone


async def test_a_follow_up_waits_behind_the_running_implementer(cfg):
    r = Run(cfg)
    gate = asyncio.Event()
    r.clients.add(init("s1"), result("Done: first."), gate=gate).add(init("s1"), result("Done: second."))
    await r.imps.start("railgun_alex", BRIEF)
    await r.imps.follow_up("railgun_alex", "for Alex too")
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(r.clients.made) == 1 and r.imps.running() == ["railgun_alex"]
    gate.set()
    await r.settle()
    assert [c.options.resume for c in r.clients.made] == [None, "s1"]
    assert r.reports == [("railgun_alex", "done", "Done: first."), ("railgun_alex", "done", "Done: second.")]
    assert r.running == [["railgun_alex"], []]  # as the first reports, its follow-up waits: the brain keeps it open


async def test_a_follow_up_with_no_session_here_resumes_the_one_given_else_starts_anew(cfg):
    r = Run(cfg)
    r.clients.add(init("s9"), result()).add(raises=RuntimeError("no claude")).add(init("s3"), result())
    await r.imps.follow_up("zoo_steve", "make it bigger", session="s9")  # a task the brain started before a restart
    await r.imps.start("ice_beam_steve", BRIEF)  # crashes before its session is known
    await r.settle()
    await r.imps.follow_up("ice_beam_steve", "try again")
    await r.settle()
    resumed, crashed, anew = r.clients.made
    assert resumed.options.resume == "s9" and resumed.prompt == follow_up_prompt("zoo_steve", "make it bigger")
    assert anew.options.resume is None and anew.prompt.startswith(
        BRIEF + "\n\nFOLLOW-UP: try again\n\nYOUR TASK: ice_beam_steve.")


async def test_a_follow_up_with_no_session_anywhere_starts_on_its_brief_and_the_session_is_kept_at_once(cfg):
    r = Run(cfg)
    r.clients.add(init("s5"), result("Done: wider.", session="s5"))
    # a task from before a restart whose session never reached its row: what it is comes from there
    await r.imps.follow_up("wall_alex", "make it wider", brief=BRIEF)
    await r.settle()
    [c] = r.clients.made
    assert c.options.resume is None and c.prompt.startswith(
        BRIEF + "\n\nFOLLOW-UP: make it wider\n\nYOUR TASK: wall_alex.")
    assert r.sessions == [("wall_alex", "s5", 0)]  # at the CLI's init, before any report


async def test_close_interrupts_what_runs_drops_what_waits_and_reports_nothing(cfg):
    r = Run(cfg)
    r.clients.add(init(), result(), gate=asyncio.Event())  # castle_steve ends when interrupted
    r.clients.add(init(), result(), gate=asyncio.Event(), deaf=True)  # zoo_steve does not, and is cancelled
    await r.imps.start("castle_steve", BRIEF)
    await r.imps.start("zoo_steve", BRIEF)
    await r.imps.follow_up("castle_steve", "and then this")
    for _ in range(5):
        await asyncio.sleep(0)
    assert await r.imps.close(0.01) == ["castle_steve", "zoo_steve"]
    assert [(c.interrupted, c.disconnected) for c in r.clients.made] == [(True, True), (True, True)]
    assert r.reports == [] and r.imps.running() == []
    await r.imps.start("wall_alex", BRIEF)  # closed: nothing starts
    assert len(r.clients.made) == 2 and r.imps.running() == []


async def test_without_vfg_prefix_the_rules_alone_and_it_is_asked_again(cfg):
    r = Run(cfg, FakeVfg().on("prefix", answer=bad("vfg: no such command")))
    await r.imps.start("castle_steve", BRIEF)
    await r.settle()
    await r.imps.start("zoo_steve", BRIEF)
    await r.settle()
    assert system(r.clients.made[0].options).endswith(
        "check passed), running as frost_staff_steve until stopped; he right-clicks with a stick to fire it.\n"
        "DESCRIPTION: A frost staff for Steve: right-click with a stick to fire a bolt that freezes what it hits; "
        "its cooldown and freeze_seconds can be changed.\n")
    # static: the same bytes for every task, with no task's name filled in
    assert system(r.clients.made[1].options) == system(r.clients.made[0].options)
    assert "zoo_steve" not in system(r.clients.made[1].options)
    assert len(r.vfg.made("prefix")) == 2
    assert r.clients.made[0].prompt.split("\n")[3] == "SAVED PLACES: none"


def test_the_clis_environment_has_no_api_key_and_nothing_of_a_parent_session():
    env = session_env({"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u", "CLAUDECODE": "1",
                       "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_OAUTH_TOKEN": "t", "NODE_OPTIONS": "x",
                       "OPEN_ROUTER_API": "or"})
    assert env == {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t", "OPEN_ROUTER_API": "or"}


# ---------------------------------------------------------------- the report, its description and what it made

async def test_the_report_goes_with_its_description_line_taken_off(cfg):
    r = Run(cfg)
    cases = {
        # its last line in bold, as the model likes it
        "torch_ring_steve": ("Done: a ring of eight torches round Steve, torch_ring_steve running.\n\n"
                             "**DESCRIPTION:** A ring of eight torches round Steve, three blocks out.",
                             "done", "Done: a ring of eight torches round Steve, torch_ring_steve running.",
                             "A ring of eight torches round Steve, three blocks out."),
        # none: the brain keeps the orchestrator's
        "castle_steve": ("Done: the castle is at 1 2 3.", "done", "Done: the castle is at 1 2 3.", ""),
        # wrapped over two lines, with an earlier description line that is not the last
        "frost_staff_steve": ("Done: Steve has a frost staff.\nDescription: an old one.\n"
                              "- _Description_: A frost staff for Steve: right-click\n  to fire,   cooldown 5 s.",
                              "done", "Done: Steve has a frost staff.\nDescription: an old one.",
                              "A frost staff for Steve: right-click to fire, cooldown 5 s."),
        # nothing but its description: the status from the whole text
        "wall_alex": ("DESCRIPTION: A stone wall round Alex's house.", "done",
                      "Done: the implementer ended without a report.", "A stone wall round Alex's house."),
        "bow_steve": ("Failed: Steve has no bow.\nDESCRIPTION: Nothing yet: Steve needs a bow first.", "failed",
                      "Failed: Steve has no bow.", "Nothing yet: Steve needs a bow first."),
    }
    for name, (text, _, _, _) in cases.items():
        r.clients.add(init(), result(text))
        await r.imps.start(name, BRIEF)
    await r.settle()
    assert sorted(r.reports) == sorted((name, status, report) for name, (_, status, report, _) in cases.items())
    assert sorted(r.described) == sorted((name, d, EMPTY) for name, (_, _, _, d) in cases.items())


def test_split_description():
    assert split_description("Done: built.\nDESCRIPTION: A hut.") == ("Done: built.", "A hut.")
    assert split_description("Done: built.") == ("Done: built.", "")
    assert split_description("Done.\n\n> **Description**: `A hut` for Alex, *oak*  \n") == ("Done.", "A hut` for Alex, *oak")
    assert split_description("Done.\n### DESCRIPTION:\n\nA hut,\nby the lake.") == ("Done.", "A hut, by the lake.")
    assert split_description("Done.\nDESCRIPTION:") == ("Done.", "")
    assert split_description("Done.\nThe description is fine: yes.") == ("Done.\nThe description is fine: yes.", "")
    report, description = split_description("Done.\nDESCRIPTION: " + "a" * 700)
    assert report == "Done." and description == "a" * 600


def made_here(cfg, kind: str, name: str) -> None:
    """A file a tool call made in made/main/, as vfg would."""
    path = {"scripts": "made/main/scripts/%s/main.py", "actions": "made/main/actions/%s.py",
            "presets": "made/main/presets/%s.py"}[kind] % name
    os.makedirs(os.path.dirname(os.path.join(cfg.repo, path)), exist_ok=True)
    with open(os.path.join(cfg.repo, path), "w") as f:
        f.write('"""x"""\n')


def effects(cfg):
    """What the allowed calls do on disk: vfg script new writes the script and its action, a Write its file;
    vfg preset save fails and writes nothing."""

    def effect(name, inp):
        if name == "Bash" and inp["command"].startswith("vfg script new "):
            made_here(cfg, "scripts", inp["command"].split()[3])
            made_here(cfg, "actions", inp["command"].split()[3])
        elif name == "Write":
            os.makedirs(os.path.dirname(os.path.join(cfg.repo, inp["file_path"])), exist_ok=True)
            with open(os.path.join(cfg.repo, inp["file_path"]), "w") as f:
                f.write(inp.get("content") or "")

    return effect


async def test_what_the_task_made_is_recorded_by_the_guard_checked_on_disk_and_kept_over_its_runs(cfg):
    name = "galick_gun_styrk"
    os.makedirs(cfg.home)
    with open(os.path.join(cfg.home, "running.json"), "w") as f:  # the brain's own copy of the task's action
        json.dump({"copies": {"galick_gun_styrk_steve": {"action": name, "script": name, "task": None}}}, f)
    r = Run(cfg)
    r.clients.add(
        init("s1"),
        bash("vfg script new galick_gun_styrk --from galick_gun --preset crater"),
        bash("curl https://example.com"),  # denied: records nothing
        tool("Write", file_path="made/main/presets/beam_clash.py", content='"""Two beams meet."""\n'),
        bash("vfg preset save galick_gun_styrk charge charge_up"),  # it failed: not on disk, dropped
        bash("vfg action check galick_gun_styrk && vfg action run galick_gun_styrk player=Styrkaeopteryx "
             "as=galick_gun_styrk --task galick_gun_styrk"),
        bash("vfg action run stop copy=old_beam --task galick_gun_styrk"),  # changes a copy, starts none
        result("Done: Styrkaeopteryx has a Galick Gun.\nDESCRIPTION: A Galick Gun for Styrkaeopteryx: hold right-click."),
        effect=effects(cfg))
    r.clients.add(
        init("s1"),
        # the task's own script, while the brain's own copy of its action runs: its own to change
        tool("Edit", file_path=os.path.join(cfg.repo, "made/main/scripts/galick_gun_styrk/main.py"),
             old_string="x", new_string="y"),
        tool("Edit", file_path="made/main/actions/galick_gun_styrk.py", old_string="x", new_string="y"),
        bash("vfg script stop galick_gun_styrk; vfg action run galick_gun_styrk --task=galick_gun_styrk "
             "as=galick_gun_styrk_2"),
        result("Done: it pierces blocks now."),
        effect=effects(cfg))
    await r.imps.start(name, BRIEF)
    await r.settle()
    await r.imps.follow_up(name, "make it pierce blocks")
    await r.settle()
    first, second = r.clients.made
    assert [v for _, _, v in first.verdicts] == [None, DENY.shape, None, None, None, None]
    assert [v for _, _, v in second.verdicts] == [None, None, None]
    assert r.reports == [(name, "done", "Done: Styrkaeopteryx has a Galick Gun."),
                         (name, "done", "Done: it pierces blocks now.")]
    made = {"scripts": [name], "actions": [name], "presets": ["beam_clash"], "copies": [name]}
    assert r.described == [(name, "A Galick Gun for Styrkaeopteryx: hold right-click.", made),
                           (name, "", {**made, "copies": [name, name + "_2"]})]
    assert r.imps._tasks[name].guard.own >= {name}


async def test_a_crash_reports_what_was_made_so_far(cfg, monkeypatch):
    r = Run(cfg)
    r.clients.add(init("s1"), bash("vfg script new ice_beam_steve --from ice_beam"),
                  bash("vfg action run ice_beam_steve as=ice_beam_steve --task ice_beam_steve"),
                  breaks=RuntimeError("the CLI went away"), effect=effects(cfg))
    await r.imps.start("ice_beam_steve", BRIEF)
    await r.settle()
    made = {"scripts": ["ice_beam_steve"], "actions": ["ice_beam_steve"], "presets": [], "copies": ["ice_beam_steve"]}
    assert r.reports == [("ice_beam_steve", "failed", "Failed: it crashed: RuntimeError: the CLI went away.")]
    assert r.described == [("ice_beam_steve", "", made)]
    # a run that cannot even start reports what the task's earlier runs made
    monkeypatch.setattr(runner, "build_options", lambda *a, **k: 1 / 0)
    await r.imps.follow_up("ice_beam_steve", "colder")
    await r.settle()
    assert r.reports[-1] == ("ice_beam_steve", "failed",
                             "Failed: the implementer could not run: ZeroDivisionError: division by zero.")
    assert r.described[-1] == ("ice_beam_steve", "", made)


async def test_after_a_restart_a_follow_up_still_changes_its_own_script_while_copies_of_it_run(cfg):
    made_here(cfg, "scripts", "galick_gun_styrk")
    made_here(cfg, "scripts", "beam_alex")
    os.makedirs(cfg.home)
    with open(os.path.join(cfg.home, "running.json"), "w") as f:
        json.dump({"copies": {"galick_gun_styrk_steve": {"action": "galick_gun_styrk", "script": "galick_gun_styrk",
                                                         "task": None},
                              "beam_alex": {"action": "beam_alex", "script": "beam_alex", "task": "beam_alex"}}}, f)
    r = Run(cfg)  # a new process: nothing recorded, the session from the task's row
    r.clients.add(init("s1"),
                  tool("Edit", file_path="made/main/scripts/galick_gun_styrk/main.py", old_string="x", new_string="y"),
                  tool("Edit", file_path="made/main/scripts/beam_alex/main.py", old_string="x", new_string="y"),
                  result("Done: it pierces blocks now."))
    await r.imps.follow_up("galick_gun_styrk", "make it pierce blocks", session="s1")
    await r.settle()
    [c] = r.clients.made
    assert [v for _, _, v in c.verdicts] == [None, DENY.running("beam_alex", {"copy": "beam_alex", "task": "beam_alex"},
                                                                "main")]
    assert r.imps._tasks["galick_gun_styrk"].guard.own == {"galick_gun_styrk"}
