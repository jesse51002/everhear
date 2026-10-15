"""lava_rain, live: a puff of lava and flame particles falls over Steve every couple of seconds,
until stopped; a look only, no blocks, no fire, no harm."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("lava_rain", wait=2, players="Steve", count=4, radius=6, drops=15, every_s=2)
    t.check("it says it rains on Steve", t.said("lava rain on Steve"))
    t.wait(2)
    t.check("it keeps going", t.running("lava_rain"))
    t.check("no harm from it", t.unhurt())
    t.stop("lava_rain")
    t.wait(0.5)
    running = t.running("lava_rain")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
