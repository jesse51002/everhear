"""The test of ice_beam: Steve right-clicks the staff, and the beam freezes and blinds what it hits
and breaks a tunnel through a wall, leaving ice round it."""
from kit import test

t = test.Test("Steve")
husk = t.mob("husk", 0, 0, 8, health=100)
wall = t.blocks("stone", 0, 0, 14, size=5)
t.run("ice_beam", player="Steve", length=20, cooldown=1)
t.check("Steve has the Ice Beam Staff", t.holds("Ice Beam Staff"))
t.use()
t.watch(3)
t.check("what it hits is frozen", t.effect("slowness", husk))
t.check("and blinded", t.effect("blindness", husk))
t.check("it breaks a tunnel through the wall", t.gone(wall, 0.1))
t.check("with ice round the tunnel", t.turned(wall, "packed_ice", 0.1))
t.check("Steve is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
