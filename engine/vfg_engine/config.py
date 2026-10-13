"""Config: every number a class uses, in one visible place (the game engine spec, "Abilities and
their config", and its config table).

A class's `Config` is a pydantic model, one per class and the same for every player. Content
writes it the short way the spec does:

    class Config(AbilityConfig):
        summary = "Burns, then explodes."          # no annotation: the base's (str) is used
        radius: float = Field(3, ge=1, le=8)       # a field of its own, with bounds
        charge_s = 0                               # zero is a value; it has to be written
        def damage(self, ctx): ...                 # a formula: a method taking ctx

Pydantic refuses an unannotated value, so `ConfigMeta` fills in the annotation before pydantic
sees the class: the base's own (with its bounds) for a field the base declares, else the value's
type. `damage` and `duration_s` (the "formula" names) may be a value or a method taking `ctx`;
the engine reads them through `value_of`. A required field left out makes the class fail its
load check, naming the field (`missing`).
"""

from __future__ import annotations

import inspect
import math
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic._internal._model_construction import ModelMetaclass

__all__ = ["AbilityConfig", "MarkConfig", "ItemConfig", "ActorConfig", "ScenarioConfig",
           "Field", "UNTIL_STOPPED", "value_of", "missing", "build", "own_fields", "FORMULA"]

UNTIL_STOPPED = math.inf
"""A duration that never ends by itself: `duration_s = UNTIL_STOPPED`. The instance runs until
it is stopped (`self.end()`, the `stop` op, its owner ending, or its user leaving it)."""

FORMULA = ("damage", "duration_s")   # the names that may be a value or a method taking ctx

_PLAIN = (bool, int, float, str, list, tuple, dict)


def _base_annotations(bases) -> dict[str, Any]:
    """Every field the bases declare, as an annotation carrying its bounds."""
    out: dict[str, Any] = {}
    for base in bases:
        for klass in getattr(base, "__mro__", ()):
            for name, info in (getattr(klass, "model_fields", None) or {}).items():
                if name in out:
                    continue
                ann = info.annotation
                out[name] = Annotated[ann, *info.metadata] if info.metadata else ann
    return out


class ConfigMeta(ModelMetaclass):
    """Fills in the annotation of every unannotated plain value before pydantic sees the class,
    and refuses a method standing in for a field that must be a value."""

    def __new__(mcs, name, bases, namespace, **kwargs):
        annotations = dict(namespace.get("__annotations__", {}))
        declared = _base_annotations(bases)
        class_vars = set().union(*(getattr(b, "__class_vars__", set()) for b in bases))
        for key, value in list(namespace.items()):
            if key.startswith("_") or key in annotations or key == "model_config" or key in class_vars:
                continue
            if inspect.isfunction(value):
                if key in declared and key not in FORMULA:
                    raise TypeError("%s.%s must be a value, not a method: only %s may be "
                                    "formulas taking ctx" % (name, key, " and ".join(FORMULA)))
                continue
            if inspect.isclass(value) or isinstance(value, (classmethod, staticmethod, property)):
                continue
            if key in declared:
                annotations[key] = declared[key]
            elif key in FORMULA:
                annotations[key] = float
            elif isinstance(value, _PLAIN):
                annotations[key] = type(value)
            # anything else is left for pydantic to refuse, in its own words
        namespace["__annotations__"] = annotations
        return super().__new__(mcs, name, bases, namespace, **kwargs)


class BaseConfig(BaseModel, metaclass=ConfigMeta):
    """What every Config has: a `summary` line. Values are checked against their bounds when
    the class loads (validate_default), and a Config cannot be changed while running (frozen)."""

    model_config = ConfigDict(validate_default=True, frozen=True, extra="forbid",
                              arbitrary_types_allowed=False)

    REQUIRED: ClassVar[tuple[str, ...]] = ()    # the config table's required names, read by `missing`

    summary: str


class AbilityConfig(BaseConfig):
    """Required: summary, damage, duration_s, charge_s, cooldown_s, scale_per_point.

    `damage` is what one target takes from one cast, scale included; for a passive ability or
    one that runs until stopped, it is what one target takes per second. `duration_s` is how
    long a cast's run lasts after its charge, or UNTIL_STOPPED."""

    REQUIRED = ("summary", "damage", "duration_s", "charge_s", "cooldown_s", "scale_per_point")

    charge_s: Annotated[float, Field(ge=0)]
    cooldown_s: Annotated[float, Field(ge=0)]
    scale_per_point: float
    cooldown_from: Literal["cast", "end"] = "cast"   # when the cooldown starts (DESIGN, Decided 1)
    self_damage: bool = True                          # whether its explosions hurt its own user


class MarkConfig(BaseConfig):
    """Required: summary, duration_s, damage (over its whole length; per second when it lasts
    until stopped). on_death ("end" or "keep") and on_milk ("keep" or "end") have defaults
    (DESIGN, Decided 4 and 8). `again`: the same mark on the same bearer refreshes the one
    there, or stacks another beside it."""

    REQUIRED = ("summary", "duration_s", "damage", "on_death", "on_milk")

    on_death: Literal["end", "keep"] = "end"
    on_milk: Literal["keep", "end"] = "keep"
    again: Literal["refresh", "stack"] = "refresh"


class ItemConfig(BaseConfig):
    """Required: summary, base (the vanilla item it is made on), name, active_in: where it
    counts as held, "mainhand", "offhand", "hands" (either), "worn" (an armour slot) or
    "inventory" (anywhere on the player)."""

    REQUIRED = ("summary", "base", "name", "active_in")

    base: str
    name: str
    active_in: Literal["mainhand", "offhand", "hands", "worn", "inventory"]


class ActorConfig(BaseConfig):
    """Required: summary, entity_type ("minecraft:wither_skeleton"), health, size (its scale,
    1 is vanilla's), damage (its melee hit). Optional: the name shown over it, the abilities it
    is granted when it spawns, and its equipment (slot -> vanilla item id)."""

    REQUIRED = ("summary", "entity_type", "health", "size", "damage")

    entity_type: str
    health: Annotated[float, Field(gt=0, le=1024)]
    size: Annotated[float, Field(ge=0.0625, le=16)]
    name: str | None = None
    abilities: list[str] = []
    equipment: dict[str, str] = {}


class ScenarioConfig(BaseConfig):
    """Required: summary, duration_s (a number of seconds, or UNTIL_STOPPED)."""

    REQUIRED = ("summary", "duration_s")

CONFIG_BASES = (AbilityConfig, MarkConfig, ItemConfig, ActorConfig, ScenarioConfig)


def _has(cls, name: str) -> bool:
    if name in cls.model_fields:
        return True
    return inspect.isfunction(getattr(cls, name, None))


def missing(cls) -> list[str]:
    """The required names `cls` (a Config class) leaves out, in the config table's order. A
    field with no default counts as left out: zero is a value and has to be written."""
    out = []
    for name in cls.REQUIRED:
        if not _has(cls, name):
            out.append(name)
        elif name in cls.model_fields and cls.model_fields[name].is_required():
            out.append(name)
    return out


def build(cls):
    """The class's one Config instance. ValueError, in words, when a required field is missing
    or a value is out of its bounds."""
    gone = missing(cls)
    if gone:
        raise ValueError("required field%s missing: %s" % ("" if len(gone) == 1 else "s", ", ".join(gone)))
    try:
        return cls()
    except ValidationError as exc:
        raise ValueError(_words(exc)) from None


def _words(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err.get("loc", ())) or "config"
        parts.append("%s: %s (it is %r)" % (where, err.get("msg"), err.get("input")))
    return "; ".join(parts)


def value_of(config, name: str, ctx=None):
    """A formula field's value: the method called with ctx, or the plain value."""
    value = getattr(config, name)
    if callable(value):
        return value(ctx)
    return value


def own_fields(config) -> dict[str, Any]:
    """The class's own fields and their values: those its config base does not declare."""
    base = next((b for b in type(config).__mro__ if b in CONFIG_BASES), BaseConfig)
    out = {}
    for name in type(config).model_fields:
        if name in base.model_fields or name in FORMULA:
            continue
        out[name] = getattr(config, name)
    return out


def engine_fields(config) -> dict[str, Any]:
    """The fields the config base declares (required and optional), formulas left out."""
    base = next((b for b in type(config).__mro__ if b in CONFIG_BASES), BaseConfig)
    return {name: getattr(config, name) for name in base.model_fields}
