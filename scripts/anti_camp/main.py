"""Anti-Camp: a player who stays within a few blocks of one spot too long is warned, then TNT falls on them."""
ASKS = ["punish anyone who camps", "TNT on anyone who stands still too long", "no hiding in one spot"]
from kit import values, fx, loop, world
import json
import math
import random
import time
import mc

VALUES = {
    "player":         {"type": "player", "default": "@a"},
    "stands_radius":  {"type": "number", "min": 0.5, "max": 64, "default": 4, "live": True},
    "stands_seconds": {"type": "number", "min": 6, "max": 3600, "default": 15, "live": True},
    "stands_warning": {"type": "str", "default": "MOVE. The Demon Lord sees you hiding...", "live": True},
    "tnt":            {"type": "int", "min": 1, "max": 30, "default": 7, "live": True},
    "height":         {"type": "int", "min": 2, "max": 60, "default": 12, "live": True},
    "fuse":           {"type": "number", "min": 0.5, "max": 10, "default": 2, "live": True},
    "mock":           {"type": "bool", "default": True, "live": True},
}


# --- preset: stands_still (copied from _presets/stands_still.py) ---
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


v = values(VALUES)
# Where the TNT falls round the camper: a plus and two corners, then at random.
SPOTS = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2)]

mc.log("campers get TNT after", v["stands_seconds"], "s")
for p in loop(stands_still(v["player"], v)):
    if v["mock"]:
        mc.rcon("tellraw @a " + json.dumps([{"text": "[Demon Lord] ", "color": "dark_red", "bold": True},
                                            {"text": "%s thinks standing still is safe." % p, "color": "red",
                                             "bold": False}]))
    for i in range(v["tnt"]):
        dx, dz = SPOTS[i] if i < len(SPOTS) else (random.randint(-3, 3), random.randint(-3, 3))
        mc.rcon('execute at %s run summon tnt ~%d ~%d ~%d {fuse:%d,Tags:["vfg","%s"]}'
                % (p, dx, v["height"], dz, int(v["fuse"] * 20), mc.TAG))
    mc.log("TNT on", p)
