"""arena, live: a small radius round where Steve started sends him back out walking (radius 2, so
Alex, 4 away, never counts toward it); shape="near" (tether) sends him back toward Alex instead.
The box shape is left to the unit test (a flat radius versus a 3D box differ only in the geometry
already proven there)."""


def test(t):
    t.start("arena", wait=2, players="Steve", shape="radius", radius=2, message="Back inside the arena")
    t.check("it says where he is kept", t.said("kept within 2 blocks of"))
    t.cmd("player Steve move forward")
    t.watch(4)
    t.cmd("player Steve move")
    t.watch(1)                                        # the next correction tick, so he settles
    t.check("he walked away, pressing to leave", t.moved(2, way="forward"))
    t.check("he ends back within the radius (Alex, 4 away, cannot count here)",
           t.near("player", 0, 0, 0, radius=2, count=1))
    t.stop("arena")

    t.start("arena", wait=2, players="Steve", shape="near", radius=2, near="Alex",
           message="Stay with the group")
    t.check("it says so", t.said("kept within 2 blocks of Alex"))
    t.cmd("player Steve move forward")
    t.watch(4)
    t.cmd("player Steve move")
    t.watch(1)
    t.check("he ends back near Alex (both within 2 of Alex's own spot)",
           t.near("player", -4, 0, 0, radius=2, count=2))
    t.check("no error in the engine's log", t.no_errors())
