"""infinity_gauntlet, live: a right-click kills a husk 10 blocks ahead, in a beam of flame; Steve
and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=40)
    t.give("infinity_gauntlet")
    t.check("Steve holds the Infinity Gauntlet", t.holds("infinity_gauntlet"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk is killed", t.killed(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
