"""held_use on a real server, one click: pressed and let go, never charged."""

from vfg_engine.livetest import verdict


def test(t):
    t.give("kit_gun")
    t.check("Steve holds the gun", t.holds("kit_gun"))
    t.wait(0.5)
    t.use()
    t.watch(1.5)
    t.check("on_press: speed", t.effect("speed"))
    t.check("on_release: glowing", t.effect("glowing"))
    jb = t.effect("jump_boost")
    t.check("not charged: no jump boost", verdict(not jb[0], jb[1]))
    t.check("no error in the engine's log", t.no_errors())
