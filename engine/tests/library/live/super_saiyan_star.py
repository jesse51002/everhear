"""super_saiyan_star, live: right-click powers Steve up into Super Saiyan form."""


def test(t):
    t.give("super_saiyan_star")
    t.check("Steve holds the star", t.holds("super_saiyan_star"))
    t.use()
    t.watch(3)
    t.check("Steve has speed", t.effect("speed"))
    t.check("no error in the engine's log", t.no_errors())
