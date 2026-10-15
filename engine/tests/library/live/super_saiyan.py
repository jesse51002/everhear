"""super_saiyan, live: three crouches power Steve up into Super Saiyan form (speed, strength); three
more power him back down."""

from vfg_engine.livetest import verdict


def test(t):
    t.grant("super_saiyan")
    t.sneak(times=3)
    t.watch(3)
    t.check("Steve has speed", t.effect("speed"))
    t.check("Steve has strength", t.effect("strength"))
    t.check("the form is on", t.running("super_saiyan_form"))
    t.sneak(times=3)
    t.watch(3)
    running = t.running("super_saiyan_form")
    t.check("the form ends", verdict(not running[0], running[1]))
    t.check("no error in the engine's log", t.no_errors())
