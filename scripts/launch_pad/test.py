"""The test of launch_pad: Steve walks onto a slime block and is thrown forward, the way he
faces, about 12 blocks through the air, and lands unhurt."""
from kit import test

t = test.Test("Steve")
t.blocks("slime_block", 0, -1, 2, size=1)          # the pad, two blocks in front of him, in the floor
t.run("launch_pad", player="Steve", launch_way="forward", launch_strength=12, cooldown=10)
t.cmd("player Steve move forward")                  # he walks onto it
t.watch(1)
t.cmd("player Steve move")                          # and lets go of the key, in the air
t.watch(4)
t.check("Steve is thrown 10 blocks or more forward", t.moved(10, way="forward"))
t.check("he flies up on the way, 4 blocks or more", t.moved(4, way="up"))
t.check("he lands unhurt", t.unhurt())
t.check("no error in its log", t.no_errors())
t.done()
