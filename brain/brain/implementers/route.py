"""Runs `vfg route` on an implementer's brief: the example scripts, presets and actions that
implementer gets in full, picked by Jev (or by words when Jev is slow or down). examples.py calls
it and logs the picks, with the router's own log line, into the brain's log.

vfg route caps the picks itself, at 3 scripts and 7 actions; trim() holds the brief to the same
caps whatever vfg printed, so a brief never grows past them.

`vfg route --task galick_gun_styrk` reads the task's row from the box itself (what the task is, and
the player's line), so only the brief goes on its stdin. It never takes longer than
VFG_ROUTE_TIMEOUT_S, that read included, and writes its own usage row."""
import json

from .config import log
from .vfg import VfgRunner, why

# vfg route's own start (Python, reading every card) on top of VFG_ROUTE_TIMEOUT_S, which counts
# only from its own start.
SLACK_S = 0.75
# Jesse's caps (Prototype_Implementer_Harness), as toolkit/lib/route.py's MOST_SCRIPTS and
# MOST_ACTIONS: the router's scripts a brief gives whole, and the actions it gives in full.
MOST_SCRIPTS = 3
MOST_ACTIONS = 7
# The order a brief takes the router's scripts in: those that run the job, then the close ones,
# then those matched by words; within each, the router's order.
VERDICTS = {"runs_it": 0, "close": 1}


class RouteFailed(Exception):
    """vfg route gave no picks: it timed out, failed, or printed something that is not its JSON."""


async def run(brief: str, task: str | None, timeout_s: float, run_vfg: VfgRunner) -> dict:
    """The JSON `vfg route` printed for this brief: {"task", "scripts", "presets", "actions", "fallback",
    "ms", "tokens", "held"?, "cut"?, "why"?, "unanswered"?}; an older vfg's has no "actions". Its stderr (the log line, any warning) goes to the log.
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


def trim(result: dict | None) -> dict | None:
    """The picks as a brief takes them: at most MOST_SCRIPTS scripts, runs_it first, and at most
    MOST_ACTIONS actions, in the router's order. A preset held by a script cut here is loaded itself,
    as vfg route does; what is cut is added to "cut"."""
    if result is None:
        return None
    out = dict(result)
    scripts = sorted(result.get("scripts") or [], key=lambda s: VERDICTS.get(s.get("verdict"), 2))
    out["scripts"] = scripts[:MOST_SCRIPTS]
    cut = {k: list(v) for k, v in (result.get("cut") or {}).items() if isinstance(v, list)}
    if len(scripts) > MOST_SCRIPTS:
        cut["scripts"] = [s.get("name") for s in scripts[MOST_SCRIPTS:]] + cut.get("scripts", [])
    if isinstance(result.get("actions"), list):
        out["actions"] = result["actions"][:MOST_ACTIONS]
        if len(result["actions"]) > MOST_ACTIONS:
            cut["actions"] = [a.get("name") for a in result["actions"][MOST_ACTIONS:]] + cut.get("actions", [])
    if cut:
        out["cut"] = cut
    loaded = {s.get("name") for s in out["scripts"]}
    held = [p for p in result.get("held") or [] if isinstance(p, dict)]
    out["presets"] = list(result.get("presets") or []) + [
        {k: v for k, v in p.items() if k != "in"} for p in held if p.get("in") not in loaded]
    kept = [p for p in held if p.get("in") in loaded]
    if kept:
        out["held"] = kept
    else:
        out.pop("held", None)
    return out


def summary(result: dict | None, failed: str | None = None) -> dict:
    """What the log keeps of one routing: the picks without their paths, and how they were made."""
    if result is None:
        return {"failed": failed or "no answer"}
    keep = {"scripts": [{k: s.get(k) for k in ("name", "source", "verdict", "p_runs_it", "p_close")}
                        for s in result.get("scripts") or []],
            "presets": [{k: p.get(k) for k in ("name", "source", "p_use")} for p in result.get("presets") or []],
            "fallback": bool(result.get("fallback")), "ms": result.get("ms"), "tokens": result.get("tokens")}
    if isinstance(result.get("actions"), list):
        keep["actions"] = [{k: a.get(k) for k in ("name", "source", "p_needed")} for a in result["actions"]]
    if result.get("held"):  # picked, and already in a loaded script ("in"): not loaded again
        keep["held"] = [{k: p.get(k) for k in ("name", "source", "p_use", "in")} for p in result["held"]]
    for k in ("cut", "why", "unanswered"):
        if result.get(k):
            keep[k] = result[k]
    return keep
