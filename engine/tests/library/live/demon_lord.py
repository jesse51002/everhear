"""demon_lord, live: round 1 begins, announced, with a supply chest beside Steve; a stop ends it.
Its round loop (armed for 20 s, a wave, cleared once its mobs are dead, scored, then the next
round's break) is on the fake clock (tests/library/test_gamemaster.py:
test_a_round_supplies_arms_and_scores_a_clean_wave and the tests after it); the minigames and the
boss cycle are each proven live on their own (blood_moon.py, lightning_storm.py, closing_ring.py,
sanctuary.py, defend_gregor.py, parkour.py, gravekeeper.py, spider_queen.py, ravager_warlord.py,
hollow_king.py)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("demon_lord", wait=3, round=1, level=1)
    t.check("it begins at round 1", t.said("the Demon Lord begins at round 1"))
    t.wait(1)
    t.check("a supply chest sits beside Steve (east, +X)", t.block(1, 0, 0, "minecraft:chest"))
    t.check("it runs on, arming the round", t.running("demon_lord"))
    t.stop(iid)
    running = t.running("demon_lord")
    t.check("a stop ends it", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
