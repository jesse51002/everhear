"""Summon Around: summon a number of one mob round each player, spread to safe spots, optionally scaled, named or glowing."""
ASKS = ["spawn a horde of zombies, a hundred zombies in a hundred blocks radius of each of us",
        "spawn as many spiders on me as possible", "don't just do zombies, do skeletons, wither skeletons, creepers",
        "a thousand mobs on each of us so we are surrounded by mobs everywhere"]
from kit import values, result, loop, world
import json
import math
import sys
import mc


# --- preset: summon (copied from _presets/summon.py) ---
# Bent: its values are this action's (mob, count, radius, scale, name, glowing; no gear, health
# or cap); the mobs are summoned at the player ten at a time and scattered by spreadplayers,
# and carry a tag of their own, never TAG, so they stay when the copy ends and a later copy of
# the same name never removes them.
def summon(at, v, *, tag):
    """count of mob round `at`, a world.Pos in its dimension: summoned there ten at a time,
    each ten scattered within radius blocks by spreadplayers (which never lands on water, lava
    or fire) and counted. They carry "vfg" and `tag`, and are persistent. Returns (how many
    were summoned, how many stand, Minecraft's refusal or None)."""
    batch = mc.TAG + "_new"
    mob = v["mob"] if ":" in v["mob"] else "minecraft:" + v["mob"]
    nbt = "{" + ",".join(['Tags:["vfg","%s","%s"]' % (tag, batch), "PersistenceRequired:1b"] + _summon_nbt(v)) + "}"
    here = "execute in %s positioned %.3f %.3f %.3f run " % (at.dim or world.OVERWORLD, at[0], at[1], at[2])
    summoned = standing = 0
    error = None
    left = v["count"]
    while left > 0 and error is None and not mc.stopping():
        n = 0
        for _ in range(min(10, left)):
            out = mc.rcon("%ssummon %s ~ ~ ~ %s" % (here, mob, nbt))
            if out.startswith("Summoned new "):
                n += 1
            else:
                error = out or "no answer from Minecraft"
                break
        left -= min(10, left)
        if n:
            _summon_spread(at, v["radius"], batch)
            standing += mc.count("@e[tag=%s]" % batch)
            summoned += n
        mc.rcon("tag @e[tag=%s] remove %s" % (batch, batch))
    return summoned, standing, error


def _summon_spread(at, radius, batch):
    """Scatter the batch within `radius` of `at`, at most 8 blocks above it (in a cave or a
    house they stay there, not on the roof); where they cannot be, they stay at `at`."""
    for gap in ((2 if radius >= 8 else 1), 0):
        out = mc.rcon("execute in %s run spreadplayers %.3f %.3f %d %d under %d false @e[tag=%s]"
                      % (at.dim or world.OVERWORLD, at[0], at[2], gap, radius, math.floor(at[1]) + 8, batch))
        if out.startswith("Spread "):
            return True
        mc.log("spread round %s: %s" % (at, out or "no answer"))
    return False


def _summon_nbt(v):
    """The NBT the values ask for, without braces or Tags (summon adds those)."""
    parts = []
    if v["name"]:
        parts += ["CustomName:%s" % json.dumps(v["name"]), "CustomNameVisible:1b"]
    if v["scale"] != 1:
        parts.append('attributes:[{id:"scale",base:%g}]' % v["scale"])
    if v["glowing"]:
        parts.append("Glowing:1b")
    return parts


# Plurals English does not make with an s.
PLURAL = {"sheep": "sheep", "cod": "cod", "salmon": "salmon", "squid": "squid", "glow_squid": "glow squid",
          "silverfish": "silverfish", "pufferfish": "pufferfish", "tropical_fish": "tropical fish",
          "wolf": "wolves", "bogged": "bogged", "breeze": "breezes"}


def online(given):
    """(the online players a player value names, as the server writes them; the names given
    that are not online). @a, @r and @p are loop.who's."""
    if given in ("@a", "@r", "@p"):
        return loop.who(given), []
    on = dict((n.lower(), n) for n in loop.online())
    names = []
    for n in given.split(","):
        if n.strip() and n.strip() not in names:
            names.append(n.strip())
    return [on[n.lower()] for n in names if n.lower() in on], [n for n in names if n.lower() not in on]


def not_online(names):
    return "%s %s not online" % (" and ".join(names), "is" if len(names) == 1 else "are")


def plural(mob, n):
    words = mob.split(":")[-1].replace("_", " ")
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
    def at(r):
        return "%s at %d %d %d" % (r["player"], r["pos"][0], r["pos"][1], r["pos"][2])
    if len(rounds) == 1:
        r = rounds[0]
        n = r["summoned"]
        head = ("%d %s" % (n, plural(mob, n))) if n == count else ("%d of %d %s" % (n, count, plural(mob, count)))
        return "%s round %s" % (head, at(r))
    if all(r["summoned"] == count for r in rounds):
        return "%d %s round each of %s" % (count, plural(mob, count), " and ".join(at(r) for r in rounds))
    return "%s: %s" % (plural(mob, 2), "; ".join("%d round %s" % (r["summoned"], at(r)) for r in rounds))


def details(v):
    d = []
    if v["name"]:
        d.append("named %s" % v["name"])
    if v["glowing"]:
        d.append("glowing")
    if v["scale"] != 1:
        d.append("%gx size" % v["scale"])
    return " (%s)" % ", ".join(d) if d else ""


def fail(summary, **facts):
    result(summary, **facts)
    sys.exit(1)


v = values()
mob = v["mob"]
# Their own tag: the task's, as `kill @e[tag=vfg_summon_zombies_steve]` takes away what the task
# zombies_steve summoned; the copy's when no task started it (the brain's own copy, or a cast's).
tag = "vfg_summon_" + (mc.task() or mc.INSTANCE)
names, offline = online(v["players"])
if not names:
    if v["players"].startswith("@"):
        fail("nobody is online")
    fail(not_online(offline), offline=offline)
rounds, error = [], None
for p in names:
    c = mc.pos(p)
    if c is None:
        offline.append(p)
        continue
    at = world.Pos(c[0], c[1], c[2], world.dim_of(p))
    summoned, standing, error = summon(at, v, tag=tag)
    rounds.append({"player": p, "pos": [int(math.floor(x)) for x in at], "dim": at.dim or world.OVERWORLD,
                   "summoned": summoned, "standing": standing})
    if error:
        break
total = sum(r["summoned"] for r in rounds)
standing = sum(r["standing"] for r in rounds)
if not rounds:
    fail(not_online(offline), offline=offline)
facts = {"mob": mob, "summoned": total, "standing": standing, "rounds": rounds, "tag": tag}
if offline:
    facts["offline"] = offline
if total == 0:  # Minecraft refused: whoever was offline is not why
    fail("could not summon %s: %s" % (plural(mob, 1), " ".join((error or "Minecraft summoned none").split())),
         **dict((k, x) for k, x in facts.items() if k != "offline"))
summary = "summoned " + report(rounds, mob, v["count"]) + details(v)
if standing < total:
    summary += "; %d still standing" % standing
if error:
    summary += "; then Minecraft said: %s" % " ".join(error.split())
if offline:
    summary += "; " + not_online(offline)
if error:  # some stand already: running it again would summon them again
    fail(summary, partial=True, **facts)
result(summary, **facts)
