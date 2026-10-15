"""gravebreaker, live: a right-click throws a husk right in front of Steve up into the air (the
self_lift ability, centred on the caster, not on where he looks); it is not hurt (self_lift deals
none); Steve and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 2, health=200, still=False)
    t.give("gravebreaker")
    t.check("Steve holds the gravebreaker", t.holds("gravebreaker"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk is thrown up", t.moved(3, "up", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
