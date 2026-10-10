"""When a player dies, or kills something (a grave where they die, an effect on a kill): where it happened, who, and which of the two."""
import re
import time

import mc
from kit import items, loop, world
from actions._types import Literal, Live, Values  # the values class: read on the PC only

VALUES = {
    "on_death_event": {"type": "enum", "of": ["death", "kill", "both"], "default": "death", "live": True},
}


class OnDeath(Values):
    on_death_event: Live[Literal["death", "kill", "both"]] = "death"
    """what sets it off: death, when the player dies; kill, when they kill anything, a mob or a player; both"""


def on_death(player, v):
    """Yield (at, p, what) each time a player (of those `player` names) dies or kills
    something, as on_death_event asks: what is "death" or "kill". For a death, at is where
    they died (a Pos in that dimension); for a kill, where the killer stands, because the
    server does not say where the victim fell. Each player is followed on their own.

        for at, p, what in loop(on_death(v["player"], v)):
            ...
    """
    deaths = items.Stat(world.objective("deathCount", "deaths"), "deathCount")      # removed on a stop
    kills = items.Stat(world.objective("totalKillCount", "kills"), "totalKillCount")
    seen = {}                           # per objective and player: the count last read
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            time.sleep(1.0)
            continue
        died, killed = _on_death_more(deaths, seen), _on_death_more(kills, seen)
        event, due = v["on_death_event"], []
        for p in people:
            if died.get(p, 0) > 0 and event != "kill":
                due.append((_on_death_where(p), p, "death"))
            if killed.get(p, 0) > 0 and event != "death":
                at = mc.pos(p)
                if at:
                    due += [(world.Pos(at[0], at[1], at[2], world.dim_of(p)), p, "kill")] * killed[p]
        for at, p, what in due:
            if at is not None:
                yield at, p, what
        time.sleep(0.25)


def _on_death_more(stat, seen):
    """How much each online player's count went up since it was last read, also across a
    time they were away: someone who dies and leaves at once is counted when they are back.
    A player read for the first time counts 0."""
    cur = stat.scores()
    out = {p: n - seen[(stat.objective, p)] for p, n in cur.items()
           if (stat.objective, p) in seen and n > seen[(stat.objective, p)]}
    seen.update(((stat.objective, p), n) for p, n in cur.items())
    return out


def _on_death_where(p):
    """Where a player last died, from LastDeathLocation ({dimension: "minecraft:the_nether",
    pos: [I; 12, 64, -30]}): a Pos in the middle of that block; None if the server does not say."""
    reply = mc.rcon("data get entity %s LastDeathLocation" % p)
    at = re.search(r"pos: \[I; (-?\d+), (-?\d+), (-?\d+)\]", reply)
    dim = re.search(r'dimension: "([a-z0-9_.-]+:[a-z0-9_./-]+)"', reply)
    if not at:
        return None
    return world.Pos(int(at.group(1)) + 0.5, int(at.group(2)), int(at.group(3)) + 0.5, dim.group(1) if dim else None)
