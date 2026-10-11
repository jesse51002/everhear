"""The running copies: ~/.vfg/running.json, read before each ask and kept fresh in the background.

vfg keeps the file (Contracts §8): every run, edit and stop through it writes through, and
`vfg script list --json --fresh` fetches the copies from EC2 again, which drops those that
ended by themselves. The dispatcher runs that fetch every VFG_RUNNING_TTL_S (30 s) in the
background; no ask waits on it, and every read is of the file:

    {"fetched_at": epoch, "copies": {copy: {"action", "script", "values", "live", "task", "started", "runs"}}}

The brain and the planner see each copy as one line: its name, its action, the values that
differ from the action's defaults and the task that started it, when that is not the copy's own
name (a task's copy is named after its task: Prototype_Named_Tasks).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from pydantic_core import to_jsonable_python

from .actions import word
from .config import log
from .vfg import Vfg, why

LINE_MAX = 300  # characters of one copy's values on its line


def read(path: Path) -> dict[str, dict[str, Any]]:
    """{copy: what running.json holds of it}; {} when there is no readable file."""
    try:
        copies = json.loads(path.read_text(encoding="utf-8"))["copies"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(copies, dict):
        return {}
    return {str(name): info for name, info in copies.items() if isinstance(info, dict)}


def differs(info: dict[str, Any], cls) -> dict[str, Any]:
    """The copy's values that differ from its action's defaults (a fixed value is a default);
    every value when its action is not loaded."""
    values = info.get("values") if isinstance(info.get("values"), dict) else {}
    if cls is None:
        return dict(values)
    out = {}
    for key, v in values.items():
        f = cls.model_fields.get(key)
        if f is None or v != to_jsonable_python(f.get_default(call_default_factory=True)):
            out[key] = v
    return out


def line(name: str, info: dict[str, Any], catalog) -> str:
    """`railgun_steve: railgun, player=Steve cooldown=0`; `ice_trail_alex_2: ice_trail_alex, its
    defaults, started by ice_trail_alex`; the task left out when the copy is named after it."""
    action = str(info.get("action") or "?")
    vals = " ".join(f"{k}={word(v)}" for k, v in differs(info, catalog.get(action)).items()) or "its defaults"
    if len(vals) > LINE_MAX:
        vals = vals[:LINE_MAX - 1] + "…"
    task = str(info.get("task") or "").strip()
    return f"{name}: {action}, {vals}" + (f", started by {task}" if task and task != name else "")


def block(copies: dict[str, dict[str, Any]], catalog) -> str:
    """The RUNNING NOW lines the selector and the planner see."""
    if not copies:
        return "RUNNING NOW: none"
    rows = "\n".join(f"- {line(n, copies[n], catalog)}" for n in sorted(copies))
    return ("RUNNING NOW (each: the copy's name: its action, the values that differ from the action's "
            f"defaults, the task that started it when that is not the copy's own name):\n{rows}")


class Refresher:
    """Runs `vfg script list --json --fresh` every `every` seconds, in the background, so
    running.json drops the copies that ended by themselves. A failure is logged once, until
    a fetch works again; the file keeps what it had."""

    def __init__(self, vfg: Vfg, every: float):
        self.vfg = vfg
        self.every = every
        self.task: asyncio.Task | None = None
        self.failing = False

    def start(self) -> None:
        if self.every > 0 and self.task is None:
            self.task = asyncio.create_task(self._loop(), name="running")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def fetch(self) -> bool:
        r = await self.vfg("script", "list", "--json", "--fresh", timeout=30)
        if r.code != 0:
            if not self.failing:
                log("running", f"vfg script list --fresh failed ({why(r)}): the running copies are as last "
                               f"fetched; trying again every {self.every:g} s")
            self.failing = True
            return False
        if self.failing:
            log("running", "vfg script list --fresh works again")
        self.failing = False
        return True

    async def _loop(self) -> None:
        while True:
            await self.fetch()
            await asyncio.sleep(self.every)
