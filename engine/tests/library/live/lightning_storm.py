"""lightning_storm, live: thunder held and a bolt falls near Steve within its first strikes; a stop
ends it. Its 10 + level strikes and its own end (22 s) are on the fake clock
(tests/library/test_gamemaster.py: test_lightning_storm_strikes_and_clears)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("lightning_storm", wait=1, players="Steve", level=1)
    t.check("it is thundering", t.weather("thunder"))
    t.check("a bolt falls near Steve", t.near("lightning_bolt", 0, 0, 0, radius=16, count=1, within=5))
    t.stop(iid)
    running = t.running("lightning_storm")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
