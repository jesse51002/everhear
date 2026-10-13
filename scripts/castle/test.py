"""The test of castle, on the build template: a castle in front of Steve: stone brick walls and a
keep, cobblestone towers."""
from kit import test

t = test.Test("Steve")                        # faces north; Alex stands 4 to his left
site = t.region(0, 0, 13, size=15)            # where it goes: 6 to 20 ahead, 7 left to 7 right, 0 to 14 up
t.run("castle", player="Steve", castle_size=15, castle_ahead=6)
t.watch(10)                                   # time to build
t.check("something is built in front of him", t.built(site, 0.05))
t.check("its walls and keep are stone bricks", t.turned(site, "stone_bricks", 0.05))
t.check("its towers are cobblestone", t.turned(site, "cobblestone", 0.02))
t.check("Steve is not hurt", t.unhurt())
t.check("no error in its log", t.no_errors())
t.done()
