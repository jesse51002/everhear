"""Fakes for every brain test: no model, no network, no real vfg, no subprocess, no sleeping,
no real ~/.vfg.

- catalog(): the core actions and railgun, from a small copy of the repo's actions/ beside a
  stub of each script, loaded once; made() writes a server's own action into a catalog of its own.
- FakeAsk stands in for structured(): ONE canned answer per output class ("Decision",
  "Response", "Planned_command"), validated against the class as structured() does.
- FakeVfg stands in for the vfg runner: it records every argv and answers by prefix.
- FakeImplementers records what is started and followed up.
- Contexts are built by hand: rows(...) from feed lines.
"""

from __future__ import annotations

import asyncio
import atexit
import itertools
import json
import shutil
import tempfile
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from vfg_llm import Answer

from brain import actions
from brain.act import Acts
from brain.action_planner import Planner
from brain.call import Call
from brain.config import REPO
from brain.context import Context, parse
from brain.loop import Brain
from brain.respond import Respond
from brain.tasks import Tasks
from brain.vfg import Later, VfgResult
from brain.world import World

CORE = ("command", "teleport", "summon_around", "save_place", "stop", "edit", "railgun")
NOW = datetime(2026, 10, 10, 18, 30, 0, tzinfo=UTC)


def _repo_copy() -> Path:
    root = Path(tempfile.mkdtemp(prefix="vfg-brain-repo-"))
    atexit.register(shutil.rmtree, root, True)
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for name in CORE:
        shutil.copy(REPO / "actions" / f"{name}.py", root / "actions")
        (root / "scripts" / name).mkdir(parents=True)
        (root / "scripts" / name / "main.py").write_text('"""a stub"""\n')
    (root / "scripts" / "_presets").symlink_to(REPO / "scripts" / "_presets")
    return root


REPO_COPY = _repo_copy()
_servers = itertools.count(1)
_shared = None


def catalog():
    """The core actions and railgun, loaded once for the session (the brain never changes them)."""
    global _shared
    if _shared is None:
        _shared = actions.load(REPO_COPY, None)
    return _shared


def made(name: str, source: str):
    """A catalog of its own, with made/<server>/actions/<name>.py beside the library."""
    server = "test%d" % next(_servers)
    path = REPO_COPY / "made" / server / "actions" / f"{name}.py"
    path.parent.mkdir(parents=True)
    path.write_text(textwrap.dedent(source).lstrip("\n"))
    script = REPO_COPY / "made" / server / "scripts" / name / "main.py"
    script.parent.mkdir(parents=True)
    script.write_text('"""a stub"""\n')
    return actions.load(REPO_COPY, server)


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path, monkeypatch):
    """Nothing in a test may reach the real ~/.vfg."""
    monkeypatch.setenv("VFG_HOME", str(tmp_path / "vfg-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("VFG_BRAIN_DIR", "VFG_REPO", "OPEN_ROUTER_API", "VFG_OPENROUTER_KEY", "VFG_SERVER", "VFG_OWNER",
                "VFG_BRAIN_MODEL", "VFG_RUNNING_TTL_S", "VFG_PLAN_CHECK"):
        monkeypatch.delenv(key, raising=False)


# ------------------------------------------------------------------ the context

def rows(*lines: str, processed: bool = False) -> Context:
    """A context from feed lines, said on NOW's day."""
    ctx = Context()
    for text in lines:
        ctx.add(parse(text, now=NOW, processed=processed))
    return ctx


# ------------------------------------------------------------------ the model

class FakeAsk:
    """structured(): `answers[output class name]` is the one answer, a dict (validated as
    structured() validates it), an Exception (raised) or an async callable taking the prompt."""

    def __init__(self, **answers: Any):
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, prompt: str, output: type, **kw: Any) -> Answer:
        self.calls.append({"prompt": prompt, "output": output.__name__, **kw})
        got = self.answers[output.__name__]
        if callable(got) and not isinstance(got, dict):
            got = await got(prompt)
        if isinstance(got, Exception):
            raise got
        value = output.model_validate_json(json.dumps(got))
        return Answer[output](value=value, cost=0.002, ms=7, prompt_tokens=1000, cached_tokens=900,
                              completion_tokens=40)

    def of(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["output"] == name]


def decision(*parts: dict, reply: str = "", record: str = "", status: str = "") -> dict:
    return {"parts": list(parts), "reply": reply, "record": record, "status": status}


def part(to: str, instruction: str = "", action: dict | None = None, *, drop: str = "", task: str = "",
         said: str = "") -> dict:
    """One part as the model writes it: the action's values as key and value pairs."""
    pairs = None if action is None else [{"key": k, "value": v} for k, v in action.items()]
    return {"part": said or instruction or to, "to": to, "instruction": instruction, "task": task,
            "action": pairs, "drop": drop}


def cmd(*commands: str) -> dict:
    return {"commands": list(commands)}


# ------------------------------------------------------------------ vfg

def ran(ok: bool, summary: str, *, copy: str = "", running: bool = False, **facts) -> VfgResult:
    """vfg action run's one JSON line."""
    line = {"ok": ok, "copy": copy, "action": copy, "running": running, "summary": summary, "facts": facts,
            "notes": []}
    return VfgResult(0 if ok else 1, json.dumps(line) + "\n", "")


STATE = {"online": [{"name": "Steve", "pos": [10, 64, -20], "dimension": "overworld",
                     "items": ["Steve has the following entity data: [{Slot: 4b, id: \"minecraft:iron_sword\", "
                               "count: 1}]", "Steve has the following entity data: 4"],
                     "health": 14.5, "max_health": 20.0, "food": 18, "xp_level": 5, "armor": 12.0,
                     "game_mode": "survival", "effects": [{"id": "minecraft:speed", "amplifier": 1, "duration": 600},
                                                          {"id": "minecraft:night_vision", "amplifier": 0,
                                                           "duration": -1}]}],
         "time": {"daytime": 6000, "day": 3, "phase": "day"}, "weather": "clear", "instances": [], "rcon": None}


class FakeVfg:
    """Records every argv; answers by the longest matching prefix, else a default: `action run`
    ok, `state` STATE, `place list` none, anything else exit 0 and nothing."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.rules: list[tuple[tuple[str, ...], Any]] = []

    def on(self, *prefix: str, answer: Any) -> FakeVfg:
        """`answer`: a VfgResult, a list of them (used in order, the last repeated) or an async
        callable taking the argv."""
        self.rules.append((prefix, answer))
        return self

    async def __call__(self, *args: str, timeout: float = 30) -> VfgResult:
        argv = list(args)
        self.calls.append(argv)
        await asyncio.sleep(0)
        for prefix, answer in sorted(self.rules, key=lambda r: -len(r[0])):
            if tuple(argv[:len(prefix)]) == prefix:
                if isinstance(answer, list):
                    return answer.pop(0) if len(answer) > 1 else answer[0]
                if callable(answer):
                    return await answer(argv)
                return answer
        if argv[0] == "action":
            return ran(True, "done", copy=argv[2])
        if argv[0] == "state":
            return VfgResult(0, json.dumps(STATE), "")
        if argv[:2] == ["place", "list"]:
            return VfgResult(0, "home 1 64 2 overworld (T3)\n", "")
        return VfgResult(0, "", "")

    def of(self, *prefix: str) -> list[list[str]]:
        return [a for a in self.calls if tuple(a[:len(prefix)]) == prefix]

    def said(self) -> list[list[str]]:
        return [a[1:] for a in self.of("say")]

    def rows(self, kind: str = "") -> list[tuple[str, str]]:
        """The AI rows posted: (fields, text)."""
        return [(a[3], a[4]) for a in self.of("feed", "post", "AI") if a[3].startswith(kind)]


class FakeImplementers:
    def __init__(self):
        self.started: list[tuple[str, str]] = []
        self.followed: list[tuple[str, str]] = []
        self.busy: list[str] = []
        self.closed = False

    async def start(self, task_id: str, brief: str) -> None:
        self.started.append((task_id, brief))
        self.busy.append(task_id)

    async def follow_up(self, task_id: str, text: str, session: str | None = None, brief: str = "") -> None:
        self.followed.append((task_id, text, brief))

    def running(self) -> list[str]:
        return list(self.busy)

    async def close(self, grace_s: float) -> list[str]:
        self.closed = True
        return list(self.busy)


class FakeRefs:
    version = "26.3"

    def names(self) -> set[str]:
        return {"give", "effect", "execute", "item"}

    def menu(self, exclude=()) -> str:
        return "give — Gives an item to a player.\neffect — Adds or removes status effects."

    def used(self, commands: list[str]):
        return {c.split()[0] for c in commands if c.strip()}, set()

    def reference(self, names, subs=frozenset()) -> str:
        return "\n".join(f"## {n}\nSyntax on this server:\n{n} <targets> …" for n in sorted(names))


# ------------------------------------------------------------------ a brain, by hand

class Kit:
    """A Brain wired to fakes, its parts at hand."""

    def __init__(self, tmp: Path, ctx: Context | None = None, *, cat=None, **answers: Any):
        self.vfg = FakeVfg()
        self.ask = FakeAsk(**answers)
        self.impl = FakeImplementers()
        self.cat = cat or catalog()
        self.ctx = ctx or Context()
        self.tasks = Tasks(tmp / "brain" / "tasks.json")
        self.later = Later(self.vfg)
        self.world = World(self.vfg, tmp / "running.json", self.cat)
        self.planner = Planner(self.ask, "plan-model", FakeRefs())
        self.call = Call(self.cat, self.ask, owner="KrownJM")
        self.respond = Respond(self.cat, self.ask, owner="KrownJM")
        self.acts = Acts(vfg=self.vfg, later=self.later, catalog=self.cat, planner=self.planner, tasks=self.tasks,
                         implementers=self.impl, world=self.world)
        self.brain = Brain(ctx=self.ctx, call=self.call, respond=self.respond, acts=self.acts, world=self.world,
                           tasks=self.tasks, implementers=self.impl, later=self.later, catalog=self.cat,
                           refs=FakeRefs(), model="brain-model", respond_model="respond-model", now=lambda: NOW)

    def running(self, copies: dict[str, dict]) -> None:
        (self.world.running_file).write_text(json.dumps({"fetched_at": 0, "copies": copies}))

    async def settle(self) -> None:
        """Every event and every write nobody waits for, done."""
        while self.brain.inflight or self.later.tasks:
            await asyncio.gather(*self.brain.inflight, *self.later.tasks)


@pytest.fixture
def kit(tmp_path):
    return lambda ctx=None, **answers: Kit(tmp_path, ctx, **answers)
