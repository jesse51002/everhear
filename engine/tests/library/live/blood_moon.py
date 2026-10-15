"""blood_moon, live: darkness and hunger on Steve, undead and spiders round him, thunder held; a stop
ends it. Its six heartbeats and its own end (45 s) are on the fake clock
(tests/library/test_gamemaster.py: test_blood_moon_darkens_and_spawns_then_ends)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("blood_moon", wait=3, players="Steve", level=1)
    t.check("Steve has darkness", t.effect("minecraft:darkness"))
    t.check("Steve has hunger", t.effect("minecraft:hunger"))
    t.check("it is thundering", t.weather("thunder"))
    # all five of MOBS (blood_moon.py), not just three: at n=3 spawned, missing husk and witch left
    # this flaky (about a 1-in-16 chance every one of the three lands on those two alone).
    t.check("undead or spiders stand round Steve", t.near("zombie", 0, 0, 0, radius=14, count=1) or
           t.near("spider", 0, 0, 0, radius=14, count=1) or t.near("skeleton", 0, 0, 0, radius=14, count=1) or
           t.near("husk", 0, 0, 0, radius=14, count=1) or t.near("witch", 0, 0, 0, radius=14, count=1))
    t.stop(iid)
    running = t.running("blood_moon")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
