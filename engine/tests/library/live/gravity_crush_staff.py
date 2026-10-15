"""gravity_crush_staff, live: a right-click crushes a husk 10 blocks ahead to the ground and
freezes it; it is not hurt (gravity_crush deals none); Steve and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.give("gravity_crush_staff")
    t.check("Steve holds the staff", t.holds("gravity_crush_staff"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk is frozen (slowed)", t.effect("slowness", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
