"""boss_fight, live: Horde King rises near Steve, with a red bar; killed, it reports dead and a
reward diamond lands where it fell. Then the same for a two-headed stand-in of the dragon fight
(dragon_head, bodies=2, so the stage holds both heads), reported dead once both are gone."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("boss_fight", wait=5, boss="horde_king", near="Steve")
    t.check("it says Horde King rises", t.said("Horde King rises"))
    t.check("Horde King stands near Steve", t.near("zombie", 0, 0, 0, radius=12, count=1))
    t.cmd("kill @e[type=minecraft:zombie,tag=everhear.actor,x=%d,y=%d,z=%d,distance=..40]"
         % tuple(int(v) for v in t.xyz()))
    t.wait(2)
    running = t.running("boss_fight")
    t.check("it ends once it is dead", verdict(not running[0], running[1]))
    t.check("it says Horde King is dead", t.said("Horde King, who rose"))
    t.check("a diamond is left where it fell", t.near("item", 0, 0, 0, radius=12, count=1))

    t.start("boss_fight", wait=5, boss="dragon_head", bodies=2, height=10, near="Steve",
            name="Two-Headed Dragon")
    t.check("it says Two-Headed Dragon rises", t.said("Two-Headed Dragon rises"))
    t.check("two dragon heads are up", t.near("ender_dragon", 0, 10, 0, radius=14, count=2))
    t.cmd("kill @e[type=minecraft:ender_dragon,tag=everhear.actor,x=%d,y=%d,z=%d,distance=..40]"
         % tuple(int(v) for v in t.xyz()))
    t.wait(2)
    running = t.running("boss_fight")
    t.check("the two-headed fight ends once both are dead", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
