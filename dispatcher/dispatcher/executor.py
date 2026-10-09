"""The action executor: runs one exact action through `vfg` and reports how it went. Code, no model.

It runs `vfg <action.vfg_argv(task)>` and says which of four things happened (`Ran.kind`):
- "ok": it ran. `vfg action run` exits 0 with one JSON line, `ok` true; any other vfg
  command exits 0, its stdout the summary.
- "refused": the action ran and Minecraft or the handler said no: exit 0 with `ok` false, or
  exit 1 from a command that prints no JSON (`vfg place save`, `vfg script run/stop`), with
  its error text. Only this goes back to the action planner, which may change it and retry.
- "timeout": exit 124. It may still have finished on EC2, so it is never retried.
- "not_run": it could not be run at all: exit 2 bad arguments, 3 mc.py not deployed, 4 SSH
  down, 5 RCON down, exit 1 from `vfg action run` (unknown action, no handler, a command too
  long), or vfg itself missing. A retry cannot fix it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .config import log
from .vfg import Vfg, VfgResult, why

GRACE_S = 5  # past vfg's own --timeout, so its clean exit 124 is the one seen


Kind = Literal["ok", "refused", "timeout", "not_run"]


@dataclass
class Ran:
    ok: bool
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    kind: Kind = "ok"


def timeout_for(action: BaseModel, default: float) -> float:
    return float(getattr(type(action), "timeout_s", None) or default)


def argv_for(action: BaseModel, task_id: str, timeout: float) -> list[str]:
    argv = [str(a) for a in action.vfg_argv(task_id)]
    if argv[:2] == ["action", "run"] and "--timeout" not in argv:
        argv += ["--timeout", f"{timeout:g}"]
    return argv


async def execute(action: BaseModel, task_id: str, vfg: Vfg, default_timeout: float, *, label: str = "") -> Ran:
    timeout = timeout_for(action, default_timeout)
    argv = argv_for(action, task_id, timeout)
    t0 = time.perf_counter()
    r = await vfg(*argv, timeout=timeout + GRACE_S)
    ran = read(r, action_run=argv[:2] == ["action", "run"], timeout=timeout)
    ms = int((time.perf_counter() - t0) * 1000)
    name = getattr(action, "action", type(action).__name__)
    log("exec", f"{task_id}{label} {name} {ran.kind} (exit {r.code}) {ms} ms: {ran.summary[:300]}")
    return ran


def read(r: VfgResult, *, action_run: bool = True, timeout: float | None = None) -> Ran:
    """`action_run`: the argv was `vfg action run`, which prints a JSON line on exit 0."""
    if r.code == 0:
        line = _json_line(r.stdout)
        if line is None:
            return Ran(True, " ".join(r.stdout.split()) or "done")
        ok = bool(line.get("ok"))
        summary = str(line.get("summary") or "").strip() or ("done" if ok else "failed, with no reason given")
        facts = {k: v for k, v in line.items() if k not in ("ok", "summary")}
        return Ran(ok, summary, facts, "ok" if ok else "refused")
    if r.code == 124:
        took = f"after {timeout:g} s" if timeout else f"({why(r)})"
        return Ran(False, f"timed out {took}; it may or may not have happened — check before redoing it",
                   kind="timeout")
    if r.code == 1 and not action_run:
        return Ran(False, why(r), kind="refused")
    return Ran(False, f"could not run it: {why(r)}", kind="not_run")


def _json_line(stdout: str) -> dict[str, Any] | None:
    for raw in reversed(stdout.strip().splitlines()):
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and "ok" in obj:
                return obj
    return None
