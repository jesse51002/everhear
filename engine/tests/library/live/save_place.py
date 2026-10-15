"""save_place, live: saves where Steve stands under a name (teleport's own live test reads it
back)."""


def test(t):
    t.start("save_place", wait=5, name="docks", player="Steve")
    t.check("it says where it saved", t.said("saved docks at"))
    t.check("no error in the engine's log", t.no_errors())
