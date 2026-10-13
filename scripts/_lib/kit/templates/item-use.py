"""The test of {name}: {player} gets its item, then uses it as the brief says: a click, a hold, a
charge, a bow. One t.check per DONE WHEN item; edit the rest as the ability needs."""
from kit import test

t = test.Test("{player}")                     # faces north; Alex stands 4 to his left
t.ground(0, 10, size=9)                       # real ground round the target: grass on dirt, short grass
husk = t.mob("husk", 0, 0, 10, health=40)     # the target: 10 ahead, on his eye line
wall = t.blocks("stone", 0, 0, 12, size=3)    # a wall 2 behind it
t.run("{name}", player="{player}")            # gives him the item, as the action does
t.check("{player} has the {item}", t.holds("{item}"))
t.use()                                       # a click; t.use(hold=3) held; t.use(draw=1.2) a bow; t.sneak(3)
t.watch(4)
# TODO DONE WHEN: one t.check per item, low bars: t.gone(wall, 0.05), t.effect("slowness", husk), t.said("fired")
t.check("it hits what he looks at", t.hurt(husk))
t.check("{player} is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
