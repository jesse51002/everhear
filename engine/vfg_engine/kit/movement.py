"""Moving beings (the kit, DESIGN2 §5 "Movement"): `push` throws anything (a mob, a player, a
projectile) up, away from a point, toward it or forward, about so many blocks; `land_safely`
moves someone onto safe ground at or round a place. Teleporting itself is the core's
`self.teleport`.

`push` is the old presets push and launch in one: both set a velocity worked out from how far it
should go. The flight is the old launch preset's, measured on 26.3 (a player, a husk and an
armor stand fly alike): gravity 0.08 a tick then 0.98 of the vertical speed kept, 0.91 of the
speed along the ground kept in the air, 0.546 on the ground. A throw along the ground leaves at
40 degrees.

**A player** cannot be given a velocity on 26.3 (no player's data is written; the engine refuses
a Motion on one), so `push` throws a player the way the old launch preset did, measured on 26.3:
wind charges let fall at their feet, all bursting on the ground in the same tick (a ring round
them for "up", one spot behind them for a throw along the ground), as a wind charge jump does.
The push reaches their own client, so they fly free. A burst also pushes whoever stands within
two blocks of it and flips a door or a button there. A player in the air, or with no open ground
round their feet to burst on, is not thrown.
"""

from __future__ import annotations

import functools
import math
import random

from ._base import PLAYER, flat_facing, key_of, pos_of, where
from .finders import PROJECTILE_TYPES

WAYS = ("away", "toward", "up", "forward")
LANDINGS = ("safe", "float", "hurt")
GRAVITY, DRAG = 0.08, 0.98          # taken off the vertical speed each tick, then what is kept of it
AIR = 0.91                          # what is kept of the speed along the ground each tick in the air
GROUND = 0.546                      # and over the first tick, leaving the ground (0.6 x 0.91)
SLOPE = 0.84                        # tan 40 degrees: how steeply a throw along the ground leaves
MOST_SPEED = 6.0                    # blocks a tick, the fastest throw the kit asks for
CUSHION_S = 1.5                     # a safe landing's Resistance V lasts the flight and this long after


def flight(along: float, up: float, first: float = GROUND) -> tuple[float, int, float]:
    """(blocks along the ground it lands and slides to a stop at, ticks in the air, blocks up at
    the top) for a throw from level ground at `along` and `up` blocks a tick."""
    x = y = top = 0.0
    t = 0
    while t < 1200:
        x, y, t = x + along, y + up, t + 1
        top = max(top, y)
        up = (up - GRAVITY) * DRAG
        along *= first if t == 1 else AIR
        if y <= 0 and up < 0:
            break
    while along > 0.003:
        x, along = x + along, along * GROUND
    return x, t, top


def _solve(f, want, lo, hi):
    """The x in [lo, hi] where the rising f(x) is `want`; the nearer end when out of reach."""
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if f(mid) < want:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def speed_for(way: str, blocks: float) -> tuple[float, float, int]:
    """(along, up, ticks in the air): the speed in blocks a tick that throws something `blocks`
    (that high for "up", that far along the ground for the others), and how long it flies.
    Worked out once per tenth of a block and remembered: it is a few thousand steps of sums."""
    return _speed_for("up" if way == "up" else "along", round(max(0.0, float(blocks)), 1))


@functools.lru_cache(maxsize=2048)
def _speed_for(way: str, blocks: float) -> tuple[float, float, int]:
    if way == "up":
        up = _solve(lambda u: flight(0.0, u)[2], blocks, 0.0, MOST_SPEED)
        return 0.0, up, flight(0.0, up)[1]
    c = _solve(lambda c: flight(c, c * SLOPE)[0], blocks, 0.0, MOST_SPEED)
    return c, c * SLOPE, flight(c, c * SLOPE)[1]


def _heading(ctx, way, at, frm, who, blocks):
    """(unit vector along the ground, blocks) for a throw, or None when there is nothing to do
    ("toward" a point it is already at)."""
    if way == "up":
        return (0.0, 0.0), blocks
    if way == "forward":
        f = flat_facing(who)
        return (f[0], f[2]), blocks
    if frm is None:
        a = random.uniform(0, 2 * math.pi)
        return (math.cos(a), math.sin(a)), blocks
    dx, dz = at[0] - frm[0], at[2] - frm[2]
    far = math.hypot(dx, dz)
    if way == "toward":
        if far < 1.5:
            return None
        return (-dx / far, -dz / far), min(blocks, far - 1.0)
    if far < 0.01:
        a = random.uniform(0, 2 * math.pi)
        return (math.cos(a), math.sin(a)), blocks
    return (dx / far, dz / far), blocks


def _type_of(ctx, x) -> str:
    t = getattr(x, "type", None)
    if isinstance(t, str):
        return t
    if getattr(x, "is_player", False):
        return PLAYER
    if isinstance(x, str):
        if ctx.world.player(x) is not None:
            return PLAYER
        e = ctx.world.get(x)
        return e.type if e is not None else ""
    return PLAYER if hasattr(x, "gamemode") else ""


def push(self, ctx, who, way: str = "away", strength: float = 6.0, frm=None, landing: str = "safe") -> int:
    """Throw `who` (a Target, a name, a uuid, a snapshot, or a list of them; one listed twice
    goes once) about `strength` blocks: "up" that high, "away" from `frm`, "toward" it (never
    past it) or "forward" the way each faces, along the ground. `frm` is a point or a being (the
    user's feet when None). A mob is given its velocity, a player is thrown by wind charges at
    their feet (see above), a projectile is turned and sent straight on, without a hop.
    `landing`: "safe" (Resistance V for the flight, so the fall does not hurt), "float" (Slow
    Falling for the way down) or "hurt" (the fall as it comes). Returns how many it threw."""
    if way not in WAYS:
        raise ValueError("way is one of %s, not %r" % (", ".join(WAYS), way))
    if landing not in LANDINGS:
        raise ValueError("landing is one of %s, not %r" % (", ".join(LANDINGS), landing))
    if frm is None and ctx.user is not None and way in ("away", "toward"):
        frm = ctx.user
    origin = pos_of(ctx, frm) if frm is not None else None
    many = who if isinstance(who, (list, tuple)) and not (len(who) == 3 and all(
        isinstance(c, (int, float)) for c in who)) else [who]
    seen, n = set(), 0
    for x in many:
        key = key_of(x)
        at = pos_of(ctx, x)
        if not key or key in seen or at is None:
            continue
        seen.add(key)
        if isinstance(x, str):
            x = ctx.world.player(x) or ctx.world.get(x) or x
        heading = _heading(ctx, way, at, origin, x, float(strength))
        if heading is None:
            continue
        (ux, uz), blocks = heading
        kind = _type_of(ctx, x)
        if kind == PLAYER:
            ticks = _wind_launch(self, ctx, x, way, ux, uz, blocks)
            if not ticks:
                continue
            n += 1
            _cushion(self, key, ticks, landing)
            continue
        if kind in PROJECTILE_TYPES:
            k = blocks / 5.0
            vec = (0.0, k, 0.0) if way == "up" else (ux * k, 0.1, uz * k)
            ticks = 0
        else:
            along, up, ticks = speed_for(way, blocks)
            vec = (ux * along, up, uz * along)
        self.motion(key, tuple(round(c, 4) for c in vec))
        n += 1
        _cushion(self, key, ticks, landing)
    return n


def _cushion(self, key, ticks, landing):
    if ticks and landing == "safe":
        self.potion("minecraft:resistance", ticks / 20.0 + CUSHION_S, 4, who=key, particles=False)
    elif ticks and landing == "float":
        self.potion("minecraft:slow_falling", ticks / 20.0 + 3.0, 0, who=key, particles=False)


# ---- a player, by wind charges (the old launch preset's, measured on 26.3) -----------------------

# A wind charge pushes along the line from its burst to the eyes, by 1.22 x (1 - d / 2.4) for d from
# the burst to the feet; one let fall on the ground bursts 0.25 above it.
PUSH, REACH, BURST, EYES = 1.22, 2.4, 0.25, 1.62
MOST_CHARGES = 16                   # wind charges in one throw, at most
REST = 0.0784                       # the vertical speed of anything standing on the ground
WIND_NBT = "{Motion:[0.0d,-1.0d,0.0d]}"     # let fall: it bursts on the ground the next tick


def _charge(o: float) -> tuple[float, float]:
    """(along, up) push of one wind charge that bursts on the ground `o` blocks from the feet."""
    k = max(0.0, 1.0 - math.hypot(o, BURST) / REACH) * PUSH
    d = math.hypot(o, EYES - BURST)
    return k * o / d, k * (EYES - BURST) / d


def _falling(f, want, lo, hi):
    """The x in [lo, hi] where the falling f(x) is `want`."""
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if f(mid) > want:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


@functools.lru_cache(maxsize=1024)
def wind_plan(way: str, blocks: float) -> tuple[int, float, int]:
    """(how many charges, how far from the feet they burst, ticks in the air) for a throw of
    `blocks`: for "up" a ring round the feet whose pushes along the ground cancel; for the others
    all at one spot behind, as near 1.6 blocks back (a 40-degree arc) as the count lets."""
    if way == "up":
        need = speed_for("up", blocks)[1] + REST
        n = max(2, int(math.ceil(need)))
        o = _falling(lambda o: _charge(o)[1], need / n, 0.0, 2.3)
        return n, o, flight(0.0, need - REST)[1]

    def far(n, o):
        a, u = _charge(o)
        return flight(n * a, n * u - REST)[0]
    best = None
    for n in range(1, MOST_CHARGES + 1):
        if far(n, 2.0) <= blocks <= far(n, 1.0):
            o = _falling(lambda o: far(n, o), blocks, 1.0, 2.0)
            if best is None or abs(o - 1.6) < abs(best[1] - 1.6):
                best = (n, o)
    n, o = best or ((1, 2.0) if blocks < far(1, 2.0) else (MOST_CHARGES, 1.0))
    a, u = _charge(o)
    return n, o, flight(n * a, n * u - REST)[1]


def _wind_launch(self, ctx, p, way, ux, uz, blocks) -> int:
    """Throw a player with wind charges burst at their feet; the ticks they fly, or 0 when they
    are in the air or there is no open ground round their feet to burst on."""
    snap = p if hasattr(p, "on_ground") else ctx.world.player(key_of(p))
    if snap is None or not snap.alive or not snap.on_ground:
        return 0
    n, o, ticks = wind_plan("up" if way == "up" else "along", round(float(blocks), 1))
    x, y, z = snap.pos
    if way == "up":
        rings = [[(x + o * math.cos(t + 2 * math.pi * i / n), z + o * math.sin(t + 2 * math.pi * i / n))
                  for i in range(n)] for t in (0.0, math.pi / n)]
    else:
        rings = [[(x - ux * o, z - uz * o)] * n]
    feet, floor = math.floor(y + 0.6), math.floor(y - 0.5)
    for ring in rings:
        cells = sorted({(math.floor(a), math.floor(b)) for a, b in ring})
        room = ctx.world.probe([(a, feet, b) for a, b in cells], "open", dim=snap.dim)
        ground = ctx.world.probe([(a, floor, b) for a, b in cells], "solid", dim=snap.dim)
        if all(room) and all(ground):
            for a, b in ring:
                # keep: they burst next game tick, whether or not the instance ends in this one
                self.spawn("minecraft:wind_charge", (a, y + 0.6, b), dim=snap.dim, nbt=WIND_NBT, keep=True)
            return ticks
    return 0


# ---- landing somewhere safe ------------------------------------------------------------------

# Ground that hurts or swallows: never landed on (the old land_safely preset's list).
UNSAFE_GROUND = ("minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow", "#minecraft:campfires",
                 "minecraft:sweet_berry_bush", "minecraft:pointed_dripstone")
RING = 8.0                          # the first ring round the place, in blocks; each next one twice as far
TRIES = 4                           # rings tried
SPOTS_PER_RING = 8


def _safe(ctx, d, columns) -> list[bool]:
    """Which (x, y, z) stand-at spots have solid, harmless ground and room for feet and head."""
    w = ctx.world
    grounds = [(x, y - 1, z) for x, y, z in columns]
    ok = [a and b for a, b in zip(w.probe(grounds, "solid", dim=d), w.probe(list(columns), "open", dim=d))]
    for bad in UNSAFE_GROUND:
        if not any(ok):
            break
        hits = w.probe([g for g, good in zip(grounds, ok) if good], bad, dim=d)
        it = iter(hits)
        ok = [good and not next(it) for good in ok]
    return ok


def land_safely(self, ctx, who, at, exact: bool = True, cushion_s: float = 5.0, dim: str | None = None):
    """Move `who` (a player or an entity) onto safe ground at `at`: on the spot itself (or one
    block up) when `exact` and its ground is solid and harmless with room to stand, else on the
    ground of the first safe spot in rings round it, 8, 16, 32 and 64 blocks out. They get
    `cushion_s` of Resistance V and Slow Falling first, so a bad landing does not hurt. Returns
    the spot they were sent to, or None when no ring had one (they are not moved)."""
    centre, d = where(ctx, at, dim)
    key = key_of(who)
    if not key:
        raise ValueError("land_safely needs someone to move, not %r" % (who,))
    spot = None
    x0, y0, z0 = (math.floor(c) for c in centre)
    if exact:
        tries = [(x0, y0, z0), (x0, y0 + 1, z0)]
        good = _safe(ctx, d, tries)
        spot = next((c for c, ok in zip(tries, good) if ok), None)
    ring = RING
    for _ in range(TRIES if spot is None else 0):
        columns = []
        for i in range(SPOTS_PER_RING):
            a = 2 * math.pi * i / SPOTS_PER_RING + random.uniform(0, 0.5)
            x, z = math.floor(centre[0] + ring * math.cos(a)), math.floor(centre[2] + ring * math.sin(a))
            feet = ctx.world.ground_y(x, z, dim=d)     # where feet stand on the column (McIo, measured)
            if feet is not None:
                columns.append((x, feet, z))
        good = _safe(ctx, d, columns) if columns else []
        spot = next((c for c, ok in zip(columns, good) if ok), None)
        if spot is not None:
            break
        ring *= 2
    if spot is None:
        return None
    if cushion_s > 0:
        self.potion("minecraft:resistance", cushion_s, 4, who=key, particles=False)
        self.potion("minecraft:slow_falling", cushion_s, 0, who=key, particles=False)
    target = (spot[0] + 0.5, float(spot[1]), spot[2] + 0.5)
    self.teleport(key, target, dim=d)
    return target
