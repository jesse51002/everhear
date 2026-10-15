"""The livetest runner's live test of test_zap: a husk on Steve's eye line, the ability granted and
bound, one cast through the engine as the bridge sends it, then one inside its cooldown."""


def test(t):
    husk = t.mob("husk", 0, 0, 6, health=40)      # 6 ahead, on his eye line
    t.grant("test_zap")
    t.bind("zap it", "test_zap")
    t.check("the cast is taken", t.cast("zap it"))
    t.watch(2)
    t.check("it hurts the husk", t.hurt(husk, at_least=3))
    t.check("Steve is not hurt", t.unhurt())
    t.check("Alex is not hurt", t.others_unhurt())
    t.check("a second cast inside its cooldown is refused", not t.cast("zap it"))
    t.check("no error in the engine's log", t.no_errors())
