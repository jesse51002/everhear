"""melody / countdown / speak on a real server: a tune played out on the kit's timers, a countdown
that calls back at zero, a voice's lines (the § colours ride in the text: whether a client shows
them needs a person's client, which a fake player has not)."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("kit_show", wait=4, what="tune")
    t.check("the tune plays out (1.5 s, then its end)", t.said("tune 2.0 s"))
    t.start("kit_show", wait=6, what="count")
    t.check("the countdown calls back at zero", t.said("zero"))
    t.start("kit_show", wait=3, what="speak")
    t.check("the lines are said", t.said("spoken"))
    quiet = t.no_errors()                     # it watches beings; here only sounds and messages
    t.check("Minecraft took every sound, title and tellraw (no error in the engine's log)",
            verdict(quiet[0] is not False, quiet[1]))
