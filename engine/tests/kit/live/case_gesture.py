"""sneak_gesture on a real server: three quick crouches (Carpet's sneak and unsneak) give speed;
a held crouch gives haste while it is held."""

from vfg_engine.livetest import verdict


def test(t):
    iid = t.start("kit_gesture")
    t.sneak(3)
    t.watch(1)
    t.check("three quick crouches: the gesture", t.effect("speed"))
    t.stop(iid)
    t.start("kit_gesture", mode="held")
    t.cmd("player Steve sneak")
    t.watch(1.5)
    t.cmd("player Steve unsneak")
    t.check("held: on_sneaking while he crouches", t.effect("haste"))
    t.stop("kit_gesture")
    t.wait(1.5)                                       # the last haste (1 s) wears off
    t.start("kit_gesture", mode="held")
    t.cmd("player Steve sneak")
    t.wait(0.15)                                      # a short crouch: the port's death_gaze miss
    t.cmd("player Steve unsneak")
    t.wait(0.2)
    now = t.cmd("data get entity Steve active_effects")     # now, not the trace: the first haste is gone
    t.check("held: a 0.15 s crouch still counts", verdict('"minecraft:haste"' in now, now[-160:] or "none"))
    t.check("no error in the engine's log", t.no_errors())
