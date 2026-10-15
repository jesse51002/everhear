"""hollow_purple_beam, live: granted and bound, a heard cast merges its orbs for 0.5 s then fires
a beam that kills a husk 10 ahead."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=1000)
    t.grant("hollow_purple_beam")
    t.bind("hollow purple", "hollow_purple_beam")
    t.check("the cast is taken", t.cast("hollow purple"))
    t.watch(2)
    t.check("the beam kills the husk", t.killed(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
