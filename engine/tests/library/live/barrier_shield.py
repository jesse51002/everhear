"""barrier_shield, live: granted, it throws a cow 3 blocks away back out. (Its default `what` is
"mobs", which barrier_shield.py's targets() call never resolves to players — death_gaze/main.py's
own around() preset gated players the same way, `with_players=what in ("players", "everything")` —
so a player is only launched when `what` is set to "players" or "everything"; that is covered by
the unit test, not this one.)"""


def test(t):
    cow = t.mob("cow", 3, 0, 0, health=20, still=False)   # still=False: it must be free to fly out
    t.grant("barrier_shield")
    t.watch(2)
    t.check("the cow was thrown out", t.moved(2, who=cow))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
