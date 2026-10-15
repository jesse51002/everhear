"""railgun, live: a right-click fires a beam through a stone wall into a husk 10 blocks ahead;
Steve and Alex (4 to his left) are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.blocks("stone", 0, 0, 5)                       # a wall between them: the beam goes through
    t.give("railgun")
    t.check("Steve holds the railgun", t.holds("railgun"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the beam hurts the husk behind the wall", t.hurt(husk, at_least=25))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
