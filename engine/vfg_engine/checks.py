"""The checks a class passes before it goes live (the game engine spec, "Live updates", step 2,
and the config table's "Checked at reload"). Each returns None, or the reason in words; the
loader names the check that failed.

    base       it subclasses one of the five kinds, and its Config the matching config base
    code       no `while True` loop and no sleep (an ability's loop is tick() and timers), no
               __init__ or __setattr__ of its own
    state      State() can be made from its defaults alone
    config     every required field has a value (zero is a value: it has to be written)
    bounds     every value within its Field bounds; duration_s a number of seconds or UNTIL_STOPPED
    damage     damage(ctx) a number at power 0, 10 and 20, and with scale_per_point above 0 it
               changes with power
    triggers   an item: its base is one an EverHear item may use, and it can report each trigger
               the class listens for (vfg_engine.bases)
    args       a Scenario's or Mark's Args: every field has a default and a description, and the
               defaults fit; only a Scenario or a Mark declares Args
    config     an actor: its entity_type exists in 26.3 (vfg_engine.registries)
"""

from __future__ import annotations

import ast
import math

from . import bases
from .config import CONFIG_BASES, build, value_of
from .content import KINDS, Thing
from .ctx import ProbeCtx
from .events import KINDS as EVENT_KINDS

POWERS = (0, 10, 20)
CONFIG_OF = {kind: cls.Config for kind, cls in KINDS.items()}


def kind_of(cls) -> str | None:
    kinds = [k for k, base in KINDS.items() if issubclass(cls, base)]
    return kinds[0] if len(kinds) == 1 else None


def check_base(cls) -> str | None:
    kinds = [k for k, base in KINDS.items() if issubclass(cls, base)]
    if len(kinds) != 1:
        return "%s must subclass exactly one of Item, Ability, Mark, Actor, Scenario" % cls.__name__
    want = CONFIG_OF[kinds[0]]
    config = cls.__dict__.get("Config")
    if config is None:
        return "%s has no Config of its own (class Config(%s): ...)" % (cls.__name__, want.__name__)
    if not (isinstance(config, type) and issubclass(config, want)):
        return "%s.Config must subclass %s" % (cls.__name__, want.__name__)
    for other in CONFIG_BASES:
        if other is not want and issubclass(config, other):
            return "%s.Config mixes %s and %s" % (cls.__name__, want.__name__, other.__name__)
    state = getattr(cls, "State", None)
    from .content import State
    if not (isinstance(state, type) and issubclass(state, State)):
        return "%s.State must subclass vfg_engine.State" % cls.__name__
    if "Args" in cls.__dict__ and kinds[0] not in ("scenario", "mark"):
        return ("%s declares Args, but only a Scenario or a Mark takes args: an %s's numbers are its "
                "Config, one for everyone" % (cls.__name__, kinds[0]))
    return None


def check_args(cls) -> str | None:
    """A Scenario's or Mark's Args: an Args subclass, every field with a default and a description."""
    from .args import Args, validate
    args = getattr(cls, "Args", None)
    if args is None:
        return None
    if not (isinstance(args, type) and issubclass(args, Args)):
        return "%s.Args must subclass vfg_engine.Args" % cls.__name__
    for name, info in args.model_fields.items():
        if info.is_required():
            return "%s.Args.%s has no default: every arg needs one" % (cls.__name__, name)
        if not (info.description or "").strip():
            return ("%s.Args.%s has no description: the brain reads it (Field(..., description=...), "
                    "or a docstring under the field)" % (cls.__name__, name))
    try:
        validate(args, {})
    except ValueError as exc:
        return "%s.Args' defaults do not fit: %s" % (cls.__name__, exc)
    return None


def class_node(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def check_code(cls, tree: ast.Module | None) -> str | None:
    for name in ("__init__", "__setattr__", "__new__"):
        if name in cls.__dict__:
            return "%s defines %s: the engine makes instances; keep what it remembers in self.state" % (
                cls.__name__, name)
    every = getattr(cls, "tick_every", None)
    if every is not None and (not isinstance(every, (int, float)) or isinstance(every, bool)
                              or not math.isfinite(every) or every <= 0):
        return "%s.every must be a number of seconds above 0, not %r" % (cls.__name__, every)
    node = class_node(tree, cls.__name__) if tree is not None else None
    if node is None:
        return None
    for sub in ast.walk(node):
        if isinstance(sub, ast.While) and isinstance(sub.test, ast.Constant) and sub.test.value:
            return ("%s has a `while True` loop (line %d): an ability's loop is tick() with `every`, "
                    "plus timers (self.after, self.every)" % (cls.__name__, sub.lineno))
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name == "sleep":
                return ("%s sleeps (line %d): content never waits; use self.after(seconds, \"method\")"
                        % (cls.__name__, sub.lineno))
    return None


def check_config(cls):
    """(the Config instance, None) or (None, (check, reason))."""
    try:
        config = build(cls.Config)
    except ValueError as exc:
        words = str(exc)
        return None, ("config" if words.startswith("required field") else "bounds", words)
    except Exception as exc:  # a formula default, a validator of the class's own
        return None, ("bounds", "%s: %s" % (type(exc).__name__, exc))
    return config, None


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_duration(config, call) -> str | None:
    """duration_s at power 1 is a number of seconds (UNTIL_STOPPED counts)."""
    if "duration_s" not in type(config).REQUIRED:
        return None
    ok, value = call(lambda: value_of(config, "duration_s", ProbeCtx(1)))
    if not ok:
        return "duration_s(ctx) failed: %s" % value
    if value == math.inf:
        return None
    if not _number(value) or value < 0:
        return "duration_s is %r: it must be a number of seconds, 0 or more, or UNTIL_STOPPED" % (value,)
    return None


def damage_at(config, call) -> tuple[dict[int, float] | None, str | None]:
    """damage(ctx) at power 0, 10 and 20, or the reason it is not a number at one of them."""
    out = {}
    for p in POWERS:
        ok, value = call(lambda p=p: value_of(config, "damage", ProbeCtx(p)))
        if not ok:
            return None, "damage(ctx) at power %d failed: %s" % (p, value)
        if not _number(value) or value < 0:
            return None, "damage(ctx) at power %d is %r: it must be a number, 0 or more" % (p, value)
        out[p] = float(value)
    return out, None


def check_damage(config, call) -> str | None:
    if "damage" not in type(config).REQUIRED:
        return None
    values, error = damage_at(config, call)
    if error:
        return error
    scale = getattr(config, "scale_per_point", 0)
    if _number(scale) and scale > 0 and len(set(values.values())) == 1:
        return ("damage(ctx) is %g at power 0, 10 and 20, but scale_per_point is %g: the damage "
                "formula must use the scale" % (values[0], scale))
    return None


def listened_kinds(node: ast.ClassDef | None) -> set[str]:
    """The event kinds a class's code listens for: self.listen(Use, ...), self.listen("use", ...)."""
    found = set()
    if node is None:
        return found
    by_name = {cls.__name__: kind for kind, cls in EVENT_KINDS.items()}
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "listen" and sub.args):
            continue
        arg = sub.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value.strip().lower())
        elif isinstance(arg, ast.Name) and arg.id in by_name:
            found.add(by_name[arg.id])
        elif isinstance(arg, ast.Attribute) and arg.attr in by_name:
            found.add(by_name[arg.attr])
    return found


def check_triggers(config, node: ast.ClassDef | None) -> str | None:
    base = config.base
    if bases.normal(base) not in bases.KNOWN_BASES:
        return bases.why_not(base, "held")
    for trigger in sorted(listened_kinds(node) & set(bases.TRIGGERS)):
        why = bases.why_not(base, trigger)
        if why:
            return "it listens for %s, but %s" % (trigger, why)
    return None


def is_content(value, module_name: str) -> bool:
    return (isinstance(value, type) and issubclass(value, Thing) and value not in KINDS.values()
            and value is not Thing and value.__module__ == module_name)


def check_entity_type(entity_type: str) -> str | None:
    """An actor's base entity exists in 26.3 (vfg_engine.registries)."""
    from .registries import ENTITY_TYPES
    name = str(entity_type).strip().lower()
    name = name if ":" in name else "minecraft:" + name
    if name not in ENTITY_TYPES:
        return "entity_type %r is not an entity in Minecraft 26.3" % entity_type
    return None
