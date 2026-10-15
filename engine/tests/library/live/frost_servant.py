"""frost_servant, live: spawned directly, it is a named stray standing where it was called."""


def test(t):
    t.spawn("frost_servant", 0, 0, 8)
    t.check("a named stray stands there", t.near("stray", 0, 0, 8, radius=2, count=1))
    t.check("no error in the engine's log", t.no_errors())
