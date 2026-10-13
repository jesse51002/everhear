"""land_safely on a real server: onto the spot asked when it is safe, round it when its ground is
magma."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("kit_land", wait=5, at=t.at(0, 0, 15))
    t.check("he lands", t.said("landed"))
    t.watch(1)
    t.check("on the spot, 15 ahead", t.moved(14, way="forward"))
    x, y, z = (int(v // 1) for v in t.xyz(10, 0, 30))
    t.cmd("setblock %d %d %d magma_block" % (x, y - 1, z))
    t.start("kit_land", wait=5, at=t.at(10, 0, 30))
    spot = (t.result().get("facts") or {}).get("spot")
    t.check("he lands", t.said("landed"))
    t.check("not on the magma", verdict(spot and (int(spot[0] // 1), int(spot[2] // 1)) != (x, z), "spot %s" % spot))
    t.watch(1)
    t.check("he is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
