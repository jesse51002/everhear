"""place_barrier, live: a bubble at a place behind Steve throws a cow placed inside it back out;
it keeps running (until stopped)."""


def test(t):
    cow = t.mob("cow", 0, 0, -8, behind=True, health=20, still=False)   # still=False: it must be free to fly out
    t.start("place_barrier", wait=5, at=t.at(0, 0, -8), radius=5)
    t.watch(2)
    t.check("the cow was thrown out", t.moved(2, who=cow))
    t.check("it is still running (until stopped)", t.running("place_barrier"))
    t.check("no error in the engine's log", t.no_errors())
