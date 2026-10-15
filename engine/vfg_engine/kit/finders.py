"""Who a power reaches, and hurting them once each (the kit, DESIGN2 §5 "Targets"): everything
round a point (`targets`), along a line (`near_line`), along where the user looks (`hitscan`),
and `hit`, which deals damage to what they found.

**The spare rules**, the same for all three finders (the old kit's `exclude`): never the user,
never a name or uuid in `spare`, never a tamed pet (an entity with an owner), never a villager, a
trader, a golem, an allay, an armor stand, a mannequin, a dropped item or an experience orb,
never anything not alive (a display, a marker, a boat), never what this instance spawned, never
a dead player or one in creative or spectator.

`what` picks among the rest: "everything" (mobs and players; abilities hurt players by default:
PvP is on), "mobs" (no players), "hostile" (monsters only: never an animal, a fish or a bat),
"players", or "projectiles" (arrows, tridents, fireballs and the like still in flight, never the
user's own).

**Where they come from.** Players are this tick's snapshot (any distance). Entities come from the
mirror (in memory, at most 0.1 s old) when the whole reach is within the mirror's radius
(`ctx.world.mirror_radius`) of a player in that dimension, else from `ctx.world.entities`, a live
read counted against the call's 50 ms; a long line is read in slices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..io import PlayerSnap
from ._base import PLAYER, Vec, add, dim_of, key_of, mul, own_spawns, sub, unit, where
from ._base import length as vlen

# Living types a hit spares.
SPARED_TYPES = frozenset("minecraft:" + t for t in (
    "villager", "wandering_trader", "iron_golem", "snow_golem", "copper_golem", "allay", "armor_stand",
    "mannequin", "item", "experience_orb"))
# Every living type 26.3 does not count as a monster (its animals, fish, bats and the like), from the
# 26.3 server jar's mob categories: "hostile" leaves these out too.
PEACEFUL_TYPES = frozenset("minecraft:" + t for t in (
    "armadillo", "axolotl", "bat", "bee", "camel", "cat", "chicken", "cod", "cow", "dolphin", "donkey",
    "fox", "frog", "glow_squid", "goat", "happy_ghast", "horse", "llama", "mooshroom", "mule", "nautilus",
    "ocelot", "panda", "parrot", "pig", "polar_bear", "pufferfish", "rabbit", "salmon", "sheep",
    "skeleton_horse", "sniffer", "squid", "strider", "tadpole", "trader_llama", "tropical_fish", "turtle",
    "wolf"))
# The monsters, for reference (what "hostile" keeps).
HOSTILE_TYPES = frozenset("minecraft:" + t for t in (
    "blaze", "bogged", "breeze", "camel_husk", "cave_spider", "creaking", "creeper", "drowned",
    "elder_guardian", "ender_dragon", "enderman", "endermite", "evoker", "ghast", "giant", "guardian",
    "hoglin", "husk", "illusioner", "magma_cube", "parched", "phantom", "piglin", "piglin_brute", "pillager",
    "ravager", "shulker", "silverfish", "skeleton", "slime", "spider", "stray", "sulfur_cube", "vex",
    "vindicator", "warden", "witch", "wither", "wither_skeleton", "zoglin", "zombie", "zombie_horse",
    "zombie_nautilus", "zombie_villager", "zombified_piglin"))
# What "projectiles" finds: things thrown or shot, while they fly.
PROJECTILE_TYPES = frozenset("minecraft:" + t for t in (
    "arrow", "spectral_arrow", "trident", "fireball", "small_fireball", "dragon_fireball", "wither_skull",
    "wind_charge", "breeze_wind_charge", "shulker_bullet", "llama_spit", "snowball", "egg", "ender_pearl",
    "splash_potion", "lingering_potion", "experience_bottle", "firework_rocket"))
WHAT = ("everything", "mobs", "hostile", "players", "projectiles")

SLICE = 32.0                       # a long line's entities are read this many blocks at a time
BODY = (0.2, 0.9, 1.6)             # where a body is tested, above its feet (a player, a zombie)
HEAD = 1.7                         # its head, above its feet
HEAD_WIDTH = 0.5                   # a line this close to the head is a headshot
KILL = 1_000_000.0                 # what `kill=True` deals, as minecraft:generic_kill
LIVE_PLAYER_MODES = ("survival", "adventure")


@dataclass(frozen=True)
class Target:
    """One being a finder found, nearest first."""
    key: str                       # a player's name or an entity's uuid: what self.damage(...) names
    type: str                      # "minecraft:player", "minecraft:zombie"
    dim: str
    pos: Vec                       # its feet
    t: float = 0.0                 # how far: from the point (targets), along the line (near_line, hitscan)
    on_head: bool = False          # the line passes within half a block of its head
    health: float | None = None

    @property
    def is_player(self) -> bool:
        return self.type == PLAYER

    @property
    def middle(self) -> Vec:
        return (self.pos[0], self.pos[1] + 0.9, self.pos[2])


@dataclass(frozen=True)
class Shot:
    """What a hitscan found: the beam from `start` to `end` (where it stopped: the first block
    when it does not go through walls, else its full length) and the targets along it."""
    start: Vec
    end: Vec
    direction: Vec
    length: float
    targets: list[Target]
    block: tuple[int, int, int] | None = None     # the block it stopped at, if it did


# ---- gathering -----------------------------------------------------------------------------

def _user_keys(ctx) -> set[str]:
    u = ctx.user
    if u is None:
        return set()
    keys = {getattr(u, "key", None), getattr(u, "name", None), getattr(u, "uuid", None)}
    return {k for k in keys if isinstance(k, str) and k}


def _spare_set(spare) -> set[str]:
    if isinstance(spare, str):
        spare = [s for s in spare.replace(",", " ").split()]
    out = set()
    for s in spare or ():
        k = key_of(s)
        if k:
            out.add(k)
            out.add(k.lower())
    return out


def _check_what(what: str):
    if what not in WHAT:
        raise ValueError("what is one of %s, not %r" % (", ".join(WHAT), what))


def _player_ok(p, dim, user, spare) -> bool:
    return (p.dim == dim and p.alive and p.gamemode in LIVE_PLAYER_MODES and p.name not in user
            and p.name not in spare and p.name.lower() not in spare)


def _entity_ok(e, what, user, spare, own) -> bool:
    if e.type == PLAYER or e.uuid in own or e.uuid in spare or e.uuid in user:
        return False
    if e.name and (e.name in spare or e.name.lower() in spare):
        return False
    if what == "projectiles":
        return e.type in PROJECTILE_TYPES and not e.in_ground and not (e.owner and e.owner in user)
    if e.health is None or e.type in SPARED_TYPES or e.owner:
        return False
    return not (what == "hostile" and e.type in PEACEFUL_TYPES)


def _in_mirror(ctx, dim, centre, reach) -> bool:
    radius = ctx.world.mirror_radius
    for p in ctx.world.players.values():
        if p.dim == dim and math.dist(p.pos, centre) + reach <= radius:
            return True
    return False


def _entities(ctx, dim, centre, reach):
    """Every entity within `reach` of a point: the mirror's when it covers it, else live."""
    if _in_mirror(ctx, dim, centre, reach):
        return ctx.world.entities_near(centre, reach, dim=dim)
    return ctx.world.entities(centre, reach, dim=dim)


def _gather(self, ctx, dim, spheres, what, spare):
    """(players, entities) a hit may touch within any of the spheres [(centre, reach)]."""
    user, spare_keys, own = _user_keys(ctx), _spare_set(spare), own_spawns(self)
    players, entities = [], {}
    if what in ("everything", "players"):
        players = [p for p in ctx.world.players.values() if _player_ok(p, dim, user, spare_keys)
                   and any(math.dist(p.pos, c) <= r for c, r in spheres)]
    if what != "players":
        for centre, reach in spheres:
            for e in _entities(ctx, dim, centre, reach):
                if e.uuid not in entities and _entity_ok(e, what, user, spare_keys, own):
                    entities[e.uuid] = e
    return players, list(entities.values())


def _target(snap, dim, t, on_head=False) -> Target:
    if isinstance(snap, PlayerSnap):
        return Target(snap.name, PLAYER, dim, tuple(snap.pos), t, on_head, snap.health)
    return Target(snap.uuid, snap.type, dim, tuple(snap.pos), t, on_head, snap.health)


def _nearest_part(centre, feet, heights=BODY) -> float:
    return min(math.dist(centre, (feet[0], feet[1] + h, feet[2])) for h in heights)


def _finish(found, most) -> list[Target]:
    found.sort(key=lambda t: t.t)
    return found[:most] if most else found


# ---- the finders ---------------------------------------------------------------------------

def targets(self, ctx, at, radius: float, what: str = "everything", spare=(), most: int = 0,
            dim: str | None = None) -> list[Target]:
    """Everything a hit may touch within `radius` of `at` (a position, a player, an entity, a
    Spot), nearest first (`t` is the distance from `at` to the nearest part of the body), by the
    spare rules. `what`: "everything", "mobs", "hostile", "players" or "projectiles". `spare`:
    names or uuids it never picks. `most`: only the nearest that many (0: all)."""
    _check_what(what)
    centre, d = where(ctx, at, dim)
    radius = float(radius)
    players, entities = _gather(self, ctx, d, [(centre, radius + 2.0)], what, spare)
    found = []
    for snap in players + entities:
        heights = (0.0,) if what == "projectiles" else BODY
        t = _nearest_part(centre, snap.pos, heights)
        if t <= radius:
            found.append(_target(snap, d, t))
    return _finish(found, most)


def near_line(self, ctx, a, b, width: float = 1.5, what: str = "everything", spare=(), most: int = 0,
              dim: str | None = None) -> list[Target]:
    """Everything a hit may touch whose body comes within `width` of the line from `a` to `b`,
    ordered by how far along it (`t`, from `a`), by the spare rules. `on_head` is set on each
    whose head the line passes within half a block of."""
    _check_what(what)
    start, d = where(ctx, a, dim)
    end = where(ctx, b, d)[0]
    width = float(width)
    span = sub(end, start)
    L = vlen(span)
    u = unit(span)
    n = max(1, int(math.ceil(L / SLICE)))
    spheres = [(add(start, mul(u, L * (i + 0.5) / n)), L / n / 2.0 + width + 2.0) for i in range(n)]
    players, entities = _gather(self, ctx, d, spheres, what, spare)
    found = []
    for snap in players + entities:
        best = None
        heights = (0.0,) if what == "projectiles" else BODY
        for h in heights:
            v = sub((snap.pos[0], snap.pos[1] + h, snap.pos[2]), start)
            t = v[0] * u[0] + v[1] * u[1] + v[2] * u[2]
            if t < -width or t > L + width:
                continue
            if vlen(sub(v, mul(u, t))) <= width:
                best = t if best is None else min(best, t)
        if best is None:
            continue
        hv = sub((snap.pos[0], snap.pos[1] + HEAD, snap.pos[2]), start)
        ht = hv[0] * u[0] + hv[1] * u[1] + hv[2] * u[2]
        on_head = what != "projectiles" and ht > 0 and vlen(sub(hv, mul(u, ht))) <= HEAD_WIDTH
        found.append(_target(snap, d, max(0.0, best), on_head))
    return _finish(found, most)


def hitscan(self, ctx, length: float = 100.0, width: float = 1.5, what: str = "everything",
            through_walls: bool = False, spare=(), most: int = 0) -> Shot:
    """A shot from the user's eyes along where they look: up to `length` blocks, stopping at the
    first solid block unless `through_walls`, never at a being. Its `targets` are what
    near_line finds within `width` of it, nearest first, each with `t` and `on_head`; when it
    stopped at a block, none beyond that block, however wide the shot."""
    user = ctx.user
    if user is None:
        raise ValueError("hitscan shoots from the user's eyes: a scenario has none (use near_line)")
    start = tuple(float(c) for c in user.eye)
    u = unit(tuple(float(c) for c in user.facing))
    d = dim_of(ctx, user)
    length = float(length)
    block = None
    if not through_walls:
        # a live read: it stops at the first solid block, never at a being
        look = ctx.world.ray(start, u, length)
        if look.kind == "block":
            length = float(look.distance)
            block = tuple(look.block) if look.block is not None else None
    end = add(start, mul(u, length))
    found = near_line(self, ctx, start, end, width, what, spare, 0 if block is not None else most, d)
    if block is not None:
        # stopped at a wall: nothing beyond it, however wide the shot (near_line's width reaches
        # round the line's end, and so round the wall's edge)
        found = [x for x in found if x.t <= length]
        found = found[:most] if most else found
    return Shot(start, end, u, length, found, block)


# ---- hurting them ---------------------------------------------------------------------------

def damage_type_name(name: str) -> str:
    name = str(name).strip().lower()
    return name if ":" in name else "minecraft:" + name


def hit(self, ctx, targets, amount: float, damage_type: str = "minecraft:generic", kill: bool = False,
        most: int = 0, head: float = 1.0) -> list:
    """Hurt each target once (a Target, a name, a uuid, a snapshot; one listed twice is hurt
    once), nearest first, by `amount` of `damage_type` through self.damage, so it is the user's
    hit and counted against the cast's damage. `kill`: kill outright instead. `most`: only the
    first that many. `head`: times the damage for a target whose head the line passed (a
    Target's on_head). Returns those it hurt."""
    kind = damage_type_name(damage_type)
    ordered = sorted(targets, key=lambda t: getattr(t, "t", 0.0))
    seen, out = set(), []
    for t in ordered:
        key = key_of(t)
        if not key or key in seen:
            continue
        seen.add(key)
        if most and len(out) >= most:
            break
        if kill:
            self.damage(key, KILL, "minecraft:generic_kill")
        else:
            self.damage(key, float(amount) * (float(head) if getattr(t, "on_head", False) else 1.0), kind)
        out.append(t)
    return out
