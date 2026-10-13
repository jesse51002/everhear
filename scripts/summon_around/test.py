"""The test of summon_around: five zombies come round Steve, within its radius."""
from kit import test

t = test.Test("Steve")
t.run("summon_around", players="Steve", mob="zombie", count=5, radius=8)
t.watch(2)
t.check("five zombies come round him", t.near("zombie", 0, 0, 0, radius=12, count=5))
t.check("no error in its log", t.no_errors())
t.done()
