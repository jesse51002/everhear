"""The action executor: runs one exact action through `vfg action run` and reports how it went.
Code, no model.

It runs `vfg action run <action> key=value … [as=<name>] --task T<n>` (a list or a dict as
JSON), which prints ONE JSON line (Contracts §8):

    {"ok": bool, "copy": str, "action": str, "running": bool, "summary": str, "facts": {},
     "notes": [], "error": str?}

and says which of five things happened (`Ran.kind`):
- "ok": it ran. A once action answers with its script's result(); one past its timeout with
  what it said so far and "still running as <copy>", `running` true, and it is never run again; an until-stopped one
  once it is ready. vfg's notes (a value clamped) are added to the summary.
- "refused": it ran and Minecraft or the script said no: `ok` false. Only this goes back to
  the action planner, which may change it and retry. A script that found a player offline
  says so in its facts (`offline`), and the pick is dropped with no planner call.
- "partial": `ok` false with `partial` in the facts: it may have acted (RCON dropped
  mid-list, a script failed after it sent a command). Never retried; the summary names what
  ran (`ran`).
- "timeout": vfg itself did not answer in time (exit 124). It may still have happened, so it
  is never retried.
- "not_run": it could not be run at all: exit 2 (a value or a key vfg refused), 3 (the box's
  scripts are not deployed), 4 (SSH down), 5 (RCON down), or no JSON line, or vfg itself
  missing. A retry cannot fix it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .actions import word
from .config import log
from .vfg import Vfg, VfgResult, why

GRACE_S = 5  # past the action's own timeout, which vfg keeps, so vfg's answer is the one seen
NOT_RUN = (2, 3, 4, 5, 127)


Kind = Literal["ok", "refused", "timeout", "not_run", "partial"]


@dataclass
class Ran:
    ok: bool
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    kind: Kind = "ok"
    copy: str = ""           # the copy's name on EC2
    running: bool = False    # it keeps running after this answer


def timeout_for(cls, default: float) -> float:
    return float(getattr(cls, "timeout_s", None) or default)


def argv(name: str, values: dict[str, Any], task_id: str, as_name: str | None = None) -> list[str]:
    """`action run <name> key=value … [as=<name>] --task T7`."""
    return ["action", "run", name, *(f"{k}={word(v)}" for k, v in values.items()),
            *([f"as={as_name}"] if as_name else []), "--task", task_id]


async def execute(name: str, cls, values: dict[str, Any], task_id: str, vfg: Vfg, default_timeout: float, *,
                  as_name: str | None = None, label: str = "") -> Ran:
    timeout = timeout_for(cls, default_timeout)
    t0 = time.perf_counter()
    r = await vfg(*argv(name, values, task_id, as_name), timeout=timeout + GRACE_S)
    ran = read(r, timeout=timeout)
    ms = int((time.perf_counter() - t0) * 1000)
    log("exec", f"{task_id}{label} {name} {ran.kind} (exit {r.code}) {ms} ms: {ran.summary[:300]}")
    return ran


def read(r: VfgResult, *, timeout: float | None = None) -> Ran:
    if r.code == 124:
        took = f"after {timeout:g} s" if timeout else f"({why(r)})"
        return Ran(False, f"timed out {took}; it may or may not have happened — check before redoing it",
                   kind="timeout")
    line = _json_line(r.stdout)
    if line is None or r.code in NOT_RUN:
        if r.code == 0:
            return Ran(True, " ".join(r.stdout.split()) or "done")
        said = str((line or {}).get("error") or (line or {}).get("summary") or "").strip() or why(r)
        return Ran(False, f"could not run it: {said}", kind="not_run")
    facts = line.get("facts") if isinstance(line.get("facts"), dict) else {}
    copy, running = str(line.get("copy") or ""), bool(line.get("running"))
    if line.get("ok"):
        notes = [str(n) for n in line.get("notes") or [] if str(n).strip()]
        summary = str(line.get("summary") or "").strip() or "done"
        if notes:
            summary += f" ({'; '.join(notes)})"
        return Ran(True, summary, facts, "ok", copy, running)
    summary = str(line.get("summary") or line.get("error") or "").strip() or "failed, with no reason given"
    if facts.get("partial"):
        ran = [str(c) for c in facts.get("ran") or []]
        took = f"; these ran and took effect: {'; '.join(ran)}" if ran else ""
        return Ran(False, f"it may have partly happened ({summary}){took} — check before redoing it",
                   facts, "partial", copy, running)
    return Ran(False, summary, facts, "refused", copy, running)


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
