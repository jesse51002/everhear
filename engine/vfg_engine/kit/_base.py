"""What every kit helper shares (the kit's plumbing; content imports the helpers, not this):
reading a position and its dimension from whatever content hands over, a few sums on vectors,
the kit's timers and the kit's memory.

**The kit's timers.** A helper that takes time (a tune, a countdown, a flight, a held button)
never waits: it schedules its next step with `self.after(seconds, "kit_step", name, *args)`.
The engine calls the instance's `kit_step(ctx, name, *args)`, which lands in `kit_step` here and
calls the step registered under `name` as `fn(self, ctx, *args)`. A step's args are plain data,
saved with the instance's timers, so a step in flight survives a reload and a restart like any
other timer, and goes when the instance (or, in an ability, the cast's run) ends.

**The kit's memory.** What a helper remembers between calls lives in `self.state.kit`, a dict on
every State, one entry per helper ("held", "melody", ...): plain data only (it is saved as JSON,
so no tuples come back as tuples, and no inf or nan).

Every content class has `kit_step` and every State has `kit` (vfg_engine.content): content
declares neither.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

log = logging.getLogger("vfg_engine")

Vec = tuple[float, float, float]
OVERWORLD = "minecraft:overworld"
NETHER = "minecraft:the_nether"
END = "minecraft:the_end"
PLAYER = "minecraft:player"
# The build height of each dimension (lowest y, highest y), and the part that is not its bedrock
# floor or roof: the kit never asks for blocks outside the first, and never carves outside the second.
LIMITS = {OVERWORLD: (-64, 319), NETHER: (0, 255), END: (0, 255)}
NO_BEDROCK = {OVERWORLD: (-59, 319), NETHER: (5, 122), END: (0, 255)}
_DIMS = {"overworld": OVERWORLD, "nether": NETHER, "the_nether": NETHER, "end": END, "the_end": END}


# ---- where things are -----------------------------------------------------------------------

def norm_dim(dim: str | None) -> str | None:
    """"nether" -> "minecraft:the_nether"; None stays None."""
    if dim is None:
        return None
    d = str(dim).strip().lower()
    return _DIMS.get(d.removeprefix("minecraft:"), d if ":" in d else "minecraft:" + d)


def _vec3(p) -> Vec | None:
    if isinstance(p, (tuple, list)) and len(p) == 3:
        try:
            v = (float(p[0]), float(p[1]), float(p[2]))
        except (TypeError, ValueError):
            return None
        return v if all(math.isfinite(c) for c in v) else None
    return None


def pos_of(ctx, x) -> Vec | None:
    """(x, y, z) of what content hands a helper: a position, a player's name or a uuid (looked up
    in this tick's world), or anything with a `.pos` (a being such as ctx.user, a snapshot, a
    Target, a Spot, a Look). None when it cannot be found (offline, gone)."""
    if x is None:
        return None
    v = _vec3(x)
    if v is not None:
        return v
    if isinstance(x, str):
        p = ctx.world.player(x)
        if p is not None:
            return tuple(float(c) for c in p.pos)
        e = ctx.world.get(x)
        return tuple(float(c) for c in e.pos) if e is not None else None
    try:
        pos = getattr(x, "pos", None)
        if callable(pos):
            pos = pos()
    except LookupError:          # a being that is not online
        return None
    return _vec3(pos)


def dim_of(ctx, x=None, dim: str | None = None) -> str:
    """The dimension: `dim` when given, else the one `x` is in (a player's, an entity's, a Spot's),
    else the user's (ctx.world.dim; the overworld in a scenario)."""
    if dim:
        return norm_dim(dim)
    if isinstance(x, str):
        p = ctx.world.player(x)
        if p is not None:
            return p.dim
        e = ctx.world.get(x)
        if e is not None:
            return e.dim
    elif x is not None:
        try:
            d = getattr(x, "dim", None)
        except LookupError:
            d = None
        if isinstance(d, str) and d:
            return norm_dim(d)
    return getattr(ctx.world, "dim", None) or OVERWORLD


def where(ctx, x, dim: str | None = None) -> tuple[Vec, str]:
    """(position, dimension) of `x`; ValueError in words when it cannot be found."""
    p = pos_of(ctx, x)
    if p is None:
        raise ValueError("%r is not a place, nor a player or an entity in this tick's world" % (x,))
    return p, dim_of(ctx, x, dim)


def key_of(x) -> str | None:
    """What the engine's verbs name someone by: a player's name, else an entity's uuid."""
    if x is None:
        return None
    if isinstance(x, str):
        return x
    key = getattr(x, "key", None)                # a Target, a being
    if isinstance(key, str) and key:
        return key
    if getattr(x, "type", None) == PLAYER and getattr(x, "name", None):
        return x.name
    if hasattr(x, "uuid") and hasattr(x, "type"):    # an EntitySnap
        return x.uuid
    name = getattr(x, "name", None)              # a PlayerSnap
    return name if isinstance(name, str) else None


def limits(dim: str) -> tuple[int, int]:
    return LIMITS.get(dim, LIMITS[OVERWORLD])


# ---- sums on vectors ------------------------------------------------------------------------

def add(a, b) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def length(a) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def unit(a, fallback: Vec = (0.0, 0.0, 1.0)) -> Vec:
    n = length(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-9 else fallback


def lerp(a, b, k: float) -> Vec:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def floor3(p) -> tuple[int, int, int]:
    return (math.floor(p[0]), math.floor(p[1]), math.floor(p[2]))


def direction(yaw: float, pitch: float = 0.0) -> Vec:
    """The unit vector a yaw and pitch (degrees, as Minecraft gives them) point along."""
    y, p = math.radians(yaw), math.radians(pitch)
    return (-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def yaw_to(frm, to) -> tuple[float, float]:
    """(yaw, pitch) that looks from one position to another: turn a spawned figure to face a
    player with `self.teleport(uuid, at, yaw=yaw)`, or summon it with `Rotation:[yawf,pitchf]`."""
    d = sub(to, frm)
    yaw = math.degrees(math.atan2(-d[0], d[2]))
    pitch = math.degrees(-math.atan2(d[1], math.hypot(d[0], d[2])))
    return yaw, pitch


def flat_facing(x) -> Vec:
    """The way a being faces along the ground, a unit vector (south when it has no facing)."""
    yaw = getattr(x, "yaw", None)
    if isinstance(yaw, (int, float)):
        return direction(yaw, 0.0)
    try:
        f = getattr(x, "facing", None)
    except LookupError:
        f = None
    if f is not None and math.hypot(f[0], f[2]) > 1e-6:
        n = math.hypot(f[0], f[2])
        return (f[0] / n, 0.0, f[2] / n)
    return (0.0, 0.0, 1.0)


def ahead(ctx, who, blocks: float, up: float = 0.0, right: float = 0.0) -> Vec | None:
    """The point `blocks` ahead of where `who` stands, along the way they face on the ground,
    `up` higher and `right` to their right; behind them with a negative `blocks`. None when they
    cannot be found. ahead(ctx, p, -12): twelve blocks behind p, level with their feet."""
    feet = pos_of(ctx, who)
    if feet is None:
        return None
    if isinstance(who, str):
        who = ctx.world.player(who) or who
    f = flat_facing(who)
    r = (-f[2], 0.0, f[0])                        # to the right of facing f, on the ground
    return (feet[0] + f[0] * blocks + r[0] * right, feet[1] + up, feet[2] + f[2] * blocks + r[2] * right)


# ---- the kit's timers ----------------------------------------------------------------------

STEPS: dict[str, Callable[..., Any]] = {}


def step(name: str):
    """Register a kit step: `fn(self, ctx, *args)`, called by kit_step(ctx, name, *args)."""
    def register(fn):
        STEPS[name] = fn
        return fn
    return register


def kit_step(self, ctx, name, *args):
    """Where the kit's timers land: the step registered under `name`. A step this kit no longer
    has (a timer saved before an upgrade of the engine) is dropped with a log line."""
    fn = STEPS.get(name)
    if fn is None:
        log.warning("%s: the kit has no step %r (an older engine's timer); dropped", self.instance, name)
        return None
    return fn(self, ctx, *args)


def later(self, seconds: float, name: str, *args) -> str:
    """Schedule the kit step `name` in `seconds`; its timer's id."""
    return self.after(max(0.0, float(seconds)), "kit_step", name, *args)


def memory(self, helper: str) -> dict:
    """The kit's memory for one helper, in self.state.kit[helper] (created empty)."""
    kit = self.state.kit
    got = kit.get(helper)
    if not isinstance(got, dict):
        got = kit[helper] = {}
    return got


def call_back(self, ctx, method: str | None, *args):
    """Call the content's own method `method(ctx, *args)` by name, when one is named; an error in
    words when the class has no such method."""
    if not method:
        return None
    fn = getattr(self, method, None)
    if not callable(fn):
        raise AttributeError("%s has no method %r to call back" % (type(self).__name__, method))
    return fn(ctx, *args)


def own_spawns(self) -> frozenset:
    """What this instance (in a cast, its run) spawned and still exists: the kit's target finders
    never pick it."""
    return frozenset(self.spawned)


def plain(x):
    """A value as plain data for a timer's args or the kit's memory: tuples to lists, deep."""
    if isinstance(x, (tuple, list)):
        return [plain(v) for v in x]
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    return x
