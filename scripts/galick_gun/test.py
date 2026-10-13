"""The test of galick_gun: Steve right-clicks, it charges, then the beam hurts what it hits and
blows a crater at its end."""
from kit import test

t = test.Test("Steve")
husk = t.mob("husk", 0, 0, 8, health=100)
end = t.blocks("stone", 0, 0, 20, size=5)
t.run("galick_gun", player="Steve", length=20, charge_seconds=1, crater_radius=3)
t.check("Steve has the Galick Gun", t.holds("Galick Gun"))
t.use()
t.watch(5)
t.check("the beam hurts what it hits", t.hurt(husk, at_least=10))
t.check("it blows a crater at its end", t.gone(end, 0.1))
t.check("Steve is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
