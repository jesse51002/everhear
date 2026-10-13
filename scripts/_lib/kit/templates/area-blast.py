"""The test of {name}: {player} says "{phrase}" and it goes off at a place: a blast, a crater, fire
round it. One t.check per DONE WHEN item; edit the rest as the ability needs."""
from kit import test

t = test.Test("{player}")                     # faces north; Alex stands 4 to his left
ground = t.ground(0, 10, size=9)              # real ground where it goes off: grass on dirt, short grass
husk = t.mob("husk", 0, 0, 10, health=40)     # in the blast: 10 ahead, on his eye line
wall = t.blocks("stone", 0, 0, 12, size=3)    # a wall 2 behind it
t.cast("{phrase}")                            # the real trigger; a place value: t.at(0, 0, 10)
t.watch(5)
# TODO DONE WHEN: one t.check per item, low bars: t.gone(ground, 0.05), t.gone(wall, 0.05), t.fire_near(0, 0, 10)
t.check("it hurts what is in it", t.hurt(husk))
t.check("{player}, 10 away, is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
