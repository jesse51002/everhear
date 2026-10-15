"""walking_dead, live: a zombie lunges faster the closer it gets to a player, and can break doors
(CanBreakDoors, read with t.cmd, which lets a read through). t.mob's `still=False` keeps its AI on
by explicitly zeroing its movement_speed base to 0 (vfg_engine.livetest.mob: `still=True` instead
leaves the base alone but freezes the mob with NoAI), so this test zombie's own base is 0, not the
real zombie's vanilla 0.23 - comparing its total speed against 0.23 is not a fair check here. This
checks the shape of the lunge instead: total speed within 6 blocks against the same zombie
teleported to 20 (walking_dead's own "else" tier), which the scenario promises is slower - the
comparison holds however the test mob's own base is set."""

import re

from vfg_engine.livetest import verdict


def _speed(t, sel):
    said = t.cmd("attribute %s minecraft:movement_speed get" % sel)
    nums = re.findall(r"[\d.]+", said)
    return (float(nums[-1]) if nums else 0.0), said


def test(t):
    t.mob("zombie", 0, 0, 4, health=20, still=False)
    sel = "@e[type=zombie,x=%d,y=%d,z=%d,distance=..30,limit=1]" % tuple(int(v) for v in t.xyz(0, 0, 4))
    t.start("walking_dead", wait=2, players="Steve")
    t.watch(2)
    near, said_near = _speed(t, sel)
    t.cmd("tp %s %s" % (sel, t.at(0, 0, 20)))
    t.watch(2)
    far, said_far = _speed(t, sel)
    t.check("within 6 blocks it lunges, faster than at 20 blocks off",
            verdict(near > far, "%s vs %s" % (said_near, said_far)))
    said2 = t.cmd("data get entity %s CanBreakDoors" % sel)
    t.check("it can break doors", verdict("1b" in said2, said2))
    t.check("no error in the engine's log", t.no_errors())
