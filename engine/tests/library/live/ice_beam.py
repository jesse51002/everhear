"""ice_beam, live: granted and bound, a heard cast fires an instant freezing beam: a husk 10 ahead
takes no damage, and (through t.player on the line) a player is slowed and blinded."""


def test(t):
    bob = t.player("Bob", 0, 0, 10)
    husk = t.mob("husk", 0, 0, 20, health=1000)
    t.grant("ice_beam")
    t.bind("ice beam", "ice_beam")
    t.check("the cast is taken", t.cast("ice beam"))
    t.watch(2)
    t.check("Bob is slowed", t.effect("minecraft:slowness", who="Bob"))
    t.check("the husk takes no damage", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
