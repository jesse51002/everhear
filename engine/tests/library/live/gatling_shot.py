"""gatling_shot, live: granted and bound, a heard cast fires one short beam into a husk 10 ahead."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=200)
    t.grant("gatling_shot")
    t.bind("gatling", "gatling_shot")
    t.check("the cast fires", t.cast("gatling"))
    t.watch(2)
    t.check("the beam hurts the husk", t.hurt(husk, at_least=25))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
