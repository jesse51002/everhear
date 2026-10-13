"""super_sniper, live: a right-click fires a long shot into a husk 10 blocks ahead, on his eye
line (a headshot: double damage); Steve and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=500)
    t.give("super_sniper")
    t.check("Steve holds the Super Sniper", t.holds("super_sniper"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the shot hurts the husk hard (a headshot)", t.hurt(husk, at_least=250))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
