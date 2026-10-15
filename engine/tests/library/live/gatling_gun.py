"""gatling_gun, live: holding right-click for 3 s rapid-fires into a husk 10 blocks ahead: more
than one shot lands (Minecraft lets about two a second through); a cow 30 ahead, past its 24
blocks, is untouched."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=500)
    cow = t.mob("cow", 0, 0, 30, health=100)
    t.give("gatling_gun")
    t.check("Steve holds the gatling gun", t.holds("gatling_gun"))
    t.wait(1)
    t.use(hold=3)
    t.watch(2)
    t.check("more than one shot hurts the husk", t.hurt(husk, at_least=60))
    t.check("the cow past its range is not hurt", t.unhurt(cow))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
