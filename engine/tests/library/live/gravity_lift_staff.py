"""gravity_lift_staff, live: a right-click throws a husk 10 blocks ahead up into the air; it is
not hurt (gravity_lift deals none); Steve and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200, still=False)
    t.give("gravity_lift_staff")
    t.check("Steve holds the staff", t.holds("gravity_lift_staff"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk is thrown up", t.moved(3, "up", husk))
    t.check("the husk is not hurt", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
