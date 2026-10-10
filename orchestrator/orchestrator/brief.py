"""What the runner adds to an implementer's brief as it starts. The guard calls rewrite() once for
every Agent call it allows, and hands the result to the CLI as the call's new prompt. Each step
takes the brief and the Agent tool's input and returns the brief to use; adding a step is one line
in STEPS."""
from typing import Awaitable, Callable

from . import vfg as vfg_module
from .config import log
from .examples import examples
from .vfg import VfgResult, why

# (the brief so far, the Agent tool's input) -> the brief to use
BriefStep = Callable[[str, dict], Awaitable[str]]

# `vfg place list` gets this long; the implementer starts either way.
PLACES_TIMEOUT_MS = 10_000


def places_line(r: VfgResult) -> str:
    """The SAVED PLACES line from `vfg place list`: its lines joined with '; ', none, or why not."""
    if r.code != 0:
        return "SAVED PLACES: unavailable (%s)" % why(r)
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if not lines or lines == ["none"]:
        return "SAVED PLACES: none"
    return "SAVED PLACES: " + "; ".join(lines)


async def saved_places(brief: str, tool_input: dict) -> str:
    """The server's named places, read fresh, so "the castle" in a brief has coordinates."""
    line = places_line(await vfg_module.vfg(["place", "list"], PLACES_TIMEOUT_MS))
    log("brief", line[:300])
    return "%s\n\n%s" % (brief.rstrip(), line)


# saved_places, then the EXAMPLES block (examples.py): the scripts and presets for this job in full,
# this server's first lines and the state of the server.
STEPS: list[BriefStep] = [saved_places, examples]


async def rewrite(brief: str, tool_input: dict) -> str:
    """Runs STEPS in order. A step that raises, or returns no text, is logged and skipped,
    leaving the brief as it was."""
    for step in STEPS:
        name = getattr(step, "__name__", repr(step))
        try:
            out = await step(brief, tool_input)
        except Exception as e:
            log("brief", "%s failed, skipped: %s" % (name, e))
            continue
        if not isinstance(out, str):
            log("brief", "%s gave no text, skipped" % name)
            continue
        brief = out
    return brief
