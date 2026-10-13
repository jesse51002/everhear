"""horde_king, live: spawned directly (not through boss_fight), it stands in netherite gear, and
its granted call_minions calls zombies round it within the cooldown."""


def test(t):
    king = t.spawn("horde_king", 0, 0, 10)
    t.check("it is a giant zombie", t.near("zombie", 0, 0, 10, radius=3, count=1))
    t.wait(11)
    t.check("it calls minions round itself", t.near("zombie", 0, 0, 10, radius=8, count=2))
    t.check("no error in the engine's log", t.no_errors())
