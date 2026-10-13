"""walking_dead, live: a zombie within 6 blocks of Steve lunges (faster than its own vanilla
0.23), and can break doors (CanBreakDoors, read with t.cmd, which lets a read through). t.mob's
`still` would itself zero out its movement_speed base (to stop it wandering), so this one is not
still: it may wander or swing at Steve a little, which the checks below do not mind."""

import re

from vfg_engine.livetest import verdict


def test(t):
    t.mob("zombie", 0, 0, 4, health=20, still=False)
    sel = "@e[type=zombie,x=%d,y=%d,z=%d,distance=..5,limit=1]" % tuple(int(v) for v in t.xyz(0, 0, 4))
    t.start("walking_dead", wait=2, players="Steve")
    t.watch(2)
    said = t.cmd("attribute %s minecraft:movement_speed get" % sel)
    nums = re.findall(r"[\d.]+", said)
    speed = float(nums[-1]) if nums else 0.0
    t.check("within 6 blocks it lunges, faster than its own 0.23", verdict(speed > 0.25, said))
    said2 = t.cmd("data get entity %s CanBreakDoors" % sel)
    t.check("it can break doors", verdict("1b" in said2, said2))
    t.check("no error in the engine's log", t.no_errors())
