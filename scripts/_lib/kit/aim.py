"""Aiming: where a player looks from, a ray along it, and who is near a line.

    r = aim.ray(p, 120)                 # None if p cannot be read (gone offline)
    r.end                               # a Pos: just before the block it hit, at an unloaded chunk, or 120 out
    r.why, r.hit_block, r.length        # "block", "unloaded", "world" or "range"; the block hit or None
    r.path                              # a Pos every block from the eyes to r.end
    for hit in aim.near_line(r, 2.0, shooter=p):
        aim.run_on(hit, "damage @s 10 minecraft:player_attack by %s" % p)
    hits = aim.near(r.end, 4, shooter=p, hostile=True)  # monsters within 4 blocks of where it ended
    aim.mark(hits, mc.TAG + "_hit1")                    # tag each once, then act on them all at once,
    mc.rcon("execute as @e[tag=%s_hit1] run damage @s 6 minecraft:magic" % mc.TAG)
    aim.untag(mc.TAG + "_hit1")                         # and take the tag off everyone after
    lk = aim.look(p, eyes="auto")                       # from crouching eyes when they crouch

The eyes are 1.62 above the feet (1.27 crouching). A look reads position and rotation once;
everything after is worked out here, never with ^ ^ ^ in a command. A ray tests up to 32
blocks in one command ("execute if block ... if block ..."), then halves the group that
failed to find the first solid block, so a 100-block ray costs about ten commands; it stops
at the first chunk that is not loaded ("That position is not loaded"). Who is near a line
is asked in 10-block slices, and who is near a place in smaller boxes when needed, because
a reply over 4 KB is cut.
"""
import collections
import math
import re

import mc
from . import exclude, world
from .world import Pos

EYES = 1.62
SNEAK_EYES = 1.27
OPEN = "#minecraft:replaceable"     # what a ray passes through: air, water, lava, grass, snow layers...

_FLOAT = r"(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)"


def direction(yaw, pitch):
    """The unit vector a yaw and pitch (degrees, as Minecraft gives them) point along."""
    y, p = math.radians(yaw), math.radians(pitch)
    return (-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


class Look(object):
    """Where a player looks from and along: origin (a Pos at the eyes), dir (unit vector),
    feet (a Pos), yaw, pitch, right (unit vector to their right, level), dim, player."""

    def __init__(self, player, feet, yaw, pitch, eyes=EYES):
        self.player, self.feet, self.yaw, self.pitch = player, feet, yaw, pitch
        self.dim = feet.dim
        self.dir = direction(yaw, pitch)
        self.origin = feet.moved(0, eyes, 0)
        r = math.radians(yaw)
        self.right = (-math.cos(r), 0.0, -math.sin(r))

    def ahead(self, k, up=0.0, right=0.0):
        """The Pos k blocks ahead of the eyes, moved up and to the right."""
        o, d, r = self.origin, self.dir, self.right
        return Pos(o[0] + d[0] * k + r[0] * right, o[1] + d[1] * k + up, o[2] + d[2] * k + r[2] * right, self.dim)


_SNEAKING = '{type:"entity_properties",entity:"this",predicate:{flags:{is_sneaking:true}}}'


def look(player, eyes=EYES):
    """How a player looks, from one read of their position and rotation (and dimension,
    which is kept 2 s); None if they cannot be read. eyes="auto": from 1.27 when they crouch
    (a shot fired crouching, a sniper zoomed in), else 1.62; it costs a command more when
    they stand."""
    at = None
    if eyes == "auto":
        v = re.findall(_FLOAT + "d", mc.rcon("execute as %s if predicate %s run data get entity @s Pos" % (player, _SNEAKING)))
        at, eyes = ((float(v[0]), float(v[1]), float(v[2])), SNEAK_EYES) if len(v) >= 3 else (None, EYES)
    at = at or mc.pos(player)
    rot = re.findall(_FLOAT + "f", mc.rcon("data get entity %s Rotation" % player))
    if not at or len(rot) < 2:
        return None
    return Look(player, Pos(at[0], at[1], at[2], world.dim_of(player)), float(rot[0]), float(rot[1]), eyes)


def cells(start, d, length):
    """Every block a line passes through, in order: [(distance it enters at, (x, y, z))]."""
    x, y, z = start[0], start[1], start[2]
    ix, iy, iz = int(math.floor(x)), int(math.floor(y)), int(math.floor(z))
    pos, cell = [x, y, z], [ix, iy, iz]
    step, tmax, tdelta = [0, 0, 0], [math.inf] * 3, [math.inf] * 3
    for a in range(3):
        if d[a] > 1e-12:
            step[a], tmax[a], tdelta[a] = 1, (cell[a] + 1 - pos[a]) / d[a], 1 / d[a]
        elif d[a] < -1e-12:
            step[a], tmax[a], tdelta[a] = -1, (pos[a] - cell[a]) / -d[a], -1 / d[a]
    out = [(0.0, tuple(cell))]
    while True:
        a = tmax.index(min(tmax))
        t = tmax[a]
        if t > length:
            return out
        cell[a] += step[a]
        tmax[a] += tdelta[a]
        out.append((t, tuple(cell)))


class Ray(object):
    """A line from start along dir that stopped after `length` blocks, and why: "block" (it
    reached hit_block), "unloaded" (a chunk that is not loaded), "world" (the top or bottom
    of the world), "range" (its full length), or "error" (the server did not answer)."""

    def __init__(self, start, dir, length, why="range", hit_block=None):
        self.start = start if isinstance(start, Pos) else Pos(*start[:3])
        self.dim = self.start.dim
        self.dir, self.length, self.why, self.hit_block = dir, max(0.0, length), why, hit_block

    def point(self, k):
        """The Pos k blocks along the ray."""
        s, d = self.start, self.dir
        return Pos(s[0] + d[0] * k, s[1] + d[1] * k, s[2] + d[2] * k, self.dim)

    @property
    def end(self):
        """Where the ray stopped."""
        return self.point(self.length)

    @property
    def path(self):
        """A Pos every block from the start to the end, both included."""
        n = int(self.length)
        return [self.point(k) for k in range(n + 1)] + ([self.end] if self.length > n else [])

    def every(self, spacing):
        """A Pos every `spacing` blocks from the start to the end, both included."""
        n = max(1, int(math.ceil(self.length / max(0.05, spacing))))
        return [self.point(self.length * i / n) for i in range(n + 1)]


def cast(start, d, max_len=100, blocks=True):
    """A ray from a place along a direction: it stops before the first block that is not
    #minecraft:replaceable (blocks=False goes through blocks), at the first chunk that is not
    loaded, or at the top or bottom of the world."""
    start = start if isinstance(start, Pos) else Pos(*start[:3])
    dim = start.dim
    lo, hi = world.limits(dim)
    cs = cells(start, d, max_len)[1:]
    end, why = float(max_len), "range"
    for i, (t, c) in enumerate(cs):
        if not lo <= c[1] <= hi:
            cs, end, why = cs[:i], t, "world"
            break
    if blocks:
        conds = ["if block %d %d %d %s" % (c[0], c[1], c[2], OPEN) for t, c in cs]
        idx = list(range(len(cs)))
    else:
        idx, seen = [], set()
        for i, (t, c) in enumerate(cs):
            if (c[0] >> 4, c[2] >> 4) not in seen:
                seen.add((c[0] >> 4, c[2] >> 4))
                idx.append(i)
        conds = ["if loaded %d 64 %d" % (cs[i][1][0], cs[i][1][2]) for i in idx]
    bad, reply = world.first_fail(conds, dim)
    if bad is None:
        return Ray(start, d, end, why)
    t, c = cs[idx[bad]]
    low = reply.lower()
    if "not loaded" in low or (not blocks and "test failed" in low):
        why = "unloaded"
    elif "out of th" in low:            # "That position is out of this world!"
        why = "world"
    elif "test failed" in low:
        why = "block"
    else:
        why = "error"
    hit = Pos(c[0], c[1], c[2], dim) if why == "block" else None
    return Ray(start, d, max(0.0, t - 0.05), why, hit)


def ray(player, max_len=100, blocks=True, eyes=EYES):
    """The ray from a player's eyes along where they look, up to max_len blocks: see cast.
    eyes="auto" aims from crouching eyes when they crouch. None if the player cannot be read."""
    lk = player if isinstance(player, Look) else look(player, eyes)
    if lk is None:
        return None
    return cast(lk.origin, lk.dir, max_len, blocks)


Hit = collections.namedtuple("Hit", "t pos name player uuid", defaults=(None,))
Hit.__doc__ = """Something near a line: t (how far along), pos (its feet, a Pos), name (a
player's name, or a mob's display name), player (True for a player), uuid (set when it was
found by its uuid, as a projectile is: mark and run_on then find it exactly, however far it
flew since; None for what near_line and near find, which is found again where it was)."""

_POS = re.compile(r"([^\]\[]*?) has the following entity data: \[%sd, %sd, %sd\]" % (_FLOAT, _FLOAT, _FLOAT))


def _dump(dim, center, frag):
    reply = mc.rcon(world.prefix(dim) + "positioned %.2f %.2f %.2f %s run data get entity @s Pos"
                    % (center[0], center[1], center[2], frag))
    return reply, [(m.group(1).strip(), float(m.group(2)), float(m.group(3)), float(m.group(4)))
                   for m in _POS.finditer(reply)]


def near_line(r, radius=1.5, shooter="", spare=(), with_players=True, heights=(0.2, 0.9, 1.6, 2.6), hostile=False):
    """Everything a hit may touch (see exclude) whose body comes within `radius` of a ray's
    line, from its start to its end, nearest first; hostile=True: monsters only among the
    mobs. Asked in 10-block slices, each slice halved again if its reply was cut at 4 KB."""
    found = {}

    def slice_(a, b, depth=0):
        mid = (a + b) / 2.0
        reach = "distance=..%.1f" % ((b - a) / 2.0 + radius + max(heights) + 0.5)
        for frag in exclude.targets(shooter, reach, spare, with_players, hostile):
            reply, ents = _dump(r.dim, r.point(mid), frag)
            if len(reply.encode("utf-8")) >= world.MAX_REPLY - 200 and depth < 3 and b - a > 1.5:
                slice_(a, mid, depth + 1)
                slice_(mid, b, depth + 1)
                return
            for name, x, y, z in ents:
                found[(round(x, 3), round(y, 3), round(z, 3), frag.startswith("as @a"))] = name

    k = 0.0
    while k < r.length or k == 0.0:
        slice_(k, min(r.length, k + 10.0))
        k += 10.0
        if r.length <= 0:
            break
    s, d = r.start, r.dir
    hits = []
    for (x, y, z, is_player), name in found.items():
        best = None
        for h in heights:
            v = (x - s[0], y + h - s[1], z - s[2])
            t = v[0] * d[0] + v[1] * d[1] + v[2] * d[2]
            if t < -radius or t > r.length + radius:
                continue
            off = (v[0] - t * d[0], v[1] - t * d[1], v[2] - t * d[2])
            if off[0] ** 2 + off[1] ** 2 + off[2] ** 2 <= radius * radius:
                best = t if best is None else min(best, t)
        if best is not None:
            hits.append(Hit(max(0.0, best), Pos(x, y, z, r.dim), mc.bare(name) if is_player else name, is_player))
    hits.sort(key=lambda h: h.t)
    return hits


def near(at, radius, shooter="", spare=(), with_players=True, hostile=False, with_mobs=True):
    """Everything a hit may touch (see exclude) within `radius` of a place, as Hits nearest
    first (t is the distance from the place); hostile=True: monsters only among the mobs;
    with_mobs=False: players only. Asked again in eight smaller boxes when a reply is cut at
    4 KB."""
    at = at if isinstance(at, Pos) else Pos(*at[:3])
    found = {}

    def ask(lo, hi, depth=0):
        """Entities within radius of `at` whose feet are in the box lo..hi."""
        for frag in exclude.targets(shooter, "distance=..%.2f" % radius, spare, with_players, hostile, with_mobs):
            if depth:
                frag += " positioned %.2f %.2f %.2f if entity @s[dx=%.2f,dy=%.2f,dz=%.2f]" % (
                    lo[0], lo[1], lo[2], hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            reply, ents = _dump(at.dim, at, frag)
            if len(reply.encode("utf-8")) >= world.MAX_REPLY - 200 and depth < 3:
                mid = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
                for cx in ((lo[0], mid[0]), (mid[0], hi[0])):
                    for cy in ((lo[1], mid[1]), (mid[1], hi[1])):
                        for cz in ((lo[2], mid[2]), (mid[2], hi[2])):
                            ask((cx[0], cy[0], cz[0]), (cx[1], cy[1], cz[1]), depth + 1)
                return
            for name, x, y, z in ents:
                found[(round(x, 3), round(y, 3), round(z, 3), frag.startswith("as @a"))] = name

    ask(tuple(v - radius for v in at), tuple(v + radius for v in at))
    hits = []
    for (x, y, z, is_player), name in found.items():
        d = math.sqrt((x - at[0]) ** 2 + (y - at[1]) ** 2 + (z - at[2]) ** 2)
        if d <= radius + 1e-6:
            hits.append(Hit(d, Pos(x, y, z, at.dim), mc.bare(name) if is_player else name, is_player))
    hits.sort(key=lambda h: h.t)
    return hits


_cleared = set()    # tag names mark has taken off everyone once in this process


def mark(hits, tag, spare=(), hostile=False):
    """Tag each hit once, where it was (a player by name, one found by its uuid exactly, a
    mob as the nearest within 1.5 blocks that a hit may touch; hostile=True: a monster), so one
    command can then act on all of them at once (execute as @e[tag=<tag>] run damage @s ...)
    and a mob listed twice is hurt once. Take the tag off after with untag(tag). The first time
    this process marks with a tag, it is first taken off anyone who still carries it: an
    earlier run stopped between its mark and its untag leaves it on them, and a player who
    carries it would never be tagged "newly", so never hurt. After that the untag every mark
    is followed by has left it on nobody, so a tag used again costs no command more. Returns
    how many were newly tagged."""
    if tag not in _cleared:
        untag(tag)
        _cleared.add(tag)
    n = 0
    for h in hits:
        if h.player:
            reply = mc.rcon("tag %s add %s" % (h.name, tag))
        elif getattr(h, "uuid", None):
            reply = mc.rcon("tag %s add %s" % (h.uuid, tag))
        else:
            x, y, z = h.pos[:3]
            frag = exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare, hostile)
            reply = mc.rcon(world.prefix(h.pos.dim) + "positioned %.3f %.3f %.3f %s run tag @s add %s"
                            % (x, y, z, frag, tag))
        n += 1 if "added tag" in reply.lower() else 0
    return n


def untag(tag):
    """Take a tag off every entity and every player, a dead one too (@e leaves the dead out,
    @a does not), so nobody carries it into the next shot."""
    mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    mc.rcon("tag @a[tag=%s] remove %s" % (tag, tag))


def run_on(hit, cmd, spare=()):
    """Run a command as one hit, as @s (and at it): "damage @s 10 minecraft:player_attack by Steve".
    A player is found by name, a hit with a uuid by its uuid, a mob again by where it was (the
    nearest within 1.5 blocks that a hit may touch). Returns the reply."""
    if hit.player:
        return mc.rcon("execute as %s at @s run %s" % (hit.name, cmd))
    if getattr(hit, "uuid", None):
        return mc.rcon("execute as %s at @s run %s" % (hit.uuid, cmd))
    x, y, z = hit.pos[:3]
    frag = exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare)
    return mc.rcon(world.prefix(hit.pos.dim) + "positioned %.3f %.3f %.3f %s at @s run %s" % (x, y, z, frag, cmd))
