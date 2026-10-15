"""arena, live: a small radius round where Steve started sends him back out walking (radius 2, so
Alex, 4 away, never counts toward it); shape="near" (tether) sends him back toward Alex instead.
The box shape is left to the unit test (a flat radius versus a 3D box differ only in the geometry
already proven there).

The tether's own radius here is wider (6, not 2): the correction (like the old tether script's)
sends a leaver back once per excursion and only fires again once they have been seen back inside
(engine/library/README.md, "one-shot correction"). A sprinting player covers about 2 to 2.5
blocks each of the scenario's half-second checks, so a radius as tight as the first check's (2)
would sometimes land him just past it again on the very next check, before he is ever seen
inside - and then, per that design, he is not corrected again. A wider radius gives the one check
right after each correction room to land inside it, so the same one-shot design settles him
reliably here too; the tight-radius, one-shot-outrun case is the unit test's
(test_near_shape_is_tether_sent_to_the_nearest_of_the_group and the class's own docstring)."""


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

    t.start("arena", wait=2, players="Steve", shape="near", radius=6, near="Alex",
           message="Stay with the group")
    t.check("it says so", t.said("kept within 6 blocks of Alex"))
    t.cmd("player Steve move forward")
    t.watch(4)
    t.cmd("player Steve move")
    t.watch(1)
    t.check("he ends back near Alex (both within 6 of Alex's own spot)",
           t.near("player", -4, 0, 0, radius=6, count=2))
    t.check("no error in the engine's log", t.no_errors())
