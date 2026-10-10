"""A horde: waves of zombies or any mix of mobs round each player, so many each, on a timer, a set number of waves or forever, never more than a cap alive at once."""
import random
import time

import mc
from kit import fx, loop, mobs
from actions._types import Field, Live, MobId, Values  # the values class: read on the PC only


class HordeWave(Values):
    horde_mobs: Live[list[MobId]] = ["zombie"]
    """the mobs a wave is made of, each one picked at random from these"""
    horde_per_player: Live[int] = Field(10, ge=1, le=100)
    """how many mobs each wave brings round each player"""
    horde_every: Live[float] = Field(30, ge=1, le=3600)
    """seconds between waves"""
    horde_cap: Live[int] = Field(60, ge=1, le=400)
    """the most of its mobs alive at once; a wave brings fewer, or none, to stay under it"""
    horde_spread: Live[int] = Field(12, ge=4, le=100)
    """how far round each player the mobs appear, in blocks"""
    horde_waves: int = Field(0, ge=0, le=1000)
    """how many waves; after the last it ends once every mob is dead (0: waves until stopped)"""
    horde_explode: Live[bool] = False
    """its creepers blow up; off: their blasts do nothing"""
    horde_announce: Live[bool] = True
    """show "Wave N" on each player's screen at each wave"""


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
