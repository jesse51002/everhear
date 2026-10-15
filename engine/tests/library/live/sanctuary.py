"""sanctuary, live: a golden platform rises somewhere out from Steve; since he never walks to it,
he is struck by lightning, weakened, and set on by a few zombies once the time runs out (level 5:
2 + level // 3 == 3 zombies, a few, not level 20's 8 - too many for Steve's own 20 HP and no gear
to reliably survive the few seconds this takes to check, which left him dead and disconnected, his
effects unreadable, more often than not)."""

from vfg_engine.livetest import verdict


def test(t):
    # self.result() fires only once judged, at the end (max(min_time_s, max_time_s - level) == 40 s
    # for level 5), past t.start's own wait_s cap (MAX_WAIT_S, 30 s) once the 3 s start wait and this
    # wait are both counted from the same start: a result that late never reaches t.said, so what it
    # says in chat as it starts is not checked here, and its ending is read through t.running below,
    # not through t.said.
    t.start("sanctuary", wait=3, players="Steve", level=5)
    t.wait(39)
    running = t.running("sanctuary")
    t.check("it ends once the time is up", verdict(not running[0], running[1]))
    t.check("Steve is struck and punished, never having reached it", t.hurt("Steve"))
    t.check("Steve is weakened", t.effect("minecraft:weakness"))
    t.check("zombies have come for him", t.near("zombie", 0, 0, 0, radius=10, count=1))
    t.check("no error in the engine's log", t.no_errors())
