"""The runner at its edge: start, follow_up, running, session and close, with a fake SDK client
and a fake vfg. The brief's EXAMPLES block is test_examples.py's; here it is one line."""
import asyncio
import json

import pytest

from brain.implementers import examples, runner
from brain.implementers.config import ImplementerConfig
from brain.implementers.runner import Implementers, follow_up_prompt, session_env

from .fakes import Clients, FakeVfg, bad, haiku, init, ok, result

BRIEF = "Give Steve an ice beam that freezes what it hits."


@pytest.fixture
def cfg(tmp_path) -> ImplementerConfig:
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "vfg-home")})


@pytest.fixture(autouse=True)
def _examples(monkeypatch):
    async def build(cfg, job, task, run):
        return "EXAMPLES for %s." % task

    monkeypatch.setattr(examples, "build", build)


class Run:
    """An Implementers with fakes, and every report it made."""

    def __init__(self, cfg, vfg=None):
        self.clients = Clients()
        self.vfg = vfg or (FakeVfg().on("prefix", answer=ok("THE KIT\n"))
                           .on("place", "list", answer=ok("castle 1 2 3\n")))
        self.reports: list[tuple[str, str, str]] = []

        async def on_report(task, status, text):
            self.reports.append((task, status, text))

        self.imps = Implementers(cfg, on_report, client_factory=self.clients, vfg=self.vfg)

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
    await r.imps.start("T7", BRIEF)
    assert r.imps.running() == ["T7"] and r.reports == []
    await r.imps.start("T8", "A pickaxe for Alex.")
    await r.settle()
    assert r.reports == [("T7", "done", "Done: Steve has an ice beam, ice_beam running as ice_beam_steve."),
                         ("T8", "done", "Done: it is built.")]
    assert r.imps.running() == [] and r.imps.session("T7") == "s1"
    c = r.clients.made[0]
    assert c.prompt == (BRIEF + "\n\nYOUR TASK: T7. Every vfg action run you send takes --task T7, and a vfg place "
                        "save --by T7.\nSAVED PLACES: castle 1 2 3\n\nEXAMPLES for T7.")
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
    assert o.system_prompt.startswith("You are an implementer") and "made/main/actions/" in o.system_prompt
    assert "%SERVER%" not in o.system_prompt and o.system_prompt.endswith("\n\nTHE KIT\n")
    assert r.clients.made[1].options.system_prompt == o.system_prompt
    assert len(r.vfg.made("prefix")) == 1
    # one usage row a run, after its report: "in" counts the cache's tokens too
    assert r.vfg.made("usage") == [
        ["usage", "add", "--component", "implementer", "--model", "claude-haiku-5-5", "--in", "2300", "--out", "300",
         "--cost", "0.012300", "--cache-read", "1000", "--cache-write", "100", "--ref", "T7"]]


async def test_how_a_run_ends_gives_its_status(cfg):
    cfg.time_limit_s = 0.01
    r = Run(cfg)
    cases = {
        "T1": ((init(), result("**Failed** — Steve has no bow.")), {}, "failed", "**Failed** — Steve has no bow."),
        "T2": ((init(), result("Built the castle at 1 2 3.")), {}, "done", "Built the castle at 1 2 3."),
        "T3": ((init(), result("API Error: 529 overloaded", is_error=True)), {}, "failed",
               "Failed: API Error: 529 overloaded"),
        "T4": ((init(), result(None, subtype="error_max_turns", is_error=True)), {}, "failed",
               "Failed: the implementer used its 40 turns."),
        "T5": ((init(), result(None, subtype="error_during_execution", is_error=True, errors=["boom"])), {}, "failed",
               "Failed: boom"),
        "T6": ((), {"raises": RuntimeError("no claude")}, "failed", "Failed: it crashed: RuntimeError: no claude."),
        "T7": ((init(),), {}, "failed", "Failed: the implementer ended without a report."),
        "T8": ((init(key="ANTHROPIC_API_KEY"), result()), {}, "failed",
               "Failed: the CLI would use an API key (ANTHROPIC_API_KEY), not the subscription, so it was stopped."),
        "T9": ((init(), result()), {"gate": asyncio.Event()}, "failed",
               "Failed: it ran over its time limit, 0.01 s, and was stopped."),
    }
    for tid, (messages, kw, _, _) in cases.items():
        r.clients.add(*messages, **kw) if messages else r.clients.add(init(), **kw)
        await r.imps.start(tid, BRIEF)
    await r.settle()
    assert sorted(r.reports) == [(tid, status, text) for tid, (_, _, status, text) in cases.items()]
    assert r.vfg.made("usage") == []  # no model_usage in these results
    assert all(c.disconnected for c in r.clients.made)


async def test_a_follow_up_resumes_the_ended_session_and_writes_only_its_own_usage(cfg):
    r = Run(cfg)
    r.clients.add(init("s1"), result(model_usage=haiku(1000, 200, 0.01)))
    r.clients.add(init("s1"), result("Done: bigger now.", model_usage=haiku(1500, 260, 0.015)))
    await r.imps.start("T7", BRIEF)
    await r.settle()
    await r.imps.follow_up("T7", "make it bigger")
    await r.settle()
    first, second = r.clients.made
    assert second.options.resume == "s1" and second.prompt == follow_up_prompt("T7", "make it bigger")
    assert second.options.system_prompt == first.options.system_prompt
    assert r.reports[-1] == ("T7", "done", "Done: bigger now.")
    assert [row[row.index("--in") + 1:row.index("--cost") + 2] for row in r.vfg.made("usage")] == [
        ["1000", "--out", "200", "--cost", "0.010000"], ["500", "--out", "60", "--cost", "0.005000"]]
    assert len(r.vfg.made("place", "list")) == 1  # a follow-up's brief is its words alone


async def test_a_follow_up_waits_behind_the_running_implementer(cfg):
    r = Run(cfg)
    gate = asyncio.Event()
    r.clients.add(init("s1"), result("Done: first."), gate=gate).add(init("s1"), result("Done: second."))
    await r.imps.start("T7", BRIEF)
    await r.imps.follow_up("T7", "for Alex too")
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(r.clients.made) == 1 and r.imps.running() == ["T7"]
    gate.set()
    await r.settle()
    assert [c.options.resume for c in r.clients.made] == [None, "s1"]
    assert r.reports == [("T7", "done", "Done: first."), ("T7", "done", "Done: second.")]


async def test_a_follow_up_with_no_session_here_resumes_the_one_given_else_starts_anew(cfg):
    r = Run(cfg)
    r.clients.add(init("s9"), result()).add(raises=RuntimeError("no claude")).add(init("s3"), result())
    await r.imps.follow_up("T9", "make it bigger", session="s9")  # a task the brain started before a restart
    await r.imps.start("T3", BRIEF)  # crashes before its session is known
    await r.settle()
    await r.imps.follow_up("T3", "try again")
    await r.settle()
    resumed, crashed, anew = r.clients.made
    assert resumed.options.resume == "s9" and resumed.prompt == follow_up_prompt("T9", "make it bigger")
    assert anew.options.resume is None and anew.prompt.startswith(BRIEF + "\n\nFOLLOW-UP: try again\n\nYOUR TASK: T3.")


async def test_close_interrupts_what_runs_drops_what_waits_and_reports_nothing(cfg):
    r = Run(cfg)
    r.clients.add(init(), result(), gate=asyncio.Event())  # T7 ends when interrupted
    r.clients.add(init(), result(), gate=asyncio.Event(), deaf=True)  # T8 does not, and is cancelled
    await r.imps.start("T7", BRIEF)
    await r.imps.start("T8", BRIEF)
    await r.imps.follow_up("T7", "and then this")
    for _ in range(5):
        await asyncio.sleep(0)
    assert await r.imps.close(0.01) == ["T7", "T8"]
    assert [(c.interrupted, c.disconnected) for c in r.clients.made] == [(True, True), (True, True)]
    assert r.reports == [] and r.imps.running() == []
    await r.imps.start("T9", BRIEF)  # closed: nothing starts
    assert len(r.clients.made) == 2 and r.imps.running() == []


async def test_without_vfg_prefix_the_rules_alone_and_it_is_asked_again(cfg):
    r = Run(cfg, FakeVfg().on("prefix", answer=bad("vfg: no such command")))
    await r.imps.start("T1", BRIEF)
    await r.settle()
    await r.imps.start("T2", BRIEF)
    await r.settle()
    assert r.clients.made[0].options.system_prompt.endswith("check passed), running as frost_staff until stopped; "
                                                            "he right-clicks with a stick to fire it.\n")
    assert len(r.vfg.made("prefix")) == 2
    assert r.clients.made[0].prompt.split("\n")[3] == "SAVED PLACES: none"


def test_the_clis_environment_has_no_api_key_and_nothing_of_a_parent_session():
    env = session_env({"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u", "CLAUDECODE": "1",
                       "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_OAUTH_TOKEN": "t", "NODE_OPTIONS": "x",
                       "OPEN_ROUTER_API": "or"})
    assert env == {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t", "OPEN_ROUTER_API": "or"}
