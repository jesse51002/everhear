"""lightning_strike, live: granted and bound, a heard cast strikes lightning on a stone wall 10
blocks ahead, hurting a husk beside it."""


def test(t):
    t.blocks("stone", 0, 0, 10)
    husk = t.mob("husk", 3, 0, 10, health=200)       # beside the wall, within the 4-block ring
    t.grant("lightning_strike")
    t.bind("lightning strike", "lightning_strike")
    t.check("the cast fires", t.cast("lightning strike"))
    t.watch(2)
    t.check("the husk near the strike is hurt (40 lightning)", t.hurt(husk, at_least=30))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
