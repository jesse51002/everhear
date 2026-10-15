"""ravager_warlord, live: spawned directly, a ravager carrying an evoker as its passenger."""


def test(t):
    t.spawn("ravager_warlord", 0, 0, 10)
    t.check("it is a ravager", t.near("ravager", 0, 0, 10, radius=3, count=1))
    t.check("it carries an evoker", t.near("evoker", 0, 0, 10, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
