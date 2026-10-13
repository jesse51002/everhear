"""The test of fireball_cast, on the cast-projectile template: Steve says "fire ball", and at once,
with nothing pressed, a fireball flies where he looks and explodes on the husk in front of a wall:
the husk is hurt, the wall broken, and he and Alex beside him are not hurt."""
from kit import test

t = test.Test("Steve")                        # faces north; Alex stands 4 to his left
t.ground(0, 10, size=9)                       # real ground round the target: grass on dirt, short grass
husk = t.mob("husk", 0, 0, 10, health=40)     # the target: 10 ahead, on his eye line
wall = t.blocks("stone", 0, 0, 12, size=3)    # a wall 2 behind it
t.bind("fireball_cast", "fire ball", explosion_power=3)
t.cast("fire ball")                           # the real trigger: it fires at once; press nothing after
t.watch(4)
t.check("it fires as it is said", t.said("Steve at"))
t.check("the fireball hurts the husk it hits", t.hurt(husk, at_least=5))
t.check("its blast breaks the wall behind", t.gone(wall, 0.05))
t.check("Steve, who said it, is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
