"""horde, live: a wave of six zombies round Steve, reported once out; killed, the horde ends.
Then summon_around's way (stay): four named cows, left standing after it ends."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("horde", wait=5, players="Steve", per_player=6, spread=8)
    t.check("it says the wave is out", t.said("summoned 6 mobs (zombie) round Steve"))
    t.check("six zombies stand round Steve", t.near("zombie", 0, 0, 0, radius=12, count=6))
    t.cmd("kill @e[type=minecraft:zombie,x=%d,y=%d,z=%d,distance=..40]" % tuple(int(v) for v in t.xyz()))
    t.wait(3)
    running = t.running("horde")
    t.check("it ends once they are dead", verdict(not running[0], running[1]))

    t.start("horde", wait=5, players="Steve", mobs=["cow"], per_player=4, spread=4, stay=True, name="Bessie",
            announce=False)
    t.check("it says they stay", t.said("summoned 4 mobs (cow) round Steve"))
    running = t.running("horde")
    t.check("it ended once they were out", verdict(not running[0], running[1]))
    # round where Steve stands now: the zombies may have shoved him a few blocks
    t.check("the four cows are still there", t.near("cow", 0, 0, 0, radius=20, count=4))
    t.check("no error in the engine's log", t.no_errors())
