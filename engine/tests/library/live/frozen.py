"""frozen, live: applied to Steve, he is slowed, weakened and (asked) blinded; applied to a husk,
it is slowed too and shows the frozen look (TicksFrozen merged onto it).

t.apply's `who` must be a bearer the engine can find (a player's name or a real uuid): t.mob()'s
own name ("husk_1") is only the test's own label for it (t.mobs[name] is its selector), so the
real uuid is read off the husk itself first, as `data get entity ... UUID` gives it."""

import re

from vfg_engine.mc.commands import uuid_str


def _uuid_of(t, name):
    ints = [int(n) for n in re.findall(r"-?\d+", t.cmd("data get entity %s UUID" % t.mobs[name]))]
    return uuid_str(ints)


def test(t):
    t.apply("frozen", seconds=5, blind=True)
    t.watch(1)
    t.check("Steve is slowed", t.effect("slowness"))
    t.check("Steve is weakened", t.effect("weakness"))
    t.check("Steve is blinded", t.effect("blindness"))

    husk = t.mob("husk", 0, 0, 10, health=200)
    t.apply("frozen", who=_uuid_of(t, husk), seconds=5)
    t.watch(1)
    t.check("the husk is slowed", t.effect("slowness", husk))
    t.check("no error in the engine's log", t.no_errors())
