"""The test of spirit_bomb: Steve holds right-click, the bomb gathers overhead, is thrown at a
pillar where he looks and blows up: what is near it is hurt, a crater is carved, and he is not."""
from kit import test

t = test.Test("Steve")
pillar = t.blocks("stone", 0, 0, 15, size=3)
husk = t.mob("husk", 3, 0, 13, health=100)
t.run("spirit_bomb", player="Steve", charge_seconds=1, crater_radius=4, explosion_power=3,
      explosion_layout="one", reach=40)
t.check("Steve has the Spirit Bomb", t.holds("Spirit Bomb"))
t.use(hold=1.5)
t.watch(7)
t.check("the blast hurts what is near it", t.hurt(husk))
t.check("it carves a crater", t.gone(pillar, 0.5))
t.check("Steve, who threw it, is not hurt", t.unhurt())
t.check("Alex, beside him, is not hurt", t.others_unhurt())
t.check("no error in its log", t.no_errors())
t.done()
