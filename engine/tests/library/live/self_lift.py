"""self_lift, live: granted and bound, a heard cast throws a husk right in front of Steve up into
the air, centred on Steve himself, not on where he looks; it is not hurt (self_lift deals none)."""


def test(t):
    husk = t.mob("husk", 0, 0, 2, health=200, still=False)
    t.grant("self_lift")
    t.bind("self lift", "self_lift")
    t.check("the cast fires", t.cast("self lift"))
    t.watch(2)
    t.check("the husk is thrown up", t.moved(3, "up", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
