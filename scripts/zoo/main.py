"""Zoo: random animals keep coming round each player, up to a most alive at once."""
ASKS = ["spawn animals around me", "a zoo", "random animals everywhere"]
from kit import values, loop, mobs
import random
import time
import mc

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "horde_mobs":       {"type": "list", "of": "mob", "live": True,
                         "default": ["pig", "cow", "sheep", "chicken", "rabbit", "fox", "goat", "llama", "cat", "wolf",
                                     "parrot", "frog", "turtle", "axolotl", "panda", "horse", "donkey", "bee",
                                     "armadillo", "camel"]},
    "horde_per_player": {"type": "int", "min": 1, "max": 100, "default": 20, "live": True},
    "horde_every":      {"type": "number", "min": 1, "max": 3600, "default": 1, "live": True},
    "horde_cap":        {"type": "int", "min": 1, "max": 400, "default": 200, "live": True},
    "horde_spread":     {"type": "int", "min": 4, "max": 100, "default": 8, "live": True},
    "horde_waves":      {"type": "int", "min": 0, "max": 1000, "default": 0},
    "horde_explode":    {"type": "bool", "default": False, "live": True},
}


# --- preset: horde_wave (copied from _presets/horde_wave.py) ---
def horde_wave(player, v):
    """Every horde_every s, a wave: horde_per_player mobs, each one of horde_mobs at random,
    at safe spots about horde_spread blocks round each player the player value names, while
    fewer than horde_cap of them are alive. After horde_waves waves (0: until the script
    stops) it waits until every one is dead, and returns how many waves it sent. Creepers
    do not blow up unless horde_explode."""
    tag = mc.TAG + "_horde"
    waves, due, last = 0, 0.0, -1
    for _ in loop(every=1):
        alive = mobs.count(tag)
        if v["horde_waves"] and waves >= v["horde_waves"]:
            if alive == 0:
                break
            continue
        if time.time() < due:
            continue
        waves, due = waves + 1, time.time() + v["horde_every"]
        kinds = list(v["horde_mobs"]) or ["zombie"]
        for p in loop.who(player):
            n = min(v["horde_per_player"], v["horde_cap"] - alive)
            if n <= 0:
                break
            for _ in range(n):
                mob = random.choice(kinds)
                safe = "creeper" in mob and not v["horde_explode"]
                alive += mobs.around(p, mob, 1, "ExplosionRadius:0b" if safe else "",
                                     spread=v["horde_spread"], tags=(tag,))
        # A zoo has no waves to announce: it only says when it grew.
        if alive != last:
            mc.log(alive, "alive")
            last = alive
    return waves


v = values(VALUES)
mc.log("a zoo round", v["player"], "of at most", v["horde_cap"])
horde_wave(v["player"], v)
