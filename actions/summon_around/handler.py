"""summon_around: summon a number of one mob round each player, spread to safe spots,
optionally scaled, named or glowing.

Run on EC2 by toolkit/remote/action.py: Python 3.9, standard library only. It never
places or removes a block. For each player it summons the mobs at the player, tagged
vfg_summon_<task>, ten at a time; each ten is scattered within the radius by
`spreadplayers` (which never lands on water, lava or fire), counted, and untagged from
the batch. It reports the count that stands and where: "summoned 100 zombies round
Steve at 197 68 251".

Every command names the player's position and dimension outright (`execute in <dim>
positioned <x y z> run ...`) rather than `execute at <player>`: inside `at`, which forks,
Minecraft drops the inner command's failure message, so "Can't summon entity of type
minecraft:player" would come back as nothing.

The replies it reads are Minecraft 26.3's, for example:
  Summoned new Zombie                      (or the mob's name, when it has one)
  Can't find element 'minecraft:nonsense' of type 'minecraft:entity_type'...<--[HERE]
  Can't summon entity of type minecraft:player
  Monsters cannot be summoned in Peaceful difficulty
  Spread 10 entity/entities around 197.5, 251.5 with an average distance of 6.21 block(s) apart
  Test passed. Count: 10
"""
import math
import re

from mc import rcon, log
import places

BATCH = 10
UP = 8                       # spreadplayers' `under`: this far above the player, so a player
                             # in a cave or a house gets the mobs there, not on the roof
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MOB_RE = re.compile(r"^[a-z0-9_]+$")
TASK_RE = re.compile(r"^T[1-9][0-9]*$")
DIM_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
DATA = " has the following entity data: "
COUNT = re.compile(r"^Test passed\. Count: (\d+)")
# Plurals English does not make with an s.
PLURAL = {"sheep": "sheep", "cod": "cod", "salmon": "salmon", "squid": "squid", "glow_squid": "glow squid",
          "silverfish": "silverfish", "pufferfish": "pufferfish", "tropical_fish": "tropical fish",
          "wolf": "wolves", "bogged": "bogged", "breeze": "breezes"}


class Failed(Exception):
    """The summon cannot be done; the message is the summary."""


class Offline(Failed):
    """A player it needs is not online: the result says who, so the dispatcher drops the
    pick at once instead of asking its planner."""

    def __init__(self, names):
        self.names = list(names)
        Failed.__init__(self, "%s %s not online" % (" and ".join(self.names), "is" if len(self.names) == 1 else "are"))


def run(args):
    try:
        return summon_around(args)
    except Offline as e:
        return {"ok": False, "summary": str(e), "offline": e.names}
    except Failed as e:
        return {"ok": False, "summary": str(e)}


def summon_around(args):
    who = str(args.get("players") or "")
    mob = str(args.get("mob") or "").lower()
    if mob.startswith("minecraft:"):
        mob = mob[len("minecraft:"):]
    count = int(args.get("count", 10))
    radius = int(args.get("radius", 16))
    scale = float(args.get("scale", 1.0))
    name = str(args.get("name") or "").strip()
    glowing = bool(args.get("glowing", False))
    task = str(args.get("task") or "")
    if who != "@a" and not PLAYER_RE.match(who):
        raise Failed("%r is not a player name" % who)
    if not MOB_RE.match(mob):
        raise Failed("%r is not a mob id" % mob)
    if not (1 <= count <= 100 and 4 <= radius <= 64 and 0.25 <= scale <= 8.0 and len(name) <= 32):
        raise Failed("count 1-100, radius 4-64, scale 0.25-8, a name of at most 32 characters")

    tag = "vfg_summon_" + (task if TASK_RE.match(task) else "task")
    batch_tag = tag + "_new"
    nbt = "{" + ",".join(nbt_parts(tag, batch_tag, name, glowing, scale)) + "}"

    names = online() if who == "@a" else [who]
    if not names:
        raise Failed("nobody is online")
    rounds, missing, error = [], [], None
    for p in names:
        at = where(p)
        if at is None:
            missing.append(p)
            continue
        summoned, standing, error = summon_round(p, at, mob, count, radius, nbt, batch_tag)
        rounds.append({"player": p, "pos": [int(math.floor(v)) for v in at[0]], "dim": at[1],
                       "summoned": summoned, "standing": standing})
        if error:
            break

    total = sum(r["summoned"] for r in rounds)
    standing = sum(r["standing"] for r in rounds)
    if not rounds:
        raise Offline(missing)
    if total == 0:
        raise Failed("could not summon %s: %s" % (mob.replace("_", " "), " ".join((error or "Minecraft summoned none").split())))

    summary = "summoned " + report(rounds, mob, count) + details(name, glowing, scale)
    if standing < total:
        summary += "; %d still standing" % standing
    if error:
        summary += "; then Minecraft said: %s" % " ".join(error.split())
    if missing:
        summary += "; %s %s not online" % (" and ".join(missing), "is" if len(missing) == 1 else "are")
    return {"ok": error is None, "summary": summary, "mob": mob, "summoned": total, "standing": standing,
            "rounds": rounds, "tag": tag}


def nbt_parts(tag, batch_tag, name, glowing, scale):
    parts = ['Tags:["vfg","%s","%s"]' % (tag, batch_tag)]
    if name:
        parts.append("CustomName:%s,CustomNameVisible:1b" % quote(name))
    if glowing:
        parts.append("Glowing:1b")
    if abs(scale - 1.0) > 1e-9:
        parts.append('attributes:[{id:"minecraft:scale",base:%sd}]' % repr(float(scale)))
    return parts


def summon_round(player, at, mob, count, radius, nbt, batch_tag):
    """Summon `count` round one player, ten at a time: (summoned, standing, error or None)."""
    summoned = standing = 0
    error = None
    left = count
    (x, y, z), dim = at
    here = "execute in %s positioned %.3f %.3f %.3f run " % (dim, x, y, z)
    while left > 0 and error is None:
        n = 0
        for _ in range(min(BATCH, left)):
            out = rcon("%ssummon minecraft:%s ~ ~ ~ %s" % (here, mob, nbt))
            if out.startswith("Summoned new "):
                n += 1
            else:
                error = out or "no answer from Minecraft"
                break
        left -= min(BATCH, left)
        if n:
            spread(player, at, radius, batch_tag)
            standing += count_tagged(batch_tag)
            summoned += n
        rcon("tag @e[tag=%s] remove %s" % (batch_tag, batch_tag))
    return summoned, standing, error


def spread(player, at, radius, batch_tag):
    """Scatter the batch within `radius` of the player; where it cannot, they stay at the player."""
    (x, y, z), dim = at
    top = int(math.floor(y)) + UP
    for gap in ((2 if radius >= 8 else 1), 0):
        out = rcon("execute in %s run spreadplayers %.3f %.3f %d %d under %d false @e[tag=%s]"
                   % (dim, x, z, gap, radius, top, batch_tag))
        if out.startswith("Spread "):
            return True
        log("spread %s: %s" % (player, out or "no answer"))
    return False


def count_tagged(t):
    m = COUNT.match(rcon("execute if entity @e[tag=%s]" % t))
    return int(m.group(1)) if m else 0


def online():
    m = re.search(r"online:(.*)", rcon("list"))
    return [p.strip() for p in m.group(1).split(",") if PLAYER_RE.match(p.strip())] if m else []


def where(player):
    """((x, y, z), dimension) of an online player, or None."""
    p = data(player, "Pos")
    try:
        pos = float(p[0]), float(p[1]), float(p[2])
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    d = data(player, "Dimension")
    return pos, d if isinstance(d, str) and DIM_RE.match(d) else "minecraft:overworld"


def data(player, path):
    """The parsed SNBT of `data get entity <player> <path>`, or None."""
    out = rcon("data get entity %s %s" % (player, path))
    i = out.find(DATA)
    if i < 0:
        return None
    try:
        return places.snbt(out[i + len(DATA):])
    except ValueError:
        return None


def quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plural(mob, n):
    words = mob.replace("_", " ")
    if n == 1:
        return words
    if mob in PLURAL:
        return PLURAL[mob]
    if words.endswith("man"):
        return words[:-3] + "men"
    if words.endswith(("s", "x", "ch", "sh")):
        return words + "es"
    if words.endswith("y") and words[-2:-1] not in ("a", "e", "i", "o", "u"):
        return words[:-1] + "ies"
    return words + "s"


def report(rounds, mob, count):
    """100 zombies round Steve at 197 68 251; 10 zombies round each of A at ... and B at ..."""
    def where_(r):
        return "%s at %d %d %d" % (r["player"], r["pos"][0], r["pos"][1], r["pos"][2])
    if len(rounds) == 1:
        r = rounds[0]
        n = r["summoned"]
        head = ("%d %s" % (n, plural(mob, n))) if n == count else ("%d of %d %s" % (n, count, plural(mob, count)))
        return "%s round %s" % (head, where_(r))
    if all(r["summoned"] == count for r in rounds):
        return "%d %s round each of %s" % (count, plural(mob, count), " and ".join(where_(r) for r in rounds))
    return "%s: %s" % (plural(mob, 2), "; ".join("%d round %s" % (r["summoned"], where_(r)) for r in rounds))


def details(name, glowing, scale):
    d = []
    if name:
        d.append("named %s" % name)
    if glowing:
        d.append("glowing")
    if abs(scale - 1.0) > 1e-9:
        d.append("%sx size" % ("%g" % scale))
    return " (%s)" % ", ".join(d) if d else ""
