"""horde_king, live: spawned directly (not through boss_fight), it stands in netherite gear, and
its granted call_minions calls zombies round it within the cooldown.

(Hostile and not held in place, it closes on the nearest player over the 11 s wait -- live, it
walked from 10 ahead to within a couple of blocks of Steve by then -- so the second check's radius
is wide enough to find it and its minions wherever that chase left them, not only at its spawn
spot; call_minions's own unit test already pins the minions down to right round its body.)"""


def test(t):
    king = t.spawn("horde_king", 0, 0, 10)
    t.check("it is a giant zombie", t.near("zombie", 0, 0, 10, radius=3, count=1))
    t.wait(11)
    t.check("it calls minions round itself", t.near("zombie", 0, 0, 10, radius=20, count=2))
    t.check("no error in the engine's log", t.no_errors())
