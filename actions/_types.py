"""The Action base, the Values base and the value types every action and preset uses.

An action is one class in actions/<name>.py (made/<server>/actions/<name>.py for one this
server's implementers made), named as its file in CamelCase. It holds no code: its
docstring's first line is its line on the menu, its ClassVars say which script it runs and
how, and its fields are the values that script reads, each with a type from here, a default,
its limits as Field(ge=, le=) and the string under it as its description. A preset's values
are a Values class beside its function in scripts/_presets/<name>.py, and the action over a
script inherits the classes of the presets the script copied:

    class Railgun(Action, preset("damage_hits")): ...

lib/py/vfg_actions.py loads, checks and renders the actions. Imported on the PC only (Python
3.12, pydantic 2): every value is checked here before anything is sent to EC2, where the
scripts get plain JSON and no pydantic.

A value given as a word (key=value on the command line) is read as its field's type: a
number; true, false, yes, no, on, off, 1 or 0; a list as a,b,c or as JSON; an option in any
case. A number outside its limits is clamped and a note says so, as the kit does; anything
else that does not fit is refused.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import re
import sys
import types
from copy import copy
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Union, get_args, get_origin

import annotated_types
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints,
                      ValidationInfo, model_validator)
from pydantic.fields import FieldInfo

REPO = Path(__file__).resolve().parent.parent

__all__ = ["Action", "Values", "Live", "preset", "Field", "Literal", "Player", "Players", "MobId", "BlockId",
           "ItemId", "ParticleId", "SoundId", "Place", "Command", "CopyName", "Any"]


# The command-safety rules: scripts/_lib/commands.py's, loaded by path, the one list of
# refused commands, so the PC and EC2 refuse the same words.
def _load_commands():
    path = REPO / "scripts" / "_lib" / "commands.py"
    spec = importlib.util.spec_from_file_location("vfg_remote_commands", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_commands = _load_commands()
REFUSED = _commands.REFUSED                  # the owner's commands
BLOCK_COMMANDS = _commands.BLOCK_COMMANDS    # commands that place or remove blocks
RCON_MAX = _commands.RCON_MAX                # what one RCON packet carries, in bytes of UTF-8
command_problem = _commands.command_problem  # why a command may not run, for the planner, or None


def _load_gone(path: Path = REPO / "scripts" / "_lib" / "kit" / "world.py") -> dict[str, str]:
    """kit/world.py's GONE, the ids 26.3 refuses and what to use instead, read without running
    it (it imports mc)."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "GONE" for t in node.targets):
            return dict(ast.literal_eval(node.value))
    raise ImportError("%s has no GONE" % path)


GONE = _load_gone()

PLAYER_PATTERN = r"^[A-Za-z0-9_]{1,16}$"
PLAYERS_PATTERN = r"^(@[arp]|[A-Za-z0-9_]{1,16}(,[A-Za-z0-9_]{1,16})*)$"
# What kit/values.py takes for each kind of id: a namespace is allowed, minecraft: is dropped.
_ID = r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+"
ID_PATTERNS = {
    "mob": r"^%s$" % _ID,
    "block": r"^#?%s(\[[^\]]*\])?(\{.*\})?$" % _ID,
    "item": r"^#?%s(\[.*\])?$" % _ID,
    "particle": r"^%s(\{.*\})?$" % _ID,
    "sound": r"^%s$" % _ID,
}
_NUM = r"-?\d+(?:\.\d+)?"
# A place as it is sent: empty (none), "x y z" with the_nether or the_end after it for
# those, or one name: a saved place's, else a player's.
AT_PATTERN = r"^(|%s %s %s( the_nether| the_end)?|[A-Za-z0-9_]{1,32})$" % (_NUM, _NUM, _NUM)
PLACE_PATTERN = r"^[a-z0-9_]{1,32}$"          # a saved place's name
COPY_PATTERN = r"^[a-z0-9][a-z0-9_]{0,47}$"   # a running copy's name, as EC2 names it
BORDER = 30_000_000                            # no x or z this far out is in the world
HEIGHTS = {"": (-64, 319), "the_nether": (0, 255), "the_end": (0, 255)}  # kit/world.py's LIMITS
_DIMS = {"overworld": "", "nether": "the_nether", "the_nether": "the_nether", "end": "the_end", "the_end": "the_end"}
_COORDS = re.compile(r"^(%s)[ ,]+(%s)[ ,]+(%s)(?:[ ,]+(?:minecraft:)?([a-z_]+))?$" % (_NUM, _NUM, _NUM))
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


class _LiveMark:
    __slots__ = ()

    def __repr__(self) -> str:
        return "LIVE"


LIVE = _LiveMark()


class Live:
    """Live[T]: a value the script reads again while it runs, so an edit to it lands within
    VFG_LIVE_POLL_S; an edit to any other value restarts the copy with the rest kept."""

    def __class_getitem__(cls, item):
        return Annotated[item, LIVE]


def _players(v: object) -> object:
    """' Steve , Alex ' -> 'Steve,Alex', as the kit reads it; a list of names is joined."""
    if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        v = ",".join(v)
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s in ("@a", "@r", "@p"):
        return s
    return ",".join(n.strip() for n in s.split(",") if n.strip())


def _id(kind: str):
    def norm(v: object) -> object:
        """'minecraft:zombie' -> 'zombie', '#minecraft:logs' -> '#logs', [states] and {nbt}
        kept; a mob or a sound lowercased first, as the kit does; an id 26.3 refuses is refused."""
        if not isinstance(v, str):
            return v
        s = v.strip().lower() if kind in ("mob", "sound") else v.strip()
        cut = min([i for i in (s.find("["), s.find("{")) if i >= 0] or [len(s)])
        head, rest = s[:cut], s[cut:]
        tag = head.startswith("#")
        bare = head[1:] if tag else head
        if bare.startswith("minecraft:"):
            bare = bare[len("minecraft:"):]
        if kind == "mob" and bare in GONE:
            raise ValueError("%s is gone in 26.3, use %s" % (bare, GONE[bare]))
        if tag and "#minecraft:" + bare in GONE:
            raise ValueError("#%s is gone in 26.3, use %s" % (bare, GONE["#minecraft:" + bare]))
        return ("#" if tag else "") + bare + rest
    norm.__name__ = "_%s_id" % kind
    return norm


def _num_word(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _place(v: object, info: ValidationInfo) -> object:
    """'100, 64, -20' -> '100 64 -20'; '1 2 3 nether' -> '1 2 3 the_nether'; 'My Castle' ->
    'my_castle' when it is saved; else a player's name as it is. With places in the context
    ({"places": {name: ...}}, the places cache), a name that is neither saved nor a player's
    name is refused; without them, names go unchecked."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return ""
    m = _COORDS.match(s.lower())
    if m:
        x, y, z = (float(g) for g in m.groups()[:3])
        d = _DIMS.get(m.group(4) or "overworld")
        if d is None:
            raise ValueError("%r: the dimension after x y z is overworld, the_nether or the_end" % s)
        lo, hi = HEIGHTS[d]
        if abs(x) >= BORDER or abs(z) >= BORDER or not lo <= y <= hi:
            raise ValueError("%s is outside the world (y from %d to %d there)" % (s, lo, hi))
        return " ".join(_num_word(c) for c in (x, y, z)) + (" " + d if d else "")
    ctx = info.context or {}
    places = ctx.get("places")
    if places is not None and "given" in ctx and info.field_name not in ctx["given"]:
        places = None  # a default or a value kept from before: checked when it was given
    name = re.sub(r"[\s-]+", "_", s.lower())
    if places is not None and name in places:
        return name
    if re.match(PLAYER_PATTERN, s):
        return s  # a player's name (or, unchecked, a saved place's): EC2 finds which as it runs
    if places is None and re.match(PLACE_PATTERN, name):
        return name
    if places is None:
        raise ValueError("%r is not x y z, a saved place's name or a player's name" % s)
    raise ValueError("%r is not a saved place (%s), x y z, or a player's name"
                     % (s, "saved: " + ", ".join(sorted(places)) if places else "none is saved"))


def clean_command(cmd: str) -> str:
    """Strip spaces and one leading slash; raise ValueError when it may not run."""
    if not isinstance(cmd, str):
        raise ValueError("a command is text")
    cmd = cmd.strip()
    if cmd.startswith("/"):
        cmd = cmd[1:].lstrip()
    if not cmd:
        raise ValueError("empty command")
    why = command_problem(cmd)
    if why:
        raise ValueError("%s: %s" % (why, cmd[:80]))
    return cmd


# The value types. A pattern comes first in each Annotated, so it stays in the JSON schema
# that holds the model's structured output to it; a BeforeValidator after it still runs
# first, so the pattern is checked on the tidied word. (A BeforeValidator ahead of the
# pattern turns it into a function check that never reaches the schema.)
Player = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PLAYER_PATTERN), Kind("player")]
Players = Annotated[str, Field(pattern=PLAYERS_PATTERN), Kind("players"), BeforeValidator(_players)]
MobId = Annotated[str, Field(pattern=ID_PATTERNS["mob"]), Kind("mob"), BeforeValidator(_id("mob"))]
BlockId = Annotated[str, Field(pattern=ID_PATTERNS["block"]), Kind("block"), BeforeValidator(_id("block"))]
ItemId = Annotated[str, Field(pattern=ID_PATTERNS["item"]), Kind("item"), BeforeValidator(_id("item"))]
ParticleId = Annotated[str, Field(pattern=ID_PATTERNS["particle"]), Kind("particle"),
                       BeforeValidator(_id("particle"))]
SoundId = Annotated[str, Field(pattern=ID_PATTERNS["sound"]), Kind("sound"), BeforeValidator(_id("sound"))]
Place = Annotated[str, Field(pattern=AT_PATTERN), Kind("place"), BeforeValidator(_place)]
Command = Annotated[str, Kind("command"), BeforeValidator(clean_command)]
# A running copy's name (stop's and edit's `copy`); empty, the default, names none, and the
# stop and edit scripts refuse it.
CopyName = Annotated[str, Field(pattern=r"^(|%s)$" % COPY_PATTERN[1:-1])]

# The functions inside those types: the only validators a field may carry.
_OURS = frozenset(m.func for t in (Players, MobId, BlockId, ItemId, ParticleId, SoundId, Place, Command)
                  for m in t.__metadata__ if isinstance(m, BeforeValidator))


# ---- reading a field's type ---------------------------------------------------------------

def unwrap(t: Any, meta: tuple | list = ()) -> tuple[Any, list]:
    """A field's type without Annotated (and without Optional, as edit_model has it), and
    everything that was annotated on it, flattened: (float, [Ge(0), Le(60), LIVE])."""
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
    """A field's type in one word: int, number, bool, str, enum, player, players, mob, block,
    item, particle, sound, place, command, or "list of <kind>"; "?" for none of these."""
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
        return "values"
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


def is_live(meta: tuple | list) -> bool:
    return any(m is LIVE for m in meta)


def type_problem(t: Any, meta: tuple | list = ()) -> str | None:
    """None when a field's type is one of this file's: bool, int, float, str, Literal[...],
    list[...] of these, Player, Players, MobId, BlockId, ItemId, ParticleId, SoundId, Place,
    Command or CopyName, Live[...] of any, with Field() limits; or dict[str, Any], the values
    an edit changes, checked against the copy's own action. Else what is not."""
    while get_origin(t) is Annotated:
        args = get_args(t)
        t, meta = args[0], list(meta) + list(args[1:])
    for m in meta:
        if isinstance(m, FieldInfo):
            m = None if all(_constraint(x) for x in m.metadata) else m
        if m is None or m is LIVE or isinstance(m, Kind) or _constraint(m):
            continue
        if getattr(m, "func", None) in _OURS:
            continue
        return "%s is not one of actions/_types.py's types: it carries %r" % (_name(t), m)
    if t in (bool, int, float, str):
        return None
    if get_origin(t) is Literal:
        bad = [a for a in get_args(t) if not isinstance(a, (str, int, bool))]
        return "a Literal of words or numbers, not %r" % bad[0] if bad else None
    if get_origin(t) is list:
        args = get_args(t)
        return type_problem(args[0]) if args else "list[...] says what it holds: list[Player]"
    if get_origin(t) is dict:
        return None if get_args(t) == (str, Any) else "a dict is dict[str, Any], the values an edit changes"
    return "%s is not one of actions/_types.py's types" % _name(t)


def _constraint(m: object) -> bool:
    """Field()'s limits and lengths, and a pattern: allowed on any field."""
    return isinstance(m, (annotated_types.BaseMetadata, StringConstraints)) \
        or type(m).__name__ == "_PydanticGeneralMetadata"


def _name(t: Any) -> str:
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
    not fit is left for pydantic to refuse, with the field's name."""
    t, meta = unwrap(t, meta)
    if raw is None:
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
    """Values with their types, limits and descriptions: a preset's, and the base of every action.

    model_validate(data, context={"notes": [], "places": {...}}): words read as their types,
    numbers clamped with a note in context["notes"], a Place checked against context["places"]
    (only for the keys in context["given"], when that is there). context["clamp"] False
    refuses a number out of range instead."""

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
    """One thing players can ask for: the script it runs and the values it takes. No code.

    The class docstring's first line is the action's line on the menu, in players' words.
    ClassVars, set without an annotation in an action (script = "railgun"):
      script     the folder under scripts/ (or made/<server>/scripts/) it runs
      runs       "once" (it ends by itself; its result() is the answer) or "until_stopped"
      takes      the fields the planner fills, the target first; the rest keep their default
      defaults   this action's own defaults for fields it inherits; still given and edited
      fixed      values this action sets; never given to a run, an edit may still change them
      timeout_s  for a once action slower than VFG_DISPATCH_ACTION_TIMEOUT_S
    A variant subclasses an action: it inherits takes (or replaces them), and its defaults
    and fixed merge over its parent's (a key it puts in defaults is no longer fixed). The
    merged values are each field's default, so GatlingGun().fire is "hold".
    """

    script: ClassVar[str] = ""
    runs: ClassVar[str | None] = None
    takes: ClassVar[list[str]] = []
    defaults: ClassVar[dict[str, Any]] = {}
    fixed: ClassVar[dict[str, Any]] = {}
    timeout_s: ClassVar[int | None] = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _merge(cls)


def _merge(cls: type[Action]) -> None:
    """Merge defaults and fixed down from the class's parents, and make them its fields'
    defaults. Kept in cls.__vfg__: each field's default as declared, after defaults
    ("unfixed"), and the class's own defaults and fixed as written."""
    own_defaults, own_fixed = dict(vars(cls).get("defaults") or {}), dict(vars(cls).get("fixed") or {})
    defaults, fixed = {}, {}
    for k in reversed(cls.__mro__):
        if k is cls:
            mine = (own_defaults, own_fixed)
        else:
            mine = getattr(k, "__vfg__", {}).get("own", ({}, {})) if "__vfg__" in vars(k) else ({}, {})
        for key, v in mine[0].items():
            defaults[key] = v
            fixed.pop(key, None)
        for key, v in mine[1].items():
            fixed[key] = v
            defaults.pop(key, None)
    declared = {}
    parents = [vars(k)["__vfg__"]["declared"] for k in cls.__mro__[1:] if "__vfg__" in vars(k)]
    own = vars(cls).get("__annotations__", {})
    for name, f in cls.__pydantic_fields__.items():
        inherited = next((p[name] for p in parents if name in p), _UNSET)
        declared[name] = inherited if name not in own and inherited is not _UNSET else \
            f.get_default(call_default_factory=True)
    unfixed = {n: defaults.get(n, d) for n, d in declared.items()}
    changed = False
    for name, f in list(cls.__pydantic_fields__.items()):
        want = fixed.get(name, unfixed[name])
        if f.default_factory is not None or f.default is not want:
            g = copy(f)
            g.default, g.default_factory = want, None
            cls.__pydantic_fields__[name] = g
            changed = True
    cls.defaults, cls.fixed = defaults, fixed
    cls.__vfg__ = {"declared": declared, "unfixed": unfixed, "own": (own_defaults, own_fixed)}
    if changed:
        cls.model_rebuild(force=True)


_UNSET = object()


# ---- presets ----------------------------------------------------------------------------------

_PC_ONLY = ("__future__", "actions", "pydantic", "typing", "annotated_types")
_presets: dict[Path, tuple[tuple, type]] = {}


def camel(name: str) -> str:
    """gatling_gun -> GatlingGun: the class a file holds."""
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))


def preset(name: str) -> type[Values]:
    """The values class of preset `name`, class <CamelName>(Values) in scripts/_presets/<name>.py
    (for a made action, else in made/<server>/presets/<name>.py), to inherit:

        class Horde(Action, preset("horde_wave")): ...

    Only the class is run, with the file's imports from actions, pydantic and typing: the
    rest of a preset imports mc and the kit, which run on EC2 and in the script tests."""
    caller = sys._getframe(1).f_globals.get("__file__")
    roots = _preset_roots(Path(caller).resolve() if caller else None)
    for root in roots:
        path = root / (name + ".py")
        if re.match(r"^[a-z][a-z0-9_]{0,47}$", name) and path.is_file():
            return _preset_class(path, name)
    raise ImportError("no preset %r in %s" % (name, " or ".join(str(r) for r in roots)))


def _preset_roots(caller: Path | None) -> list[Path]:
    if caller is not None and caller.parent.name == "actions":
        if len(caller.parents) > 3 and caller.parents[2].name == "made":
            return [caller.parents[3] / "scripts" / "_presets", caller.parents[1] / "presets"]
        return [caller.parents[1] / "scripts" / "_presets"]
    return [REPO / "scripts" / "_presets"]


def _preset_class(path: Path, name: str) -> type[Values]:
    st = path.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _presets.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] in _PC_ONLY:
            keep.append(node)
        elif isinstance(node, ast.Import) and all(a.name.split(".")[0] in _PC_ONLY for a in node.names):
            keep.append(node)
        elif isinstance(node, ast.ClassDef) and any(_base_name(b) == "Values" for b in node.bases):
            keep.append(node)
    made = path.parent.name != "_presets"
    modname = "vfg_made__%s__preset__%s" % (path.parents[1].name, name) if made else "vfg_preset__" + name
    mod = types.ModuleType(modname)
    mod.__file__ = str(path)
    sys.modules[modname] = mod
    try:
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), mod.__dict__)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    cls = mod.__dict__.get(camel(name))
    if not (isinstance(cls, type) and issubclass(cls, Values)):
        raise ImportError("%s has no class %s(Values)" % (path, camel(name)))
    _presets[path] = (stamp, cls)
    return cls


def _base_name(node: ast.expr) -> str:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
