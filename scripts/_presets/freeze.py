"""Stop what was hit from moving for a few seconds, and wrap it in ice if asked."""
import itertools
import math
import threading

import mc
from kit import area, exclude, loop, world

VALUES = {
    "freeze_seconds": {"type": "number", "min": 1, "max": 120, "default": 5, "live": True},
    "freeze_shell": {"type": "bool", "default": False, "live": True},
    "freeze_block": {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"],
                     "default": "ice", "live": True},
}


def freeze(hits, v):
    """Freeze every hit (a list of aim.Hit, from aim.near_line, around or lock_on) for
    freeze_seconds: it cannot walk or hurt anything, and a mob shows the frost (and takes
    the cold's 1 damage every 2 s, as in powder snow). With freeze_shell, a shell of
    freeze_block round each body, one block wide and two high, that melts when the freeze
    ends; it only fills air, so nothing is replaced (never bedrock, never a chest). Pets and
    villagers are never frozen. Returns how many were frozen."""
    secs = int(math.ceil(v["freeze_seconds"]))
    tag = "%s_frz%d" % (mc.TAG, next(_FREEZE_N))      # each call tags with its own name
    frozen = [h for h in hits if _freeze_mark(h, tag)]
    if not frozen:
        return 0
    # Slowness and weakness X (amplifier 9; above 127 it is saved as a negative byte) leave
    # no speed and no attack, and wear off by themselves even if the script dies.
    mc.rcon("effect give @e[tag=%s] slowness %d 9 true" % (tag, secs))
    mc.rcon("effect give @e[tag=%s] weakness %d 9 true" % (tag, secs))
    mc.rcon("execute as @e[tag=%s,type=!player] run data merge entity @s {TicksFrozen:%d}" % (tag, 140 + 40 * secs))
    mc.rcon("execute at @e[tag=%s] run particle snowflake ~ ~1 ~ 0.4 0.6 0.4 0.02 25 force" % tag)
    mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    if v["freeze_shell"]:
        shells = [_freeze_shell(h.pos, v["freeze_block"]) for h in frozen]
        with _FREEZE_LOCK:
            if not _FREEZE_SHELLS:
                mc.on_stop(lambda: [_freeze_melt(s) for s in list(_FREEZE_SHELLS)])
            _FREEZE_SHELLS.extend(shells)
        loop.background(_freeze_thaw, shells, secs)
    return len(frozen)


_FREEZE_N = itertools.count(1)
_FREEZE_SHELLS = []         # shells still standing; a stop melts them
_FREEZE_LOCK = threading.Lock()


def _freeze_mark(h, tag):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch. True if it was tagged now (a hit listed twice is tagged once)."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1"), tag))
    return reply.startswith("Added tag")


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
