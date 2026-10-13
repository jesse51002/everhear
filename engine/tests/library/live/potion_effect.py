"""potion_effect, live: applied to Steve, he gets the effect at the given level, kept up until
cleared."""


def test(t):
    t.apply("potion_effect", effect="speed", amplifier=3)
    t.watch(2)
    t.check("Steve has speed", t.effect("speed"))
    t.clear("potion_effect")
    t.check("no error in the engine's log", t.no_errors())
