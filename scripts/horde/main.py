"""Horde: a hundred zombies, or any mix of mobs, at safe spots round each player at once, in waves if asked; it ends when they are all dead."""
ASKS = ["spawn a horde of zombies, a hundred zombies in a hundred blocks radius of each of us",
        "do not just zombies, also skeletons, wither skeletons, creepers, silverfish, everything",
        "do a thousand mobs on each of us so we are surrounded by mobs everywhere",
        "be careful not to kill each other"]
from kit import values, fx, loop, mobs
import random
import time
import mc

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "horde_mobs":       {"type": "list", "of": "mob", "default": ["zombie"], "live": True},
    "horde_per_player": {"type": "int", "min": 1, "max": 100, "default": 100, "live": True},
    "horde_every":      {"type": "number", "min": 1, "max": 3600, "default": 30, "live": True},
    "horde_cap":        {"type": "int", "min": 1, "max": 400, "default": 300, "live": True},
    "horde_spread":     {"type": "int", "min": 4, "max": 100, "default": 60, "live": True},
    "horde_waves":      {"type": "int", "min": 0, "max": 1000, "default": 1},
    "horde_explode":    {"type": "bool", "default": False, "live": True},
    "horde_announce":   {"type": "bool", "default": True, "live": True},
}


# --- preset: horde_wave (copied from _presets/horde_wave.py) ---
def horde_wave(player, v):
    """Every horde_every s, a wave: horde_per_player mobs, each one of horde_mobs at random,
    at safe spots about horde_spread blocks round each player the player value names, while
    fewer than horde_cap of them are alive. After horde_waves waves (0: until the script
    stops) it waits until every one is dead, and returns how many waves it sent. Creepers
    do not blow up unless horde_explode. horde_announce: "Wave N" on their screens and in the
    log at each wave; off, the log says only when the count alive changed (a zoo, every 1 s)."""
    tag = mc.TAG + "_horde"
    waves, due, said = 0, 0.0, -1
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
            if v["horde_announce"]:
                fx.actionbar(p, "Wave %d" % waves, "red")
        if v["horde_announce"]:
            mc.log("wave", waves, "-", alive, "alive")
        elif alive != said:
            mc.log(alive, "alive")
            said = alive
    return waves


v = values(VALUES)
horde_wave(v["player"], v)      # one wave by default; it ends when the last of them is dead
