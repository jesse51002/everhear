"""mountains, live: a range beside Steve (mountain_trees folded in as its trees arg). It is the
biggest build in the library (about 5,000 terrain columns, two caves, its spruce and its biomes),
so this proves it starts cleanly and is building after a few seconds, then stops; the whole build
and its result are on the fake clock (tests/library/test_builds.py)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("mountains", wait=0, players="Steve", away=20, seed=3, trees=5)
    t.wait(5)
    t.check("it is building the range", t.running("mountains"))
    t.stop(iid)
    running = t.running("mountains")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    quiet = t.no_errors()                     # it watches beings; here only blocks change
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
