"""Fakes for every test: no model, no network, no real vfg, no real ~/.vfg.

- FakeLLM stands in for structured(): scripted answers per output class name
  ("Selection", "Planned_command", "Final_teleport", …), validated against the class as
  structured() does, with one repair from the next answer when the first does not validate.
- FakeVfg stands in for the vfg runner: it records every argv and answers by prefix.
- FakeRefs stands in for refs.Refs (§8).
- Test-local action classes, shaped like actions/_types.py's.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vfg_llm import Answer, LlmError

from dispatcher.batch import Batch
from dispatcher.config import Config
from dispatcher.inbox import State, new_task
from dispatcher.reply import Reply
from dispatcher.task import Dispatcher
from dispatcher.vfg import VfgResult


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path, monkeypatch):
    """Nothing in a test may reach the real ~/.vfg."""
    monkeypatch.setenv("VFG_HOME", str(tmp_path / "vfg-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("VFG_DISPATCH_DIR", "VFG_ORCH_DIR", "VFG_REPO", "OPEN_ROUTER_API", "VFG_OPENROUTER_KEY",
                "VFG_DISPATCH_MODEL", "VFG_DISPATCH_RETRIES", "VFG_DISPATCH_WINDOW_S",
                "VFG_DISPATCH_ACTION_TIMEOUT_S"):
        monkeypatch.delenv(key, raising=False)


# ------------------------------------------------------------------ actions

BLOCKS = {"setblock", "fill", "clone", "place", "fillbiome"}
OWNER = {"stop", "op", "deop", "ban", "whitelist", "kick"}


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def vfg_argv(self, task: str) -> list[str]:
        args = json.dumps(self.model_dump(mode="json", exclude={"action"}))
        return ["action", "run", self.action, "--args", args, "--task", task]


class Command(Action):
    """Run vanilla commands, in order: anything that takes effect at once — items, effects, time, weather. Never places or removes blocks.

    Up to five commands, run in the order written."""
    action: Literal["command"]
    commands: list[str] = Field(min_length=1, max_length=5, description="the commands, in the order they run")

    @field_validator("commands")
    @classmethod
    def _no_blocks(cls, value: list[str]) -> list[str]:
        for c in value:
            words = c.lstrip("/").split()
            heads = [words[0]] + [words[i + 1] for i, w in enumerate(words[:-1]) if w == "run"]
            if BLOCKS & set(heads):
                raise ValueError(f"{c!r} places or removes blocks; building is the orchestrator's")
        return [c.lstrip("/") for c in value]


class Teleport(Action):
    """Move a player, or everyone, to a biome, a structure, a saved place or coordinates, landing on safe ground.

    To another player it is one tp, which command does."""
    action: Literal["teleport"]
    player: str = Field(pattern=r"^([A-Za-z0-9_]{1,16}|@a)$", description="who moves")
    to: Literal["biome", "structure", "place", "coords"] = Field(description="what kind of target")
    target: str = Field("", description="a biome or structure id, a saved place's name, or 'x z'")
    timeout_s: ClassVar[int] = 40


class RunScript(Action):
    """Start one of the scripts in scripts/, by name, with its arguments."""
    action: Literal["run_script"]
    name: Literal["zeus", "tnt_rain"] = Field(description="the script")
    args: list[str] = Field(default_factory=list, description="its arguments")

    def vfg_argv(self, task: str) -> list[str]:
        return ["script", "run", self.name, *self.args]


CATALOG = {"command": Command, "teleport": Teleport, "run_script": RunScript}


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

def ok(summary: str = "done", **facts) -> VfgResult:
    return VfgResult(0, json.dumps({"ok": True, "summary": summary, **facts}) + "\n", "")


def bad(summary: str, **facts) -> VfgResult:
    return VfgResult(0, json.dumps({"ok": False, "summary": summary, **facts}) + "\n", "")


def fail(code: int = 1, stderr: str = "ssh: connect to host failed") -> VfgResult:
    return VfgResult(code, "", stderr + "\n")


class FakeVfg:
    """Records every argv; answers by the longest matching prefix rule, else a default.
    A rule's answer is a VfgResult, a list of them (used in order, the last repeated), or a
    callable taking the argv (sync or async) and returning one."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.rules: list[tuple[tuple[str, ...], Any]] = []
        self.events: list[tuple[str, list[str]]] = []  # ("start"|"end", argv)

    def on(self, *prefix: str, answer: Any) -> FakeVfg:
        self.rules.append((prefix, answer))
        return self

    async def __call__(self, *args: str, timeout: float = 30) -> VfgResult:
        argv = list(args)
        self.calls.append(argv)
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
            return ok()
        return VfgResult(0, "", "")

    def replies(self, task: str | None = None) -> list[tuple[str, str, str]]:
        """(id, status, text) of every `vfg orch reply` call, attempts included."""
        return [(a[2], a[3], a[4]) for a in self.calls if a[:2] == ["orch", "reply"] and (task is None or a[2] == task)]

    def hands(self) -> list[list[dict]]:
        return [json.loads(a[2]) for a in self.calls if a[:2] == ["orch", "hand"]]

    def actions(self) -> list[list[str]]:
        return [a for a in self.calls if a[:2] in (["action", "run"], ["script", "run"], ["place", "save"])]


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
    def __init__(self, tmp: Path, *, catalog=None, refs="fake", retries: int = 1, window_s: float = 0.05):
        self.tmp = tmp
        self.cfg = Config(home=tmp / "vfg-home", dispatch_dir=tmp / "dispatch", orch_dir=tmp / "orch",
                          repo=tmp / "repo", retries=retries, window_s=window_s, action_timeout_s=20,
                          stop_grace_s=0.5)
        self.cfg.dispatch_dir.mkdir(parents=True, exist_ok=True)
        self.llm = FakeLLM()
        self.vfg = FakeVfg()
        self.refs = FakeRefs() if refs == "fake" else refs
        self.state = State(self.cfg.state_file)
        self.reply = Reply(self.state, self.vfg, retry_s=(0.01,))
        self.batch = Batch(self.state, self.vfg, window_s, retry_s=(0.01,))
        self.d = Dispatcher(self.cfg, catalog=CATALOG if catalog is None else catalog, refs=self.refs,
                            vfg=self.vfg, ask=self.llm, state=self.state, reply=self.reply, batch=self.batch,
                            scripts={"zeus": "Makes every trident call lightning. | Usage: zeus <player> | Ends: when stopped",
                                     "tnt_rain": "Drops TNT round players. | Usage: tnt_rain [every_s=20] | Ends: when stopped"},
                            hidden=frozenset(BLOCKS | OWNER))
        self.batch.on_refused = self.d.refused
        self.n = 0

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
    return {"action": "command", "commands": list(commands)}


def tp(player: str, to: str, target: str = "") -> dict:
    return {"action": "teleport", "player": player, "to": to, "target": target}
