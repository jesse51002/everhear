"""spear_bomb, live: thrown, it explodes and carves a crater where it lands or hits, hurting a
husk ahead; Steve, who threw it, is spared."""


def test(t):
    ground = t.ground(right=0, forward=8, size=9, depth=3)
    husk = t.mob("husk", 0, 0, 8, health=200)
    t.give("spear_bomb")
    t.check("Steve holds the spear bomb", t.holds("spear_bomb"))
    t.wait(1)
    t.use(draw=0.6)          # a trident only throws past 10 ticks (0.5 s) held; 0.3 s never looses it
    t.watch(4)
    t.check("the blast hurts the husk", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("the ground is carved into a crater", t.gone(ground, share=0.1))
    t.check("no error in the engine's log", t.no_errors())
