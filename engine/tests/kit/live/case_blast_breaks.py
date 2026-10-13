"""blast that breaks blocks, on a real server: vanilla TNT whose explosion_power is the blast's
power (power 6 tears a dirt cube apart, power 1 barely scratches its twin), and a spared player
right beside it shielded."""

from vfg_engine.livetest import verdict


def test(t):
    big = t.blocks("dirt", 6, 0, 12, size=5)
    small = t.blocks("dirt", -6, 0, 12, size=5)
    t.start("kit_blast", wait=5, at=t.at(6, 2, 12), power=6, breaks=True)
    t.start("kit_blast", wait=5, at=t.at(-6, 2, 12), power=1, breaks=True)
    t.wait(1.5)
    n6 = t.gone(big, share=0.5)
    n1 = t.gone(small, share=0.3)
    t.check("power 6 tears its cube apart", n6)
    t.check("power 1 leaves its twin mostly whole", verdict(not n1[0], n1[1]))
    t.check("a TNT blast went off where it was asked", t.said("1 blasts"))
    t.start("kit_blast", wait=5, at=t.at(0, 0, 3), power=4, breaks=True, spare="Steve")
    t.watch(2)
    t.check("Steve, 3 from TNT but spared, is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
