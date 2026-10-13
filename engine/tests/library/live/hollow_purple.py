"""hollow_purple, live: right-click merges a blue and a red orb for 0.5 s, then a beam through a
stone wall kills a husk 10 ahead; it carves a tunnel to a crater at its end. Steve is untouched."""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=1000)
    t.blocks("stone", 0, 0, 5)                       # a wall between them: the beam goes through
    t.give("hollow_purple")
    t.check("Steve holds hollow purple", t.holds("hollow_purple"))
    t.use()
    t.watch(2)                                       # 0.5 s charging, then the beam
    t.check("the beam kills the husk behind the wall", t.killed(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.wait(2)                                        # the crater's block queue drains
    t.check("the beam's end is carved open", t.block(0, 2, 10, "air"))   # 2: Steve's eye height, up from his feet
    t.check("no error in the engine's log", t.no_errors())
