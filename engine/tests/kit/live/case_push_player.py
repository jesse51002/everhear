"""push on a player, on a real server: Steve thrown up, then forward, by wind charges spawned at his
feet already falling (their Motion in the summon's NBT), landing unhurt."""


def test(t):
    t.start("kit_push", wait=5, player="Steve", way="up", strength=8)
    t.check("Steve is thrown", t.said("1 thrown"))
    t.watch(3)
    t.check("up about 8 blocks", t.moved(5, way="up"))
    t.check("he lands unhurt", t.unhurt())
    t.start("kit_push", wait=5, player="Steve", way="forward", strength=10)
    t.check("Steve is thrown again", t.said("1 thrown"))
    t.watch(3)
    t.check("forward about 10 blocks", t.moved(6, way="forward"))
    t.check("he lands unhurt again", t.unhurt())
    t.check("Alex, 4 away, is not hurt by the bursts", t.others_unhurt())
    t.check("no error in the engine's log", t.no_errors())
