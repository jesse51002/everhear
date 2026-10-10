"""Wherever a player walks (everything I walk on turns to ice): each step on the ground, the player and where they stand."""
import math
import re

import mc
from kit import loop, world

VALUES = {
    "walk_every": {"type": "number", "min": 0.5, "max": 32, "default": 1, "live": True},
}

_WALK_STEPS_POS = re.compile(r"\[(-?[\d.]+(?:[eE]-?\d+)?)d, (-?[\d.]+(?:[eE]-?\d+)?)d, (-?[\d.]+(?:[eE]-?\d+)?)d\]")


def walk_steps(player, v):
    """For each player a player value names ("@a", "Steve,Alex"), yield (name, world.Pos of
    their feet) when they stand on the ground walk_every blocks or more from where they last
    did, a block higher or lower, or in another dimension; and once when first seen. In the
    air (jumping, falling, flying, swimming) nothing comes.

        for p, at in loop(walk_steps(v["player"], v)):
            replace_blocks(under_feet(at, v), v)
    """
    last = {}
    while not mc.stopping():
        for p in loop.who(player):
            reply = mc.rcon("execute as %s if entity @s[nbt={OnGround:1b}] run data get entity @s Pos" % p)
            m = _WALK_STEPS_POS.search(reply)
            if not m:
                continue
            at = world.Pos(float(m.group(1)), float(m.group(2)), float(m.group(3)), world.dim_of(p))
            was = last.get(p)
            if (was is not None and was.dim == at.dim and math.floor(was[1] - 0.01) == math.floor(at[1] - 0.01)
                    and math.hypot(at[0] - was[0], at[2] - was[2]) < v["walk_every"]):
                continue
            last[p] = at
            yield p, at
        loop.wait(0.1)
