"""Runs `vfg route` on an implementer's brief, from the guard: the example scripts and presets
that implementer gets in full, picked by Jev (or by words when Jev is slow or down). examples.py
calls it; the picks go into the task's record in state.json, beside the implementer they went to,
and the router's own log line into the runner's log.

`vfg route --task T7` reads the task's text and context from state.json itself, so only the brief
goes on its stdin. It never takes longer than VFG_ROUTE_TIMEOUT_S, and writes its own usage row."""
import json
from typing import Awaitable, Callable

from . import vfg as vfg_module
from .config import iso, log, now_ms
from .tasks import Store
from .vfg import VfgResult, why

# vfg route's own start (Python, reading every card) on top of VFG_ROUTE_TIMEOUT_S, which counts
# only from its own start.
SLACK_S = 0.75

# (args, timeout in ms, stdin) -> the result; vfg_module.vfg, or a test's.
Runner = Callable[..., Awaitable[VfgResult]]


class RouteFailed(Exception):
    """vfg route gave no picks: it timed out, failed, or printed something that is not its JSON."""


def _runner() -> Runner:
    return vfg_module.vfg  # looked up at each call, so a test can swap the module's function


async def run(brief: str, task: str | None, timeout_s: float, runner: Runner | None = None) -> dict:
    """The JSON `vfg route` printed for this brief: {"task", "scripts", "presets", "fallback", "ms",
    "tokens", "why"?, "unanswered"?}. Its stderr (the log line, any warning) goes to the runner's log.
    Raises RouteFailed with why."""
    args = ["route"] + (["--task", task] if task else [])
    r = await (runner or _runner())(args, (timeout_s + SLACK_S) * 1000, brief)
    for line in r.stderr.splitlines():
        if line.strip():
            log("route", line.strip()[:500])
    if r.code != 0:
        raise RouteFailed(why(r))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RouteFailed("vfg route printed something that is not JSON: %s" % r.stdout.strip()[:120])
    if not isinstance(out, dict) or not isinstance(out.get("scripts"), list) or not isinstance(out.get("presets"), list):
        raise RouteFailed("vfg route's JSON has no scripts and presets")
    return out


def summary(result: dict | None, failed: str | None = None) -> dict:
    """What state.json keeps of one routing: the picks without their paths, and how they were made."""
    if result is None:
        return {"failed": failed or "no answer"}
    keep = {"scripts": [{k: s.get(k) for k in ("name", "source", "verdict", "p_runs_it", "p_close")}
                        for s in result.get("scripts") or []],
            "presets": [{k: p.get(k) for k in ("name", "source", "p_use")} for p in result.get("presets") or []],
            "fallback": bool(result.get("fallback")), "ms": result.get("ms"), "tokens": result.get("tokens")}
    for k in ("why", "unanswered"):
        if result.get(k):
            keep[k] = result[k]
    return keep


def record(store: Store | None, task: str | None, description: str, entry: dict) -> None:
    """Appends one implementer's entry ({"description", "at", "route", "loaded", ...}) to its
    task's record and saves state.json. Nothing happens for a task the store does not know."""
    if store is None or not task:
        return
    t = store.get(task)
    if t is None:
        return
    t.implementers.append({"description": description, "at": iso(now_ms()), **entry})
    store.save()
