"""lightning_storm, live: thunder held, a bolt falls near Steve every 2 s, 10 + level times, then it
clears and ends."""

from vfg_engine.livetest import verdict


def test(t):
    # self.result() fires only once it ends, at (10 + level) x 2 s (22 s), past t.start's own
    # wait_s cap (MAX_WAIT_S, 30 s) once the 3 s settle and this wait are both counted from the
    # same start: a result that late never reaches t.said, so what it says in chat as it starts is
    # checked through the weather it holds instead, and its ending through t.running and the
    # weather clearing, not through t.said.
    t.start("lightning_storm", wait=3, players="Steve", level=1)
    t.check("it is thundering", t.weather("thunder"))
    # not "Steve is unhurt": strike() picks a bolt's spot the same way the old script's lightning_storm
    # did (randint(-spread, spread) on each axis, with no minimum), so a bolt can land right on a
    # player by chance, same as it always could; that is kept, not a porting bug to guard against here.
    t.wait(23)
    running = t.running("lightning_storm")
    t.check("it ends after its strikes", verdict(not running[0], running[1]))
    t.check("the weather clears again", t.weather("clear"))
    t.check("no error in the engine's log", t.no_errors())
