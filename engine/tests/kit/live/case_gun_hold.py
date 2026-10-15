"""held_use on a real server, a right-click held 2 s (Carpet's use at a person's rate): pressed,
held down, charged after 1 s, let go: each callback's potion shows on Steve."""


def test(t):
    t.give("kit_gun")
    t.check("Steve holds the gun", t.holds("kit_gun"))
    t.wait(0.5)
    t.use(hold=2)
    t.watch(1)
    t.check("on_press: speed", t.effect("speed"))
    t.check("on_hold, every 0.5 s while held: haste", t.effect("haste"))
    t.check("on_charged, held 1 s: jump boost", t.effect("jump_boost"))
    t.check("on_release when let go: glowing", t.effect("glowing"))
    t.check("no error in the engine's log", t.no_errors())
