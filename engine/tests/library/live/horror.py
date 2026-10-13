"""horror, live: the dark falls on Steve almost at once; then, in a second run, a shadow stands
watching behind him."""


def test(t):
    t.start("horror", wait=3, players="Steve", events=["dark"], weights=[1], min_s=1, max_s=1, each=True, haze=True)
    t.wait(3)
    t.check("the dark falls on Steve", t.effect("darkness"))
    t.check("no error in the engine's log", t.no_errors())
    t.stop("horror")

    t.start("horror", wait=3, players="Steve", events=["shadow"], weights=[1], min_s=1, max_s=1, each=True,
            haze=False)
    t.wait(3)
    t.check("a shadow stands behind Steve", t.near("armor_stand", 0, 0, -12, radius=4, count=1))
    t.check("no error in the engine's log", t.no_errors())
