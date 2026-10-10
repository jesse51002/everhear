"""Strike lightning at a place or a player: one bolt or many, scattered or in a ring, at once or one by one, never on the one who called it."""
import math
import random

import mc
from kit import loop, world
from actions._types import Field, Literal, Live, Player, Values  # the values class: read on the PC only

VALUES = {
    "lightning_count": {"type": "int", "min": 1, "max": 50, "default": 1, "live": True},
    "lightning_spread": {"type": "number", "min": 0, "max": 32, "default": 3, "live": True},
    "lightning_layout": {"type": "enum", "of": ["scatter", "ring"], "default": "scatter", "live": True},
    "lightning_every": {"type": "number", "min": 0, "max": 5, "default": 0, "live": True},
    "lightning_spare": {"type": "list", "of": "player", "default": [], "live": True},
}


class Lightning(Values):
    lightning_count: Live[int] = Field(1, ge=1, le=50)
    """how many bolts: the first at the place, the rest round it"""
    lightning_spread: Live[float] = Field(3, ge=0, le=32)
    """how far round the place the other bolts strike, in blocks"""
    lightning_layout: Live[Literal["scatter", "ring"]] = "scatter"
    """scatter: the other bolts anywhere within lightning_spread; ring: evenly round at lightning_spread (5 make a plus)"""
    lightning_every: Live[float] = Field(0, ge=0, le=5)
    """seconds between bolts (0: all at once)"""
    lightning_spare: Live[list[Player]] = []
    """players no bolt strikes within 4 blocks of; the one who called it is always spared"""


def lightning(at, by, v):
    """Strike lightning_count bolts: the first at the place (or a player's name: where they
    stand), the rest within lightning_spread blocks of it, scattered at random or evenly in
    a ring ("ring" with 5 is a plus), lightning_every seconds apart (0: all at once). A bolt
    hurts and sets fire to what is within about 3 blocks, so none lands within 4 blocks of
    by (the player who called it, or "") or of a player in lightning_spare. Returns how many
    struck."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    n, s = v["lightning_count"], v["lightning_spread"]
    points = [tuple(at[:3])]
    for i in range(1, n):
        if v["lightning_layout"] == "ring":
            a, d = 2 * math.pi * (i - 1) / (n - 1), s
        else:
            a, d = random.uniform(0, 2 * math.pi), random.uniform(min(1.0, s), s)
        points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    keep = "".join("unless entity @a[name=%s,distance=..4] " % mc.bare(p) for p in [by] + list(v["lightning_spare"])
                   if p and not p.startswith("@"))
    struck = 0
    for i, (x, y, z) in enumerate(points):
        if i and v["lightning_every"] and not loop.wait(v["lightning_every"]):
            break
        reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %srun summon lightning_bolt ~ ~ ~ "
                        "{Tags:[\"vfg\",\"%s\"]}" % (x, y, z, keep, mc.TAG))
        struck += 1 if "Summoned" in reply else 0
    return struck
