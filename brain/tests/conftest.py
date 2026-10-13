"""Fakes for every brain test: no model, no network, no real vfg, no subprocess, no sleeping,
no real ~/.vfg.

- catalog(): the brain's menu, the game engine's ops (actions/engine_*.py), from a copy of the
  repo's actions/, loaded once.
- FakeAsk stands in for structured(): canned answers per output class ("Step", "Response"), one
  for every call or a list taken in order, each validated against the class as structured() does.
- FakeVfg stands in for the vfg runner: it records every argv and answers by prefix; `vfg engine
  overview` with OVERVIEW (a few classes, no instances, the place home) unless a test says otherwise.
- FakeImplementers records what is started and followed up, by the task's name.
- Tick is the loop's clock, moved by hand.
- Contexts are built by hand: rows(...) from feed lines; steps by step(), act(), hand_off() and
  follow_up().
"""

from __future__ import annotations

import asyncio
import atexit
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from vfg_llm import Answer

from brain import actions
from brain.act import Acts
from brain.call import Call
from brain.config import REPO
from brain.context import Context, parse
from brain.loop import Brain
from brain.respond import Respond
from brain.tasks import Tasks
from brain.vfg import Later, VfgResult
from brain.world import World

NOW = datetime(2026, 10, 10, 18, 30, 0, tzinfo=UTC)


def _repo_copy() -> Path:
    """The repo's actions/ (the engine's ops) beside the real _types.py, and an empty
    engine/library/ and made/main/content/, whose files are the names a new task may not take."""
    root = Path(tempfile.mkdtemp(prefix="vfg-brain-repo-"))
    atexit.register(shutil.rmtree, root, True)
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for path in sorted((REPO / "actions").glob("engine_*.py")):
        shutil.copy(path, root / "actions")
    (root / "engine" / "library").mkdir(parents=True)
    (root / "made" / "main" / "content").mkdir(parents=True)
    return root


REPO_COPY = _repo_copy()
_shared = None


def catalog():
    """The engine's ops, the brain's whole menu, loaded once for the session (the brain never
    changes them)."""
    global _shared
    if _shared is None:
        _shared = actions.load(REPO_COPY)
    return _shared


engine_catalog = catalog


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path, monkeypatch):
    """Nothing in a test may reach the real ~/.vfg."""
    monkeypatch.setenv("VFG_HOME", str(tmp_path / "vfg-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("VFG_BRAIN_DIR", "VFG_REPO", "OPEN_ROUTER_API", "VFG_OPENROUTER_KEY", "VFG_SERVER", "VFG_OWNER",
                "VFG_BRAIN_MODEL", "VFG_RUNNING_TTL_S", "VFG_PLAN_CHECK", "VFG_USAGE_QUEUE"):
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
    """structured(): `answers[output class name]` is the answer to every call, or a list of them
    taken in order, one a call; each a dict (validated as structured() validates it), an Exception
    (raised) or an async callable taking the prompt."""

    def __init__(self, **answers: Any):
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, prompt: str, output: type, **kw: Any) -> Answer:
        self.calls.append({"prompt": prompt, "output": output.__name__, "cls": output, **kw})
        got = self.answers[output.__name__]
        if isinstance(got, list):
            assert got, f"no canned {output.__name__} left for call {len(self.of(output.__name__))}"
            got = got.pop(0)
        if callable(got) and not isinstance(got, dict):
            got = await got(prompt)
        if isinstance(got, Exception):
            raise got
        value = output.model_validate_json(json.dumps(got))
        return Answer[output](value=value, cost=0.002, ms=7, prompt_tokens=1000, cached_tokens=900,
                              completion_tokens=40)

    def of(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["output"] == name]


def step(*actions: dict, end: str = "reply", reply: str = "", record: str = "", status: str = "",
         order: str = "together", read: tuple[str, ...] = (), hand_offs: tuple[dict, ...] = (),
         follow_ups: tuple[dict, ...] = ()) -> dict:
    """One step as the model writes it."""
    return {"order": order, "actions": list(actions), "read_items": list(read), "hand_offs": list(hand_offs),
            "follow_ups": list(follow_ups), "end": end, "reply": reply, "record": record, "status": status}


def act(to: str, instruction: str, action: dict | None = None) -> dict:
    """One action as the model writes it: its values as key and value pairs."""
    return {"to": to, "instruction": instruction, "action": [{"key": k, "value": v} for k, v in (action or {}).items()]}


def cmd(*commands: str) -> dict:
    """engine_start's values for the library's command scenario (TODO(library): its args' names)."""
    return {"scenario": "command", "args.commands": list(commands)}


def start(scenario: str, **args: Any) -> dict:
    """engine_start's values: the scenario, and each of its args as `args.<name>`."""
    return {"scenario": scenario, **{f"args.{k}": v for k, v in args.items()}}


FIREBALL = {"words": "“Can you make an incantation? That fires a fireball whenever I say Hot fireball”",
            "picture": "a fireball from KrownJM's eyes, where they look, fast and straight, trailing flame",
            "assumed": "it stops at the first mob, player or block it meets, explodes there, breaks blocks and "
                       "sets fire",
            "cast": "the incantation “hot fireball”, for KrownJM",
            "done_when": ["saying it fires one", "it stops at the first mob, player or block", "it explodes there"]}


def hand_off(name: str = "hot_fireball_krown", description: str = "Hot Fireball for KrownJM: say hot fireball.",
             **parts: Any) -> dict:
    return {"name": name, "description": description, **FIREBALL, **parts}


def follow_up(task: str, brief: str = "", description: str = "", cancel: bool = False) -> dict:
    return {"task": task, "brief": brief, "description": description, "cancel": cancel}


# ------------------------------------------------------------------ vfg

def ran(ok: bool, summary: str, *, action: str = "", running: bool = False, **facts) -> VfgResult:
    """vfg action run's one JSON line."""
    line = {"ok": ok, "action": action, "running": running, "summary": summary, "facts": facts, "notes": []}
    return VfgResult(0 if ok else 1, json.dumps(line) + "\n", "")


STATE = {"online": [{"name": "Steve", "pos": [10, 64, -20], "dimension": "overworld",
                     "items": ["Steve has the following entity data: [{Slot: 4b, id: \"minecraft:iron_sword\", "
                               "count: 1}]", "Steve has the following entity data: 4"],
                     "health": 14.5, "max_health": 20.0, "food": 18, "xp_level": 5, "armor": 12.0,
                     "game_mode": "survival", "effects": [{"id": "minecraft:speed", "amplifier": 1, "duration": 600},
                                                          {"id": "minecraft:night_vision", "amplifier": 0,
                                                           "duration": -1}]}],
         "time": {"daytime": 6000, "day": 3, "phase": "day"}, "weather": "clear", "instances": [], "rcon": None}


def klass(id_: str, kind: str, summary: str, args: list[dict] | None = None) -> dict:
    """One class as the engine's classes op gives it."""
    return {"id": id_, "kind": kind, "summary": summary, "file": f"engine/library/{id_}.py", "args": args or []}


def arg(name: str, type_: str, default: Any = None, description: str = "", **bounds: Any) -> dict:
    return {"name": name, "type": type_, "default": default, "bounds": bounds, "description": description}


CLASSES = [
    klass("command", "scenario", "Runs Minecraft commands, at once.",
          [arg("commands", "list[Command]", [], "the commands, each one line")]),
    klass("teleport", "scenario", "Takes players to a place.",
          [arg("players", "Players", "@a", "who goes"), arg("at", "Place", None, "where to")]),
    klass("horde", "scenario", "A horde of mobs round each player until it is stopped.",
          [arg("count", "int", 10, "how many", ge=1, le=100)]),
    klass("fireball", "ability", "Burns for burn_s seconds, then explodes in a radius."),
    klass("fire_staff", "item", "A staff that shoots fire where you look."),
    klass("giant", "mark", "Twice as big for a while.", [arg("factor", "float", 2.0, "how much bigger", ge=1, le=16)]),
]
OVERVIEW = {"ok": True, "classes": CLASSES, "instances": [],
            "places": [{"name": "home", "dim": "minecraft:overworld", "pos": [1, 64, 2], "by": "home_steve"}],
            "incantations": []}


def overview(**parts: Any) -> VfgResult:
    """`vfg engine overview --json`: OVERVIEW with `parts` in place of its own."""
    return VfgResult(0, json.dumps({**OVERVIEW, **parts}), "")


class FakeVfg:
    """Records every argv; answers by the longest matching prefix, else a default: `action run`
    ok, `state` STATE, `engine overview` OVERVIEW, anything else exit 0 and nothing."""

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
            return ran(True, "done", action=argv[2])
        if argv[0] == "state":
            return VfgResult(0, json.dumps(STATE), "")
        if argv[:2] == ["engine", "overview"]:
            return overview()
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
        self.followed: list[tuple[str, str, str | None, str]] = []
        self.busy: list[str] = []
        self.closed = False
        self.on_start = None  # called with the name at each start, to look at the world then

    async def start(self, name: str, brief: str) -> None:
        if self.on_start is not None:
            self.on_start(name)
        self.started.append((name, brief))
        self.busy.append(name)

    async def follow_up(self, name: str, text: str, session: str | None = None, brief: str = "") -> None:
        self.followed.append((name, text, session, brief))

    def running(self) -> list[str]:
        return list(self.busy)

    async def close(self, grace_s: float) -> list[str]:
        self.closed = True
        return list(self.busy)


class Tick:
    """The loop's clock, moved by hand: `tick.t += 30`."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


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
    """A Brain wired to fakes, its parts at hand. A turn does not wait for its rows to come back
    (settle_s 0) unless a test sets the brain's settle_s."""

    def __init__(self, tmp: Path, ctx: Context | None = None, *, cat=None, **answers: Any):
        self.vfg = FakeVfg()
        self.ask = FakeAsk(**answers)
        self.impl = FakeImplementers()
        self.tick = Tick()
        self.cat = cat or catalog()
        self.ctx = ctx or Context()
        self.tasks = Tasks(send=self.send, clock=Clock())
        self.later = Later(self.vfg)
        self.world = World(self.vfg)
        self.call = Call(self.cat, self.ask, owner="KrownJM")
        self.respond = Respond(self.cat, self.ask, owner="KrownJM")
        self.acts = Acts(vfg=self.vfg, later=self.later, catalog=self.cat, tasks=self.tasks, implementers=self.impl,
                         world=self.world, repo=REPO_COPY, server="main")
        self.brain = Brain(ctx=self.ctx, call=self.call, respond=self.respond, acts=self.acts, world=self.world,
                           tasks=self.tasks, implementers=self.impl, later=self.later, catalog=self.cat,
                           refs=FakeRefs(), now=lambda: NOW, clock=self.tick, settle_s=0)

    async def send(self, row: dict) -> bool:
        """`vfg task set`, as __main__ sends it."""
        r = await self.vfg("task", "set", json.dumps(row, ensure_ascii=False), timeout=30)
        return r.code == 0

    def written(self, name: str = "") -> list[dict]:
        """Every row `vfg task set` was given (only `name`'s), in order."""
        rows = [json.loads(a[2]) for a in self.vfg.of("task", "set")]
        return [r for r in rows if not name or r["name"] == name]

    def running(self, *instances: dict) -> None:
        """The engine runs these instances (as its instances op gives them) from now on."""
        self.vfg.on("engine", "overview", answer=overview(instances=list(instances)))

    async def settle(self) -> None:
        """Every event and every write nobody waits for, done."""
        while self.brain.inflight or self.later.tasks or self.tasks.writes:
            await asyncio.gather(*self.brain.inflight, *self.later.tasks, *self.tasks.writes.values())
            await asyncio.sleep(0)  # a gather of finished tasks returns at once: let their callbacks run


class Clock:
    """now_iso(), a millisecond later at each call, from NOW."""

    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return (NOW + timedelta(milliseconds=self.n)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def kit(tmp_path):
    return lambda ctx=None, **answers: Kit(tmp_path, ctx, **answers)
