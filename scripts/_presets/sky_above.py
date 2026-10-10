"""Random spots in the open sky round players or a place: where rain, falling lava, meteors or a TNT drop start."""
import math
import random

import mc
from kit import aim, loop, world
from actions._types import Field, Live, Values  # the values class: read on the PC only


class SkyAbove(Values):
    sky_radius: Live[float] = Field(14, ge=0, le=128)
    """how far out round each player or the place the spots are, in blocks"""
    sky_height: Live[float] = Field(20, ge=1, le=300)
    """how high over them, in blocks, never above the top of the world"""
    sky_band: Live[float] = Field(0, ge=0, le=100)
    """how much higher or lower than sky_height a spot may be, at random, in blocks (0: all at sky_height)"""
    sky_count: Live[int] = Field(6, ge=1, le=100)
    """how many spots round each player or the place, each time"""
    sky_open: Live[bool] = True
    """only spots in open air: none in a mountain, under a roof or in an unloaded chunk, so fewer may come; off: all"""


def sky_above(at, v):
    """sky_count points round each of `at` (a player's name, a player value such as "@a" or
    "Steve,Alex", or a place), up to sky_radius blocks out, sky_height above them give or
    take sky_band, below the top of the world. With sky_open only the points in open air
    are kept: none in a mountain, a roof or a chunk that is not loaded, so fewer may come
    back. Returns a list of world.Pos."""
    if isinstance(at, str):
        centres = []
        for p in loop.who(at):
            c = mc.pos(p)
            if c:
                centres.append(world.Pos(c[0], c[1], c[2], world.dim_of(p)))
    else:
        centres = [at if isinstance(at, world.Pos) else world.Pos(*at[:3])]
    out = []
    for c in centres:
        top = world.limits(c.dim)[1] - 1
        for _ in range(v["sky_count"]):
            a, d = random.uniform(0, 2 * math.pi), v["sky_radius"] * math.sqrt(random.random())
            y = min(top, c[1] + v["sky_height"] + random.uniform(-v["sky_band"], v["sky_band"]))
            out.append(world.Pos(c[0] + d * math.cos(a), y, c[2] + d * math.sin(a), c.dim))
    if not v["sky_open"]:
        return out
    kept = []
    for dim in set(p.dim for p in out):
        here = [p for p in out if p.dim == dim]
        ok = world.passes(["if block %d %d %d %s" % (p.block() + (aim.OPEN,)) for p in here], dim)
        kept += [p for p, good in zip(here, ok) if good]
    return kept
