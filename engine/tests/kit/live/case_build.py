"""build_shape on a real server: a hollow box (walls, air inside) and a dome, built by the block
queue, done(ctx) called when they stand."""

from vfg_engine.livetest import verdict


def test(t):
    box = t.region(0, 0, 10, size=7)
    t.start("kit_build", wait=10, at=t.at(0, 0, 10), kind="box", width=7, height=5, depth=7, hollow=True,
            blocks="70% stone_bricks 30% cobblestone")
    t.check("it says when the box is built", t.said("built"))
    t.check("its wall stands", t.turned((box[0], box[1], box[2], box[0], box[1] + 4, box[5]),
                                        ["stone_bricks", "cobblestone"], share=1.0))
    t.check("a mix: some cobblestone", t.turned((box[0], box[1], box[2], box[3], box[1] + 4, box[5]),
                                                "cobblestone", share=0.05))
    t.check("air inside", t.gone((box[0] + 1, box[1] + 1, box[2] + 1, box[3] - 1, box[1] + 3, box[5] - 1), share=1.0))
    t.start("kit_build", wait=10, at=t.at(0, 0, 25), kind="dome", width=11, height=6, depth=11, blocks="glass")
    t.check("the dome is built", t.said("built"))
    dome = t.region(0, 0, 25, size=11)
    t.check("glass in its middle, to near its top", t.block(0, 4, 25, "glass"))
    t.check("its corners are air", t.block(-5, 0, 20, "air"))
    t.check("the dome is mostly glass", t.turned(dome, "glass", share=0.25))
    quiet = t.no_errors()                     # it watches beings; here only blocks change
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
