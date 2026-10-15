"""thunderbrand, live: a right-click strikes lightning on a stone wall 10 blocks ahead, hurting a
husk beside it (the lightning_strike ability, a sword look over lightning_staff's own mechanic);
Steve and Alex (off to the side) are unhurt."""


def test(t):
    t.blocks("stone", 0, 0, 10)
    husk = t.mob("husk", 3, 0, 10, health=200)         # beside the wall, within the 4-block ring
    t.give("thunderbrand")
    t.check("Steve holds the thunderbrand", t.holds("thunderbrand"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk near the strike is hurt (40 lightning)", t.hurt(husk, at_least=30))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
