"""The test of railgun: Steve clicks the gun, and the beam hurts what it passes through a wall,
and not him."""
from kit import test

t = test.Test("Steve")
husk = t.mob("husk", 0, 0, 10, health=100)
wall = t.blocks("stone", 0, 0, 6, size=3)
t.run("railgun", player="Steve", damage_amount=30, cooldown=1, range=30)
t.check("Steve has the Railgun", t.holds("Railgun"))
t.use()
t.watch(2)
t.check("the beam goes through the wall and hurts what it hits", t.hurt(husk, at_least=10))
t.check("the wall stands", t.turned(wall, "stone", 1.0))
t.check("Steve is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
