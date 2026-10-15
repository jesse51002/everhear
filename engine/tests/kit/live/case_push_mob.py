"""push on mobs, on a real server: zombies round a point thrown away from it, a villager left."""


def test(t):
    a = t.mob("zombie", 2, 0, 10, health=40, still=False)
    b = t.mob("husk", -2, 0, 10, health=40, still=False)
    villager = t.mob("villager", 0, 0, 12, health=20, still=False)
    t.start("kit_push", wait=5, at=t.at(0, 0, 10), way="away", strength=8)
    t.check("two thrown", t.said("2 thrown"))
    t.watch(3)
    t.check("the zombie flies off", t.moved(4, who=a))
    t.check("the husk flies off", t.moved(4, who=b))
    t.check("the villager is left where it was", not t.moved(1, who=villager)[0])
    t.check("the zombie lands unhurt (a safe landing)", t.unhurt(a))
    t.check("no error in the engine's log", t.no_errors())
