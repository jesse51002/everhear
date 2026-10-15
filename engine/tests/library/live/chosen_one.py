"""chosen_one, live: the cinematic's opening for Steve: slowness and darkness at once, then the giant
glowing figure ahead of him; a stop takes it away. The whole 18 s timeline (five whispered lines, the
blade given, the figure gone, the result) is on the fake clock (tests/library/test_visitors.py:
test_chosen_one_names_steve_and_gives_the_blade)."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("chosen_one", wait=0, player="Steve", blade="Test Blade")
    t.check("Steve is slowed", t.effect("minecraft:slowness"))
    t.check("and in the dark", t.effect("minecraft:darkness"))
    t.check("the giant figure appears ahead of him", t.near("mannequin", 0, 4, 9, radius=6, count=1, within=5))
    t.stop(iid)
    t.wait(0.5)
    gone = t.near("mannequin", 0, 4, 9, radius=8, count=1, within=0)
    t.check("a stop takes it away", verdict(not gone[0], gone[1]))
    t.check("no error in the engine's log", t.no_errors())
