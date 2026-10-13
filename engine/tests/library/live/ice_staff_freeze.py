"""ice_staff_freeze, live: granted and bound, a heard cast freezes and hurts a husk 10 blocks
ahead; no beam."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=400)
    t.grant("ice_staff_freeze")
    t.bind("ice freeze", "ice_staff_freeze")
    t.check("the cast fires", t.cast("ice freeze"))
    t.watch(2)
    t.check("the husk is hurt (200 magic)", t.hurt(husk, at_least=150))
    t.check("the husk is frozen (slowed)", t.effect("slowness", husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
