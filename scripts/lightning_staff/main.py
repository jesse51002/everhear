"""Lightning Staff: right-click to strike lightning on the block you look at, at once, with extra bolts round it and damage to everything near."""
ASKS = ["can you make me a lightning staff", "is it possible to increase the lightning's damage",
        "increase the amount of lightning strikes", "the lightning is so hard to aim, it's hyper delayed",
        "stop the lightning striking me"]
from kit import values, items, aim, fx, loop, exclude, world
import itertools
import math
import mc
import random
import re

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "item":             {"type": "str", "default": "Lightning Staff"},
    "cooldown":         {"type": "number", "min": 0, "max": 60, "default": 1, "live": True},
    "range":            {"type": "int", "min": 4, "max": 300, "default": 120, "live": True},
    "damage_amount":    {"type": "number", "min": 0, "max": 100000, "default": 40, "live": True},
    "damage_type":      {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "lightning_bolt", "live": True},
    "damage_kill":      {"type": "bool", "default": False, "live": True},
    "damage_most":      {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head":      {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare":     {"type": "list", "of": "player", "default": [], "live": True},
    "lightning_count":  {"type": "int", "min": 1, "max": 50, "default": 4, "live": True},
    "lightning_spread": {"type": "number", "min": 0, "max": 32, "default": 3, "live": True},
    "lightning_layout": {"type": "enum", "of": ["scatter", "ring"], "default": "scatter", "live": True},
    "lightning_every":  {"type": "number", "min": 0, "max": 5, "default": 0, "live": True},
    "lightning_spare":  {"type": "list", "of": "player", "default": [], "live": True},
    "around_radius":    {"type": "number", "min": 1, "max": 128, "default": 4, "live": True},
    "around_what":      {"type": "enum", "of": ["mobs", "players", "everything"], "default": "everything", "live": True},
    "around_most":      {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
}


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_most: only the nearest that many (1:
    the first only; 0: all). shooter: a player's name, or their aim.look(), with which a hit
    whose head the look passes takes damage_head times the damage. Never the shooter, a
    name in damage_spare, a pet, a villager or anything else exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    count = {}
    for h in hits:
        tag = body + "h" if v["damage_head"] > 1 and _damage_on_head(h, shooter) else body
        count[tag] = count.get(tag, 0) + _damage_mark(h, tag, spare)
    for tag, n in count.items():
        if not n:
            continue
        if v["damage_kill"]:
            _damage_all(tag, 1000000, "generic_kill", by)
        else:
            _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    return sum(count.values())


_DAMAGE_N = itertools.count(1)


def _damage_mark(h, tag, spare):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch, so a hit listed twice is tagged once. 1 if it was tagged now, else 0."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare), tag))
    return 1 if reply.startswith("Added tag") else 0


def _damage_all(tag, amount, kind, by):
    """Damage everything tagged, once, as `by`; as nobody if `by` cannot be found (gone offline)."""
    cmd = "execute as @e[tag=%s] run damage @s %.1f minecraft:%s" % (tag, amount, kind)
    if by and "Applied" in mc.rcon(cmd + " by " + by):
        return
    mc.rcon(cmd)


def _damage_on_head(h, look):
    """True if a look (aim.look) passes within half a block of the head of a body standing at
    the hit: 1.7 above its feet, as tall as a player or a zombie."""
    if not hasattr(look, "origin"):
        return False
    o, d = look.origin, look.dir
    w = [h.pos[0] - o[0], h.pos[1] + 1.7 - o[1], h.pos[2] - o[2]]
    t = w[0] * d[0] + w[1] * d[1] + w[2] * d[2]
    return t > 0 and math.sqrt(sum((w[i] - t * d[i]) ** 2 for i in range(3))) <= 0.5


# --- preset: lightning (copied from _presets/lightning.py) ---
def lightning(at, by, v):
    """Strike lightning_count bolts: the first at the place (or a player's name: where they
    stand), the rest within lightning_spread blocks of it, scattered at random or evenly in
    a ring ("ring" with 5 is a plus), lightning_every seconds apart (0: all at once). A bolt
    hurts and sets fire to what is within about 3 blocks, so none lands within 4 blocks of
    by (the player who called it, or "") or of a player in lightning_spare. Returns how many
    struck."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    n, s = v["lightning_count"], v["lightning_spread"]
    points = [tuple(at[:3])]
    for i in range(1, n):
        if v["lightning_layout"] == "ring":
            a, d = 2 * math.pi * (i - 1) / (n - 1), s
        else:
            a, d = random.uniform(0, 2 * math.pi), random.uniform(min(1.0, s), s)
        points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    keep = "".join("unless entity @a[name=%s,distance=..4] " % mc.bare(p) for p in [by] + list(v["lightning_spare"])
                   if p and not p.startswith("@"))
    struck = 0
    for i, (x, y, z) in enumerate(points):
        if i and v["lightning_every"] and not loop.wait(v["lightning_every"]):
            break
        reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %srun summon lightning_bolt ~ ~ ~ "
                        "{Tags:[\"vfg\",\"%s\"]}" % (x, y, z, keep, mc.TAG))
        struck += 1 if "Summoned" in reply else 0
    return struck


# --- preset: around (copied from _presets/around.py) ---
def around(at, v):
    """Every mob, player or both (around_what) within around_radius of a place or a player
    (a name: never that player), as a list of aim.Hit nearest first, the shape aim.near_line
    gives, so damage_hits, freeze and push take either. around_most: the nearest that many
    (0: all). Never a pet, a villager or anything else exclude spares, nor a player in
    creative or spectator. For the blocks round a place, area.box(at, r) and area.sphere."""
    me = ""
    if isinstance(at, str):
        me, feet = at, mc.pos(at)
        if feet is None:
            return []
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(me))
    r, what = v["around_radius"], v["around_what"]
    found = {}
    if what in ("mobs", "everything"):
        _around_ask(at, exclude.mobs("distance=..%g" % r), r, False, found)
    if what in ("players", "everything"):
        _around_ask(at, exclude.players(me, "distance=..%g" % r), r, True, found)
    dim = world.dim(at)
    hits = []
    for (x, y, z, player), name in found.items():
        t = math.sqrt((x - at[0]) ** 2 + (y - at[1]) ** 2 + (z - at[2]) ** 2)
        hits.append(aim.Hit(t, world.Pos(x, y, z, dim), mc.bare(name) if player else name, player))
    hits.sort(key=lambda h: h.t)
    return hits[:v["around_most"]] if v["around_most"] else hits


_AROUND_POS = re.compile(r"([^\]\[]*?) has the following entity data: \[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]")


def _around_ask(at, frag, r, player, found, box=None, depth=0):
    """Ask where everything a fragment selects is; a reply cut at 4 KB is asked again in
    eight smaller boxes."""
    if box is None:
        box = (at[0] - r, at[1] - r, at[2] - r, 2 * r)
    x, y, z, size = box
    inside = "" if depth == 0 else "positioned %.2f %.2f %.2f if entity @s[dx=%.2f,dy=%.2f,dz=%.2f] " % (
        x, y, z, size, size, size)
    reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %s %srun data get entity @s Pos" % (
        at[0], at[1], at[2], frag, inside))
    if len(reply.encode("utf-8")) >= world.MAX_REPLY - 200 and depth < 3:
        half = size / 2.0
        for i in range(8):
            _around_ask(at, frag, r, player, found,
                        (x + half * (i & 1), y + half * (i >> 1 & 1), z + half * (i >> 2 & 1), half), depth + 1)
        return
    for m in _AROUND_POS.finditer(reply):
        found[(float(m.group(2)), float(m.group(3)), float(m.group(4)), player)] = m.group(1).strip()


v = values(VALUES)
for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"], color="yellow")):
    r = aim.ray(p, v["range"])
    if r is None or r.why != "block":
        fx.actionbar(p, "No target in range", "gray")
        continue
    lightning(r.end, p, v)          # never within 4 blocks of p, nor of anyone in lightning_spare
    damage_hits(around(r.end, v), p, v)
