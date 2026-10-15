"""summoning_staff_summon, live: granted and bound, a heard cast summons zombies near where Steve
looks, holding mob_griefing off while they live."""


def test(t):
    # the stage floor is barrier, which the ray sees through (#everhear:ray_through); a real
    # block flush with it, where the steep look lands, gives the ray something to hit
    t.blocks("stone", 0, -3, 1, size=3)
    t.grant("summoning_staff_summon")
    t.bind("summon zombies", "summoning_staff_summon")
    t.look(pitch=75)
    t.check("the cast fires", t.cast("summon zombies"))
    t.watch(3)
    t.check("zombies appear near where he looked", t.near("zombie", 0, -1, 2, radius=10, count=1))
    said = t.cmd("gamerule mob_griefing")
    t.check("mob_griefing is held off while they live", "false" in (said or "").lower())
    t.check("no error in the engine's log", t.no_errors())
