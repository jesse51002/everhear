"""The test of {name}: {player} says "{phrase}" and at once, with nothing pressed, it moves him: a
dash, a jump, a launch, flight. One t.check per DONE WHEN item; edit the rest as the ability needs."""
from kit import test

t = test.Test("{player}")                     # faces north (forward is -z); Alex stands 4 to his left
t.cast("{phrase}")                            # the real trigger: it fires at once; press nothing after
t.watch(5)                                    # where he goes, 4 times a second
# TODO DONE WHEN: one t.check per item, low bars: t.moved(3, way="up"), t.moved(5, way="forward"), t.effect("slow_falling")
t.check("it moves him", t.moved(3))
t.check("{player} lands unhurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
