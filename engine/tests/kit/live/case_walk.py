"""steps / chunks_entered on a real server: Steve walks north (Carpet's move forward) about 15
blocks, over a chunk's edge: three steps give speed, the second chunk jump boost."""


def test(t):
    t.start("kit_walk")
    t.wait(0.5)
    t.cmd("player Steve move forward")
    t.watch(3.5)
    t.cmd("player Steve move")
    t.check("he walked", t.moved(8, way="forward"))
    t.check("three steps counted", t.effect("speed"))
    t.check("a second chunk entered", t.effect("jump_boost"))
    t.check("no error in the engine's log", t.no_errors())
