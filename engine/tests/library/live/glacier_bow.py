"""glacier_bow, live: given arrows and the bow, a drawn shot freezes a husk where it lands or hits
(the shared frozen mark); the bow itself deals no damage, and Steve, who shot it, is unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 8, health=200, still=False)
    t.give("glacier_bow")
    t.check("Steve holds the glacier bow", t.holds("glacier_bow"))
    t.cmd("give %s minecraft:arrow 64" % t.caster)
    t.wait(1)
    t.use(draw=0.3)
    t.watch(3)
    t.check("the husk near the arrow is frozen", t.effect("slowness", husk))
    t.check("the husk is not hurt by the bow", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
