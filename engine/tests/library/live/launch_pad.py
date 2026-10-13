"""launch_pad, live: a slime block under Steve's feet throws him straight up, landing safely
(the kit's push, by wind charges)."""


def test(t):
    t.blocks("slime_block", 0, -1, 0, size=1)          # the floor block right under his feet becomes the pad
    t.start("launch_pad", wait=2, players="Steve", block="slime_block", way="up", strength=10, cooldown=3)
    t.check("it says so", t.said("standing on minecraft:slime_block throws them 10 blocks up"))
    t.watch(3)
    t.check("he was launched upward", t.moved(6, way="up"))
    t.check("he lands without fall damage", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
