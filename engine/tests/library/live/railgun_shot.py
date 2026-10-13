"""railgun_shot, live: granted and bound, a heard cast fires the beam into a husk 10 blocks ahead."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.grant("railgun_shot")
    t.bind("railgun", "railgun_shot")
    t.check("the cast fires", t.cast("railgun"))
    t.watch(2)
    t.check("the beam hurts the husk", t.hurt(husk, at_least=25))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
