"""summon_near / safe_spots / mob_nbt / boss_bar on a real server: three named zombies round Steve
on the floor, never a fourth while they live, a bar on their summed health, all gone at the stop."""

import re

from vfg_engine.livetest import verdict


def _here(t, extra):
    x, y, z = t.xyz(0, 0, 0)
    return "@e[%s,x=%s,y=%s,z=%s,distance=..12]" % (extra, x, y, z)


def test(t):
    iid = t.start("kit_horde")
    t.wait(2.5)
    t.check("three zombies round Steve", t.near("zombie", 0, 0, 0, radius=8, count=3))
    t.wait(2)
    n = re.search(r"Count: (\d+)", t.cmd("execute if entity %s" % _here(t, "type=zombie")))
    t.check("never more than three (the cap)", verdict(n and int(n.group(1)) == 3, n.group(0) if n else "none"))
    named = t.cmd("execute if entity %s" % _here(t, "type=zombie,name=Grunt"))
    t.check("named by mob_nbt", verdict("Count: 3" in named, named))
    bars = t.cmd("bossbar list")
    t.check("a boss bar, The Horde", verdict("The Horde" in bars, bars[:300]))
    bid = "everhear:kit_horde_%s_boss" % iid.rsplit("#", 1)[-1]       # the core's: <class>_<n>_<bar>
    value = t.cmd("bossbar get %s value" % bid)
    t.check("on their summed health, 3 x 30", verdict("90" in value, value))
    t.stop(iid)
    t.wait(1)
    left = t.cmd("execute if entity %s" % _here(t, "type=zombie"))
    t.check("the zombies go at the stop", verdict("passed" not in left.lower(), left or "none"))
    t.check("the bar goes too", verdict("The Horde" not in t.cmd("bossbar list"), "bossbar list"))
    quiet = t.no_errors()                     # it watches what Steve and the test's mobs do; here none
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
