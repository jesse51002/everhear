"""The brief an implementer starts with: the brain's brief, then a line naming its task and
saying that what it makes takes the task's name, the server's saved places read fresh, and the
EXAMPLES block (examples.py). The places and the block are read at once; whichever fails is logged
and left out, and the brief still goes. One log line per brief says each part's size in
characters:

    brief ice_beam_steve: 31204 chars: the brain's brief 812, task and places 260, header 905,
    made 310, actions 9120 (5 in full, by Jev), made actions 402 (3), scripts 17033 (2), tests 1210 (1),
    presets 2362 (1), named 0 (0), running 0 (0); route jev, state ok, 1320 ms"""
import asyncio
import re

from . import examples
from .config import ImplementerConfig, log
from .vfg import VfgResult, VfgRunner, why

# `vfg place list` gets this long; the implementer starts either way.
PLACES_TIMEOUT_S = 10.0


# When this vfg has no `place` command, the brief says so, so an implementer told to save a place
# does not try.
PLACES_OFF = "SAVED PLACES: off (this vfg has no place command): save no place, whatever the brief says; PLACE none."


def no_place_command(r: VfgResult) -> bool:
    """vfg's argparse refused `place` as a command it does not have."""
    return r.code == 2 and re.search(r"invalid choice: '?place'?", r.stderr) is not None


def places_line(r: VfgResult) -> str:
    """The SAVED PLACES line from `vfg place list`: its lines joined with '; ', none, off when vfg has no
    place command, or why not."""
    if r.code != 0:
        return PLACES_OFF if no_place_command(r) else "SAVED PLACES: unavailable (%s)" % why(r)
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if not lines or lines == ["none"]:
        return "SAVED PLACES: none"
    return "SAVED PLACES: " + "; ".join(lines)


async def saved_places(run: VfgRunner) -> str:
    """The server's named places, read fresh, so "the castle" in a brief has coordinates."""
    line = places_line(await run("place", "list", timeout=PLACES_TIMEOUT_S))
    log("brief", line[:300])
    return line


def sizes_line(task: str, total: int, brain: int, head: int, sizes: dict | None) -> str:
    """The brief's parts in characters, in its order (examples.compose names the block's)."""
    parts = ["the brain's brief %d" % brain, "task and places %d" % head]
    if not sizes:
        parts.append("no EXAMPLES block")
        return "%s: %d chars: %s" % (task, total, ", ".join(parts))
    n = lambda k: sizes.get(k + "_n", 0)  # noqa: E731
    parts += ["header %d" % sizes.get("header", 0), "made %d" % sizes.get("made", 0),
              "actions %d (%d in full%s)" % (sizes.get("actions", 0), n("actions"),
                                             ", " + sizes["actions_how"] if sizes.get("actions_how") else ""),
              "made actions %d (%d)" % (sizes.get("made_actions", 0), n("made_actions"))]
    parts += ["%s %d (%d)" % (k, sizes.get(k, 0), n(k)) for k in ("scripts", "tests", "presets", "named", "running")]
    return "%s: %d chars: %s; route %s, state %s, %s ms" % (
        task, total, ", ".join(parts), sizes.get("route", "?"), sizes.get("state", "?"), sizes.get("ms", "?"))


def task_line(name: str) -> str:
    """The task's name, and that the script, the action and the copy it makes are named so too
    (Prototype_Named_Tasks)."""
    return ("YOUR TASK: {0}. Name what you make {0}: vfg script new {0}, its action {0}, and run it as={0}. "
            "Every vfg action run you send takes --task {0}, and a vfg place save --by {0}.".format(name))


async def compose(brief: str, task: str, cfg: ImplementerConfig, run: VfgRunner) -> str:
    """The brain's brief, the task line and the saved places, then the EXAMPLES block for the brief."""
    sizes: dict = {}
    places, block = await asyncio.gather(saved_places(run), examples.build(cfg, brief, task, run, sizes=sizes),
                                         return_exceptions=True)
    head = task_line(task)
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
