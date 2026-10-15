"""stormcaller, live: thrown, it calls lightning down where it lands or hits and hurts a husk
waiting about there (the same shape as zeus and lightning_strike, plus its own declared damage
round the strike); Steve, who threw it, is spared (never within 4 blocks of him)."""


def test(t):
    husk = t.mob("husk", 0, 0, 21, health=200)        # about where the thrown trident comes down
    t.give("stormcaller")
    t.check("Steve holds the stormcaller", t.holds("stormcaller"))
    t.wait(1)
    t.use(draw=1.2)                                    # thrown ahead
    t.watch(3)
    t.check("the husk near where it landed is hurt", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
