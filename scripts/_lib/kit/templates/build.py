"""The test of {name}: {player} says "{phrase}" and something is built in front of him. One t.check
per DONE WHEN item; edit the rest as the ability needs."""
from kit import test

t = test.Test("{player}")                     # faces north; Alex stands 4 to his left
site = t.region(0, 0, 10, size=15)            # where it goes: 3 to 17 ahead, 7 left to 7 right, 0 to 14 up
t.cast("{phrase}")                            # the real trigger: it builds at once; press nothing after
t.watch(8)                                    # time to build
# TODO DONE WHEN: one t.check per item, low bars: t.turned(site, "stone_bricks", 0.02), t.block(0, 0, 5, "oak_door")
t.check("something is built in front of him", t.built(site, 0.02))
t.check("{player} is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
