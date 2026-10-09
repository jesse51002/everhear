"""Barrier Shield: a bubble of light round a player, or round a place, that throws back every mob that comes inside it."""
ASKS = ["make a barrier around me", "a barrier that pushes things away", "like an infinity barrier",
        "give my sniper nest a barrier that protects it from all harm"]
from kit import values, aim, exclude, fx, loop, world
import math
import re
import mc
import random

VALUES = {
    "player":        {"type": "player", "default": "@a", "live": True},
    "at":            {"type": "list", "of": "number", "default": [], "live": True},
    "around_radius": {"type": "number", "min": 1, "max": 128, "default": 5, "live": True},
    "around_what":   {"type": "enum", "of": ["mobs", "players", "everything"], "default": "mobs", "live": True},
    "around_most":   {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "push_way":      {"type": "enum", "of": ["away", "toward", "up"], "default": "away", "live": True},
    "push_strength": {"type": "number", "min": 0.5, "max": 40, "default": 4, "live": True},
}


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


# --- preset: push (copied from _presets/push.py) ---
def push(hits, frm, v):
    """Move every hit (a list of aim.Hit, from aim.near_line, around or lock_on) about
    push_strength blocks: "away" from frm, "toward" it (never past it), or "up" into the
    air. frm: a place, or a player's name for where they stand. A mob is thrown (its
    Motion), so it flies, lands and slides; a player is moved there at once, and only into
    room to stand, since a player's Motion cannot be set; "up" floats a player up instead.
    Pets and villagers are never moved. Returns how many were moved."""
    way, s = v["push_way"], v["push_strength"]
    if isinstance(frm, str):
        frm = mc.pos(frm)
        if frm is None:
            return 0
    seen, n = set(), 0
    for h in hits:
        key = h.name if h.player else (h.name, round(h.pos[0], 1), round(h.pos[1], 1), round(h.pos[2], 1))
        if key in seen:
            continue                    # listed twice: moved once
        seen.add(key)
        dx, dz = h.pos[0] - frm[0], h.pos[2] - frm[2]
        far = math.hypot(dx, dz)
        if far < 0.01:
            a = random.uniform(0, 2 * math.pi)
            dx, dz, far = math.cos(a), math.sin(a), 1.0
        ux, uz = dx / far, dz / far
        if way == "toward":
            ux, uz, s_here = -ux, -uz, min(s, far - 1.5)
            if s_here <= 0:
                continue
        else:
            s_here = s
        n += _push_player(h, ux, uz, s_here, way) if h.player else _push_mob(h, ux, uz, s_here, way)
    return n


def _push_mob(h, ux, uz, s, way):
    """Throw a mob: about 5 blocks of flight and slide for each 1 of horizontal Motion, with a
    hop of 0.4 so it leaves the ground; straight up, a Motion of sqrt(height / 5.4)."""
    if way == "up":
        motion = (0.0, math.sqrt(s / 5.4), 0.0)
    else:
        motion = (ux * s / 5.0, 0.4, uz * s / 5.0)
    x, y, z = h.pos[:3]
    mob = exclude.mobs("distance=..1.5,sort=nearest,limit=1")        # found again where it was
    reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run data modify entity @s "
                    "Motion set value [%.3fd,%.3fd,%.3fd]" % ((x, y, z, mob) + motion))
    return 1 if "Modified" in reply else 0


def _push_player(h, ux, uz, s, way):
    """Move a player: up by levitation (about 0.75 blocks a second for each level), else to
    the spot s blocks along, or half as far, whichever has room to stand."""
    if way == "up":
        level = max(0, int(round(s / 0.75)) - 1)
        return 1 if "Applied" in mc.rcon("effect give %s levitation 1 %d true" % (h.name, min(level, 127))) else 0
    x, y, z = h.pos[:3]
    for k in (s, s / 2.0):
        tx, ty, tz = x + ux * k, y + 0.1, z + uz * k
        bx, by, bz = int(math.floor(tx)), int(math.floor(ty)), int(math.floor(tz))
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "if block %d %d %d #minecraft:replaceable if block %d %d %d "
                        "#minecraft:replaceable run tp %s %.3f %.3f %.3f" % (bx, by, bz, bx, by + 1, bz, h.name, tx, ty, tz))
        if "Teleported" in reply:
            return 1
    return 0


def ring(c, r, turn):
    """The bubble round a player or a place: three rings of light, turning, and a crown of soul fire."""
    if isinstance(c, str):
        feet = mc.pos(c)
        if feet is None:
            return
        c = world.Pos(feet[0], feet[1], feet[2], world.dim_of(c))
    for dy, k in ((-2.0, 0.8), (1.0, 1.0), (4.0, 0.8)):
        for i in range(8):
            a = turn + i * math.pi / 4
            fx.particle("end_rod", c.moved(k * r * math.cos(a), dy, k * r * math.sin(a)))
    for i in range(4):
        a = 2 * turn + i * math.pi / 2
        fx.particle("soul_fire_flame", c.moved(0.5 * r * math.cos(a), 4.5, 0.5 * r * math.sin(a)))


v = values(VALUES)
for n in loop(every=0.2):
    # Round each player the player value names, or round one place (x y z, in the overworld).
    centres = [world.Pos(v["at"][0], v["at"][1], v["at"][2])] if len(v["at"]) >= 3 else loop.who(v["player"])
    for c in centres:
        push(around(c, v), c, v)                # a player's own bubble never throws them
        if n % 3 == 0:
            ring(c, v["around_radius"], n * 0.1)
