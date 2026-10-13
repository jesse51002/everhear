"""Blowing things up (the kit, DESIGN2 §5 "Blasts"): `blast` (one explosion or a pattern of them,
breaking blocks or not), `lightning` (bolts round a point) and `crater` (a hole carved through the
engine's block queue).

**A blast that breaks blocks** is vanilla TNT (fuse 0, `explosion_power`), so the world breaks the
way Minecraft's own explosions break it. Its damage is vanilla's: neither counted against the
cast's damage nor credited to the user. What the spare rules spare (the user, `spare`, pets,
villagers and the rest of SPARED_TYPES within its reach) gets two seconds of Resistance V first.

**A blast that breaks nothing** is the kit's own: a flash and a boom, then damage that falls off
with distance as a vanilla explosion's does (every target in reach exposed), dealt through
self.damage (counted and credited) to what `targets` finds, and a push away from the middle. One
target at the very middle of one blast takes 14 x power + 1 (TNT, power 4: 57).
"""

from __future__ import annotations

import math
import random

from ._base import NO_BEDROCK, OVERWORLD, Vec, floor3, pos_of, where
from .movement import push
from .finders import PLAYER, SPARED_TYPES, _entities, _gather, _nearest_part, _spare_set, _target, _user_keys

LAYOUTS = ("one", "plus", "ring", "rings")
SHIELD_S = 2.0                      # Resistance V on what a breaking blast spares
MAX_FORCELOAD = 256                 # chunks a crater loads first, at most (a radius of about 120)


def blast_points(at: Vec, layout: str = "one", spread: float = 4.0) -> list[Vec]:
    """Where a pattern's blasts go: the point, then "plus" four more `spread` out, "ring" eight
    round it, "rings" six at `spread` and ten at twice that, all level with it."""
    if layout not in LAYOUTS:
        raise ValueError("layout is one of %s, not %r" % (", ".join(LAYOUTS), layout))
    s = float(spread)
    rings = {"one": [], "plus": [(s, 4, 0.0)], "ring": [(s, 8, 0.0)], "rings": [(s, 6, 0.3), (2 * s, 10, 0.1)]}
    points = [tuple(at)]
    for d, n, turn in rings[layout]:
        for k in range(n):
            a = 2 * math.pi * k / n + turn
            points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    return points


def blast_damage(power: float, distance: float) -> float:
    """What a breaking-nothing blast of `power` deals at `distance` from its middle (vanilla's
    formula, fully exposed): 0 past twice the power."""
    reach = 2.0 * float(power)
    if reach <= 0 or distance >= reach:
        return 0.0
    impact = 1.0 - distance / reach
    return (impact * impact + impact) / 2.0 * 7.0 * reach + 1.0


def blast(self, ctx, at, power: float = 4.0, layout: str = "one", spread: float = 4.0,
          breaks: bool = False, keep: float = 0.0, spare=(), dim: str | None = None) -> list[Vec]:
    """Blow up `at` with blasts of `power` (TNT is 4; 0: none) laid out by `layout` ("one",
    "plus", "ring", "rings") `spread` blocks apart. `breaks`: real TNT that breaks blocks, else a
    blast that only hurts and throws back. No blast goes off within `keep` blocks (along the
    ground) of a player. The user, `spare` and what the spare rules spare are never hurt.
    Returns the points that went off."""
    centre, d = where(ctx, at, dim)
    power = float(power)
    if power <= 0:
        return []
    points = blast_points(centre, layout, spread)
    if keep > 0:
        people = [p.pos for p in ctx.world.players.values() if p.dim == d and p.alive]
        points = [q for q in points if all(math.hypot(q[0] - p[0], q[2] - p[2]) >= keep for p in people)]
    if not points:
        return []
    if breaks:
        _shield(self, ctx, d, centre, 2 * power + max(math.dist(q, centre) for q in points), spare)
        for q in points:
            # keep: it goes off next game tick, so an instance that ends in this one must not take it away
            self.spawn("minecraft:tnt", q, dim=d, nbt="{fuse:0s,explosion_power:%gf}" % power, keep=True)
        return points
    dealt, knock = {}, {}
    reach = 2 * power
    # one read for the whole pattern (not one a blast: they overlap), then each blast's own share
    whole = max(math.dist(q, centre) for q in points) + reach
    players, entities = _gather(self, ctx, d, [(centre, whole + 2.0)], "everything", spare)
    for q in points:
        self.particle("minecraft:explosion_emitter", q, count=max(1, int(power / 2)),
                      delta=(power / 2, power / 3, power / 2), force=True, dim=d)
        self.sound("minecraft:entity.generic.explode", q, volume=max(1.0, power), pitch=0.8, dim=d)
        for snap in players + entities:
            far = _nearest_part(q, snap.pos)
            amount = blast_damage(power, far)
            if amount <= 0:
                continue
            t = _target(snap, d, far)
            dealt[t.key] = dealt.get(t.key, 0.0) + amount
            impact = 1.0 - far / reach
            if impact > knock.get(t.key, (0.0,))[0]:
                knock[t.key] = (impact, q, t)
    for key, amount in dealt.items():
        self.damage(key, amount, "minecraft:explosion")
    for impact, q, t in knock.values():
        push(self, ctx, t, "away", strength=max(1.0, 6.0 * impact), frm=q, landing="hurt")
    return points


def _shield(self, ctx, dim, centre, reach, spare):
    """Two seconds of Resistance V on what a breaking blast must not hurt: the user, the spared
    names, and pets, villagers and the rest of SPARED_TYPES within reach."""
    keep = _user_keys(ctx) | _spare_set(spare)
    for p in ctx.world.players.values():
        if p.dim == dim and p.alive and (p.name in keep or p.name.lower() in keep):
            self.potion("minecraft:resistance", SHIELD_S, 4, who=p.name, particles=False)
    user = ctx.user
    if user is not None and not getattr(user, "is_player", False):
        self.potion("minecraft:resistance", SHIELD_S, 4, who=user.key, particles=False)
    for e in _entities(ctx, dim, centre, reach):
        if e.type != PLAYER and e.health is not None and (e.owner or e.type in SPARED_TYPES or e.uuid in keep):
            self.potion("minecraft:resistance", SHIELD_S, 4, who=e.uuid, particles=False)


def lightning(self, ctx, at, count: int = 1, spread: float = 3.0, layout: str = "scatter",
              keep_away: float = 4.0, spare=(), dim: str | None = None) -> list[Vec]:
    """Strike `count` bolts: the first at `at`, the rest within `spread` of it, scattered at random
    ("scatter") or evenly round it ("ring"; 5 make a plus). None lands within `keep_away` of the
    user or of a player in `spare`. A bolt is vanilla's: its own few points of damage and its fire
    are Minecraft's (not counted); deal what the power promises with hit(targets(...)). Returns
    the points struck."""
    if layout not in ("scatter", "ring"):
        raise ValueError("layout is scatter or ring, not %r" % (layout,))
    centre, d = where(ctx, at, dim)
    n, s = max(0, int(count)), float(spread)
    points = [centre] if n else []
    for i in range(1, n):
        if layout == "ring":
            a, r = 2 * math.pi * (i - 1) / (n - 1), s
        else:
            a, r = random.uniform(0, 2 * math.pi), random.uniform(min(1.0, s), s)
        points.append((centre[0] + r * math.cos(a), centre[1], centre[2] + r * math.sin(a)))
    away = []
    if ctx.user is not None:
        p = pos_of(ctx, ctx.user)
        if p is not None:
            away.append(p)
    names = _spare_set(spare)
    away += [p.pos for p in ctx.world.players.values() if p.dim == d and (p.name in names or p.name.lower() in names)]
    struck = [q for q in points if all(math.dist(q, a) >= keep_away for a in away)]
    for q in struck:
        self.spawn("minecraft:lightning_bolt", q, dim=d, keep=True)
    return struck


def _chunks(x1, z1, x2, z2) -> int:
    return ((x2 >> 4) - (x1 >> 4) + 1) * ((z2 >> 4) - (z1 >> 4) + 1)


def crater(self, ctx, at, radius: int, leave: str = "air", tunnel: int = 0, path=(), load: bool = True,
           dim: str | None = None) -> None:
    """Carve a ball of `radius` round `at` through the engine's block queue (spread over ticks,
    never the bedrock floor nor a protected box), then leave "air", "lava" (a pool in its lowest
    third) or "fire" (burning on its floor). `path`: points along a beam, each with a cube
    `tunnel` blocks round it carved first. `load`: forceload the chunks it reaches first when they
    are few (up to 256, a radius of about 120), so the whole hole is carved; else only what is
    loaded. The class's own `done(ctx)` is called when the queue has placed it all. The queue
    carves a ball of radius 1024 at most, planned over ticks."""
    if leave not in ("air", "lava", "fire"):
        raise ValueError("leave is air, lava or fire, not %r" % (leave,))
    r = int(radius)
    if r <= 0:
        return
    centre, d = where(ctx, at, dim)
    cx, cy, cz = floor3(centre)
    q = self.blocks
    if load and _chunks(cx - r, cz - r, cx + r, cz + r) <= MAX_FORCELOAD:
        q.forceload((cx - r, cy, cz - r), (cx + r, cy, cz + r), dim=d)
    t = int(tunnel)
    for p in path if t > 0 else ():
        px, py, pz = floor3(p)
        q.fill((px - t, py - t, pz - t), (px + t, py + t, pz + t), "minecraft:air", dim=d)
    q.sphere((cx + 0.5, cy + 0.5, cz + 0.5), r, "minecraft:air", dim=d)
    if leave == "air":
        return
    lo = max(cy - r, NO_BEDROCK.get(d, NO_BEDROCK[OVERWORLD])[0])
    top = cy - r + max(1, r // 3) if leave == "lava" else cy
    for y in range(lo, top + 1):
        w = math.sqrt(max(0.0, r * r - (y - cy) ** 2))
        half = int(w / math.sqrt(2))                # the square inside this layer's circle
        q.fill((cx - half, y, cz - half), (cx + half, y, cz + half), "minecraft:" + leave,
               replace="minecraft:air", strict=leave != "fire", dim=d)
