"""barrier_shield, live: granted, it throws a cow 3 blocks away back out. (Its default `what` is
"mobs", which barrier_shield.py's targets() call never resolves to players — death_gaze/main.py's
own around() preset gated players the same way, `with_players=what in ("players", "everything")` —
so a player is only launched when `what` is set to "players" or "everything"; that is covered by
the unit test, not this one.)"""

from vfg_engine.livetest import verdict


def test(t):
    cow = t.mob("cow", 3, 0, 0, health=20, still=False)   # still=False: it must be free to fly out
    t.grant("barrier_shield")
    t.watch(2)
    # from where it was placed, not from just after the grant: the first push can come in the grant's
    # own tick, before t.moved's mark, and then reads short
    pos = t.cmd("data get entity %s Pos" % t.mobs[cow])
    x = float(pos.split("[", 1)[1].split("d", 1)[0])
    placed = t.xyz(3, 0, 0)[0]
    t.check("the cow was thrown out", verdict(x - placed >= 2, "the cow is %.1f blocks further out" % (x - placed)))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
