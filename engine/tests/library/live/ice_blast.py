"""ice_blast, live: granted to Steve directly (not through ice_queen), it freezes and hurts every
player near him (Alex, 4 to his left; never himself) within its cooldown."""


def test(t):
    t.grant("ice_blast")
    t.wait(9)
    t.check("Alex is hurt by the cold", t.hurt("Alex"))
    t.check("Alex has slowness while frozen", t.effect("minecraft:slowness", who="Alex"))
    t.check("Steve himself is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
