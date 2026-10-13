"""sneak_gesture on a real server: three quick crouches (Carpet's sneak and unsneak) give speed;
a held crouch gives haste while it is held."""


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
    t.check("no error in the engine's log", t.no_errors())
