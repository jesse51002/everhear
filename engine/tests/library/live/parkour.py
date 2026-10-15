"""parkour, live: a jumping course rises between a start and a golden goal, and Steve is moved onto
it; stopped early here (it otherwise runs 90 s or more before it scores and ends, covered by the
unit test). Its course sits at Config.y (230 by default), well below the stage's own safe-to-build
band (y 280 up, per the library README's "Running a live test on realmc"), so this checks the
move onto the course rather than specific blocks at that height; self.result() fires only once it
finishes, which this never reaches (stopped early instead), so what it says in chat as it starts
is not checked either."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("parkour", wait=3, players="Steve", level=1)
    t.check("Steve is moved onto the course", t.moved(at_least=1))
    t.stop("parkour")
    t.wait(0.5)
    running = t.running("parkour")
    t.check("a stop ends it, undoing its blocks and putting fall damage and PvP back", verdict(not running[0],
           running[1]))
    t.check("no error in the engine's log", t.no_errors())
