"""Teleport: move a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, their spawn point or where they last died, landing on safe ground."""
ASKS = ["can you teleport me to a village in the overworld", "can you teleport me to a snowy biome",
        "can I be teleported ten thousand blocks away from these nerds", "teleport us all ten thousand blocks away",
        "teleport all of us to the castle", "just teleport us to a mountain biome", "take us to a mountain quickly"]
from kit import values, result, loop, world
import math
import random
import re
import sys
import mc
import places


# --- preset: land_safely (copied from _presets/land_safely.py) ---
# Bent: its values come as `land`, LAND below (the preset's defaults), not from this copy's
# values, since the teleport action keeps its four fields.
def land_safely(player, at, land, *, exact=False, top=False, others=()):
    """Move `player` (an online player's name) to `at`, a world.Pos in its dimension, onto safe
    ground, and everyone in `others` onto them. Each gets land_cushion s of Resistance V and
    Slow Falling first. With exact (at's y is where to stand: a saved place, a spawn point, a
    death) they stand on at itself when its ground is solid and harmless and there is room
    (or one block up, on a slab, a carpet or a bed). Else spreadplayers lands them within
    land_ring blocks of it, at or below at's y (top: on the highest ground, at's y not being
    known, below the Nether's roof; at None: round the world spawn), and again twice as far
    while the ground there is magma, cactus, powder snow, a campfire, berries, dripstone,
    water or lava, land_tries rings in all. Whoever moved more than land_spawn_after blocks
    within one dimension gets their spawn point where they landed.

    Returns {"pos": the world.Pos landed on, "ring": its radius (0: the spot itself), "moved":
    [names], "spawn_set": [names]}; None when no ring had safe ground (they are put back where
    they were) or the player went offline."""
    movers = [player] + [p for p in others if p != player]
    start = dict((p, _land_safely_where(p)) for p in movers)
    if start[player] is None:
        mc.log("land_safely: %s is not online" % player)
        return None
    if land["land_cushion"]:
        for p in movers:
            mc.rcon("effect give %s minecraft:resistance %d 4 true" % (p, land["land_cushion"]))
            mc.rcon("effect give %s minecraft:slow_falling %d 0 true" % (p, land["land_cushion"]))
    landed = _land_safely_land(player, at, land, exact, top)
    if landed is None:
        back = start[player]
        if _land_safely_where(player) != back:
            mc.rcon("execute in %s run tp %s %.3f %.3f %.3f" % ((back.dim or world.OVERWORLD, player) + tuple(back)))
        return None
    pos, ring = landed
    moved, spawn_set = [player], []
    for p in movers[1:]:
        out = mc.rcon("tp %s %s" % (p, player))
        if out.startswith("Teleported"):
            moved.append(p)
        else:
            mc.log("land_safely: tp %s %s: %s" % (p, player, out or "no answer"))
    for p in moved:
        if _land_safely_spawn(p, start[p], pos, land):
            spawn_set.append(p)
    return {"pos": pos, "ring": ring, "moved": moved, "spawn_set": spawn_set}


# Ground that hurts or swallows: never landed on. #minecraft:replaceable (air, short grass,
# snow layers...) is room to stand in; water and lava are in it too, so they are refused apart.
_LAND_SAFELY_UNSAFE = ("minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow", "#minecraft:campfires",
                       "minecraft:sweet_berry_bush", "minecraft:pointed_dripstone", "minecraft:water",
                       "minecraft:lava")
_LAND_SAFELY_SWALLOWS = ("minecraft:water", "minecraft:lava", "minecraft:powder_snow")
_LAND_SAFELY_FREE = "#minecraft:replaceable"
_LAND_SAFELY_ROOF = {world.NETHER: 120}       # spreadplayers' `under` in the Nether: below its bedrock roof
_LAND_SAFELY_SKY = 320                        # and anywhere else: the top of the world


def _land_safely_where(p):
    """Where an online player stands, as a world.Pos in their dimension; None when offline."""
    c = mc.pos(p)
    return world.Pos(c[0], c[1], c[2], world.dim_of(p)) if c else None


def _land_safely_land(player, at, land, exact, top):
    """(the world.Pos landed on, the ring), or None."""
    dim = (at.dim if at is not None else None) or world.OVERWORLD
    if exact and at is not None:
        spot = _land_safely_spot(at, dim)
        if spot is not None:
            out = mc.rcon("execute in %s run tp %s %.1f %.1f %.1f" % ((dim, player) + spot))
            if out.startswith("Teleported"):
                return world.Pos(spot[0], spot[1], spot[2], dim), 0
            mc.log("land_safely: tp to the spot itself: %s" % (out or "no answer"))
        else:
            mc.log("land_safely: %d %d %d is not safe to stand on: landing round it" % at.block())
    if at is None:      # the world spawn, where RCON's own commands run
        cmd = "spreadplayers ~ ~ 0 %%d under %d false %s" % (_LAND_SAFELY_SKY, player)
    else:
        under = _LAND_SAFELY_ROOF.get(dim, _LAND_SAFELY_SKY) if top else int(math.floor(at[1])) + 2
        cmd = "execute in %s run spreadplayers %d %d 0 %%d under %d false %s" % (
            dim, math.floor(at[0]), math.floor(at[2]), under, player)
    ring = land["land_ring"]
    for _ in range(land["land_tries"]):
        out = mc.rcon(cmd % ring)
        if out.startswith("Spread "):
            c = mc.pos(player)
            if c is not None and _land_safely_safe(c, dim):
                return world.Pos(c[0], c[1], c[2], dim), ring
            mc.log("land_safely: ring %d: unsafe ground at %s" % (ring, "%d %d %d" % tuple(math.floor(x) for x in c)
                                                                 if c else "an unknown spot"))
        elif out.startswith("Could not spread"):
            mc.log("land_safely: ring %d: no room (%s)" % (ring, out))
        else:
            mc.log("land_safely: spreadplayers: %s" % (out or "no answer"))
            return None
        ring *= 2
    return None


def _land_safely_spot(at, dim):
    """The block centre to stand on at `at`, or one block up (a slab, a carpet, a bed there);
    None when neither has solid ground that does not hurt and room for feet and head."""
    x, y, z = at.block()
    for up in (0, 1):
        check = "execute in %s positioned %d %d %d unless block ~ ~-1 ~ %s" % (dim, x, y + up, z, _LAND_SAFELY_FREE)
        check += "".join(" unless block ~ ~-1 ~ " + b for b in _LAND_SAFELY_UNSAFE)
        for head in ("~", "~1"):
            check += " if block ~ %s ~ %s" % (head, _LAND_SAFELY_FREE)
            check += "".join(" unless block ~ %s ~ %s" % (head, b) for b in _LAND_SAFELY_SWALLOWS)
        if mc.rcon(check).startswith("Test passed"):
            return (x + 0.5, float(y + up), z + 0.5)
    return None


def _land_safely_safe(c, dim):
    """True when a landing stands on nothing that hurts and in nothing that swallows. On 26.3
    a chain of conditions answers "Test passed", or nothing when one fails."""
    check = "execute in %s positioned %.3f %.3f %.3f" % (dim, c[0], c[1], c[2])
    check += "".join(" unless block ~ ~-1 ~ " + b for b in _LAND_SAFELY_UNSAFE)
    check += "".join(" unless block ~ ~ ~ " + b for b in _LAND_SAFELY_SWALLOWS)
    return mc.rcon(check).startswith("Test passed")


def _land_safely_spawn(p, frm, to, land):
    """Set p's spawn point where they landed when they came more than land_spawn_after blocks
    within one dimension. True when it was set."""
    if not land["land_spawn_after"] or frm is None or frm.dim != to.dim:
        return False
    if math.hypot(to[0] - frm[0], to[2] - frm[2]) <= land["land_spawn_after"]:
        return False
    out = mc.rcon("execute in %s run spawnpoint %s %d %d %d" % ((to.dim or world.OVERWORLD, p)
                                                               + tuple(math.floor(x) for x in to)))
    if not out.startswith("Set spawn point"):
        mc.log("land_safely: spawnpoint %s: %s" % (p, out or "no answer"))
        return False
    return True


# land_safely's values: Resistance and Slow Falling for 5 s, rings of 8, 16, 32 and 64 blocks,
# and the spawn point set where a move of over 1,000 blocks lands.
LAND = {"land_cushion": 5, "land_ring": 8, "land_tries": 4, "land_spawn_after": 1000}
BORDER = 29999000
DIMS = {"overworld": world.OVERWORLD, "the_nether": world.NETHER, "the_end": world.END}
DIM_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
ID_RE = re.compile(r"^[a-z0-9_]+$")
LOCATED = re.compile(r"^The nearest (\S+)(?: \((\S+)\))? is at \[(-?\d+), (-?\d+|~), (-?\d+)\]")
DATA = " has the following entity data: "
# Structures whose id puts the kind first: village_plains is a plains village.
KIND_FIRST = ("village", "ruined_portal", "ocean_ruin", "shipwreck", "trial_chambers")


class Failed(Exception):
    """The teleport cannot be done; the message is the summary. `offline`: the players it
    needed who are not online, so the dispatcher drops the ask instead of planning it again."""

    def __init__(self, summary, offline=()):
        Exception.__init__(self, summary)
        self.offline = list(offline)


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


# ------------------------------------------------------------------ where to

def resolve(to, target, dim, player, start, whose):
    """Where to land, from `to` and `target`: {"at": a world.Pos, or None for the world spawn;
    "exact": land on it itself when safe; "top": its height is not known; "label": the place in
    words; "phrase": what the summary says before the landing's x y z; "show_xz"}."""
    if to in ("biome", "structure"):
        return locate(to, target, dim, player, start)
    if to == "place":
        name = target.lower()
        if not places.NAME_RE.match(name):
            raise Failed("%r is not a place name" % target)
        try:
            p = places.get(mc.rcon, name)
        except places.PlaceError as e:
            raise Failed("could not read the saved places: %s" % e)
        if p is None:
            raise Failed("there is no saved place called %s" % name)
        x, y, z = p["pos"]
        return dest(world.Pos(x, y, z, p["dim"]), name, "to %s at" % name, exact=True)
    if to == "coords":
        nums = target.replace(",", " ").split()
        try:
            nums = [int(math.floor(float(n))) for n in nums]
        except ValueError:
            nums = []
        if len(nums) not in (2, 3):
            raise Failed("coordinates are 'x z' or 'x y z', not %r" % target)
        y = nums[1] if len(nums) == 3 else 0
        return dest(world.Pos(clamp(nums[0]), y, clamp(nums[-1]), dim), " ".join(str(n) for n in nums), "to",
                    top=len(nums) == 2, show_xz=False)
    if to == "far":
        try:
            d = int(target or "1000")
        except ValueError:
            raise Failed("far takes a distance in blocks, not %r" % target)
        if not 1 <= d <= 1000000:
            raise Failed("far takes a distance from 1 to 1000000 blocks")
        a = random.uniform(0, 2 * math.pi)
        cx, cz = scaled(start, dim)
        return dest(world.Pos(clamp(cx + d * math.cos(a)), 0, clamp(cz + d * math.sin(a)), dim),
                    "a spot %d blocks away" % d, "%d blocks away, to" % d, top=True)
    if to == "spawn_point":
        r = data(player, "respawn")
        if r is None:
            # No bed or anchor: the world spawn, which is where RCON's commands run from.
            return {"at": None, "exact": False, "top": True, "label": "the world spawn",
                    "phrase": "to the world spawn at", "show_xz": False}
        x, y, z, d = pos_dim(r, "respawn")
        return dest(world.Pos(x, y, z, d), "%s spawn point" % whose, "to %s spawn point at" % whose, exact=True)
    if to == "last_death":
        r = data(player, "LastDeathLocation")
        if r is None:
            raise Failed("%s has no death on record" % player)
        x, y, z, d = pos_dim(r, "LastDeathLocation")
        who = "they" if whose == "their" else player
        return dest(world.Pos(x, y, z, d), "where %s last died" % who, "to where %s last died, at" % who, exact=True)
    raise Failed("unknown destination %r" % to)


def dest(at, label, phrase, exact=False, top=False, show_xz=True):
    return {"at": at, "exact": exact, "top": top, "label": label, "phrase": phrase, "show_xz": show_xz}


def locate(kind, target, dim, player, start):
    """The nearest biome or structure from the player's spot (in `dim`, scaled to it). A
    structure id Minecraft does not know, such as village, is tried once as its tag,
    #minecraft:village, before it fails."""
    tag = target.startswith("#")
    ident = target.lstrip("#").lower()
    if ident.startswith("minecraft:"):
        ident = ident[len("minecraft:"):]
    if not ID_RE.match(ident):
        raise Failed("%r is not a %s id" % (target, kind))
    full = ("#" if tag else "") + "minecraft:" + ident
    cx, cz = scaled(start, dim)
    ask = "execute in %s positioned %d 0 %d run locate %s %%s" % (dim, cx, cz, kind)
    out = mc.rcon(ask % full)
    if kind == "structure" and not tag and out.startswith("There is no structure"):
        again = mc.rcon(ask % ("#" + full))
        if LOCATED.match(again) or again.startswith("Could not find"):
            mc.log("no structure %s, but a tag #%s: using it" % (full, full))
            full, out = "#" + full, again
    m = LOCATED.match(out)
    if m:
        label = describe(m.group(2) or m.group(1))
        return dest(world.Pos(int(m.group(3)), 0, int(m.group(5)), dim), label, "to %s at" % label, top=True)
    words = describe(full)
    if out.startswith("Could not find"):
        raise Failed("no %s within reach of %s%s" % (words[words.index(" ") + 1:], player, in_dim(dim)))
    if out.startswith("There is no") or out.startswith("Can't find") or "<--[HERE]" in out:
        raise Failed("Minecraft has no %s called %s" % (kind, full))
    raise Failed("locate %s %s failed: %s" % (kind, full, out or "no answer from Minecraft"))


def describe(ident):
    """minecraft:snowy_taiga -> a snowy taiga; minecraft:village_plains -> a plains village."""
    name = ident.lstrip("#").split(":")[-1]
    if name.startswith("is_"):          # biome tags: #minecraft:is_forest
        name = name[3:]
    for k in KIND_FIRST:
        if name.startswith(k + "_"):
            name = name[len(k) + 1:] + "_" + k
            break
    words = name.replace("_", " ")
    return ("an " if words[:1] in "aeiou" else "a ") + words


def scaled(at, dim):
    """A place's x z as seen from another dimension: the Nether is 8 times smaller."""
    frm = at.dim or world.OVERWORLD
    if frm == dim:
        return int(math.floor(at[0])), int(math.floor(at[2]))
    if dim == world.NETHER and frm == world.OVERWORLD:
        return int(at[0] // 8), int(at[2] // 8)
    if dim == world.OVERWORLD and frm == world.NETHER:
        return clamp(at[0] * 8), clamp(at[2] * 8)
    return 0, 0


def clamp(x):
    return int(max(-BORDER, min(BORDER, math.floor(x))))


def data(player, path):
    """The SNBT after "has the following entity data: ", parsed; None when the path is missing."""
    out = mc.rcon("data get entity %s %s" % (player, path))
    i = out.find(DATA)
    if i >= 0:
        try:
            return places.snbt(out[i + len(DATA):])
        except ValueError as e:
            raise Failed("cannot read %s's %s: %s" % (player, path, e))
    if out.startswith("Found no elements matching"):
        return None
    if out.startswith("No entity was found") or out.startswith("No player was found"):
        raise Failed(not_online([player]), [player])
    raise Failed("Minecraft did not answer about %s: %s" % (player, out or "no reply"))


def pos_dim(r, what):
    """(x, y, z, dimension) from {pos: [I; x, y, z], dimension: "..."}."""
    try:
        x, y, z = [int(c) for c in r["pos"]]
        d = r.get("dimension") or world.OVERWORLD
    except (TypeError, ValueError, KeyError, AttributeError):
        raise Failed("cannot read %s: %r" % (what, r))
    if not isinstance(d, str) or not DIM_RE.match(d):
        raise Failed("cannot read %s's dimension: %r" % (what, d))
    return x, y, z, d


def xyz(at):
    return " ".join("%d" % math.floor(c) for c in at)


def in_dim(dim):
    if not dim or dim == world.OVERWORLD:
        return ""
    return " in the " + dim.split(":")[-1].replace("the_", "").replace("_", " ")


# ------------------------------------------------------------------ the move

def teleport(v):
    who = v["player"]
    dim = DIMS[v["dimension"]]
    names, offline = online(who)
    if not names:
        raise Failed("nobody is online" if who.startswith("@") else not_online(offline), offline)
    first = names[0]
    start = mc.pos(first)
    if start is None:
        raise Failed(not_online([first]), [first] + offline)
    start = world.Pos(start[0], start[1], start[2], world.dim_of(first))
    group = len(names) > 1 or who == "@a"
    d = resolve(v["to"], v["target"].strip(), dim, first, start, "%s's" % first if group else "their")
    land = dict(LAND, land_spawn_after=0) if v["to"] == "spawn_point" else LAND
    landed = land_safely(first, d["at"], land, exact=d["exact"], top=d["top"], others=names[1:])
    if landed is None:
        at = "" if d["at"] is None or not d["show_xz"] else " at %d %d" % (d["at"][0], d["at"][2])
        raise Failed("no safe spot within %d rings of %s%s; %s is still where they were"
                     % (land["land_tries"], d["label"], at, first))
    moved = landed["moved"]
    if who == "@a":
        who_text = "everyone (%s)" % ", ".join(moved)
    else:
        who_text = " and ".join(moved)
    pos = landed["pos"]
    summary = "teleported %s %s %s%s" % (who_text, d["phrase"], xyz(pos), in_dim(pos.dim))
    if landed["spawn_set"]:
        summary += "; spawn point set there"
    if offline:
        summary += "; " + not_online(offline)
    facts = {"players": moved, "pos": [int(math.floor(c)) for c in pos], "dim": pos.dim or world.OVERWORLD,
             "to": v["to"], "target": d["label"], "ring": landed["ring"], "spawn_set": landed["spawn_set"]}
    if offline:
        facts["offline"] = offline
    return summary, facts


v = values()
try:
    summary, facts = teleport(v)
except Failed as e:
    result(str(e), **({"offline": e.offline} if e.offline else {}))
    sys.exit(1)
result(summary, **facts)
