"""hollow_king, live: spawned directly, a giant zombie in netherite head and chest gear, faster
than a plain zombie (its own movement_speed modifier added at start)."""


def test(t):
    t.spawn("hollow_king", 0, 0, 10)
    t.check("it is a giant zombie", t.near("zombie", 0, 0, 10, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
