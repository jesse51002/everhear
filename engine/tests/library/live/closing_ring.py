"""closing_ring, live: the world border shuts onto Steve and shrinks, undead and arachnids arriving
with each tightening; stopped early here (it otherwise runs about 85 s before it releases on its
own, covered by the unit test)."""

import time

from vfg_engine.livetest import verdict

MOB_KINDS = ("zombie", "skeleton", "spider", "creeper")       # closing_ring.py's own MOBS


def _a_mob_arrived(t, deadline):
    """Round-robin the four kinds closing_ring's own wave draws from, rather than a long within on
    each in turn (which would chain to four times the wait on a flat miss): this box's own engine,
    shared with other agents' servers, sometimes answers several seconds late, and a creeper (one of
    the four) can otherwise reach, prime and blow itself up on Steve before a slower check looks."""
    v = None
    while True:
        for kind in MOB_KINDS:
            v = t.near(kind, 0, 0, 0, radius=20, count=1, within=0.1)
            if v[0]:
                return v
        if time.time() > deadline:
            return v
        time.sleep(0.5)


def test(t):
    # self.result() fires only once it releases, which this never reaches (stopped early instead):
    # what it says in chat as it starts is not checked here, only the mobs its first wave brings.
    t.start("closing_ring", wait=3, players="Steve", level=4)
    # settle_s (5), then begin_squeeze only schedules the first wave, due wave_every_s (10) later
    # still: settle_s + wave_every_s == 15 s before the first tightening actually arrives. Checking
    # soon after that, not letting it run on to a second wave or beyond, keeps this clear of the
    # sustained, knockback-heavy combat a longer run risks on this stage's own 40-block-radius floor
    # (its mobs can otherwise walk a player back over the edge; see closing_ring.py's own NEEDS).
    t.check("mobs have come with the first tightening", _a_mob_arrived(t, time.time() + 15))
    t.stop("closing_ring")
    t.wait(0.5)
    running = t.running("closing_ring")
    t.check("a stop ends it and opens the border back up", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
