"""Runs `vfg route` on an implementer's brief: the example scripts and presets that implementer
gets in full, picked by Jev (or by words when Jev is slow or down). examples.py calls it and logs
the picks, with the router's own log line, into the brain's log.

`vfg route --task T7` reads the task's words and the player's line from the brain's tasks.json
itself, so only the brief goes on its stdin. It never takes longer than VFG_ROUTE_TIMEOUT_S, and
writes its own usage row."""
import json

from .config import log
from .vfg import VfgRunner, why

# vfg route's own start (Python, reading every card) on top of VFG_ROUTE_TIMEOUT_S, which counts
# only from its own start.
SLACK_S = 0.75


class RouteFailed(Exception):
    """vfg route gave no picks: it timed out, failed, or printed something that is not its JSON."""


async def run(brief: str, task: str | None, timeout_s: float, run_vfg: VfgRunner) -> dict:
    """The JSON `vfg route` printed for this brief: {"task", "scripts", "presets", "fallback", "ms",
    "tokens", "held"?, "why"?, "unanswered"?}. Its stderr (the log line, any warning) goes to the log.
    Raises RouteFailed with why."""
    args = ["route"] + (["--task", task] if task else [])
    r = await run_vfg(*args, timeout=timeout_s + SLACK_S, stdin=brief)
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
    """What the log keeps of one routing: the picks without their paths, and how they were made."""
    if result is None:
        return {"failed": failed or "no answer"}
    keep = {"scripts": [{k: s.get(k) for k in ("name", "source", "verdict", "p_runs_it", "p_close")}
                        for s in result.get("scripts") or []],
            "presets": [{k: p.get(k) for k in ("name", "source", "p_use")} for p in result.get("presets") or []],
            "fallback": bool(result.get("fallback")), "ms": result.get("ms"), "tokens": result.get("tokens")}
    if result.get("held"):  # picked, and already in a loaded script ("in"): not loaded again
        keep["held"] = [{k: p.get(k) for k in ("name", "source", "p_use", "in")} for p in result["held"]]
    for k in ("why", "unanswered"):
        if result.get(k):
            keep[k] = result[k]
    return keep
