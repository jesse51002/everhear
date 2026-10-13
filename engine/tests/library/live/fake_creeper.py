"""fake_creeper, live: the defused creeper horror plants behind a player, spawned directly."""


def test(t):
    t.spawn("fake_creeper", 0, 0, -2)
    t.check("it stands behind Steve", t.near("creeper", 0, 0, -2, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
