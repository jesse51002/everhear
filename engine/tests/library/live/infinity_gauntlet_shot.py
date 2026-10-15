"""infinity_gauntlet_shot, live: granted and bound, a heard cast kills a husk 10 blocks ahead."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=40)
    t.grant("infinity_gauntlet_shot")
    t.bind("snap", "infinity_gauntlet_shot")
    t.check("the cast fires", t.cast("snap"))
    t.watch(2)
    t.check("the husk is killed", t.killed(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
