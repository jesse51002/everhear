"""blood_moon, live: darkness and hunger on Steve, undead and spiders round him, midnight and
thunder held, a warden's heartbeat every 7 s, six times, then it ends on its own."""

from vfg_engine.livetest import verdict


def test(t):
    # self.result() fires only once it is over, at 6 x beat_s (45 s), past t.start's own wait_s cap
    # (MAX_WAIT_S, 30 s): a result that late never reaches t.said, so what it says in chat as it
    # starts (BLOOD MOON...) is checked through its effects instead, and its ending through
    # t.running below, not through t.said.
    t.start("blood_moon", wait=3, players="Steve", level=1)
    t.check("Steve has darkness", t.effect("minecraft:darkness"))
    t.check("Steve has hunger", t.effect("minecraft:hunger"))
    t.check("it is thundering", t.weather("thunder"))
    # all five of MOBS (blood_moon.py), not just three: at n=3 spawned, missing husk and witch left
    # this flaky (about a 1-in-16 chance every one of the three lands on those two alone).
    t.check("undead or spiders stand round Steve", t.near("zombie", 0, 0, 0, radius=14, count=1) or
           t.near("spider", 0, 0, 0, radius=14, count=1) or t.near("skeleton", 0, 0, 0, radius=14, count=1) or
           t.near("husk", 0, 0, 0, radius=14, count=1) or t.near("witch", 0, 0, 0, radius=14, count=1))
    t.wait(43)
    running = t.running("blood_moon")
    t.check("it ends after six heartbeats", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
