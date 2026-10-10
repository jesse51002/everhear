"""Land players safely at a place: Resistance and Slow Falling first, then on the spot itself when its ground is safe, else spread round it, further out until the ground is safe; a long move sets their spawn point there."""
import math

import mc
from kit import world
from actions._types import Field, Values  # the values class: read on the PC only

VALUES = {
    "land_cushion": {"type": "int", "min": 0, "max": 60, "default": 5},
    "land_ring": {"type": "int", "min": 1, "max": 64, "default": 8},
    "land_tries": {"type": "int", "min": 1, "max": 8, "default": 4},
    "land_spawn_after": {"type": "int", "min": 0, "max": 60000000, "default": 1000},
}


class LandSafely(Values):
    land_cushion: int = Field(5, ge=0, le=60)
    """seconds of Resistance and Slow Falling given before the move, so a bad landing does not hurt (0: none)"""
    land_ring: int = Field(8, ge=1, le=64)
    """how far from the place they may land on the first try, in blocks; each try after it twice as far"""
    land_tries: int = Field(4, ge=1, le=8)
    """how many rings are tried while the ground they land on is unsafe, before they are put back where they were"""
    land_spawn_after: int = Field(1000, ge=0, le=60000000)
    """a move longer than this, in blocks, in one dimension, sets their spawn point where they land (0: never)"""


def land_safely(player, at, v, *, exact=False, top=False, others=()):
    """Move `player` (an online player's name) to `at`, a world.Pos in its dimension, onto safe
    ground, and everyone in `others` onto them. Each gets land_cushion s of Resistance V and
    Slow Falling first. With exact (at's y is where to stand: a saved place, a spawn point, a
    death) they stand on at itself when its ground is solid and harmless and there is room
    (or one block up, on a slab, a carpet or a bed). Else spreadplayers lands them within
    land_ring blocks of it, at or below at's y (top: on the highest ground, at's y not being
    known, below the Nether's roof; at None: round the world spawn), and again twice as far
    while the ground there is magma, cactus, powder snow, a campfire, berries, dripstone,
    water or lava, land_tries rings in all. Whoever moved more than land_spawn_after blocks
    within one dimension gets their spawn point where they landed.

    Returns {"pos": the world.Pos landed on, "ring": its radius (0: the spot itself), "moved":
    [names], "spawn_set": [names]}; None when no ring had safe ground (they are put back where
    they were) or the player went offline."""
    movers = [player] + [p for p in others if p != player]
    start = dict((p, _land_safely_where(p)) for p in movers)
    if start[player] is None:
        mc.log("land_safely: %s is not online" % player)
        return None
    if v["land_cushion"]:
        for p in movers:
            mc.rcon("effect give %s minecraft:resistance %d 4 true" % (p, v["land_cushion"]))
            mc.rcon("effect give %s minecraft:slow_falling %d 0 true" % (p, v["land_cushion"]))
    landed = _land_safely_land(player, at, v, exact, top)
    if landed is None:
        back = start[player]
        if _land_safely_where(player) != back:
            mc.rcon("execute in %s run tp %s %.3f %.3f %.3f" % ((back.dim or world.OVERWORLD, player) + tuple(back)))
        return None
    pos, ring = landed
    moved, spawn_set = [player], []
    for p in movers[1:]:
        out = mc.rcon("tp %s %s" % (p, player))
        if out.startswith("Teleported"):
            moved.append(p)
        else:
            mc.log("land_safely: tp %s %s: %s" % (p, player, out or "no answer"))
    for p in moved:
        if _land_safely_spawn(p, start[p], pos, v):
            spawn_set.append(p)
    return {"pos": pos, "ring": ring, "moved": moved, "spawn_set": spawn_set}


# Ground that hurts or swallows: never landed on. #minecraft:replaceable (air, short grass,
# snow layers...) is room to stand in; water and lava are in it too, so they are refused apart.
_LAND_SAFELY_UNSAFE = ("minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow", "#minecraft:campfires",
                       "minecraft:sweet_berry_bush", "minecraft:pointed_dripstone", "minecraft:water",
                       "minecraft:lava")
_LAND_SAFELY_SWALLOWS = ("minecraft:water", "minecraft:lava", "minecraft:powder_snow")
_LAND_SAFELY_FREE = "#minecraft:replaceable"
_LAND_SAFELY_ROOF = {world.NETHER: 120}       # spreadplayers' `under` in the Nether: below its bedrock roof
_LAND_SAFELY_SKY = 320                        # and anywhere else: the top of the world


def _land_safely_where(p):
    """Where an online player stands, as a world.Pos in their dimension; None when offline."""
    c = mc.pos(p)
    return world.Pos(c[0], c[1], c[2], world.dim_of(p)) if c else None


def _land_safely_land(player, at, v, exact, top):
    """(the world.Pos landed on, the ring), or None."""
    dim = (at.dim if at is not None else None) or world.OVERWORLD
    if exact and at is not None:
        spot = _land_safely_spot(at, dim)
        if spot is not None:
            out = mc.rcon("execute in %s run tp %s %.1f %.1f %.1f" % ((dim, player) + spot))
            if out.startswith("Teleported"):
                return world.Pos(spot[0], spot[1], spot[2], dim), 0
            mc.log("land_safely: tp to the spot itself: %s" % (out or "no answer"))
        else:
            mc.log("land_safely: %d %d %d is not safe to stand on: landing round it" % at.block())
    if at is None:      # the world spawn, where RCON's own commands run
        cmd = "spreadplayers ~ ~ 0 %%d under %d false %s" % (_LAND_SAFELY_SKY, player)
    else:
        under = _LAND_SAFELY_ROOF.get(dim, _LAND_SAFELY_SKY) if top else int(math.floor(at[1])) + 2
        cmd = "execute in %s run spreadplayers %d %d 0 %%d under %d false %s" % (
            dim, math.floor(at[0]), math.floor(at[2]), under, player)
    ring = v["land_ring"]
    for _ in range(v["land_tries"]):
        out = mc.rcon(cmd % ring)
        if out.startswith("Spread "):
            c = mc.pos(player)
            if c is not None and _land_safely_safe(c, dim):
                return world.Pos(c[0], c[1], c[2], dim), ring
            mc.log("land_safely: ring %d: unsafe ground at %s" % (ring, "%d %d %d" % tuple(math.floor(x) for x in c)
                                                                 if c else "an unknown spot"))
        elif out.startswith("Could not spread"):
            mc.log("land_safely: ring %d: no room (%s)" % (ring, out))
        else:
            mc.log("land_safely: spreadplayers: %s" % (out or "no answer"))
            return None
        ring *= 2
    return None


def _land_safely_spot(at, dim):
    """The block centre to stand on at `at`, or one block up (a slab, a carpet, a bed there);
    None when neither has solid ground that does not hurt and room for feet and head."""
    x, y, z = at.block()
    for up in (0, 1):
        check = "execute in %s positioned %d %d %d unless block ~ ~-1 ~ %s" % (dim, x, y + up, z, _LAND_SAFELY_FREE)
        check += "".join(" unless block ~ ~-1 ~ " + b for b in _LAND_SAFELY_UNSAFE)
        for head in ("~", "~1"):
            check += " if block ~ %s ~ %s" % (head, _LAND_SAFELY_FREE)
            check += "".join(" unless block ~ %s ~ %s" % (head, b) for b in _LAND_SAFELY_SWALLOWS)
        if mc.rcon(check).startswith("Test passed"):
            return (x + 0.5, float(y + up), z + 0.5)
    return None


def _land_safely_safe(c, dim):
    """True when a landing stands on nothing that hurts and in nothing that swallows. On 26.3
    a chain of conditions answers "Test passed", or nothing when one fails."""
    check = "execute in %s positioned %.3f %.3f %.3f" % (dim, c[0], c[1], c[2])
    check += "".join(" unless block ~ ~-1 ~ " + b for b in _LAND_SAFELY_UNSAFE)
    check += "".join(" unless block ~ ~ ~ " + b for b in _LAND_SAFELY_SWALLOWS)
    return mc.rcon(check).startswith("Test passed")


def _land_safely_spawn(p, frm, to, v):
    """Set p's spawn point where they landed when they came more than land_spawn_after blocks
    within one dimension. True when it was set."""
    if not v["land_spawn_after"] or frm is None or frm.dim != to.dim:
        return False
    if math.hypot(to[0] - frm[0], to[2] - frm[2]) <= v["land_spawn_after"]:
        return False
    out = mc.rcon("execute in %s run spawnpoint %s %d %d %d" % ((to.dim or world.OVERWORLD, p)
                                                               + tuple(math.floor(x) for x in to)))
    if not out.startswith("Set spawn point"):
        mc.log("land_safely: spawnpoint %s: %s" % (p, out or "no answer"))
        return False
    return True
