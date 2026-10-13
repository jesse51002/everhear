"""The test of super_saiyan: three crouches power Steve up (speed and strength), and looking up
he flies."""
from kit import test

t = test.Test("Steve")
t.run("super_saiyan", player="Steve", charge_seconds=1, seconds=30)
t.check("Steve has the star", t.holds("Super Saiyan"))
t.sneak(3)
t.wait(3)
t.check("he powers up: speed", t.effect("speed"))
t.check("and strength", t.effect("strength"))
t.look(pitch=-60)
t.watch(3)
t.check("looking up, he flies", t.moved(2, way="up"))
t.check("no error in its log", t.no_errors())
t.done()
