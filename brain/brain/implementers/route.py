"""Runs `vfg route` on an implementer's brief: the engine classes that implementer gets whole, picked
by Jev (or by words when Jev is slow or down), from the library (engine/library/) and this server's
own (made/<server>/content/). examples.py calls it and logs the picks, with the router's own log
line, into the brain's log.

vfg route caps the picks itself, at 3 classes, the one that runs the job first (CONTRACT2, `vfg
route`); trim() holds the brief to the same cap whatever vfg printed, so a brief never grows past it.

`vfg route --task galick_gun_styrk` reads the task's row from the box itself (what the task is, and
the player's line), so only the brief goes on its stdin. It never takes longer than
VFG_ROUTE_TIMEOUT_S, that read included, and writes its own usage row."""
import json

from .config import log
from .vfg import VfgRunner, why

# vfg route's own start (Python, reading every class) on top of VFG_ROUTE_TIMEOUT_S, which counts
# only from its own start.
SLACK_S = 0.75
# Jesse's cap (Prototype_Implementer_Harness), as vfg route's: the classes a brief gives whole.
MOST_CLASSES = 3
# The order a brief takes the router's classes in: those that run the job, then the close ones,
# then those matched by words; within each, the router's order.
VERDICTS = {"runs_it": 0, "close": 1}


class RouteFailed(Exception):
    """vfg route gave no picks: it timed out, failed, or printed something that is not its JSON."""


async def run(brief: str, task: str | None, timeout_s: float, run_vfg: VfgRunner) -> dict:
    """The JSON `vfg route` printed for this brief: {"task", "classes": [{"name", "kind", "source",
    "verdict", "p_runs_it", "p_close", "path"}], "fallback", "ms", "tokens", "held"?, "cut"?,
    "why"?, "unanswered"?}. Its stderr (the log line, any warning) goes to the log. Raises
    RouteFailed with why."""
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
    if not isinstance(out, dict) or not isinstance(out.get("classes"), list):
        raise RouteFailed("vfg route's JSON has no classes")
    return out


def trim(result: dict | None) -> dict | None:
    """The picks as a brief takes them: at most MOST_CLASSES classes, runs_it first, then close,
    then the rest, each in the router's order; what is cut is added to "cut"."""
    if result is None:
        return None
    out = dict(result)
    classes = sorted((c for c in result.get("classes") or [] if isinstance(c, dict)),
                     key=lambda c: VERDICTS.get(c.get("verdict"), 2))
    out["classes"] = classes[:MOST_CLASSES]
    got = result.get("cut")          # vfg route's: the names past its cap, best first
    cut = [str(x) for x in got] if isinstance(got, list) else \
        [str(x) for v in got.values() if isinstance(v, list) for x in v] if isinstance(got, dict) else []
    if len(classes) > MOST_CLASSES:
        cut = [c.get("name") for c in classes[MOST_CLASSES:]] + cut
    if cut:
        out["cut"] = cut
    return out


def summary(result: dict | None, failed: str | None = None) -> dict:
    """What the log keeps of one routing: the picks without their paths, and how they were made."""
    if result is None:
        return {"failed": failed or "no answer"}
    keep = {"classes": [{k: c.get(k) for k in ("name", "kind", "source", "verdict", "p_runs_it", "p_close")}
                        for c in result.get("classes") or [] if isinstance(c, dict)],
            "fallback": bool(result.get("fallback")), "ms": result.get("ms"), "tokens": result.get("tokens")}
    for k in ("held", "cut", "why", "unanswered"):
        if result.get(k):
            keep[k] = result[k]
    return keep
