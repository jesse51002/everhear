"""The test of tnt_rain, on the world-event template: lit TNT falls round Steve onto real ground and
blows up, hurting him and holing the ground."""
from kit import test

t = test.Test("Steve")                        # faces north; Alex stands 4 to his left
ground = t.ground(0, 0, size=25)              # real ground round him: grass on dirt, short grass
t.run("tnt_rain", player="Steve", sky_count=6, sky_radius=3, sky_height=10, fuse=4, every=30)
t.check("lit TNT falls round him", t.near("tnt", 0, 0, 0, radius=14, count=4))
t.watch(5)
t.check("it blows up near him", t.hurt("Steve"))
t.check("it holes the ground", t.gone(ground, 0.01))
t.check("no error in its log", t.no_errors())
t.done()
