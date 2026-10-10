"""teleport: move a player, or everyone, to a biome, a structure, a saved place,
coordinates, far away, their spawn point or where they last died, landing on safe ground.

Run on EC2 by toolkit/remote/action.py: Python 3.9, standard library only. It never
places or removes a block. The steps, as the Minecraft_Actions page has them:

1. Resolve the target: `locate` from the player's position, a saved place, the
   player's own `respawn` or `LastDeathLocation`, coordinates, or a random point. A
   structure id Minecraft does not know, such as `village`, is tried once as its tag,
   `#minecraft:village`, before it fails.
2. Cushion: Resistance V and Slow Falling for 5 seconds.
3. A target with a known x y z (a saved place, the spawn point, the last death) is
   reached exactly: when the block below is solid and not unsafe, and the feet and the
   head are free and not water or lava, `tp` there (or one block up, standing on a slab,
   a carpet or a bed there). Only an unsafe spot falls back to the rings below.
4. Land: `spreadplayers <x> <z> 0 <r> under <top> false <player>`, r = 8.
5. Check the ground under the landing; land again with r doubled, four rings in all
   (8, 16, 32, 64), then fail: no safe spot (and the player goes back where they were).
6. Settle: set the spawn point there when the move was over 1,000 blocks, read the
   position back, and report it with the place it reached.

Everyone (@a) is the same steps for the first player online, then `tp` for the rest.

The replies it reads are Minecraft 26.3's, for example:
  The nearest minecraft:snowy_taiga is at [-1408, 64, 1408] (1992 blocks away)
  The nearest #minecraft:village (minecraft:village_plains) is at [-544, ~, 320] (631 blocks away)
  Could not find a biome of type "minecraft:nether_wastes" within a reasonable distance
  Spread 1 entity/entities around -1407.5, 1408.5 with an average distance of 0.00 block(s) apart
  Could not spread 1 entity/entities around -95.5, -319.5 (too many entities for space - ...)
  Steve has the following entity data: [-1408.5d, 68.0d, 1410.5d]
  Steve has the following entity data: {pos: [I; 12, 64, -30], dimension: "minecraft:overworld", ...}
  Found no elements matching respawn
  Test passed / Test failed
"""
import math
import random
import re

from mc import rcon, log
import places

RINGS = (8, 16, 32, 64)
TOP = 320                                   # spreadplayers' `under`: the top of the world...
TOPS = {"minecraft:the_nether": 120}        # ...or below the Nether's bedrock roof
BORDER = 29999000
SPAWN_AFTER = 1000                          # a longer move sets the spawn point where they land
CUSHION = ("minecraft:resistance 5 4 true", "minecraft:slow_falling 5 0 true")
UNSAFE_GROUND = ("minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow", "#minecraft:campfires",
                 "minecraft:sweet_berry_bush", "minecraft:pointed_dripstone", "minecraft:water",
                 "minecraft:lava")
UNSAFE_FEET = ("minecraft:water", "minecraft:lava", "minecraft:powder_snow")
FREE = "#minecraft:replaceable"             # air, short grass, snow layers... (water and lava too: refused apart)
OVERWORLD = "minecraft:overworld"
DIMS = {"overworld": "minecraft:overworld", "the_nether": "minecraft:the_nether",
        "the_end": "minecraft:the_end"}

PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
DIM_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
ID_RE = re.compile(r"^[a-z0-9_]+$")
LOCATED = re.compile(r"^The nearest (\S+)(?: \((\S+)\))? is at \[(-?\d+), (-?\d+|~), (-?\d+)\]")
DATA = " has the following entity data: "
# Structures whose id puts the kind first: village_plains is a plains village.
KIND_FIRST = ("village", "ruined_portal", "ocean_ruin", "shipwreck", "trial_chambers")


class Failed(Exception):
    """The teleport cannot be done; the message is the summary."""


class Offline(Failed):
    """A player it needs is not online: the result says who, so the dispatcher drops the
    pick at once instead of asking its planner."""

    def __init__(self, names):
        self.names = list(names)
        Failed.__init__(self, "%s %s not online" % (" and ".join(self.names), "is" if len(self.names) == 1 else "are"))


def run(args):
    try:
        return teleport(args)
    except Offline as e:
        return {"ok": False, "summary": str(e), "offline": e.names}
    except Failed as e:
        return {"ok": False, "summary": str(e)}
    except places.PlaceError as e:
        return {"ok": False, "summary": "could not read the saved places: %s" % e}


def teleport(args):
    who = str(args.get("player") or "")
    to = str(args.get("to") or "")
    target = str(args.get("target") or "").strip()
    dim = DIMS.get(str(args.get("dimension") or "overworld"))
    if who != "@a" and not PLAYER_RE.match(who):
        raise Failed("%r is not a player name" % who)
    if dim is None:
        raise Failed("unknown dimension %r" % args.get("dimension"))

    if who == "@a":
        names = online()
        if not names:
            raise Failed("nobody is online")
    else:
        names = [who]
    first = names[0]
    pos0, dim0 = where(first)        # raises Failed when the player is offline
    start = {first: (pos0, dim0)}
    for p in names[1:]:
        start[p] = where_or_none(p)

    dest = resolve(to, target, dim, first, pos0, dim0, everyone=who == "@a")
    for c in CUSHION:
        rcon("effect give %s %s" % (who if who == "@a" else first, c))

    landed = land(first, dest)
    if landed is None:
        back = ""
        if where_or_none(first) != (pos0, dim0):
            rcon("execute in %s run tp %s %s" % (dim0, first, xyz(pos0, "%.3f")))
            back = "; %s is back where they were" % first
        raise Failed("no safe spot within %d rings of %s%s%s" % (len(RINGS), dest["label"], at_xz(dest), back))
    pos1, dim1, ring = landed

    spawn_set = []
    if settle(first, pos0, dim0, pos1, dim1, to):
        spawn_set.append(first)
    moved = [first]
    for p in names[1:]:
        out = rcon("tp %s %s" % (p, first))
        if not out.startswith("Teleported"):
            log("tp %s: %s" % (p, out or "no answer"))
            continue
        moved.append(p)
        if start.get(p) and settle(p, start[p][0], start[p][1], pos1, dim1, to):
            spawn_set.append(p)

    who_text = first if who != "@a" else "everyone (%s)" % ", ".join(moved)
    summary = "teleported %s %s %s%s" % (who_text, dest["phrase"], xyz(pos1), in_dim(dim1))
    if spawn_set:
        summary += "; spawn point set there"
    return {"ok": True, "summary": summary, "players": moved, "pos": [int(math.floor(v)) for v in pos1],
            "dim": dim1, "to": to, "target": dest["label"], "ring": ring, "spawn_set": spawn_set}


# ------------------------------------------------------------------ resolve

def resolve(to, target, dim, player, pos0, dim0, everyone=False):
    """Where to land, as dest() gives it; "dim" None is the world spawn."""
    whose = "%s's" % player if everyone else "their"
    if to in ("biome", "structure"):
        return locate(to, target, dim, player, pos0, dim0)
    if to == "place":
        name = target.lower()
        if not places.NAME_RE.match(name):
            raise Failed("%r is not a place name" % target)
        p = places.get(rcon, name)
        if p is None:
            raise Failed("there is no saved place called %s" % name)
        x, y, z = p["pos"]
        return dest(x, z, y, p["dim"], name, "to %s at" % name, exact=True)
    if to == "coords":
        nums = target.replace(",", " ").split()
        try:
            nums = [int(math.floor(float(n))) for n in nums]
        except ValueError:
            nums = []
        if len(nums) not in (2, 3):
            raise Failed("coordinates are 'x z' or 'x y z', not %r" % target)
        x, z = clamp(nums[0]), clamp(nums[-1])
        y = nums[1] if len(nums) == 3 else None
        label = " ".join(str(n) for n in nums)
        return dest(x, z, y, dim, label, "to", show_xz=False)
    if to == "far":
        try:
            d = int(target or "1000")
        except ValueError:
            raise Failed("far takes a distance in blocks, not %r" % target)
        if not 1 <= d <= 1000000:
            raise Failed("far takes a distance from 1 to 1000000 blocks")
        a = random.uniform(0, 2 * math.pi)
        cx, cz = scaled(pos0, dim0, dim)
        x, z = clamp(cx + d * math.cos(a)), clamp(cz + d * math.sin(a))
        return dest(x, z, None, dim, "a spot %d blocks away" % d, "%d blocks away, to" % d)
    if to == "spawn_point":
        r = data(player, "respawn")
        if r is None:
            # No bed or anchor: the world spawn, which is where RCON's commands run from.
            return {"x": None, "z": None, "y": None, "dim": None, "label": "the world spawn",
                    "phrase": "to the world spawn at", "show_xz": False}
        x, y, z, d = pos_dim(r, "respawn")
        return dest(x, z, y, d, "%s spawn point" % whose, "to %s spawn point at" % whose, exact=True)
    if to == "last_death":
        r = data(player, "LastDeathLocation")
        if r is None:
            raise Failed("%s has no death on record" % player)
        x, y, z, d = pos_dim(r, "LastDeathLocation")
        who = player if everyone else "they"
        return dest(x, z, y, d, "where %s last died" % who, "to where %s last died, at" % who, exact=True)
    raise Failed("unknown destination %r" % to)


def dest(x, z, y, dim, label, phrase, show_xz=True, exact=False):
    """{"x", "z": the centre; "y": a known height or None; "dim"; "label": the place in words;
    "phrase": what the report says before the landing's x y z; "exact": land on x y z itself
    when it is safe}."""
    return {"x": int(x), "z": int(z), "y": y, "dim": dim, "label": label, "phrase": phrase,
            "show_xz": show_xz, "exact": exact}


def locate(kind, target, dim, player, pos0, dim0):
    tag = target.startswith("#")
    ident = target.lstrip("#").lower()
    if ident.startswith("minecraft:"):
        ident = ident[len("minecraft:"):]
    if not ID_RE.match(ident):
        raise Failed("%r is not a %s id" % (target, kind))
    full = ("#" if tag else "") + "minecraft:" + ident
    cx, cz = scaled(pos0, dim0, dim)
    ask = "execute in %s positioned %d 0 %d run locate %s %%s" % (dim, cx, cz, kind)
    out = rcon(ask % full)
    if kind == "structure" and not tag and out.startswith("There is no structure"):
        # village, ruined_portal...: a tag of several structures, not one. Try the tag once.
        again = rcon(ask % ("#" + full))
        if LOCATED.match(again) or again.startswith("Could not find"):
            log("no structure %s, but a tag #%s: using it" % (full, full))
            full, out = "#" + full, again
    m = LOCATED.match(out)
    if m:
        found = m.group(2) or m.group(1)
        label = describe(found, kind)
        return dest(int(m.group(3)), int(m.group(5)), None, dim, label, "to %s at" % label)
    words = describe(full, kind)
    if out.startswith("Could not find"):
        raise Failed("no %s within reach of %s%s" % (words[words.index(" ") + 1:], player, in_dim(dim)))
    if out.startswith("There is no") or out.startswith("Can't find") or "<--[HERE]" in out:
        raise Failed("Minecraft has no %s called %s" % (kind, full))
    raise Failed("locate %s %s failed: %s" % (kind, full, out or "no answer from Minecraft"))


def describe(ident, kind=""):
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


def scaled(pos, dim_from, dim_to):
    """The player's x z, as seen from another dimension: the Nether is 8 times smaller."""
    x, z = pos[0], pos[2]
    if dim_from == dim_to:
        return int(math.floor(x)), int(math.floor(z))
    if dim_to == "minecraft:the_nether" and dim_from == "minecraft:overworld":
        return int(x // 8), int(z // 8)
    if dim_to == "minecraft:overworld" and dim_from == "minecraft:the_nether":
        return clamp(x * 8), clamp(z * 8)
    return 0, 0


def clamp(v):
    return int(max(-BORDER, min(BORDER, math.floor(v))))


# ------------------------------------------------------------------ land

def land(player, d):
    """Land `player` on `d` exactly when it has a safe known x y z, else near it, ring by
    ring: ((x, y, z), dim, ring), ring 0 for the exact spot; or None."""
    if d.get("exact") and d["y"] is not None and d["dim"]:
        spot = exact_spot(d)
        if spot is not None:
            out = rcon("execute in %s run tp %s %s" % (d["dim"], player, xyz(spot, "%.1f")))
            if out.startswith("Teleported"):
                pos, dim = where(player)
                return pos, dim, 0
            if out.startswith("No entity was found") or out.startswith("No player was found"):
                raise Offline([player])
            log("tp to the exact spot: %s" % (out or "no answer from Minecraft"))
        else:
            log("the exact spot %d %d %d is not safe: landing round it" % (d["x"], int(d["y"]), d["z"]))
    if d["y"] is not None:
        top = int(d["y"]) + 2       # a known height: land at or below it, not on the roof above
    else:
        top = TOPS.get(d["dim"], TOP)
    if d["x"] is None:              # the world spawn: RCON's own position
        cmd = "spreadplayers ~ ~ 0 %d under " + str(top) + " false " + player
    else:
        cmd = ("execute in %s run spreadplayers %d %d 0 " % (d["dim"], d["x"], d["z"])
               + "%d under " + str(top) + " false " + player)
    for r in RINGS:
        out = rcon(cmd % r)
        if out.startswith("Spread "):
            pos, dim = where(player)
            if safe(pos, dim):
                return pos, dim, r
            log("ring %d: unsafe ground at %s" % (r, xyz(pos)))
        elif out.startswith("Could not spread"):
            log("ring %d: no spot (%s)" % (r, out))
        elif out.startswith("No entity was found") or out.startswith("No player was found"):
            raise Offline([player])
        else:
            raise Failed("spreadplayers failed: %s" % (out or "no answer from Minecraft"))
    return None


def exact_spot(d):
    """The block centre to stand on at d's x y z, or one block up (on a slab, a carpet, a
    bed); None when neither is safe: solid ground that does not hurt, the feet and the head
    free and not water or lava."""
    x, y, z = d["x"], int(math.floor(d["y"])), d["z"]
    for up in (0, 1):
        check = "execute in %s positioned %d %d %d unless block ~ ~-1 ~ %s" % (d["dim"], x, y + up, z, FREE)
        check += "".join(" unless block ~ ~-1 ~ " + b for b in UNSAFE_GROUND)
        for head in ("~", "~1"):
            check += " if block ~ %s ~ %s" % (head, FREE)
            check += "".join(" unless block ~ %s ~ %s" % (head, b) for b in UNSAFE_FEET)
        if rcon(check).startswith("Test passed"):
            return (x + 0.5, float(y + up), z + 0.5)
    return None


def safe(pos, dim):
    """False when the landing stands on or in something that hurts or swallows."""
    check = "execute in %s positioned %s" % (dim, xyz(pos, "%.3f"))
    check += "".join(" unless block ~ ~-1 ~ " + b for b in UNSAFE_GROUND)
    check += "".join(" unless block ~ ~ ~ " + b for b in UNSAFE_FEET)
    out = rcon(check)
    if out.startswith("Test passed"):
        return True
    if out.startswith("Test failed"):
        return False
    log("ground check answered %r; spreadplayers' own check stands" % out)
    return True


def settle(player, pos0, dim0, pos1, dim1, to):
    """Set the spawn point where `player` landed when the move was over 1,000 blocks."""
    if to == "spawn_point" or dim0 != dim1:
        return False
    if math.hypot(pos1[0] - pos0[0], pos1[2] - pos0[2]) <= SPAWN_AFTER:
        return False
    out = rcon("execute in %s run spawnpoint %s %s" % (dim1, player, xyz(pos1)))
    if not out.startswith("Set spawn point"):
        log("spawnpoint %s: %s" % (player, out or "no answer"))
        return False
    return True


# ------------------------------------------------------------------ players

def online():
    m = re.search(r"online:(.*)", rcon("list"))
    return [p.strip() for p in m.group(1).split(",") if PLAYER_RE.match(p.strip())] if m else []


def data(player, path):
    """The SNBT after "has the following entity data: ", parsed; None when the path is missing."""
    out = rcon("data get entity %s %s" % (player, path))
    i = out.find(DATA)
    if i >= 0:
        try:
            return places.snbt(out[i + len(DATA):])
        except ValueError as e:
            raise Failed("cannot read %s's %s: %s" % (player, path, e))
    if out.startswith("Found no elements matching"):
        return None
    if out.startswith("No entity was found") or out.startswith("No player was found"):
        raise Offline([player])
    raise Failed("Minecraft did not answer about %s: %s" % (player, out or "no reply"))


def where(player):
    """((x, y, z), dimension) of an online player."""
    p = data(player, "Pos")
    if not isinstance(p, list) or len(p) != 3:
        raise Failed("cannot read where %s is" % player)
    d = data(player, "Dimension")
    return (float(p[0]), float(p[1]), float(p[2])), d if isinstance(d, str) and DIM_RE.match(d) else OVERWORLD


def where_or_none(player):
    try:
        return where(player)
    except Failed:
        return None


def pos_dim(v, what):
    """(x, y, z, dimension) from {pos: [I; x, y, z], dimension: "..."}."""
    try:
        x, y, z = [int(c) for c in v["pos"]]
        d = v.get("dimension") or OVERWORLD
    except (TypeError, ValueError, KeyError, AttributeError):
        raise Failed("cannot read %s: %r" % (what, v))
    if not isinstance(d, str) or not DIM_RE.match(d):
        raise Failed("cannot read %s's dimension: %r" % (what, d))
    return x, y, z, d


# ------------------------------------------------------------------ words

def xyz(pos, fmt="%d"):
    if fmt == "%d":
        return " ".join("%d" % math.floor(v) for v in pos)
    return " ".join(fmt % v for v in pos)


def at_xz(d):
    if d["x"] is None or not d.get("show_xz", True):
        return ""
    return " at %d %d" % (d["x"], d["z"])


def in_dim(dim):
    if not dim or dim == "minecraft:overworld":
        return ""
    return " in the " + dim.split(":")[-1].replace("the_", "").replace("_", " ")
