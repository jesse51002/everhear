"""The running copies: ~/.vfg/running.json, read before each ask and kept fresh in the background.

vfg keeps the file (Contracts §8): every run, edit and stop through it writes through, and
`vfg script list --json --fresh` fetches the copies from EC2 again, which drops those that
ended by themselves. The dispatcher runs that fetch every VFG_RUNNING_TTL_S (30 s) in the
background; no ask waits on it, and every read is of the file:

    {"fetched_at": epoch, "copies": {copy: {"action", "script", "values", "live", "task", "started", "runs"}}}

Respond sees each copy as one line (block()): its name, its action, the values that differ from
the action's defaults and the task that started it, when that is not the copy's own name (a task's
copy is named after its task: Prototype_Named_Tasks). The brain, which edits and stops copies with
its tools, sees each in full (full()): every value of its action with its value now, its limits or
choices, live or restart, and who started it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

import vfg_actions
from pydantic_core import to_jsonable_python

from .actions import word
from .config import log
from .vfg import Vfg, why

LINE_MAX = 300  # characters of one copy's values on its line
VALUE_MAX = 40  # characters of a value in full()
CHOICES_MAX = 8  # choices shown for one value
CAST = "cast"  # the bridge's task for an incantation's copies


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
    """The RUNNING NOW lines Respond sees."""
    if not copies:
        return "RUNNING NOW: none"
    rows = "\n".join(f"- {line(n, copies[n], catalog)}" for n in sorted(copies))
    return ("RUNNING NOW (each: the copy's name: its action, the values that differ from the action's "
            f"defaults, the task that started it when that is not the copy's own name):\n{rows}")


def _shown(v: Any) -> str:
    """A value in full(): as `key=<this>` carries it, text with a space or nothing in it quoted, cut
    at VALUE_MAX characters."""
    text = word(v)
    if isinstance(v, str) and (not v or re.search(r"\s", v)):
        text = json.dumps(v, ensure_ascii=False)
    return text if len(text) <= VALUE_MAX else text[:VALUE_MAX - 1] + "…"


def _bounds(var: dict) -> str:
    lo, hi = var.get("ge"), var.get("le")
    if lo is not None or hi is not None:
        return f" ({'' if lo is None else word(lo)}..{'' if hi is None else word(hi)})"
    options = var.get("choices")
    if options:
        shown = "|".join(word(o) for o in options[:CHOICES_MAX])
        more = len(options) - CHOICES_MAX
        return f" ({shown}{f'|…+{more}' if more > 0 else ''})"
    return ""


def _started_by(info: dict) -> str:
    task = str(info.get("task") or "").strip()
    if task == CAST:
        return "an incantation started it"
    return f"started by task {task}" if task else "the brain started it"


def full(copies: dict[str, dict[str, Any]], catalog) -> str:
    """RUNNING NOW as the brain sees it, two or three lines a copy, sorted by name: its action and
    who started it, then every value of its action but the fixed ones, with its value now, its
    limits or choices, live or restart. A copy whose action is not loaded shows running.json's
    values and live list."""
    if not copies:
        return "RUNNING NOW: none"
    rows = []
    for c in sorted(copies):
        info = copies[c]
        action = str(info.get("action") or "?")
        now = info.get("values") if isinstance(info.get("values"), dict) else {}
        cls = catalog.get(action) if catalog is not None else None
        live, restart = [], []
        if cls is not None:
            for var in vfg_actions.variables(cls):
                if var["fixed"]:
                    continue
                v = now.get(var["name"], var["default"])
                (live if var["live"] else restart).append(f"{var['name']}={_shown(v)}{_bounds(var)}")
        else:
            marked = set(info.get("live") or []) if isinstance(info.get("live"), list) else set()
            for k, v in now.items():
                (live if k in marked else restart).append(f"{k}={_shown(v)}")
        text = f"- {c}: {action}, {_started_by(info)}"
        if live:
            text += "\n  live: " + " ".join(live)
        if restart:
            text += "\n  restart: " + " ".join(restart)
        rows.append(text)
    return ("RUNNING NOW (each copy: its action, who started it; then every value of its action with its value "
            "now, its limits or choices, live (changes at once) or restart):\n" + "\n".join(rows))


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
