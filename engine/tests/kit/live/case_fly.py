"""fly on a real server: an orb, a block and a meteor from Steve's eyes to a point, each landing
there; the block displays carry the block asked for (block_state from the id alone), move along,
and are taken away when it lands."""

import re

from vfg_engine.livetest import verdict

DISPLAYS = "@e[type=block_display,x=3000,y=296,z=3000,distance=..60]"


def _count(t):
    got = re.search(r"Count: (\d+)", t.cmd("execute if entity %s" % DISPLAYS))
    return int(got.group(1)) if got else 0


def test(t):
    t.start("kit_fly", wait=5, at=t.at(0, 0, 20), look="orb", speed=20)
    t.check("the orb lands where it was sent", t.said("landed"))
    t.start("kit_fly", at=t.at(0, 0, 20), look="block", speed=8)
    t.wait(0.4)
    t.check("one block display flies", verdict(_count(t) == 1, "%d displays" % _count(t)))
    state = t.cmd("data get entity %s block_state" % DISPLAYS.replace("]", ",limit=1]"))
    t.check("it shows magma or obsidian (the mix)", verdict(re.search(r"magma_block|obsidian", state), state[:200]))
    z0 = t.cmd("data get entity %s Pos[2]" % DISPLAYS.replace("]", ",limit=1]"))
    t.wait(0.6)
    z1 = t.cmd("data get entity %s Pos[2]" % DISPLAYS.replace("]", ",limit=1]"))
    t.check("it moves along", verdict(z0 != z1, "%s -> %s" % (z0[-30:], z1[-30:])))
    t.wait(3)
    t.check("it is taken away when it lands", verdict(_count(t) == 0, "%d displays left" % _count(t)))
    t.start("kit_fly", at=t.at(0, 0, 20), look="meteor", speed=8)
    t.wait(0.4)
    n = _count(t)
    t.check("the meteor is 19 displays", verdict(n == 19, "%d displays" % n))
    shown = t.cmd("data get entity %s block_state" % DISPLAYS.replace("]", ",limit=1,sort=random]"))
    t.check("of the mix's blocks", verdict(re.search(r"magma_block|obsidian", shown), shown[:200]))
    t.wait(3.5)
    t.check("all taken away when it lands", verdict(_count(t) == 0, "%d displays left" % _count(t)))
    quiet = t.no_errors()                     # it watches beings; here only displays and sounds
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
