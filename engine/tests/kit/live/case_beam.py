"""hitscan / hit / beam_line on a real server: a headshot on the eye line, a villager on the line
spared, a husk behind a wall not reached, nobody else hurt."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=40)               # on his eye line: its head
    villager = t.mob("villager", 0.5, 0, 6, health=20)      # on the line too: spared
    t.blocks("stone", 0, 0, 16, size=3)                     # a wall 15 to 17 ahead
    behind = t.mob("husk", 0, 0, 20, health=40)
    t.grant("kit_beam")
    t.bind("kit beam", "kit_beam")
    t.check("the cast is taken", t.cast("kit beam"))
    t.watch(2)
    t.check("the husk takes a headshot: 8 doubled", t.hurt(husk, at_least=15))
    t.check("the villager on the line is spared", t.unhurt(villager))
    t.check("the husk behind the wall is not reached", t.unhurt(behind))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
