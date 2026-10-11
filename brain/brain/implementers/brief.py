"""The brief an implementer starts with: the orchestrator's brief, then a line naming its task and
saying that what it makes takes the task's name, the server's saved places read fresh, and the
EXAMPLES block (examples.py). The places and the block are read at once; whichever fails is logged
and left out, and the brief still goes."""
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


def task_line(name: str) -> str:
    """The task's name, and that the script, the action and the copy it makes are named so too
    (Prototype_Named_Tasks)."""
    return ("YOUR TASK: {0}. Name what you make {0}: vfg script new {0}, its action {0}, and run it as={0}. "
            "Every vfg action run you send takes --task {0}, and a vfg place save --by {0}.".format(name))


async def compose(brief: str, task: str, cfg: ImplementerConfig, run: VfgRunner) -> str:
    """The orchestrator's brief, the task line and the saved places, then the EXAMPLES block for the brief."""
    places, block = await asyncio.gather(saved_places(run), examples.build(cfg, brief, task, run),
                                         return_exceptions=True)
    head = task_line(task)
    if isinstance(places, BaseException):
        log("brief", "%s: the saved places failed, left out: %s" % (task, places))
    else:
        head += "\n" + places
    out = "%s\n\n%s" % (brief.rstrip(), head)
    if isinstance(block, BaseException):
        log("brief", "%s: the EXAMPLES block failed, left out: %s" % (task, block))
        return out
    return "%s\n\n%s" % (out, block)
