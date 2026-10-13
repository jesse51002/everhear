"""sniper_shot, live: granted and bound, a heard cast fires a long shot into a husk 10 blocks
ahead, on his eye line (a headshot: double damage)."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=500)
    t.grant("sniper_shot")
    t.bind("snipe", "sniper_shot")
    t.check("the cast fires", t.cast("snipe"))
    t.watch(2)
    t.check("the shot hurts the husk hard (a headshot)", t.hurt(husk, at_least=250))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
