"""mountains, live: a range beside Steve (mountain_trees folded in as its trees arg). It is the
biggest build in the library (about 5,000 terrain columns, two caves, its spruce and its biomes),
so this only proves it runs cleanly and makes a start within the wait, not that it finishes."""


def test(t):
    instance = t.start("mountains", wait=60, players="Steve", away=20, seed=3, trees=5)
    t.check("no error in the engine's log", t.no_errors())
    t.check("it is still building the range, or it already finished",
            t.running(instance) or t.said("mountain range built over x"))
