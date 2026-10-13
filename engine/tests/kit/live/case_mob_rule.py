"""world_mob_rule on a real server: the world's creeper gets a 3 s fuse and half its speed while the
rule runs, both put back at the stop."""

import re

from vfg_engine.livetest import verdict


def _speed(t):
    got = re.search(r"([\d.]+)", t.cmd("attribute @e[tag=vfg_test_creeper_1,limit=1] minecraft:movement_speed get")
                    .split("is")[-1])
    return float(got.group(1)) if got else None


def test(t):
    t.mob("creeper", 0, 0, 8, health=20)
    base = _speed(t)
    iid = t.start("kit_mob_rule")
    t.wait(1.5)
    fuse = t.cmd("data get entity @e[tag=vfg_test_creeper_1,limit=1] Fuse")
    t.check("a 60-tick fuse while it runs", verdict(fuse.rstrip().endswith("60s"), fuse))
    slow = _speed(t)
    t.check("half its speed", verdict(base and slow and abs(slow - base / 2) < 1e-3, "%s -> %s" % (base, slow)))
    t.stop(iid)
    t.wait(1)
    fuse = t.cmd("data get entity @e[tag=vfg_test_creeper_1,limit=1] Fuse")
    t.check("its fuse put back at the stop", verdict(fuse.rstrip().endswith("30s"), fuse))
    t.check("its speed put back", verdict(_speed(t) == base, "%s" % _speed(t)))
    quiet = t.no_errors()                     # nothing it watches moves here
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
