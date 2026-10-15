"""boom_bow, live: given arrows and the bow, a drawn shot explodes where the arrow lands or hits,
hurting a husk ahead; Steve, who shot it, is spared."""


def test(t):
    husk = t.mob("husk", 0, 0, 8, health=200)
    t.give("boom_bow")
    t.check("Steve holds the boom bow", t.holds("boom_bow"))
    t.cmd("give %s minecraft:arrow 64" % t.caster)
    t.wait(1)
    t.use(draw=0.3)
    t.watch(3)
    t.check("the blast hurts the husk", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
