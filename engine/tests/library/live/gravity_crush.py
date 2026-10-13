"""gravity_crush, live: granted and bound, a heard cast crushes and freezes a husk 10 blocks
ahead; it is not hurt (gravity_crush deals none)."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.grant("gravity_crush")
    t.bind("gravity crush", "gravity_crush")
    t.check("the cast fires", t.cast("gravity crush"))
    t.watch(2)
    t.check("the husk is frozen (slowed)", t.effect("slowness", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
