"""The test of {name}: {player} says "{phrase}" and the world round him changes: mobs, weather, a
rain of things. One t.check per DONE WHEN item; edit the rest as the ability needs."""
from kit import test

t = test.Test("{player}")                     # faces north; Alex stands 4 to his left
t.ground(0, 0, size=25)                       # real ground round him: grass on dirt, short grass to burn
t.cast("{phrase}")                            # the real trigger: it fires at once; press nothing after
t.watch(5)
# TODO DONE WHEN: one t.check per item, low bars: t.near("zombie", 0, 0, 0, radius=16, count=3),
# t.weather("rain"), t.near("lightning_bolt", 0, 0, 0, radius=20), t.fire_near(0, 0, 0, radius=10)
t.check("{player} is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
