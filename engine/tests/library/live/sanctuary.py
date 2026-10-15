"""sanctuary, live: started for Steve it raises its platform and holds thunder; a stop ends
it. Its judging at the end (25 s at the least: spared or punished with lightning, weakness
and zombies) is on the fake clock (tests/library/test_gamemaster.py: test_sanctuary_spares_or_punishes)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("sanctuary", wait=2, players="Steve", level=5)
    t.check("it holds thunder while they look for it", t.weather("thunder"))
    t.check("it is running, counting down", t.running("sanctuary"))
    t.stop(iid)
    running = t.running("sanctuary")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    t.check("Steve was never judged", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
