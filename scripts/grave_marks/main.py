"""Grave Marks: where a player dies, a skull and a glowing name mark the spot for a while, and chat says where."""
ASKS = ["mark where I died", "show me where I died", "a grave where you die"]
from kit import values, items, loop, world
import itertools
import json
import math
import re
import threading
import time
import mc

VALUES = {
    "player":         {"type": "player", "default": "@a"},
    "on_death_event": {"type": "enum", "of": ["death", "kill", "both"], "default": "death", "live": True},
    "seconds":        {"type": "number", "min": 5, "max": 3600, "default": 30, "live": True},
    "found_within":   {"type": "number", "min": 0, "max": 16, "default": 3, "live": True},
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
graves = {}         # its tag -> {"p", "at" (the skull's block), "dim", "made", "left"}
lock = threading.Lock()
GRAVE_IDS = itertools.count(1)


def clear(tag):
    """Take one mark away: its skull and its floating name."""
    with lock:
        g = graves.pop(tag, None)
    if g:
        x, y, z = g["at"]
        mc.rcon(world.run(g["dim"], "fill %d %d %d %d %d %d air replace skeleton_skull" % (x, y, z, x, y, z)))
        mc.rcon("kill @e[tag=%s]" % tag)


def clear_all():
    with lock:
        tags = list(graves)
    for tag in tags:
        clear(tag)


def mark(at, p, what, n):
    x, y, z = at.block()
    tag = "%s_g%d" % (mc.TAG, next(GRAVE_IDS))
    mc.rcon(world.run(at.dim, "setblock %d %d %d skeleton_skull replace" % (x, y, z)))
    label = "%s died here (#%d)" % (p, n) if what == "death" else "%s made a kill here (#%d)" % (p, n)
    mc.rcon(world.run(at.dim, 'summon armor_stand %d.5 %d %d.5 {Invisible:1b,Marker:1b,NoGravity:1b,Glowing:1b,'
                              'CustomNameVisible:1b,Tags:["vfg","%s","%s"],CustomName:%s}'
                      % (x, y + 1, z, mc.TAG, tag, json.dumps({"text": label, "color": "red"}))))
    with lock:
        graves[tag] = {"p": p, "at": (x, y, z), "dim": at.dim, "made": time.time(), "left": False}
    where = (at.dim or world.OVERWORLD).split(":")[-1]
    mc.rcon("tellraw @a " + json.dumps([
        {"text": "[Grave] ", "color": "dark_red", "bold": True},
        {"text": "%s %s at %d %d %d (%s). A skull marks the spot." % (p, "died" if what == "death" else "made a kill",
                                                                      x, y, z, where),
         "color": "gray", "bold": False}]))
    mc.log(p, what, "at", x, y, z, where)


def watch():
    """Each second: a mark that is old enough fades, and so does one its player walks back
    to (once they have been away from it: while they lie dead on it, it stays)."""
    while not mc.stopping():
        with lock:
            items_ = list(graves.items())
        for tag, g in items_:
            if time.time() - g["made"] >= v["seconds"]:
                clear(tag)
                continue
            feet = mc.pos(g["p"])
            if feet is None:
                continue
            middle = (g["at"][0] + 0.5, g["at"][1], g["at"][2] + 0.5)
            near = world.dim_of(g["p"]) == g["dim"] and math.dist(feet, middle) <= v["found_within"]
            if not near:
                g["left"] = True
            elif g["left"]:
                clear(tag)
                mc.log(g["p"], "found their grave")
                mc.rcon("tellraw %s %s" % (g["p"], json.dumps({"text": "You found your grave. The mark fades.",
                                                               "color": "gray", "italic": True})))
        loop.wait(1)


mc.on_stop(clear_all)
loop.background(watch)
mc.log("marking graves for", v["seconds"], "s")
deaths = {}
for at, p, what in loop(on_death(v["player"], v)):
    deaths[p] = deaths.get(p, 0) + 1
    mark(at, p, what, deaths[p])
