"""Fakes for every test: no model, no network, no real vfg, no real ~/.vfg.

- REPO_COPY: the repo's actions/ (the real _types.py, the six core actions, railgun and
  gatling_gun, its presets) beside a stub main.py for every script, so the catalog loads them
  whether or not their scripts are written yet. catalog() loads it, with a made/<server>/
  folder of its own for each call, so a test may add made actions.
- FakeLLM stands in for structured(): scripted answers per output class name
  ("Selection", "Planned_command", "Final_teleport", …), validated against the class as
  structured() does, with one repair from the next answer when the first does not validate.
- FakeVfg stands in for the vfg runner: it records every argv and answers by prefix;
  `vfg action run` answers with its one JSON line (Contracts §8).
- FakeRefs stands in for refs.Refs.
"""

from __future__ import annotations

import asyncio
import atexit
import itertools
import json
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from vfg_llm import Answer, LlmError

from dispatcher.actions import Catalog
from dispatcher.batch import Batch
from dispatcher.config import REPO, Config
from dispatcher.inbox import State, new_task
from dispatcher.reply import Reply
from dispatcher.task import Dispatcher
from dispatcher.vfg import VfgResult

CORE = ("command", "teleport", "summon_around", "save_place", "stop", "edit")


def _repo_copy() -> Path:
    root = Path(tempfile.mkdtemp(prefix="vfg-dispatcher-repo-"))
    atexit.register(shutil.rmtree, root, True)
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for p in (REPO / "actions").glob("*.py"):
        if not p.name.startswith("_"):
            shutil.copy(p, root / "actions")
    (root / "scripts").mkdir()
    (root / "scripts" / "_presets").symlink_to(REPO / "scripts" / "_presets")
    for name in {*CORE, *(p.name for p in (REPO / "scripts").iterdir() if not p.name.startswith((".", "_")))}:
        (root / "scripts" / name).mkdir(exist_ok=True)
        (root / "scripts" / name / "main.py").write_text('"""a stub"""\n')
    return root


REPO_COPY = _repo_copy()
_servers = itertools.count(1)


def catalog() -> Catalog:
    """The library's actions, and a made/<server>/ of its own (empty until a test writes one)."""
    return Catalog(REPO_COPY, "test%d" % next(_servers))


def made(cat: Catalog, name: str, source: str) -> Path:
    """Write made/<server>/actions/<name>.py for this catalog's server (and its script's stub)."""
    path = REPO_COPY / "made" / cat.server / "actions" / (name + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip("\n"))
    script = REPO_COPY / "made" / cat.server / "scripts" / name / "main.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('"""a stub"""\n')
    return path


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path, monkeypatch):
    """Nothing in a test may reach the real ~/.vfg."""
    monkeypatch.setenv("VFG_HOME", str(tmp_path / "vfg-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("VFG_DISPATCH_DIR", "VFG_ORCH_DIR", "VFG_REPO", "OPEN_ROUTER_API", "VFG_OPENROUTER_KEY",
                "VFG_DISPATCH_MODEL", "VFG_DISPATCH_RETRIES", "VFG_DISPATCH_WINDOW_S",
                "VFG_DISPATCH_ACTION_TIMEOUT_S", "VFG_SERVER", "VFG_RUNNING_TTL_S"):
        monkeypatch.delenv(key, raising=False)


BLOCKS = {"setblock", "fill", "clone", "place", "fillbiome"}
OWNER = {"stop", "op", "deop", "ban", "whitelist", "kick"}


# ------------------------------------------------------------------ the model

@dataclass
class Call:
    output: str
    prompt: str
    system: str | None


class FakeLLM:
    """`script[name]` is a list of answers, used in order: a dict (validated), an Exception
    (raised), or an async/sync callable taking the prompt and returning either."""

    def __init__(self, **script: list[Any]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[Call] = []
        self.outputs: list[type] = []

    def add(self, name: str, *answers: Any) -> FakeLLM:
        self.script.setdefault(name, []).extend(answers)
        return self

    async def _next(self, name: str, prompt: str):
        queue = self.script.get(name)
        if not queue:
            raise AssertionError(f"no scripted answer left for {name}")
        answer = queue.pop(0)
        if callable(answer):
            answer = answer(prompt)
            if asyncio.iscoroutine(answer):
                answer = await answer
        if isinstance(answer, Exception):
            raise answer
        return answer

    def prompts(self, name: str | None = None) -> list[str]:
        return [c.prompt for c in self.calls if name is None or c.output == name]

    async def __call__(self, prompt, output, *, system=None, model=None, **_):
        await asyncio.sleep(0)  # a real call yields to the loop
        name = output.__name__
        self.calls.append(Call(name, prompt, system))
        self.outputs.append(output)
        answer = await self._next(name, prompt)
        try:
            value = output.model_validate(answer)
        except ValidationError as first:  # structured() asks once more, with the errors
            self.calls.append(Call(name, f"REPAIR: {first}", system))
            try:
                value = output.model_validate(await self._next(name, prompt))
            except (ValidationError, AssertionError):
                raise LlmError(f"{model} twice failed to match {name}") from None
        return Answer[output](value=value, cost=0.00001, ms=7)


# ------------------------------------------------------------------ vfg

def line(ok: bool, summary: str, *, copy: str = "", action: str = "", running: bool = False, notes=(),
         error: str | None = None, **facts) -> str:
    """vfg action run's one JSON line."""
    out = {"ok": ok, "copy": copy, "action": action, "running": running, "summary": summary, "facts": facts,
           "notes": list(notes)}
    if error is not None:
        out["error"] = error
    return json.dumps(out) + "\n"


def ok(summary: str = "done", **kw) -> VfgResult:
    return VfgResult(0, line(True, summary, **kw), "")


def bad(summary: str, code: int = 1, **kw) -> VfgResult:
    return VfgResult(code, line(False, summary, **kw), "")


def fail(code: int = 1, stderr: str = "ssh: connect to host failed") -> VfgResult:
    return VfgResult(code, "", stderr + "\n")


class FakeVfg:
    """Records every argv; answers by the longest matching prefix rule, else a default.
    A rule's answer is a VfgResult, a list of them (used in order, the last repeated), or a
    callable taking the argv (sync or async) and returning one."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []  # each call's, in the order of calls
        self.rules: list[tuple[tuple[str, ...], Any]] = []
        self.events: list[tuple[str, list[str]]] = []  # ("start"|"end", argv)

    def on(self, *prefix: str, answer: Any) -> FakeVfg:
        self.rules.append((prefix, answer))
        return self

    async def __call__(self, *args: str, timeout: float = 30) -> VfgResult:
        argv = list(args)
        self.calls.append(argv)
        self.timeouts.append(timeout)
        self.events.append(("start", argv))
        await asyncio.sleep(0)
        try:
            return await self._answer(argv)
        finally:
            self.events.append(("end", argv))

    async def _answer(self, argv: list[str]) -> VfgResult:
        for prefix, answer in sorted(self.rules, key=lambda r: -len(r[0])):
            if tuple(argv[: len(prefix)]) == prefix:
                if isinstance(answer, list):
                    return answer.pop(0) if len(answer) > 1 else answer[0]
                if callable(answer):
                    got = answer(argv)
                    return await got if asyncio.iscoroutine(got) else got
                return answer
        if argv[:2] == ["place", "list"]:
            return VfgResult(0, "none\n", "")
        if argv[:2] == ["orch", "hand"]:
            ids = [x["id"] for x in json.loads(argv[2])]
            return VfgResult(0, "".join(f"handed {i}\n" for i in ids), "")
        if argv[:2] == ["orch", "reply"]:
            return VfgResult(0, "17\n", "")
        if argv[:2] == ["action", "run"]:
            return ok(copy=argv[2], action=argv[2])
        return VfgResult(0, "", "")

    def replies(self, task: str | None = None) -> list[tuple[str, str, str]]:
        """(id, status, text) of every `vfg orch reply` call, attempts included."""
        return [(a[2], a[3], a[4]) for a in self.calls if a[:2] == ["orch", "reply"] and (task is None or a[2] == task)]

    def hands(self) -> list[list[dict]]:
        return [json.loads(a[2]) for a in self.calls if a[:2] == ["orch", "hand"]]

    def actions(self) -> list[list[str]]:
        return [a for a in self.calls if a[:2] == ["action", "run"]]


def args_of(argv: list[str]) -> dict[str, Any]:
    """`vfg action run <name> k=v … --task T1`'s values: a list or a dict read back from JSON."""
    out: dict[str, Any] = {}
    for a in argv[3:argv.index("--task")]:
        key, _, value = a.partition("=")
        out[key] = json.loads(value) if value[:1] in ("[", "{") else value
    return out


# ------------------------------------------------------------------ refs

SUBS = {"as", "at", "positioned", "if", "unless", "in", "anchored", "facing", "rotated", "align", "store", "on",
        "summon", "run"}


class FakeRefs:
    version = "26.3"
    MENU = {
        "give": "Gives an item to a player.",
        "effect": "Adds or removes status effects.",
        "execute": "Executes another command.",
        "item": "Replaces items in inventories.",
        "kill": "Kills entities.",
        "time": "Changes or queries the world's game time.",
        "weather": "Sets the weather.",
        "data": "Gets, merges, modifies, and removes NBT data.",
        "setblock": "Changes a block to another block.",
        "op": "Grants operator status to a player.",
    }

    def __init__(self):
        self.referenced: list[tuple[set[str], set[str]]] = []

    def names(self) -> set[str]:
        return set(self.MENU)

    def menu(self, exclude=()) -> str:
        return "\n".join(f"{n} — {d}" for n, d in self.MENU.items() if n not in set(exclude))

    def used(self, commands: list[str]) -> tuple[set[str], set[str]]:
        names, subs = set(), set()
        for c in commands:
            words = c.split()
            while words:
                names.add(words[0])
                if words[0] != "execute":
                    break
                rest = words[1:]
                head = rest[: rest.index("run") + 1] if "run" in rest else rest
                subs |= {w for w in head if w in SUBS}
                if "run" not in rest:
                    break
                words = rest[rest.index("run") + 1:]
        return names, subs

    def reference(self, names: set[str], execute_subs: set[str] = frozenset()) -> str:
        self.referenced.append((set(names), set(execute_subs)))
        out = []
        for n in sorted(names):
            out.append(f"## {n}\n{n} <targets> ...  (usage of {n})")
            if n == "execute":
                out.append("execute subcommands: " + " ".join(sorted(execute_subs)))
        return "\n".join(out)


# ------------------------------------------------------------------ the harness

class Harness:
    def __init__(self, tmp: Path, *, cat: Catalog | None = None, refs="fake", retries: int = 1,
                 window_s: float = 0.05):
        self.tmp = tmp
        self.catalog = catalog() if cat is None else cat
        self.cfg = Config(home=tmp / "vfg-home", dispatch_dir=tmp / "dispatch", orch_dir=tmp / "orch",
                          repo=REPO_COPY, server=self.catalog.server, retries=retries, window_s=window_s,
                          action_timeout_s=20, stop_grace_s=0.5)
        self.cfg.dispatch_dir.mkdir(parents=True, exist_ok=True)
        self.llm = FakeLLM()
        self.vfg = FakeVfg()
        self.refs = FakeRefs() if refs == "fake" else refs
        self.state = State(self.cfg.state_file)
        self.reply = Reply(self.state, self.vfg, retry_s=(0.01,))
        self.batch = Batch(self.state, self.vfg, window_s, retry_s=(0.01,))
        self.d = Dispatcher(self.cfg, catalog=self.catalog, refs=self.refs, vfg=self.vfg, ask=self.llm,
                            state=self.state, reply=self.reply, batch=self.batch, hidden=frozenset(BLOCKS | OWNER))
        self.batch.on_refused = self.d.refused
        self.n = 0

    def running(self, **copies: dict) -> None:
        """Write running.json as vfg keeps it: {copy: {"action", "values", "task", …}}."""
        self.cfg.home.mkdir(parents=True, exist_ok=True)
        full = {name: {"script": info.get("action"), "live": [], "started": 1760000000, "runs": "until_stopped",
                       **info} for name, info in copies.items()}
        self.cfg.running_file.write_text(json.dumps({"fetched_at": 1760000000, "copies": full}))

    def submit(self, task: str, context: str = "") -> str:
        self.n += 1
        tid = f"T{self.n}"
        self.state.tasks[tid] = new_task({"id": tid, "at": "2026-10-09T12:00:00.000Z", "task": task,
                                          "context": context})
        return tid

    async def run(self, task: str, context: str = "") -> str:
        tid = self.submit(task, context)
        await self.d.run(tid)
        await self.settle()
        return tid

    async def settle(self) -> None:
        """Let the replies and the batch finish."""
        assert await self.reply.drain(2), "replies left unsent"
        if self.state.batch:
            assert await self.batch.hand(), "batch left unhanded"
        for _ in range(5):
            await asyncio.sleep(0)


@pytest.fixture
def h(tmp_path) -> Harness:
    return Harness(tmp_path)


def select(*picks: tuple[str, str], orchestrator: str = "") -> dict:
    """The selector's answer, as parts: each pick an action part, then the orchestrator's;
    with neither, the whole task is one orchestrator part. Adjacent command parts are
    merged into one pick by the selector."""
    parts = [{"part": i, "to": a, "instruction": i} for a, i in picks]
    if orchestrator or not parts:
        parts.append({"part": orchestrator or "all of it", "to": "orchestrator", "instruction": orchestrator})
    return {"parts": parts}


def planned(action: dict | None = None, *, say: str = "", hand_on: str = "", drop: str = "") -> dict:
    return {"action": action, "hand_on": hand_on, "drop": drop, "say": say}


def cmd(*commands: str) -> dict:
    return {"commands": list(commands)}


def tp(player: str, to: str, target: str = "") -> dict:
    return {"player": player, "to": to, "target": target}
