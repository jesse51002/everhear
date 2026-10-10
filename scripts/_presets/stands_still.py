"""When a player camps: stays within a few blocks of one spot for too long (anti-camping), with a warning first."""
import math
import time

import mc
from kit import fx, loop, world
from actions._types import Field, Live, Values  # the values class: read on the PC only

VALUES = {
    "stands_radius":  {"type": "number", "min": 0.5, "max": 64, "default": 4, "live": True},
    "stands_seconds": {"type": "number", "min": 1, "max": 3600, "default": 15, "live": True},
    "stands_warning": {"type": "str", "default": "Move!", "live": True},
}


class StandsStill(Values):
    stands_radius: Live[float] = Field(4, ge=0.5, le=64)
    """how far a player may move and still count as staying put, in blocks across the ground"""
    stands_seconds: Live[float] = Field(15, ge=1, le=3600)
    """how long a player may stay put, in seconds, before it fires; then it counts again"""
    stands_warning: Live[str] = "Move!"
    """the warning on their screen in the last 5 s, with the seconds left (empty: none)"""


def stands_still(player, v):
    """A generator: a player's name each time they have stayed within stands_radius blocks
    (flat) of one spot for stands_seconds s; then it counts again from where they stand.
    In the last 5 s their actionbar shows stands_warning and the seconds left ("" for none)."""
    spot = {}
    for _ in loop(every=0.5):
        for p in loop.who(player):
            at, d, now = mc.pos(p), world.dim_of(p), time.time()
            if at is None:
                continue
            s = spot.get(p)
            if s is None or s[3] != d or math.hypot(at[0] - s[0], at[2] - s[2]) > v["stands_radius"]:
                spot[p] = (at[0], at[1], at[2], d, now)
                continue
            left = s[4] + v["stands_seconds"] - now
            if left <= 0:
                spot[p] = (at[0], at[1], at[2], d, now)
                yield p
            elif left <= 5 and v["stands_warning"]:
                fx.actionbar(p, "%s %d" % (v["stands_warning"], math.ceil(left)), "red")
