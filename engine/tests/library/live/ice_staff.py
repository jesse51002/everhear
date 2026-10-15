"""ice_staff, live: a right-click on a husk 10 blocks ahead freezes and hurts it; no beam. Steve
and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=400)
    t.give("ice_staff")
    t.check("Steve holds the staff", t.holds("ice_staff"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk is hurt (200 magic)", t.hurt(husk, at_least=150))
    t.check("the husk is frozen (slowed)", t.effect("slowness", husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
