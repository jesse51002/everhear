"""wither_wand_summon, live: granted and bound, a heard cast summons withers near where Steve
looks, holding mob_griefing off while they live."""


def test(t):
    t.grant("wither_wand_summon")
    t.bind("summon withers", "wither_wand_summon")
    t.look(pitch=75)
    t.check("the cast fires", t.cast("summon withers"))
    t.watch(3)
    t.check("withers appear near where he looked", t.near("wither", 0, -1, 2, radius=10, count=1))
    said = t.cmd("gamerule mob_griefing")
    t.check("mob_griefing is held off while they live", "false" in (said or "").lower())
    t.check("no error in the engine's log", t.no_errors())
