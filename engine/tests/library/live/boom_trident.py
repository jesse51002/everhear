"""boom_trident, live: thrown, it explodes where it lands or hits, hurting a husk ahead; Steve,
who threw it, is spared."""


def test(t):
    husk = t.mob("husk", 0, 0, 8, health=200)
    t.give("boom_trident")
    t.check("Steve holds the boom trident", t.holds("boom_trident"))
    t.wait(1)
    t.use(draw=0.6)          # a trident only throws past 10 ticks (0.5 s) held; 0.3 s never looses it
    t.watch(3)
    t.check("the blast hurts the husk", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
