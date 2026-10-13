"""call_minions, live: granted to Steve directly (not through horde_king), it calls zombies round
him on its own, within its cooldown; no cast needed, as it is passive."""


def test(t):
    t.grant("call_minions")
    t.wait(11)
    t.check("it calls zombies round Steve", t.near("zombie", 0, 0, 0, radius=8, count=1))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
