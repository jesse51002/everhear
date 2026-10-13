"""The test of wither_wand: Steve right-clicks the wand looking at the floor, and withers appear
where he looks."""
from kit import test

t = test.Test("Steve")
t.run("wither_wand", player="Steve", summon_count=2, cooldown=1)
t.check("Steve has the Wither Wand", t.holds("Wither Wand"))
t.look(pitch=15)                          # at the floor 6 blocks ahead
t.use()
t.check("withers appear where he looks", t.near("wither", 0, 0, 6, radius=10, count=2))
t.check("no error in its log", t.no_errors())
t.done()
