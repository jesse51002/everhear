"""lightning on a real server: five bolts round a point, none near a spared player."""


def test(t):
    zombie = t.mob("zombie", 0, 0, 12, health=40)
    t.start("kit_lightning", wait=5, at=t.at(0, 0, 12))
    t.check("five bolts", t.said("5 bolts"))
    t.check("they strike there", t.near("lightning_bolt", 0, 0, 12, radius=5, count=3, within=1))
    t.watch(1.5)
    t.check("the zombie in the middle is struck", t.hurt(zombie, at_least=1))
    t.start("kit_lightning", wait=5, at=t.at(0, 0, 0), spare="Steve")
    t.check("none lands near Steve, who is spared", t.said("0 bolts"))
    t.watch(1.5)
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
