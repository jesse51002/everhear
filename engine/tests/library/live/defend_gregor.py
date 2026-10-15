"""defend_gregor, live: a hut and Old Gregor rise near Steve with a chest of supplies, a horde
coming for him every 12 s; stopped early here (it otherwise runs well over a minute before it
scores and ends, covered by the unit test)."""

import time

from vfg_engine.livetest import verdict


def _block_arrives(t, right, up, forward, block, deadline):
    """t.block has no retry of its own: the hut's block queue, on this box's engine (shared with
    other agents' servers), can answer a build a little late under load."""
    v = t.block(right, up, forward, block)
    while not v[0] and time.time() < deadline:
        time.sleep(0.5)
        v = t.block(right, up, forward, block)
    return v


def test(t):
    # self.result() fires only once it finishes, which this never reaches (stopped early instead):
    # what it says in chat as it starts is not checked here, only the hut and Gregor it raises.
    t.start("defend_gregor", wait=3, players="Steve", level=1)
    t.check("Old Gregor stands in his hut", t.near("villager", 6, 0, -6, radius=15, count=1))
    t.check("a supply chest sits beside him", _block_arrives(t, 7, 0, -7, "minecraft:chest", time.time() + 6))
    t.stop("defend_gregor")
    t.wait(0.5)
    running = t.running("defend_gregor")
    t.check("a stop ends it and opens the border back up", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
