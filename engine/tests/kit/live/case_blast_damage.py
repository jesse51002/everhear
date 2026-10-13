"""blast that breaks nothing, on a real server: falloff damage and a push away from the middle,
a villager spared, the blocks round it whole."""


def test(t):
    near = t.mob("zombie", 2, 0, 10, health=100, still=False)
    far = t.mob("zombie", -5, 0, 10, health=100, still=False)
    villager = t.mob("villager", -2, 0, 10, health=20)
    cube = t.blocks("stone", 0, 0, 13, size=3)
    t.start("kit_blast", wait=5, at=t.at(0, 0, 10), power=4, breaks=False)
    t.watch(2)
    t.check("the zombie 2 away is hurt", t.hurt(near, at_least=20))
    t.check("the zombie 5 away is hurt less", t.hurt(far, at_least=1))
    t.check("and not as much", not t.hurt(far, at_least=20)[0])
    t.check("the zombie is thrown back", t.moved(1, who=near))
    t.check("the villager is spared", t.unhurt(villager))
    t.check("nothing breaks", t.built(cube, share=1.0))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
