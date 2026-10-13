"""The test of boom_bow, on the item-use template: Steve draws his bow at a husk standing in front of
a wall; the arrow explodes where it hits, hurting the husk and breaking the wall, and neither he nor
Alex beside him is hurt."""
from kit import test

t = test.Test("Steve")                        # faces north; Alex stands 4 to his left
t.ground(0, 10, size=9)                       # real ground round the target: grass on dirt, short grass
husk = t.mob("husk", 0, 0, 10, health=40)     # the target: 10 ahead, on his eye line
wall = t.blocks("stone", 0, 0, 12, size=3)    # a wall 2 behind it
t.run("boom_bow", player="Steve", explosion_power=4)
t.cmd("give Steve bow")                       # a bow of his own, as a player brings one
t.cmd("give Steve arrow 16")
t.use(draw=1.2)                               # draws the bow and lets go
t.watch(4)
t.check("the arrow explodes on the husk it hits", t.hurt(husk, at_least=5))
t.check("the blast breaks the wall behind it", t.gone(wall, 0.05))
t.check("Steve, who shot it, is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
