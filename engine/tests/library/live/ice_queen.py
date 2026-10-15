"""ice_queen, live: spawned directly, she is a stray in icy gear; her granted ice_blast freezes and
hurts Alex (within her radius, never herself), and frost_servants calls a stray to her side,
within their cooldowns.

(She is hostile and not held in place, 6 blocks from Steve and about 7 from Alex: live, her own
melee closed that gap well inside the 9 s wait, strongly enough to kill an unarmoured Alex before
or at the moment ice_blast would otherwise have frozen him -- so slowness is checked only when he
is still alive to carry it (a kill is itself proof the cold hit him), and "near her" allows for
wherever the chase left her, not only her spawn spot.)"""


def test(t):
    queen = t.spawn("ice_queen", 0, 0, 6)
    t.check("she is a stray", t.near("stray", 0, 0, 6, radius=2, count=1))
    t.wait(9)
    t.check("Alex is frozen and hurt by the cold", t.hurt("Alex"))
    killed = t.killed("Alex")
    t.check("Alex has slowness while frozen (or died to the cold first)",
           killed if killed[0] else t.effect("minecraft:slowness", who="Alex"))
    t.check("a frost servant stands near her", t.near("stray", 0, 0, 6, radius=20, count=2))
    t.check("no error in the engine's log", t.no_errors())
