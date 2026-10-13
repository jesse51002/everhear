"""galick_gun_beam, live: granted and bound, a heard cast charges for 1.5 s then fires a beam into
a husk 10 ahead."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=1000)
    t.grant("galick_gun_beam")
    t.bind("galick gun", "galick_gun_beam")
    t.check("the cast is taken", t.cast("galick gun"))
    t.watch(3)
    t.check("the beam hurts the husk", t.hurt(husk, at_least=50))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
