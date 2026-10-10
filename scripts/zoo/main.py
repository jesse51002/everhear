"""Zoo: random animals keep coming round each player, up to a most alive at once."""
ASKS = ["spawn animals around me", "a zoo", "random animals everywhere"]
from kit import values, result, loop, mobs, fx
import random
import time
import mc


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


v = values()
result("a zoo round %s: %d animals each, never more than %d alive"
       % (v["player"], v["horde_per_player"], v["horde_cap"]), player=v["player"],
       per_player=v["horde_per_player"], cap=v["horde_cap"], animals=list(v["horde_mobs"]))
horde_wave(v["player"], v)
