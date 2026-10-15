"""galick_gun, live: right-click charges the fanfare for 1.5 s, then a beam through a stone wall
hurts a husk 10 ahead; it carves a tunnel to a crater at its end. Steve and Alex are unhurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=1000)
    t.blocks("stone", 0, 0, 5)                       # a wall between them: the beam goes through
    t.give("galick_gun")
    t.check("Steve holds the galick gun", t.holds("galick_gun"))
    t.use()
    t.watch(3)                                       # 1.5 s charging, then the beam
    t.check("the beam hurts the husk behind the wall", t.hurt(husk, at_least=50))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.wait(2)                                        # the crater's block queue drains
    t.check("the beam's end is carved open", t.block(0, 2, 10, "air"))   # 2: Steve's eye height, up from his feet
    t.check("no error in the engine's log", t.no_errors())
