"""crater on a real server: a ball carved out of a stone cube by the block queue, done(ctx)
called when it is carved; a lava crater leaves lava in its bottom."""

from vfg_engine.livetest import verdict


def test(t):
    cube = t.blocks("stone", 0, 0, 12, size=9)
    t.start("kit_crater", wait=10, at=t.at(0, 4, 12), radius=3)
    t.check("it says when it is carved", t.said("carved"))
    inner = (cube[0] + 3, cube[1] + 3, cube[2] + 3, cube[3] - 3, cube[4] - 3, cube[5] - 3)
    t.check("the middle is carved out", t.gone(inner, share=0.9))
    t.check("the cube's corners stand", t.block(-4, 0, 16, "stone"))
    lava = t.blocks("stone", 10, 0, 12, size=9)
    t.start("kit_crater", wait=10, at=t.at(10, 4, 12), radius=4, leave="lava")
    t.check("the lava crater is carved too", t.said("carved"))
    t.check("lava lies in its bottom", t.turned((lava[0] + 3, lava[1] + 1, lava[2] + 3, lava[3] - 3, lava[1] + 1,
                                                 lava[5] - 3), "lava", share=0.5))
    quiet = t.no_errors()                     # it watches beings; here only blocks change
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
