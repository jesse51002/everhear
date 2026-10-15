"""fireball, live: granted and bound to "fire ball", it flies to a husk 10 blocks ahead and blasts
there; Steve and Alex are unhurt (the blast spares them)."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.grant("fireball")
    t.bind("fire ball", "fireball")
    t.check("the cast is taken", t.cast("fire ball"))
    t.watch(3)
    t.check("it hurts the husk", t.hurt(husk, at_least=30))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
