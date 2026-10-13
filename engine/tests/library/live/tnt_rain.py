"""tnt_rain, live: a volley of lit TNT falls over Steve, blowing holes; a stop takes away what is
still falling."""

from vfg_engine.livetest import verdict


def test(t):
    t.ground(0, 0, size=20, depth=6)
    t.start("tnt_rain", wait=2, players="Steve", count=5, radius=5, every_s=6, fuse_s=3)
    t.check("it says it rains TNT on Steve", t.said("TNT rain on Steve"))
    t.check("TNT falls over him", t.near("tnt", 0, 12, 0, radius=10, count=1))
    t.wait(4)
    t.check("it blew holes in the ground", t.gone(t.region(0, 0, 0, size=14), share=0.01))
    t.stop("tnt_rain")
    t.wait(0.5)
    running = t.running("tnt_rain")
    t.check("a stop takes away what is still falling", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
