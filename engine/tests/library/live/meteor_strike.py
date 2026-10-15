"""meteor_strike, live: granted and bound, a heard cast drops a meteor onto a stone wall Steve
looks at, then explodes, hurting a husk beside it."""


def test(t):
    t.blocks("stone", 0, 1, 15, size=3)
    husk = t.mob("husk", 2, 0, 15, health=200)
    t.grant("meteor_strike")
    t.bind("meteor", "meteor_strike")
    t.check("the cast fires", t.cast("meteor"))
    t.watch(6)
    t.check("the blast hurts the husk", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
