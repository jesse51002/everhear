"""gravekeeper, live: spawned directly (demon_lord spawns it itself, not through boss_fight), it
stands in as a giant wither skeleton with a never-dropped netherite sword."""


def test(t):
    t.spawn("gravekeeper", 0, 0, 10)
    t.check("it is a giant wither skeleton", t.near("wither_skeleton", 0, 0, 10, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
