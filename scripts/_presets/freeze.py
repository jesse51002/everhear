"""Freeze what was hit for a few seconds: it cannot move or attack, blinded if asked, wrapped in a block of ice if asked."""
import itertools
import math
import re
import threading

import mc
from kit import aim, area, loop, world
from actions._types import Field, Literal, Live, Values  # the values class: read on the PC only

VALUES = {
    "freeze_seconds": {"type": "number", "min": 1, "max": 120, "default": 5, "live": True},
    "freeze_shell": {"type": "bool", "default": False, "live": True},
    "freeze_block": {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"],
                     "default": "ice", "live": True},
    "freeze_blindness": {"type": "bool", "default": False, "live": True},
}


class Freeze(Values):
    freeze_seconds: Live[float] = Field(5, ge=1, le=120)
    """how long what is hit cannot move or attack, in seconds (rounded up to whole seconds)"""
    freeze_shell: Live[bool] = False
    """wrap each in a shell of freeze_block that melts when the freeze ends; it only fills air"""
    freeze_block: Live[Literal["ice", "packed_ice", "blue_ice", "snow_block"]] = "ice"
    """the block of the shell; does nothing while freeze_shell is off"""
    freeze_blindness: Live[bool] = False
    """blind them too while frozen"""


def freeze(hits, v):
    """Freeze every hit (a list of aim.Hit, from aim.near_line, around or lock_on) for
    freeze_seconds: it cannot walk or hurt anything, and a mob shows the frost (and takes
    the cold's 1 damage every 2 s, as in powder snow). With freeze_shell, a shell of
    freeze_block round each body, one block wide and two high, that melts when the freeze
    ends; it only fills air, so nothing is replaced (never bedrock, never a chest).
    freeze_blindness blinds it too (blindness IV, as the run's ice beam did). Pets and
    villagers are never frozen. Returns how many were frozen."""
    secs = int(math.ceil(v["freeze_seconds"]))
    tag = "%s_frz%d" % (mc.TAG, next(_FREEZE_N))      # each call tags with its own name
    if not aim.mark(hits, tag):                         # each once, where it was
        aim.untag(tag)
        return 0
    # Slowness and weakness X (amplifier 9; above 127 it is saved as a negative byte) leave
    # no speed and no attack, and wear off by themselves even if the script dies.
    mc.rcon("effect give @e[tag=%s] slowness %d 9 true" % (tag, secs))
    mc.rcon("effect give @e[tag=%s] weakness %d 9 true" % (tag, secs))
    if v["freeze_blindness"]:
        mc.rcon("effect give @e[tag=%s] blindness %d 3 true" % (tag, secs))
    mc.rcon("execute as @e[tag=%s,type=!player] run data merge entity @s {TicksFrozen:%d}" % (tag, 140 + 40 * secs))
    mc.rcon("execute at @e[tag=%s] run particle snowflake ~ ~1 ~ 0.4 0.6 0.4 0.02 25 force" % tag)
    frozen = [world.Pos(float(x), float(y), float(z), world.dim(hits[0].pos)) for x, y, z in _FREEZE_POS.findall(
        mc.rcon("execute as @e[tag=%s] run data get entity @s Pos" % tag))]
    aim.untag(tag)
    if v["freeze_shell"]:
        shells = [_freeze_shell(at, v["freeze_block"]) for at in frozen]
        with _FREEZE_LOCK:
            if not _FREEZE_SHELLS:
                mc.on_stop(lambda: [_freeze_melt(s) for s in list(_FREEZE_SHELLS)])
            _FREEZE_SHELLS.extend(shells)
        loop.background(_freeze_thaw, shells, secs)
    return len(frozen)


_FREEZE_N = itertools.count(1)
_FREEZE_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")
_FREEZE_SHELLS = []         # shells still standing; a stop melts them
_FREEZE_LOCK = threading.Lock()


def _freeze_shell(at, block):
    """A shell of block round the body standing at a place, put into air only: a floor, a roof
    and four walls, leaving the body's own two blocks free. Returns (block, dim, boxes)."""
    x, y, z = (int(math.floor(c)) for c in at[:3])
    boxes = [(x - 1, y - 1, z - 1, x + 1, y - 1, z + 1), (x - 1, y + 2, z - 1, x + 1, y + 2, z + 1),
             (x - 1, y, z - 1, x + 1, y + 1, z - 1), (x - 1, y, z + 1, x + 1, y + 1, z + 1),
             (x - 1, y, z, x - 1, y + 1, z), (x + 1, y, z, x + 1, y + 1, z)]
    dim, loaded = world.dim(at), {}
    for b in boxes:
        area.fill(b, block, "keep", dim=dim, spare_protected=True, keep_bedrock=True, most_mspt=0, loaded=loaded)
    return (block, dim, boxes)


def _freeze_thaw(shells, secs):
    """Melt shells once their freeze is over."""
    if loop.wait(secs):
        for s in shells:
            _freeze_melt(s)


def _freeze_melt(shell):
    """Turn a shell's block back to air, in the shell's own boxes only. Plain fills (a few
    blocks each), because the kit's fill does nothing once the script is stopping."""
    block, dim, boxes = shell
    with _FREEZE_LOCK:
        if shell not in _FREEZE_SHELLS:
            return
        _FREEZE_SHELLS.remove(shell)
    for b in boxes:
        mc.rcon(world.run(dim, "fill %d %d %d %d %d %d air replace %s" % (b + (block,))))
