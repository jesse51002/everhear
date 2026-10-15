"""blast_at_look, live: granted and bound, a heard cast blasts the stone wall 10 blocks ahead,
hurting a husk beside it; no blocks break."""


def test(t):
    t.blocks("stone", 0, 0, 10)
    husk = t.mob("husk", 3, 0, 10, health=200)
    t.grant("blast_at_look")
    t.bind("blast at look", "blast_at_look")
    t.check("the cast fires", t.cast("blast at look"))
    t.watch(2)
    t.check("the husk near the blast is hurt", t.hurt(husk))
    t.check("the wall stands: no blocks break", t.block(0, 0, 10, "minecraft:stone"))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
