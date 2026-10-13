"""The five kinds of class content writes (the game engine spec, "What agents write"): Item,
Ability, Mark, Actor, Scenario.

An instance is made by the engine, never by content: one per holder (an item), per grant (an
ability), per bearer (a mark), per spawned entity (an actor) and per start (a scenario). It keeps
nothing on itself. What it remembers is `self.state`, a pydantic model of plain data (the class's
`State`); what it reads is `ctx`; what it does goes through the methods below, and whatever they
made (modifiers, listens, timers, spawns, holds) the engine removes when the instance ends, or, in
an ability, when the cast's run ends. The engine keeps only names and data (`(instance,
"on_hit")`, never a function), so a class can be swapped while it runs.

Every method here hands its work to the engine (`self._h`, a vfg_engine.handle.Handle); the
engine decides when anything reaches Minecraft.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from .config import AbilityConfig, ActorConfig, BaseConfig, ItemConfig, MarkConfig, ScenarioConfig


class State(BaseModel):
    """An instance's memory: plain data only (numbers, text, lists, dicts of them), saved with the
    engine's state and given back after a reload or a restart. A field added later takes its
    default; a state that no longer fits a new class stops that instance."""

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


def snake(name: str) -> str:
    """FireStaff -> fire_staff, Fireball -> fireball, IceQueenBoss -> ice_queen_boss."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


class ThingMeta(type):
    """Keeps a class's `every = 0.5` (its tick rate) as `tick_every`, so `self.every(...)` stays a
    method, and gives every class an `id` (its name in snake_case unless it sets one)."""

    def __new__(mcs, name, bases, namespace, **kwargs):
        if "every" in namespace and not callable(namespace["every"]):
            namespace["tick_every"] = namespace.pop("every")
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if "id" not in namespace:
            cls.id = snake(name)
        return cls


class Thing(metaclass=ThingMeta):
    kind: ClassVar[str] = ""
    id: ClassVar[str] = ""
    tick_every: ClassVar[float | None] = None    # set by `every = <seconds>`: tick(ctx) that often
    Config: ClassVar[type[BaseConfig]] = BaseConfig
    State: ClassVar[type[State]] = State

    _h: Any   # the engine's handle; set by the engine only

    # ---- what the instance is -------------------------------------------------------------

    def __setattr__(self, name, value):
        raise AttributeError(
            "%s.%s: an instance keeps nothing on self; put it in self.state (declare the field "
            "in %s.State)" % (type(self).__name__, name, type(self).__name__))

    @property
    def config(self):
        """The class's Config, the same for everyone."""
        return self._h.config

    @property
    def state(self):
        """This instance's State (in an ability, during a cast: that cast's run's own State)."""
        return self._h.state

    @property
    def instance(self) -> str:
        """This instance's id, e.g. "ability:fireball#12"."""
        return self._h.iid

    # ---- subscriptions and timers: stored by name -----------------------------------------

    def listen(self, event, method: str, about: str = "user"):
        """Call `method(ctx, ev)` on each `event` (a class such as Hit, or its kind). `about`:
        "user" (default) for events about this instance's user (for an item, its holder, and for
        events naming an item, this item's), "anyone" for every one. Scenarios hear everything."""
        self._h.listen(event, method, about)

    def unlisten(self, event, method: str | None = None):
        self._h.unlisten(event, method)

    def every(self, seconds: float, method: str) -> str:
        """Call `method(ctx)` every `seconds` while this lives; returns the timer's id."""
        return self._h.timer(seconds, method, (), repeat=True)

    def after(self, seconds: float, method: str, *args) -> str:
        """Call `method(ctx, *args)` once, `seconds` from now (args: plain data); returns its id."""
        return self._h.timer(seconds, method, args, repeat=False)

    def cancel(self, timer_id: str):
        self._h.cancel(timer_id)

    def end(self):
        """End this instance (in an ability, during a cast: end that cast's run) once this call returns."""
        self._h.end()

    # ---- what it does ---------------------------------------------------------------------

    def modifier(self, attribute: str, amount: float | None, operation: str = "add_value"):
        """Set this instance's modifier on its user's `attribute` ("movement_speed"), by its own
        everhear: id; None takes it off. Removed when this ends."""
        self._h.modifier(attribute, amount, operation)

    def potion(self, effect: str, seconds: float, amplifier: int = 0, who=None, particles: bool = True):
        """Give a potion and forget it: Minecraft owns its stacking, expiry, milk and death. The
        engine never clears one."""
        self._h.potion(effect, seconds, amplifier, who, particles)

    def keep_potion(self, effect: str, amplifier: int = 0, particles: bool = False):
        """As a beacon does: a ~3 s potion given again every ~2 s while this lives."""
        self._h.keep_potion(effect, amplifier, particles)

    def damage(self, target, amount: float, damage_type: str = "minecraft:generic"):
        """Deal damage through the engine, counted against this cast's declared damage."""
        self._h.damage(target, amount, damage_type)

    def explode(self, at, radius: float, damage: float) -> list[str]:
        """An explosion's damage, sound and particles at `at`: `damage` to every living thing in
        `radius`, the user too unless the ability's self_damage is off. No blocks break. Returns
        whom it hit (names and uuids)."""
        return self._h.explode(at, radius, damage)

    def spawn(self, what: str, at=None, dim: str | None = None) -> str:
        """Spawn an Actor class by id, or a vanilla entity ("minecraft:zombie"); returns its uuid.
        It is removed when this ends."""
        return self._h.spawn(what, at, dim)

    def apply(self, who, mark: str, seconds: float | None = None) -> str | None:
        """Apply a mark to `who`; returns the mark's instance id. A scenario owns the marks it applies."""
        return self._h.apply(who, mark, seconds)

    def grant(self, who, ability: str):
        """Grant an ability to a player or an actor for as long as this lives."""
        self._h.grant(who, ability)

    def hold(self, setting: str, value, priority: int = 0):
        """Hold a world setting (a gamerule; "time"; "weather") at `value` while this lives."""
        self._h.hold(setting, value, priority)

    def release(self, setting: str):
        self._h.release(setting)

    def watch(self, area: str, center=None, radius: float | None = None, box=None,
              dim: str = "minecraft:overworld"):
        """Watch an area (a sphere or a box): Enter and Exit events for each player crossing it."""
        self._h.watch(area, center, radius, box, dim)

    def command(self, text: str):
        """A raw command, the escape hatch for what the methods above do not do."""
        self._h.command(text)

    def particle(self, name: str, at, count: int = 1, delta=(0.0, 0.0, 0.0), speed: float = 0.0):
        self._h.particle(name, at, count, delta, speed)

    def sound(self, name: str, at=None, volume: float = 1.0, pitch: float = 1.0):
        self._h.sound(name, at, volume, pitch)

    def message(self, text: str, to=None, where: str = "chat"):
        """To the user (when a player) unless `to` names players; `to=()` is everyone."""
        self._h.message(text, to, where)


class Item(Thing):
    """One class per item in the item book; one instance per holder, made when they hold it
    where `active_in` says and ended when they let go."""
    kind = "item"
    Config = ItemConfig


class Ability(Thing):
    """Granted to a player or an actor; one instance per grant. Passive (start/every/listen from
    the grant) or triggered: `on_cast(ctx, ev)`, each cast its own run, ended at duration_s."""
    kind = "ability"
    Config = AbilityConfig


class Mark(Thing):
    """Timed by the engine on its bearer, with its own modifier ids."""
    kind = "mark"
    Config = MarkConfig

    @property
    def stack(self) -> int | None:
        """Which of a stacking mark's copies this is (1, 2, ...); None for one that refreshes."""
        return self._h.record.stack


class Actor(Thing):
    """A custom mob or boss: one instance per spawned entity; `ctx.user` is its body."""
    kind = "actor"
    Config = ActorConfig


class Scenario(Thing):
    """World-level: a horde, a storm, a boss fight, a zone. Owns what it spawns, the marks it
    applies and the settings it holds."""
    kind = "scenario"
    Config = ScenarioConfig

    @property
    def args(self) -> dict:
        """What the `start` op was given."""
        return self._h.record.args


KINDS: dict[str, type[Thing]] = {cls.kind: cls for cls in (Item, Ability, Mark, Actor, Scenario)}


def ticks_every(cls) -> float | None:
    """How often the engine calls the class's tick(ctx): its `every`, when it has a tick()."""
    return cls.tick_every if cls.tick_every and callable(getattr(cls, "tick", None)) else None
