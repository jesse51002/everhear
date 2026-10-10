"""Kill FX: sounds for each player alone: a sting when they die, rising chimes for a kill streak, hit markers and a thud when hurt."""
ASKS = ["kill streak sounds", "hit markers", "a sound when I get a kill"]
from kit import values, items, loop, world
import re
import time
import mc

VALUES = {
    "player":         {"type": "player", "default": "@a"},
    "on_death_event": {"type": "enum", "of": ["death", "kill", "both"], "default": "both", "live": True},
    "streak_s":       {"type": "number", "min": 1, "max": 60, "default": 8, "live": True},
    "hit_markers":    {"type": "bool", "default": True, "live": True},
    "hurt_sound":     {"type": "bool", "default": True, "live": True},
    "volume":         {"type": "number", "min": 0.1, "max": 4, "default": 1, "live": True},
}


# --- preset: on_death (copied from _presets/on_death.py) ---
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


v = values(VALUES)


def play(p, sound, volume, pitch):
    """A sound only p hears."""
    mc.rcon("execute as %s at @s run playsound %s master @s ~ ~ ~ %g %g" % (p, sound, volume * v["volume"], pitch))


def hits():
    """Each quarter second: a hit marker for whoever dealt damage, a thud for whoever took it."""
    dealt = items.Stat(world.objective("minecraft.custom:minecraft.damage_dealt", "dealt"),
                       "minecraft.custom:minecraft.damage_dealt")
    taken = items.Stat(world.objective("minecraft.custom:minecraft.damage_taken", "taken"),
                       "minecraft.custom:minecraft.damage_taken")
    scored = set()
    while not mc.stopping():
        people = loop.who(v["player"])
        if not scored.issuperset(people):
            # A player with no score yet makes items.Stat misread the next name (see on_death).
            for stat in (dealt, taken):
                mc.rcon("scoreboard players add @a %s 0" % stat.objective)
            scored.update(people)
        if v["hit_markers"]:
            for p, n in dealt.changes().items():
                if n > 0 and p in people:
                    play(p, "entity.arrow.hit_player", 0.6, 1.6)
        else:
            dealt.changes()
        if v["hurt_sound"]:
            for p, n in taken.changes().items():
                if n > 0 and p in people:
                    play(p, "entity.player.hurt_sweet_berry_bush", 0.5, 0.6)
        else:
            taken.changes()
        loop.wait(0.25)


loop.background(hits)
mc.log("kill fx on")
streak, streak_at = {}, {}
for at, p, what in loop(on_death(v["player"], v)):
    if what == "death":
        streak[p] = 0
        play(p, "entity.player.death", 1, 0.7)
        mc.log(p, "died")
        continue
    now = time.time()
    streak[p] = streak.get(p, 0) + 1 if now - streak_at.get(p, 0) < v["streak_s"] else 1
    streak_at[p] = now
    play(p, "entity.experience_orb.pickup", 1, min(0.8 + 0.2 * streak[p], 2.0))
    if streak[p] >= 3:
        play(p, "entity.firework_rocket.twinkle", 1, 1)
    mc.log(p, "kill streak", streak[p])
