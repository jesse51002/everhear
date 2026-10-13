"""super_saiyan_form, live: applied directly, Steve gets speed, strength and jump boost."""


def test(t):
    t.apply("super_saiyan_form", seconds=10)
    t.watch(2)
    t.check("Steve has speed", t.effect("speed"))
    t.check("Steve has jump boost", t.effect("jump_boost"))
    t.check("no error in the engine's log", t.no_errors())
