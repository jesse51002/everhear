"""The livetest runner's live test of a one-shot build: test_pillar ends within its start's wait;
it ran and placed blocks, so unhurt and no_errors can say (PASS, not CAN'T TELL), and its result is
said. The last check comes after a held crouch nothing listens to: CAN'T TELL, as it should be."""


def test(t):
    cube = t.region(0, 0, 8)
    t.start("test_pillar", wait=10, at=t.at(0, 0, 8))
    t.check("the cube is built", t.built(cube, 0.9))
    t.check("its result says so", t.said("stone cube"))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
    t.sneak(hold=1.0)                                  # a held crouch: nothing listens, nothing breaks
    t.check("still no error after a held crouch", t.no_errors())
