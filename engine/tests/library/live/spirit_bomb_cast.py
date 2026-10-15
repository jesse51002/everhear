"""spirit_bomb_cast, live: granted and bound, a heard cast charges for 5 s then an orb flies where
Steve looks -- up to its 100-block reach, stopped sooner only by a block in the way -- bursting on
a wall 20 ahead and hurting a husk beside it."""


def test(t):
    t.blocks("stone", 0, 0, 20)                      # a wall 20 ahead: where the orb lands (reach is 100)
    husk = t.mob("husk", 2, 0, 20, health=1000)       # by the landing point, beside the wall
    t.grant("spirit_bomb_cast")
    t.bind("spirit bomb", "spirit_bomb_cast")
    t.check("the cast is taken", t.cast("spirit bomb"))
    t.watch(8)                                       # 5 s charge, under a second to fly, then the burst
    t.check("the burst hurts the husk", t.hurt(husk, at_least=100))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
