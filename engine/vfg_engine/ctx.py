"""`ctx`: what a handler reads (the game engine spec, "Two things every handler has", and
"Reading the world without waiting").

    ctx.user      who is using it: an item's holder, an ability's caster, a mark's bearer, an
                  actor's own body (a Player or an ActorBeing); None in a scenario
    ctx.source    who caused it (the being that applied a mark; else the user)
    ctx.world     this tick's snapshot and the mirror: players, entities_near, players_near, cone
    ctx.now       the engine's time, in seconds
    ctx.running   in an ability's cast: the other runs of it still going (each can .end())
    ctx.places    the engine's saved places: get(name), all()
    ctx.query(c)  one command now, and its reply; counted against the call's time limit

Everything but `query` and the live reads of ctx.world (probe, ground_y, ray, entities, loaded,
mspt) is already in memory; those ask Minecraft now, and their time counts against the call.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .io import EntitySnap, PlayerSnap, Vec

PLAYER = "minecraft:player"
MIRROR_RADIUS = 32.0          # the mirror's reach when the Io does not say (McMirror's default)

if TYPE_CHECKING:
    from .runtime import Runtime


def _center(t) -> Vec:
    x, y, z = t.pos
    return (x, y + 0.9, z)


class World:
    """This tick's world, read from memory: the snapshot (players, tracked entities) and the
    mirror (entities around players, at most about 0.1 s old). Positions default to `dim`, the
    user's dimension."""

    def __init__(self, rt: "Runtime", dim: str = "minecraft:overworld", exclude: str | None = None):
        self._rt = rt
        self.dim = dim
        self._exclude = exclude

    @property
    def game_tick(self) -> int:
        return self._rt.snapshot.game_tick

    @property
    def mirror_radius(self) -> float:
        """How far round each player entities_near and get can see (the mirror's reach); past it,
        read live with entities()."""
        return float(getattr(self._rt.io_mirror(), "radius", MIRROR_RADIUS))

    @property
    def players(self) -> dict[str, PlayerSnap]:
        return dict(self._rt.snapshot.players)

    def player(self, name: str) -> PlayerSnap | None:
        return self._rt.snapshot.players.get(name)

    def get(self, uuid: str) -> EntitySnap | None:
        return self._rt.snapshot.tracked.get(uuid) or self._rt.io_mirror().get(uuid)

    def entities_near(self, pos, radius: float, dim: str | None = None) -> list[EntitySnap]:
        """Entities (not players: those are `players_near`) within `radius` of `pos`, from the
        mirror, which lists players too."""
        pos = _vec(pos)
        found = {e.uuid: e for e in self._rt.io_mirror().entities_near(dim or self.dim, pos, radius)
                 if e.type != PLAYER}
        for e in self._rt.snapshot.tracked.values():   # tracked ones are this tick's, so fresher
            if e.dim == (dim or self.dim) and math.dist(e.pos, pos) <= radius:
                found[e.uuid] = e
        return list(found.values())

    # ---- live reads: each asks Minecraft now, counted against the call's 50 ms -------------

    def probe(self, points, test: str, dim: str | None = None) -> list[bool]:
        """Many blocks in one read: for each (x, y, z), whether it is `test`: a block id, a #tag,
        "solid", or "open" (it and the block above are air)."""
        pts = [tuple(int(math.floor(c)) for c in p) for p in points]
        return self._rt.read("probe", lambda io: list(io.probe(dim or self.dim, pts, test)))

    def ground_y(self, x, z, dim: str | None = None) -> int | None:
        """The y where feet stand on column x, z: one above the highest solid block (26.3's
        motion_blocking_no_leaves heightmap: fluids count, leaves do not); None when the chunk
        is not loaded."""
        return self._rt.read("ground_y", lambda io: io.ground_y(dim or self.dim, int(math.floor(x)),
                                                                int(math.floor(z))))

    def ray(self, origin, direction, length: float, through_blocks: bool = False,
            dim: str | None = None):
        """A ray from any point, any length; it stops at the first solid block (unless
        through_blocks), never at an entity. A Look: kind "block" or "none", pos, distance."""
        o, d = _vec(origin), _vec(direction)
        return self._rt.read("ray", lambda io: io.ray(dim or self.dim, o, d, float(length), bool(through_blocks)))

    def entities(self, center, radius: float, types=None, dim: str | None = None) -> list[EntitySnap]:
        """Living entities round a point at any distance, in loaded chunks (not players: those are
        `players_near`); `types` a list of entity types."""
        c = _vec(center)
        types = [t if ":" in t else "minecraft:" + t for t in types] if types else None
        found = self._rt.read("entities", lambda io: list(io.entities(dim or self.dim, c, float(radius), types)))
        return [e for e in found if e.type != PLAYER]

    def loaded(self, x, z, dim: str | None = None) -> bool:
        return self._rt.read("loaded", lambda io: bool(io.loaded(dim or self.dim, int(math.floor(x)),
                                                                 int(math.floor(z)))))

    @property
    def mspt(self) -> float:
        """The server's milliseconds per tick (50 is lag), read at most every half second."""
        return self._rt.mspt()

    def players_near(self, pos, radius: float, dim: str | None = None) -> list[PlayerSnap]:
        pos = _vec(pos)
        return [p for p in self._rt.snapshot.players.values()
                if p.alive and p.dim == (dim or self.dim) and math.dist(p.pos, pos) <= radius]

    def cone(self, origin, direction, angle: float, reach: float, dim: str | None = None,
             include_user: bool = False) -> list[PlayerSnap | EntitySnap]:
        """Players and living entities within `reach` of `origin` and `angle` degrees of
        `direction` (the user left out unless include_user)."""
        origin, d = _vec(origin), _vec(direction)
        n = math.sqrt(sum(c * c for c in d)) or 1.0
        d = (d[0] / n, d[1] / n, d[2] / n)
        limit = math.cos(math.radians(angle))
        out = []
        candidates = list(self.players_near(origin, reach + 2, dim)) + [
            e for e in self.entities_near(origin, reach + 2, dim) if e.health is not None]
        for t in candidates:
            key = t.name if isinstance(t, PlayerSnap) else t.uuid
            if key == self._exclude and not include_user:
                continue
            c = _center(t)
            v = (c[0] - origin[0], c[1] - origin[1], c[2] - origin[2])
            dist = math.sqrt(sum(x * x for x in v))
            if dist > reach:
                continue
            if dist < 1e-6 or (v[0] * d[0] + v[1] * d[1] + v[2] * d[2]) / dist >= limit:
                out.append(t)
        return out


class RunView:
    """Another run of the same ability, as `ctx.running` lists it."""

    def __init__(self, rt, record, run):
        self._rt, self._record, self._run = rt, record, run
        self.id = run.n
        self.started = run.started
        self.ends = run.ends
        self.charging = not run.fired

    def end(self):
        """End that run once this call returns."""
        self._rt.later(lambda: self._rt.end_run(self._record, self._run.n, "ended by a new cast"))


class Places:
    """ctx.places: the engine's saved places in this world, each {dim, pos, yaw, by, at}."""

    def __init__(self, rt):
        self._rt = rt

    def get(self, name):
        return self._rt.places.get(name)

    def all(self) -> dict:
        return self._rt.places.all()


class Ctx:
    def __init__(self, rt: "Runtime", user=None, source=None, running=()):
        self._rt = rt
        self.places = Places(rt)
        self.user = user
        self.source = source if source is not None else user
        self.now = rt.now
        self.running = list(running)
        dim = "minecraft:overworld"
        if user is not None:
            try:
                dim = user.dim
            except LookupError:
                pass
        self.world = World(rt, dim, exclude=user.key if user is not None else None)

    def query(self, command: str) -> str:
        """One command sent now, and Minecraft's reply. It counts against this call's limit."""
        return self._rt.query(command)


def _vec(p) -> Vec:
    if hasattr(p, "pos") and not isinstance(p, (tuple, list)):
        p = p.pos
    x, y, z = p
    return (float(x), float(y), float(z))


# ---- the probe a config formula is checked with (reload checks, `meta`) ---------------------

class ProbeUser:
    """A stand-in user at a given power, for working out damage(ctx) without a world."""
    is_player = True
    key = name = target = "probe"
    dim = "minecraft:overworld"
    pos = eye = (0.0, 64.0, 0.0)
    facing = (0.0, 0.0, 1.0)
    health = 20.0
    alive = True

    def __init__(self, power: float):
        self._power = power

    def power(self):
        return self._power

    def looking_at(self):
        return (0.0, 64.0, 5.0)

    def has(self, ability):
        return False

    def cast(self, ability):
        raise RuntimeError("a config formula cannot cast")


class ProbeCtx:
    def __init__(self, power: float):
        self.user = self.source = ProbeUser(power)
        self.now = 0.0
        self.running = []
        self.world = None

    def query(self, command):
        raise RuntimeError("a config formula cannot query Minecraft")
