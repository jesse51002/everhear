"""The blocks under a player's feet (ice under my feet, a path where I walk): a radius wide and a depth deep, and above them if asked."""
import math

import mc
from kit import world

VALUES = {
    "under_radius": {"type": "int", "min": 0, "max": 32, "default": 2, "live": True},
    "under_depth": {"type": "int", "min": 1, "max": 64, "default": 1, "live": True},
    "under_above": {"type": "int", "min": 0, "max": 64, "default": 0, "live": True},
}


def under_feet(at, v):
    """at: a player's name, or where they stand (a world.Pos, as walk_steps gives). The
    world.Box from the block they stand on (a slab's too) down under_depth blocks,
    under_radius out each way, and under_above blocks up from it: 2 takes in the feet and the
    head, which a rule that leaves air alone never fills. None when a player cannot be read."""
    if isinstance(at, str):
        p = mc.pos(at)
        if not p:
            return None
        at = world.Pos(p[0], p[1], p[2], world.dim_of(at))
    x, y, z = int(math.floor(at[0])), int(math.floor(at[1] - 0.01)), int(math.floor(at[2]))
    r = v["under_radius"]
    return world.Box(x - r, y - v["under_depth"] + 1, z - r, x + r, y + v["under_above"], z + r, dim=world.dim(at))
