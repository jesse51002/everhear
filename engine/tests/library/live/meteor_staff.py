"""meteor_staff, live: right-click and a meteor falls onto a stone wall Steve looks at, then
explodes, hurting a husk beside it; Steve is not hurt."""


def test(t):
    t.blocks("stone", 0, 1, 15, size=3)
    husk = t.mob("husk", 2, 0, 15, health=200)
    t.give("meteor_staff")
    t.check("Steve holds the meteor staff", t.holds("meteor_staff"))
    t.wait(1)
    t.use()
    t.watch(6)
    t.check("the blast hurts the husk", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
