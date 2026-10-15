"""doomcleaver, live: a right-click blasts the stone wall 10 blocks ahead, hurting a husk beside
it (the blast_at_look ability); no blocks break; Steve and Alex (off to the side) are unhurt."""


def test(t):
    t.blocks("stone", 0, 0, 10)
    husk = t.mob("husk", 3, 0, 10, health=200)         # beside the wall, within the blast's reach
    t.give("doomcleaver")
    t.check("Steve holds the doomcleaver", t.holds("doomcleaver"))
    t.wait(1)
    t.use()
    t.watch(2)
    t.check("the husk near the blast is hurt", t.hurt(husk))
    t.check("the wall stands: no blocks break", t.block(0, 0, 10, "minecraft:stone"))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
