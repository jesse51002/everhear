"""gravity_lift, live: granted and bound, a heard cast throws a husk 10 blocks ahead up into the
air; it is not hurt (gravity_lift deals none)."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200, still=False)
    t.grant("gravity_lift")
    t.bind("gravity lift", "gravity_lift")
    t.check("the cast fires", t.cast("gravity lift"))
    t.watch(2)
    t.check("the husk is thrown up", t.moved(3, "up", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
