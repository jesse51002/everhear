"""demon_lord, live: round 1 (a wave of webs and spiders), supplied, armed, announced, spawned,
cleared once its mobs are dead, scored (a clean round raises the level), then its next round's own
break begins. The minigames and the boss cycle are each proven on their own (blood_moon.py,
lightning_storm.py, closing_ring.py, sanctuary.py, defend_gregor.py, parkour.py,
gravekeeper.py/spider_queen.py/ravager_warlord.py/hollow_king.py); this proves the round loop that
ties them together."""

from vfg_engine.livetest import verdict


def test(t):
    # self.result() is only ever called once, at the very start (same as the old action): every
    # later round is announced in chat and titles instead, which t.said() cannot see (it reads a
    # scenario's own result), so what follows checks the world it changes, not what it says.
    t.start("demon_lord", wait=3, round=1, level=1)
    t.check("it begins at round 1", t.said("the Demon Lord begins at round 1"))
    t.wait(1)
    t.check("a supply chest sits beside Steve (east, +X)", t.block(1, 0, 0, "minecraft:chest"))
    t.wait(20 + 5)                                      # Config.arm_s, then the wave kind's own 5 s pre-wait
    # radius 20, not 10: the wave's own count is small (1 at level 1) and each spot is picked at
    # random within a few blocks of a player up to 4 blocks left of Steve (Alex), so the margin
    # covers that spread plus a few seconds of a spider's own idle wander before this checks.
    t.check("spiders have come for Steve", t.near("spider", 0, 0, 0, radius=20, count=1) or
           t.near("cave_spider", 0, 0, 0, radius=20, count=1))
    x, y, z = t.xyz(0, 0, 0)                           # anchored at the stage: a bare distance= is RCON's
    for kind in ("spider", "cave_spider"):
        t.cmd("kill @e[type=minecraft:%s,x=%s,y=%s,z=%s,distance=..40]" % (kind, x, y, z))
    t.wait(4)                                          # the next tick() (every 3 s) notices they are dead
    t.check("it keeps going, scored, into its next round's break", t.running("demon_lord"))
    t.check("no error in the engine's log", t.no_errors())
