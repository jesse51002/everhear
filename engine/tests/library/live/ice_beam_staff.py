"""ice_beam_staff, live: right-click fires an instant freezing beam: a player standing on it is
slowed, weakened and blinded; a husk further along takes no damage. It carves an open lane and
ices the ground round it, at Steve's eye height."""


def test(t):
    t.blocks("stone", 0, 0, 10, size=5)              # solid ground at beam height, for the lane to carve
    bob = t.player("Bob", 0, 0, 12)
    husk = t.mob("husk", 0, 0, 20, health=1000)
    t.give("ice_beam_staff")
    t.check("Steve holds the ice beam staff", t.holds("ice_beam_staff"))
    t.use()
    t.watch(2)
    t.check("Bob is slowed", t.effect("minecraft:slowness", who="Bob"))
    t.check("Bob is blinded", t.effect("minecraft:blindness", who="Bob"))
    t.check("the husk takes no damage", t.unhurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.wait(2)                                        # the icing queue drains
    t.check("the lane is carved open", t.block(0, 2, 10, "air"))
    t.check("no error in the engine's log", t.no_errors())
