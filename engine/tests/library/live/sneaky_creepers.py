"""sneaky_creepers, live: a creeper within reach of Steve turns invisible, and its fuse (set once,
when first seen) is longer than the game's own 1.5 s. Fuse is read with t.cmd("data get entity ...
Fuse"), a read t.cmd lets through (its check only flags data modify/merge/remove, never get)."""

import re

from vfg_engine.livetest import verdict


def test(t):
    creeper = t.mob("creeper", 0, 0, 4, health=20)
    sel = "@e[type=creeper,x=%d,y=%d,z=%d,distance=..5,limit=1]" % tuple(int(v) for v in t.xyz(0, 0, 4))
    t.start("sneaky_creepers", wait=2, players="Steve", invisible_within=10, fuse=5)
    t.watch(2)
    t.check("it turns invisible", t.effect("invisibility", who=creeper))
    said = t.cmd("data get entity %s Fuse" % sel)
    nums = re.findall(r"[\d.]+", said)
    ticks = float(nums[-1]) if nums else 0.0
    t.check("its fuse is set to 5 s (100 ticks), longer than the game's own 30", verdict(ticks >= 100, said))
    t.check("no error in the engine's log", t.no_errors())
