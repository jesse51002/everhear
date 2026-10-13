"""rocket_launcher, live: right-click fires a fireball at a husk 10 blocks ahead; Steve is unhurt
(the blast spares him)."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.give("rocket_launcher")
    t.check("Steve holds the rocket launcher", t.holds("rocket_launcher"))
    t.use()
    t.watch(3)
    t.check("it hurts the husk", t.hurt(husk, at_least=30))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
