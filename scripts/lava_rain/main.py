"""Lava Rain: drops of lava and flame fall out of the sky round every player, only to look at: no blocks, no fire, no harm."""
ASKS = ["if you can make it rain lava, like lava particles over the world, that'd be even better",
        "make it rain lava", "lava particles falling over all of us", "more lava rain", "remove the lava rain"]
from kit import values, result, aim, fx, loop, world
import math
import random
import mc


# --- preset: sky_above (copied from _presets/sky_above.py) ---
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


v = values()
# Every `every` s, sky_count spots in the open sky round each player the player value names
# (sky_radius out, sky_height above them give or take sky_band), and at each a puff of drops
# of one of `particles`, picked at random, spread 4 blocks wide. Until it is stopped.
result("lava rain on %s: %d puffs of %d drops round each, every %g s" % (v["player"], v["sky_count"], v["drops"],
                                                                        v["every"]), player=v["player"])
for _ in loop(every=v["every"]):
    for at in sky_above(v["player"], v):
        fx.particle(random.choice(v["particles"] or ["falling_lava"]), at, spread=(4, 1, 4), count=v["drops"])
