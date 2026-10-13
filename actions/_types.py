"""The Action and Values bases, and the few value types an action uses (the game engine spec).

Every action is one of the game engine's operations on the brain's menu: one class in
actions/<name>.py, named as its file in CamelCase. It holds no code: its docstring's first line is
its line on the menu, its ClassVars say which of the engine's control ops it goes to (`engine`),
which fields the brain fills (`takes`) and how long it may take (`timeout_s`), and its fields are
what that op takes, each with a type from here, a default, its limits as Field(ge=, le=) or a
pattern, and the string under it as its description:

    class EngineGrant(Action):
        '''Give a player one of the engine's abilities ...'''

        engine = "grant"
        takes = ["player", "ability"]

        player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
        '''who gets it: one player's exact name'''

lib/py/vfg_actions.py loads, checks and renders the actions, on the PC only (Python 3.12,
pydantic 2): vfg checks every value against its action before it sends the op to the engine,
which checks a scenario's or a mark's args (Args) against the class's own Args.

A value given as a word (key=value on the command line) is read as its field's type: a number;
true, false, yes, no, on, off, 1 or 0; a list as a,b,c or as JSON; an option in any case. A number
outside its limits is clamped and a note says so; anything else that does not fit is refused.
"""
from __future__ import annotations

import json
import math
import types
from typing import Annotated, Any, ClassVar, Literal, Union, get_args, get_origin

import annotated_types
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, model_validator
from pydantic.fields import FieldInfo

__all__ = ["Action", "Values", "Field", "Literal", "Any", "Args", "Python", "Player", "ClassId", "Instance", "Kind"]

PYTHON_MAX = 20000  # characters of Python source one value may hold: a Config is far less
_BOOLS = {"true": True, "yes": True, "on": True, "1": True, "y": True,
          "false": False, "no": False, "off": False, "0": False, "n": False}


class Kind:
    """Marks a type as one of this file's, by name: what vfg action check, the menu and
    actions.json call it."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return "Kind(%r)" % self.name


# One player's exact name; empty for none given (the op refuses it empty where it needs one). Engine
# bindings, grants and marks are per player, never @a.
Player = Annotated[str, Field(pattern=r"^(|[A-Za-z0-9_]{1,16})$"), Kind("player")]
# A class's id, as the engine names it (fireball, fire_staff); empty for none given.
ClassId = Annotated[str, Field(pattern=r"^(|[a-z][a-z0-9_]{0,63})$"), Kind("class id")]
# One running instance, as the engine names it (scenario:horde#3); empty for none given.
Instance = Annotated[str, Field(pattern=r"^(|[A-Za-z0-9_.:@#-]{1,96})$"), Kind("instance")]
# A scenario's or a mark's args, by name (args.at, args.count): checked by the engine against the
# class's own Args, never here, as the class can change at any reload.
Args = Annotated[dict[str, Any], Kind("args")]
# A class's source as written, every line, its indentation and its comments kept (a Config the
# brain rewrites), which text, one line of words, cannot carry. vfg_actions takes it whole.
Python = Annotated[str, Kind("python")]


# ---- reading a field's type ---------------------------------------------------------------

def unwrap(t: Any, meta: tuple | list = ()) -> tuple[Any, list]:
    """A field's type without Annotated (and without Optional), and everything that was annotated
    on it, flattened: (float, [Ge(0), Le(60)])."""
    out = []
    for m in meta:
        out += m.metadata if isinstance(m, FieldInfo) else [m]
    while True:
        if get_origin(t) is Annotated:
            args = get_args(t)
            t = args[0]
            for m in args[1:]:
                out += m.metadata if isinstance(m, FieldInfo) else [m]
            continue
        if get_origin(t) in (Union, types.UnionType):
            args = [a for a in get_args(t) if a is not type(None)]
            if len(args) == 1:
                t = args[0]
                continue
        return t, out


def kind(t: Any, meta: tuple | list = ()) -> str:
    """A field's type in a word or two: int, number, bool, str, enum, args, python, or "list of
    <kind>"; "?" for none of these."""
    t, meta = unwrap(t, meta)
    for m in meta:
        if isinstance(m, Kind):
            return m.name
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t is float:
        return "number"
    if t is str:
        return "str"
    if get_origin(t) is Literal:
        return "enum"
    if get_origin(t) is list:
        args = get_args(t)
        return "list of " + kind(args[0]) if args else "list"
    if get_origin(t) is dict:
        return "args"
    return "?"


def choices(t: Any, meta: tuple | list = ()) -> list | None:
    """The options of an enum, or of each item of a list of one; None for anything else."""
    t, _ = unwrap(t, meta)
    if get_origin(t) is list and get_args(t):
        t, _ = unwrap(get_args(t)[0])
    return list(get_args(t)) if get_origin(t) is Literal else None


def limits(t: Any, meta: tuple | list = ()) -> tuple[float | None, float | None]:
    """A number's (ge, le), None where it has none."""
    _, meta = unwrap(t, meta)
    lo = hi = None
    for m in meta:
        if isinstance(m, annotated_types.Ge):
            lo = m.ge
        elif isinstance(m, annotated_types.Le):
            hi = m.le
        elif isinstance(m, annotated_types.Interval):
            lo = m.ge if m.ge is not None else lo
            hi = m.le if m.le is not None else hi
    return lo, hi


def type_problem(t: Any, meta: tuple | list = ()) -> str | None:
    """None when a field's type is one of this file's: bool, int, float, str, Literal[...],
    list[...] of these, Args or Python, with Field() limits, lengths and a pattern. Else what is
    not."""
    while get_origin(t) is Annotated:
        args = get_args(t)
        t, meta = args[0], list(meta) + list(args[1:])
    for m in meta:
        if isinstance(m, FieldInfo):
            m = None if all(_constraint(x) for x in m.metadata) else m
        if m is None or isinstance(m, Kind) or _constraint(m):
            continue
        return "%s is not one of actions/_types.py's types: it carries %r" % (_name(t), m)
    if t in (bool, int, float, str):
        return None
    if get_origin(t) is Literal:
        bad = [a for a in get_args(t) if not isinstance(a, (str, int, bool))]
        return "a Literal of words or numbers, not %r" % bad[0] if bad else None
    if get_origin(t) is list:
        args = get_args(t)
        return type_problem(args[0]) if args else "list[...] says what it holds: list[str]"
    if get_origin(t) is dict:
        return None if get_args(t) == (str, Any) else "a dict is Args, dict[str, Any]: a scenario's or a mark's args"
    return "%s is not one of actions/_types.py's types" % _name(t)


def _constraint(m: object) -> bool:
    """Field()'s limits and lengths, and a pattern: allowed on any field."""
    return isinstance(m, (annotated_types.BaseMetadata, StringConstraints)) \
        or type(m).__name__ == "_PydanticGeneralMetadata"


def _name(t: Any) -> str:
    """A type as it is written: Optional[int] whether spelled so or int | None (Python 3.14
    names every union Union)."""
    if get_origin(t) in (Union, types.UnionType):
        args = get_args(t)
        rest = [a for a in args if a is not type(None)]
        if len(rest) == 1 and len(args) == 2:
            return "Optional[%s]" % _name(rest[0])
        return "Union[%s]" % ", ".join(_name(a) for a in args)
    return getattr(t, "__name__", None) or repr(t)


# ---- reading words --------------------------------------------------------------------------

def _show(x: object) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return "%d" % x
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def _read(key: str, t: Any, meta: list, raw: object, notes: list, problems: list, clamp: bool) -> object:
    """One value as its field's type reads it: a word from the command line made a number,
    a bool, a list or an option; a number clamped to its limits, with a note. What still does
    not fit is left for pydantic to refuse, with the field's name. Python source is taken whole,
    its lines kept; args come as an object, or a word holding one in JSON."""
    t, meta = unwrap(t, meta)
    if raw is None:
        return raw
    named = next((m.name for m in meta if isinstance(m, Kind)), None)
    if named == "python":
        if not isinstance(raw, str):
            problems.append("%s is Python source, as text" % key)
            return raw
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in text):
            problems.append("%s holds a control character that is not a line end or a tab" % key)
        elif len(text) > PYTHON_MAX:
            problems.append("%s is %d characters of Python; at most %d" % (key, len(text), PYTHON_MAX))
        return text
    if get_origin(t) is dict:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                problems.append('%s is an object: one key and value per arg, as JSON {"at": "home"}' % key)
                return raw
        if not isinstance(raw, dict):
            problems.append('%s is an object: one key and value per arg, as JSON {"at": "home"}' % key)
        return raw
    if t is bool:
        if isinstance(raw, str):
            if raw.strip().lower() not in _BOOLS:
                problems.append("%s is true or false, not %s" % (key, raw))
                return raw
            return _BOOLS[raw.strip().lower()]
        return raw
    if t in (int, float):
        if isinstance(raw, bool):
            problems.append("%s takes a number, not %s" % (key, _show(raw)))
            return raw
        if isinstance(raw, str):
            try:
                x = float(raw.strip())
            except ValueError:
                return raw
        elif isinstance(raw, (int, float)):
            x = float(raw)
        else:
            return raw
        if not math.isfinite(x):
            problems.append("%s takes a finite number, not %s" % (key, _show(raw)))
            return raw
        lo, hi = limits(t, meta)
        if clamp and lo is not None and x < lo:
            notes.append("%s=%s is under its min, clamped to %s" % (key, _show(raw), _show(lo)))
            x = float(lo)
        elif clamp and hi is not None and x > hi:
            notes.append("%s=%s is over its max, clamped to %s" % (key, _show(raw), _show(hi)))
            x = float(hi)
        return int(round(x)) if t is int else x
    if get_origin(t) is Literal:
        if isinstance(raw, str):
            for o in get_args(t):
                if isinstance(o, str) and o.lower() == raw.strip().lower():
                    return o
        return raw
    if t is str:
        if isinstance(raw, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in raw):
                problems.append("%s holds a newline or another control character" % key)
                return raw
            return raw.strip()
        return raw
    if get_origin(t) is list:
        if isinstance(raw, str):
            s = raw.strip()
            if s.startswith("["):
                try:
                    raw = json.loads(s)
                except ValueError:
                    problems.append('%s is a list: a,b,c or JSON like ["a","b"]' % key)
                    return raw
            else:
                raw = [p.strip() for p in s.split(",") if p.strip()]
        if isinstance(raw, (list, tuple)) and get_args(t):
            return [_read("%s[%d]" % (key, i), get_args(t)[0], [], x, notes, problems, clamp)
                    for i, x in enumerate(raw)]
        return raw
    return raw


class Values(BaseModel):
    """Values with their types, limits and descriptions: the base of every action.

    model_validate(data, context={"notes": []}): words read as their types, numbers clamped with a
    note in context["notes"]. context["clamp"] False refuses a number out of range instead."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    @model_validator(mode="before")
    @classmethod
    def _read_words(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        ctx = info.context or {}
        notes = ctx["notes"] if isinstance(ctx.get("notes"), list) else []
        out, problems = {}, []
        for key, raw in data.items():
            field = cls.model_fields.get(key)
            out[key] = raw if field is None else \
                _read(key, field.annotation, field.metadata, raw, notes, problems, ctx.get("clamp", True))
        if problems:
            raise ValueError("; ".join(problems))
        return out


class Action(Values):
    """One of the engine's operations on the brain's menu: the op its values go to, and the values
    it takes. No code.

    The class docstring's first line is the action's line on the menu, in players' words.
    ClassVars, set without an annotation in an action (engine = "grant"):
      engine     the engine's control op its values go to (vfg sends it through `vfg engine`)
      takes      the fields the brain fills, the target first; the rest keep their default
      timeout_s  for an op slower than VFG_DISPATCH_ACTION_TIMEOUT_S (a bind's alias call, a reload)
    """

    engine: ClassVar[str] = ""
    takes: ClassVar[list[str]] = []
    timeout_s: ClassVar[int | None] = None


def camel(name: str) -> str:
    """gatling_gun -> GatlingGun: the class a file holds."""
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))
