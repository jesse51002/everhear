"""chosen_one, live: the cinematic for Steve, naming him and handing him the blade."""


def test(t):
    t.start("chosen_one", wait=25, player="Steve", blade="Test Blade")
    t.check("it says Steve is chosen", t.said("Steve is the chosen one"))
    t.check("he holds the blade", t.holds("netherite_sword"))
    t.check("no error in the engine's log", t.no_errors())
