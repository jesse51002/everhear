"""ice_queen, live: spawned directly, she is a stray in icy gear; her granted ice_blast freezes and
hurts Alex (within her radius, never herself), and frost_servants calls a stray to her side,
within their cooldowns."""


def test(t):
    queen = t.spawn("ice_queen", 0, 0, 6)
    t.check("she is a stray", t.near("stray", 0, 0, 6, radius=2, count=1))
    t.wait(9)
    t.check("Alex is frozen and hurt by the cold", t.hurt("Alex"))
    t.check("Alex has slowness while frozen", t.effect("minecraft:slowness", who="Alex"))
    t.check("a frost servant stands near her", t.near("stray", 0, 0, 6, radius=6, count=2))
    t.check("no error in the engine's log", t.no_errors())
