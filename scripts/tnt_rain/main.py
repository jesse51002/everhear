"""TNT Rain: primed TNT falls from the sky round every player, or the ones named, a volley every few seconds."""
ASKS = ["rain TNT on us", "TNT falling from the sky", "make it rain TNT"]
from kit import values, aim, loop, world
import math
import random
import mc

VALUES = {
    "player":     {"type": "player", "default": "@a"},
    "every":      {"type": "number", "min": 1, "max": 3600, "default": 20, "live": True},
    "fuse":       {"type": "number", "min": 0.5, "max": 10, "default": 4, "live": True},
    "sky_radius": {"type": "number", "min": 0, "max": 128, "default": 7, "live": True},
    "sky_height": {"type": "number", "min": 1, "max": 300, "default": 25, "live": True},
    "sky_band":   {"type": "number", "min": 0, "max": 100, "default": 0, "live": True},
    "sky_count":  {"type": "int", "min": 1, "max": 40, "default": 10, "live": True},
    "sky_open":   {"type": "bool", "default": True, "live": True},
}


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


v = values(VALUES)
mc.log("every", v["every"], "s,", v["sky_count"], "TNT each, on", v["player"])
for _ in loop(every=v["every"]):
    points = sky_above(v["player"], v)
    for at in points:
        mc.rcon(world.run(at.dim, 'summon tnt %.1f %.1f %.1f {fuse:%d,Tags:["vfg","%s"]}'
                          % (at[0], at[1], at[2], int(v["fuse"] * 20), mc.TAG)))
    if points:
        mc.log("dropped", len(points), "TNT")
