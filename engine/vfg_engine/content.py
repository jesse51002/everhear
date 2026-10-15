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

from .args import Args
from .config import AbilityConfig, ActorConfig, BaseConfig, ItemConfig, MarkConfig, ScenarioConfig


class State(BaseModel):
    """An instance's memory: plain data only (numbers, text, lists, dicts of them), saved with the
    engine's state and given back after a reload or a restart. A field added later takes its
    default; a state that no longer fits a new class stops that instance."""

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    kit: dict = {}      # what the kit's helpers remember between calls (vfg_engine.kit), plain data


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

    def listen(self, event, method: str, about: str = "user", blocks=None):
        """Call `method(ctx, ev)` on each `event` (a class such as Hit, or its kind). `about`:
        "user" (default) for events about this instance's user (for an item, its holder, and for
        events naming an item, this item's), "anyone" for every one. Scenarios hear everything.
        A Mine listen names the blocks it watches: blocks=["minecraft:diamond_ore"]."""
        self._h.listen(event, method, about, blocks)

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

    def kit_step(self, ctx, name: str, *args):
        """Where the kit's timers land (self.after(s, "kit_step", name, ...)); content does not
        call it."""
        from .kit import kit_step
        kit_step(self, ctx, name, *args)

    # ---- what it does ---------------------------------------------------------------------

    def modifier(self, attribute: str, amount: float | None, operation: str = "add_value", on=None):
        """Set this instance's modifier on its user's `attribute` ("movement_speed"), or `on`
        another entity (a uuid: a world mob), by its own everhear: id; None takes it off.
        Removed when this ends."""
        self._h.modifier(attribute, amount, operation, on)

    def potion(self, effect: str, seconds: float, amplifier: int = 0, who=None, particles: bool = True):
        """Give a potion and forget it: Minecraft owns its stacking, expiry, milk and death. The
        engine never clears one."""
        self._h.potion(effect, seconds, amplifier, who, particles)

    def keep_potion(self, effect: str, amplifier: int = 0, particles: bool = False, seconds: float = 3):
        """As a beacon does: a `seconds` potion given again every 2 s (every 1 s when `seconds` is
        10 or more) while this lives. Night vision flickers under 10 s: give it seconds=12."""
        self._h.keep_potion(effect, amplifier, particles, seconds)

    def damage(self, target, amount: float, damage_type: str = "minecraft:generic"):
        """Deal damage through the engine, counted against this cast's declared damage."""
        self._h.damage(target, amount, damage_type)

    def explode(self, at, radius: float, damage: float) -> list[str]:
        """An explosion's damage, sound and particles at `at`: `damage` to every living thing in
        `radius`, the user too unless the ability's self_damage is off. No blocks break. Returns
        whom it hit (names and uuids)."""
        return self._h.explode(at, radius, damage)

    def spawn(self, what: str, at=None, dim: str | None = None, nbt: str | None = None,
              keep: bool = False) -> str:
        """Spawn an Actor class by id, or a vanilla entity ("minecraft:zombie") with extra SNBT
        `nbt`; returns its uuid. It is removed when this ends; with keep=True it stays in the
        world when this ends by itself, and is removed only when this is stopped."""
        return self._h.spawn(what, at, dim, nbt, keep)

    @property
    def spawned(self) -> list[str]:
        """The uuids of what this instance (in an ability's cast: that run) spawned that still exist."""
        return self._h.spawned()

    def remove(self, uuid: str):
        """Take away one thing this instance spawned, before it ends."""
        self._h.remove(uuid)

    def item_stack(self, item: str, count: int = 1) -> str:
        """The SNBT of an EverHear item's stack as a give builds it ({id, count, components}): to
        put one in a chest or a mob's hand."""
        return self._h.item_stack(item, count)

    def apply(self, who, mark: str, seconds: float | None = None, **args) -> str | None:
        """Apply a mark to `who` (a player, an actor, or any mob's uuid), with the mark's Args;
        returns the mark's instance id. A scenario owns the marks it applies."""
        return self._h.apply(who, mark, seconds, args)

    def start_scenario(self, scenario: str, **args) -> str:
        """Start a scenario with its Args; this instance owns it (it ends when this ends).
        Returns its instance id. (Not `self.start`: that is the start(ctx) hook content writes.)"""
        return self._h.start_scenario(scenario, args)

    def result(self, summary: str, **facts):
        """A scenario's result: what the `start` op answers (and the brain hears) once it ends.
        Facts are plain data. A one-shot calls this, then self.end()."""
        self._h.result(summary, facts)

    def save_place(self, name: str, at, by: str | None = None) -> str:
        """Save a named place (the engine's own, per world): `at` a Spot, a player, a position or
        "x y z [dim]". Returns the name as kept."""
        return self._h.save_place(name, at, by)

    def forget_place(self, name: str) -> bool:
        return self._h.forget_place(name)

    def teleport(self, who, at, dim: str | None = None, yaw: float | None = None, pitch: float | None = None):
        """Move a player or an entity to `at` (a Spot keeps its dimension and yaw)."""
        self._h.teleport(who, at, dim, yaw, pitch)

    def motion(self, who, vec):
        """Set an entity's velocity (blocks per tick)."""
        self._h.motion(who, vec)

    def entity_data(self, who, snbt: str):
        """`data merge entity`: SNBT onto an entity (TicksFrozen, NoAI...)."""
        self._h.entity_data(who, snbt)

    def ignite(self, who, seconds: float):
        """Set a player or a mob on fire for `seconds`."""
        self._h.ignite(who, seconds)

    @property
    def blocks(self):
        """This instance's block queue: fill, setblock, replace, sphere, forceload; `.done`."""
        return self._h.blocks

    def ask(self, command: str, method: str):
        """A slow command (locate, spreadplayers, place feature) run after this tick's batch,
        outside the 50 ms cut; its reply comes as `method(ctx, reply)` next tick."""
        self._h.ask(command, method)

    def bossbar(self, id: str, name: str | None = None, color: str | None = None, value: int | None = None,
                max: int | None = None, players=None):
        """Create or change this instance's boss bar `id`; removed when this ends. `players`: a
        Players arg ("@a") or names."""
        self._h.bossbar(id, name, color, value, max, players)

    def stop_sound(self, name: str | None = None, to=None):
        """Stop a sound (all of them when no name) for `to` (names), or everyone."""
        self._h.stop_sound(name, to)

    def spawnpoint(self, player, at):
        """Set a player's respawn point."""
        self._h.spawnpoint(player, at)

    def grant(self, who, ability: str):
        """Grant an ability to a player or an actor for as long as this lives."""
        self._h.grant(who, ability)

    def hold(self, setting: str, value, priority: int = 0):
        """Hold a world setting (a gamerule; "time"; "weather") at `value` while this lives."""
        self._h.hold(setting, value, priority)

    def release(self, setting: str):
        self._h.release(setting)

    def watch(self, area: str, center=None, radius: float | None = None, box=None,
              dim: str = "minecraft:overworld", height: float | None = None):
        """Watch an area: Enter and Exit events for each player crossing it. A sphere (center,
        radius), a flat cylinder (center, radius, height: that many blocks tall round the center's
        y, or math.inf for all of it), or a box (box=(corner, corner))."""
        self._h.watch(area, center, radius, box, dim, height)

    def command(self, text: str):
        """A raw command, the escape hatch for what the methods above do not do."""
        self._h.command(text)

    def particle(self, name: str, at, count: int = 1, delta=(0.0, 0.0, 0.0), speed: float = 0.0,
                 force: bool = False, to=None, dim: str | None = None):
        """Particles at `at` (a Spot keeps its dimension); force: seen from far away; to: only
        these players."""
        self._h.particle(name, at, count, delta, speed, force, to, dim)

    def sound(self, name: str, at=None, volume: float = 1.0, pitch: float = 1.0, to=None,
              dim: str | None = None):
        """A sound at `at` (the user when None); to: only these players hear it."""
        self._h.sound(name, at, volume, pitch, to, dim)

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
    Args: ClassVar[type[Args]] = Args

    @property
    def args(self):
        """What it was applied with: its Args, validated."""
        return self._h.args

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
    Args: ClassVar[type[Args]] = Args

    @property
    def args(self):
        """What it was started with: its Args, validated (a Place arg is a Spot)."""
        return self._h.args


KINDS: dict[str, type[Thing]] = {cls.kind: cls for cls in (Item, Ability, Mark, Actor, Scenario)}


def ticks_every(cls) -> float | None:
    """How often the engine calls the class's tick(ctx): its `every`, when it has a tick()."""
    return cls.tick_every if cls.tick_every and callable(getattr(cls, "tick", None)) else None
