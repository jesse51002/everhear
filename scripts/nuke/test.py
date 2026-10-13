"""The test of nuke, on the area-blast template: a nuke on real ground 15 ahead counts down, then
kills what lives within its radius and carves the ground, sparing Steve and Alex, outside it."""
from kit import test

t = test.Test("Steve")                        # faces north; Alex stands 4 to his left
ground = t.ground(0, 15, size=9)              # real ground where it goes off: grass on dirt, short grass
husk = t.mob("husk", 0, 0, 15, health=40)     # in the blast
t.run("nuke", player="Steve", nuke_at=t.at(0, 0, 15), countdown_seconds=2, crater_radius=6)
t.watch(6)
t.check("it says it went off", t.said("went off"))
t.check("what lives within its radius dies", t.killed(husk))
t.check("the ground there is carved away", t.gone(ground, 0.3))
t.check("Steve, 15 blocks away, lives unhurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
