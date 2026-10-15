"""The brief an implementer starts with: the brain's brief, then a line naming its task and saying
that what it makes takes the task's name, the server's saved places read fresh (`vfg engine places
--json`: the engine's own), and the EXAMPLES block (examples.py). The places and the block are read
at once; whichever fails is logged and left out, and the brief still goes. One log line per brief
says each part's size in characters:

    brief frost_staff_steve: 21204 chars: the brain's brief 812, task and places 260, header 905,
    made 310, classes 17033 (2), tests 1210 (1), named 0 (0); route jev, state ok, instances ok, 1320 ms"""
import asyncio

from . import examples
from .config import ImplementerConfig, log
from .vfg import VfgRunner, engine_read

# `vfg engine places` gets this long; the implementer starts either way.
PLACES_TIMEOUT_S = 10.0


def place_line(p: dict) -> str:
    """`castle 197 68 251 overworld (castle_steve)`: a place's name, where, its dimension and who saved it."""
    pos = " ".join(examples.coord(c) for c in p.get("pos") or []) or "?"
    dim = str(p.get("dim") or "?")
    dim = dim[len("minecraft:"):] if dim.startswith("minecraft:") else dim
    return "%s %s %s%s" % (p.get("name"), pos, dim, " (%s)" % p["by"] if p.get("by") else "")


def places_line(data: dict | None, failed: str | None = None) -> str:
    """The SAVED PLACES line from `vfg engine places --json`'s reply: each place, joined with '; ',
    none, or why not."""
    if data is None:
        return "SAVED PLACES: unavailable (%s)" % (failed or "no answer")
    rows = [p for p in data.get("places") or [] if isinstance(p, dict) and p.get("name")]
    return "SAVED PLACES: " + ("; ".join(place_line(p) for p in rows) if rows else "none")


async def saved_places(run: VfgRunner) -> str:
    """The server's named places, read fresh, so "the castle" in a brief has coordinates."""
    line = places_line(*await engine_read(run, "places", timeout=PLACES_TIMEOUT_S))
    log("brief", line[:300])
    return line


def sizes_line(task: str, total: int, brain: int, head: int, sizes: dict | None) -> str:
    """The brief's parts in characters, in its order (examples.compose names the block's)."""
    parts = ["the brain's brief %d" % brain, "task and places %d" % head]
    if not sizes:
        parts.append("no EXAMPLES block")
        return "%s: %d chars: %s" % (task, total, ", ".join(parts))
    parts += ["header %d" % sizes.get("header", 0), "made %d" % sizes.get("made", 0)]
    parts += ["%s %d (%d)" % (k, sizes.get(k, 0), sizes.get(k + "_n", 0)) for k in ("classes", "tests", "named")]
    return "%s: %d chars: %s; route %s, state %s, instances %s, %s ms" % (
        task, total, ", ".join(parts), sizes.get("route", "?"), sizes.get("state", "?"), sizes.get("instances", "?"),
        sizes.get("ms", "?"))


def task_line(name: str, server: str) -> str:
    """The task's name, and that the class it makes, its test and its bindings are named by it
    (Prototype_Named_Tasks)."""
    return ("YOUR TASK: {0}. Name what you make {0}: your class made/{1}/content/{0}.py (another it needs, "
            "{0}_<what>), its test made/{1}/tests/{0}.py. Every vfg engine bind you send takes --by {0}."
            .format(name, server))


async def compose(brief: str, task: str, cfg: ImplementerConfig, run: VfgRunner) -> str:
    """The brain's brief, the task line and the saved places, then the EXAMPLES block for the brief."""
    sizes: dict = {}
    places, block = await asyncio.gather(saved_places(run), examples.build(cfg, brief, task, run, sizes=sizes),
                                         return_exceptions=True)
    head = task_line(task, cfg.server)
    if isinstance(places, BaseException):
        log("brief", "%s: the saved places failed, left out: %s" % (task, places))
    else:
        head += "\n" + places
    out = "%s\n\n%s" % (brief.rstrip(), head)
    if isinstance(block, BaseException):
        log("brief", "%s: the EXAMPLES block failed, left out: %s" % (task, block))
        block, sizes = "", {}
    else:
        out = "%s\n\n%s" % (out, block)
    # the task line and the places count the blank lines between the parts
    log("brief", sizes_line(task, len(out), len(brief.rstrip()), len(out) - len(brief.rstrip()) - len(block), sizes))
    return out
