"""wither_wand, live: right-click and withers appear near where Steve looks (steeply down, so the
stage floor is well within its range), holding mob_griefing off while they live."""


def test(t):
    t.give("wither_wand")
    t.check("Steve holds the wither wand", t.holds("wither_wand"))
    t.look(pitch=75)
    t.wait(1)
    t.use()
    t.watch(3)
    t.check("withers appear near where he looked", t.near("wither", 0, -1, 2, radius=10, count=1))
    said = t.cmd("gamerule mob_griefing")
    t.check("mob_griefing is held off while they live", "false" in (said or "").lower())
    t.check("no error in the engine's log", t.no_errors())
