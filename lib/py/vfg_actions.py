"""The actions on the PC: load them, check them and their values, render them, and keep
~/.vfg/actions.json.

An action is one pydantic class in actions/<name>.py, or in made/<server>/actions/<name>.py
for one this server's implementers made; actions/_types.py has the base classes and the value
types, actions/README.md how to write one. vfg, the dispatcher, the orchestrator's brief hook
and Jev's router all go through here:

    cat = Catalog(repo, server="main")        every action; cat.refresh() reloads what changed
    cat.names(), cat.get(name), cat.source(name), cat.file(name), cat.errors()
    check_action(name, cls)                   what `vfg action check` says; [] when it is fine
    check_values(cls, {"cooldown": "0"}, places=...)  -> Checked(values, notes, live)
    check_edit(cls, current, {"cooldown": "0"})       -> Edit(values, changed, live_only, notes)
    menu_line(name, cls), describe(name, cls, current=None)
    planner_model(cls), edit_model(cls)       the models structured() answers in
    variables(cls)                            its fields as a task's `variables`: [{name, type, ...}]
    cache(repo, server, home)                 home/actions.json, built again when it is stale

Loading. The repo goes first on sys.path and a library action is imported as actions.<name>,
so a variant, or a made action, subclasses one with `from actions.railgun import Railgun`. A
made action is run from its file as the module vfg_made__<server>__<name>, never imported by
name, so made actions do not import each other. refresh() looks at every file's mtime and size
and reloads what changed: a made action file alone; when anything in actions/ or a preset
changed, everything, since a variant holds its parent's class. A file that will not import,
holds no class named as the file in CamelCase (or more than one action class), or fails
check_action is logged and left out, with why in errors().
"""

from __future__ import annotations

import fcntl
import importlib
import inspect
import json
import logging
import os
import re
import sys
import time
import types
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from pydantic_core import to_jsonable_python

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SCRIPT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
RUNS = ("once", "until_stopped")


def _types(cls=None):
    """actions/_types.py: the one `cls` was made with (it is loaded again when it changes),
    else the one `import actions._types` gives."""
    for k in getattr(cls, "__mro__", ()):
        if k.__name__ == "Values" and k.__module__.endswith("_types") and k.__module__ in sys.modules:
            return sys.modules[k.__module__]
    return importlib.import_module("actions._types")


# ------------------------------------------------------------------ loading

class Catalog:
    """Every action of a repo: the library's (actions/*.py) and, unless server is None, this
    server's (made/<server>/actions/*.py). A made name equal to a library name is left out."""

    def __init__(self, repo: Path, server: str | None = "main"):
        self.repo = Path(repo).resolve()
        self.server = server
        self._classes: dict[str, type] = {}
        self._source: dict[str, str] = {}
        self._files: dict[str, Path] = {}
        self._lib_errors: dict[str, str] = {}
        self._made_errors: dict[str, str] = {}
        self._stamps: dict[Path, tuple] = {}
        self._loaded = False
        self.refresh()

    def names(self) -> list[str]:
        """The library's names, then this server's, each in order."""
        return sorted(self._classes, key=lambda n: (self._source[n] != "library", n))

    def get(self, name: str):
        return self._classes.get(name)

    def source(self, name: str) -> Literal["library", "made"]:
        return self._source[name]  # type: ignore[return-value]

    def file(self, name: str) -> Path:
        return self._files[name]

    def errors(self) -> dict[str, str]:
        """{file stem: why it was left out}."""
        out = dict(self._lib_errors)
        for stem, why in self._made_errors.items():
            out[stem] = out[stem] + "; " + why if stem in out else why
        return out

    def refresh(self) -> list[str]:
        """Load every file that changed since the last load (the first time: all of them).
        Returns the names that are new, gone or loaded again."""
        lib = _stamps(self.repo / "actions", underscore=True)
        lib.update(_stamps(self.repo / "scripts" / "_presets"))
        made: dict[Path, tuple] = {}
        if self.server:
            made = _stamps(self.repo / "made" / self.server / "actions")
            made.update(_stamps(self.repo / "made" / self.server / "presets"))
        old = self._stamps
        old_lib = {p: s for p, s in old.items() if not _is_made(p, self.repo)}
        old_made = {p: s for p, s in old.items() if _is_made(p, self.repo)}
        before = dict(self._classes)
        if not self._loaded or lib != old_lib:
            types_py = self.repo / "actions" / "_types.py"
            self._load_library(fresh_types=self._loaded and lib.get(types_py) != old_lib.get(types_py))
            self._load_made(None)
        elif made != old_made:
            moved = [p for p in set(made) | set(old_made) if made.get(p) != old_made.get(p)]
            self._load_made(None if any(p.parent.name == "presets" for p in moved) else moved)
        self._stamps, self._loaded = {**lib, **made}, True
        return sorted(n for n in set(before) | set(self._classes) if before.get(n) is not self._classes.get(n))

    def _load_library(self, fresh_types: bool) -> None:
        _use_repo(self.repo)
        importlib.invalidate_caches()
        stems = sorted(p.stem for p in (self.repo / "actions").glob("*.py") if not p.name.startswith("_"))
        gone = [n for n, s in self._source.items() if s == "library"]
        for key in ["actions." + n for n in set(stems) | set(gone)] + (["actions._types"] if fresh_types else []):
            sys.modules.pop(key, None)  # a variant imports its parent anew too
        for name in gone:
            self._drop(name)
        self._lib_errors = {}
        for stem in stems:
            try:
                module = importlib.import_module("actions." + stem)
            except Exception as e:  # noqa: BLE001 — one broken action never stops the rest
                self._fail(stem, "library", "failed to import: %s: %s" % (type(e).__name__, e))
                continue
            self._take(stem, module, self.repo / "actions" / (stem + ".py"), "library")

    def _load_made(self, paths: list[Path] | None) -> None:
        """Run made action files from their source: these (each changed, added or gone), or
        all of them."""
        if not self.server:
            return
        folder = self.repo / "made" / self.server / "actions"
        if paths is None:
            for name in [n for n, s in self._source.items() if s == "made"]:
                self._drop(name)
            self._made_errors = {}
            paths = sorted(folder.glob("*.py"))
        _use_repo(self.repo)
        for path in sorted(paths):
            stem = path.stem
            if stem.startswith("_") or path.parent != folder:
                continue
            if self._source.get(stem) == "made":
                self._drop(stem)
            self._made_errors.pop(stem, None)
            if not path.exists():
                continue
            if (self.repo / "actions" / (stem + ".py")).exists():
                self._fail(stem, "made", "made/%s/actions/%s.py: %s is a library action's name"
                           % (self.server, stem, stem))
                continue
            modname = "vfg_made__%s__%s" % (self.server, stem)
            module = types.ModuleType(modname)
            module.__file__ = str(path)
            sys.modules[modname] = module
            try:
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
            except Exception as e:  # noqa: BLE001
                sys.modules.pop(modname, None)
                self._fail(stem, "made", "failed to import: %s: %s" % (type(e).__name__, e))
                continue
            self._take(stem, module, path, "made")

    def _take(self, stem: str, module, path: Path, source: str) -> None:
        T = _types()
        if not NAME_RE.match(stem):
            return self._fail(stem, source, "%s is not an action name: a-z, 0-9 and _, starting with a letter" % stem)
        mine = [v for v in vars(module).values()
                if isinstance(v, type) and issubclass(v, T.Action) and v.__module__ == module.__name__]
        cls = getattr(module, T.camel(stem), None)
        if cls not in mine:
            return self._fail(stem, source, "no class %s(Action) in it, named as the file" % T.camel(stem))
        if len(mine) > 1:
            return self._fail(stem, source, "one action class per file, not %s"
                              % ", ".join(sorted(c.__name__ for c in mine)))
        problems = _check(stem, cls, self.repo, source, self.server)
        if problems:
            return self._fail(stem, source, "; ".join(problems))
        self._classes[stem], self._source[stem], self._files[stem] = cls, source, path

    def _fail(self, stem: str, source: str, why: str) -> None:
        logger.warning("action %s left out: %s", stem, why)
        (self._lib_errors if source == "library" else self._made_errors)[stem] = why

    def _drop(self, name: str) -> None:
        self._classes.pop(name, None)
        self._source.pop(name, None)
        self._files.pop(name, None)


def _stamps(folder: Path, underscore: bool = False) -> dict[Path, tuple]:
    out = {}
    if not folder.is_dir():
        return out
    for p in folder.glob("*.py"):
        if p.name == "__init__.py" or (p.name.startswith("_") and not underscore):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out[p] = (st.st_mtime_ns, st.st_size)
    return out


def _is_made(path: Path, repo: Path) -> bool:
    try:
        return path.relative_to(repo).parts[0] == "made"
    except ValueError:
        return False


def _use_repo(repo: Path) -> None:
    """The repo first on sys.path, and `actions` its own package, not another checkout's."""
    if not sys.path or sys.path[0] != str(repo):
        if str(repo) in sys.path:
            sys.path.remove(str(repo))
        sys.path.insert(0, str(repo))
    have = sys.modules.get("actions")
    if have is not None and Path(getattr(have, "__file__", "") or "").resolve().parent != repo / "actions":
        for key in [k for k in sys.modules if k == "actions" or k.startswith("actions.")]:
            del sys.modules[key]


# ------------------------------------------------------------------ checking an action

def check_action(name: str, cls) -> list[str]:
    """What is wrong with an action, in words, as `vfg action check <name>` prints it; [] when
    it is fine. Where it lives (library or made, which repo) is read from its class's file."""
    repo, source, server = _where(cls)
    return _check(name, cls, repo, source, server)


def _where(cls) -> tuple[Path | None, str, str | None]:
    module = sys.modules.get(cls.__module__)
    path = Path(getattr(module, "__file__", "") or "").resolve() if module else None
    if path and path.parent.name == "actions" and len(path.parents) > 3 and path.parents[2].name == "made":
        return path.parents[3], "made", path.parents[1].name
    if path and path.parent.name == "actions":
        return path.parents[1], "library", None
    return None, "library", None


def _check(name: str, cls, repo: Path | None, source: str, server: str | None) -> list[str]:
    T = _types(cls)
    out = []
    if not (isinstance(cls, type) and issubclass(cls, T.Action)):
        return ["%s is not an Action" % getattr(cls, "__name__", cls)]
    if not NAME_RE.match(name):
        out.append("%s is not an action name: a-z, 0-9 and _, starting with a letter" % name)
    elif cls.__name__ != T.camel(name):
        out.append("the class is named %s; in %s.py it is %s" % (cls.__name__, name, T.camel(name)))
    doc = inspect.cleandoc(vars(cls).get("__doc__") or "")
    if not doc.split("\n", 1)[0].strip():
        out.append("no docstring: its first line is the action's line on the menu, in players' words")
    if not isinstance(cls.script, str) or not SCRIPT_RE.match(cls.script):
        out.append("script is the folder of the script it runs, not %r" % (cls.script,))
    elif repo is not None:
        where = [repo / "scripts" / cls.script / "main.py"]
        if source == "made" and server:
            where.insert(0, repo / "made" / server / "scripts" / cls.script / "main.py")
        if not any(p.is_file() for p in where):
            out.append("script %s: there is no %s" % (cls.script, " or ".join(str(p.relative_to(repo)) for p in where)))
    if cls.runs not in RUNS:
        out.append("runs is once or until_stopped, not %r" % (cls.runs,))
    if cls.timeout_s is not None and (not isinstance(cls.timeout_s, int) or cls.timeout_s <= 0):
        out.append("timeout_s is a whole number of seconds, or None")
    if source == "made" and repo is not None and (repo / "actions" / (name + ".py")).exists():
        out.append("%s is a library action's name: a made action needs its own" % name)
    for attr in ("takes", "defaults", "fixed", "descriptions"):
        if not isinstance(getattr(cls, attr), list if attr == "takes" else dict):
            out.append("%s is a %s" % (attr, "list of field names" if attr == "takes" else
                                       "dict of field: words" if attr == "descriptions" else "dict of field: value"))
            return out
    fields = cls.model_fields
    own_defaults, own_fixed = cls.__vfg__["own"]
    for key in sorted(set(own_defaults) & set(own_fixed)):
        out.append("%s is in both defaults and fixed" % key)
    for attr, given in (("defaults", cls.defaults), ("fixed", cls.fixed), ("descriptions", cls.descriptions)):
        for key in given:
            if key not in fields:
                out.append("%s names %s, which is not one of its fields" % (attr, key))
    own = set(T.declares(cls)) & set(fields)
    for key, text in cls.descriptions.items():
        if key in fields and not (isinstance(text, str) and text.strip()):
            out.append("descriptions gives %s no words: write what it is, in players' words" % key)
    for key in sorted(own & set(cls.__vfg__.get("descriptions", {}))):
        out.append("descriptions names %s, which it writes itself: its description is the string under it" % key)
    for key in [k for k in fields if k in own]:
        hit = T.inherited(cls, key)
        if hit is not None and T.same_type(fields[key], hit[1]):
            out.append("%s is written again with %s's type and limits: drop the field and give its default in "
                       "defaults, its words in descriptions" % (key, hit[0].__name__))
    for key in cls.takes:
        if key not in fields:
            out.append("takes names %s, which is not one of its fields" % key)
        elif key in cls.fixed:
            out.append("takes names %s, which it fixes" % key)
    if len(set(cls.takes)) != len(cls.takes):
        out.append("takes names a field twice")
    for key, f in fields.items():
        why = T.type_problem(f.annotation, f.metadata)
        if why:
            out.append("%s: %s" % (key, why))
        if not (f.description or "").strip():
            out.append("%s has no description: write it in a string under the field" % key)
    out += _code_in(cls)
    if not out:
        # Every default, as declared or set by defaults or fixed, must be one its field takes.
        notes: list[str] = []
        try:
            cls.model_validate({k: f.get_default(call_default_factory=True) for k, f in fields.items()},
                               context={"notes": notes, "clamp": False})
        except ValidationError as e:
            for err in e.errors(include_url=False):
                key = str(err["loc"][0]) if err["loc"] else ""
                where = "fixed" if key in cls.fixed else "defaults" if key in cls.defaults else "its default"
                out.append("%s=%s in %s: %s" % (key, _word(err.get("input")), where, _msg(err, cls)))
    return out


def _code_in(cls) -> list[str]:
    """An action holds no code: no method or validator of its own."""
    T = _types(cls)
    out = []
    for k in cls.__mro__:
        if k in (T.Action, T.Values) or not issubclass(k, T.Values) or k.__module__.startswith("pydantic"):
            continue
        for attr, v in vars(k).items():
            if attr.startswith("__") or attr in ("model_config", "model_fields", "model_computed_fields"):
                continue
            if isinstance(v, (types.FunctionType, classmethod, staticmethod, property)) \
                    or type(v).__name__ == "PydanticDescriptorProxy":
                out.append("%s.%s is code: an action holds none, its script does" % (k.__name__, attr))
    return out


# ------------------------------------------------------------------ checking values

@dataclass
class Checked:
    values: dict           # every field: default <- defaults <- fixed <- given, clamped, JSON-able
    notes: list[str] = field(default_factory=list)   # "cooldown=500 is over its max, clamped to 60"
    live: list[str] = field(default_factory=list)    # the Live fields' names


@dataclass
class Edit:
    values: dict           # the full new set: current <- given
    changed: list[str] = field(default_factory=list)
    live_only: bool = True  # every changed field is Live (so also when nothing changed)
    notes: list[str] = field(default_factory=list)


def check_values(cls, given: dict, *, places: dict | None = None, allow_fixed: bool = False) -> Checked:
    """The values a run of action `cls` gets: every field, its default as the action sets it
    (defaults, fixed), then `given` (words from the command line, or what the planner
    filled). A number out of range is clamped and a note says so. A saved place is checked
    against `places` (the places cache: {name: ...}) when it is given. Raises ValueError,
    saying what is wrong, for an unknown key, a fixed one (unless allow_fixed), or a value
    its field refuses."""
    _known(cls, given)
    if not allow_fixed:
        fixed = [k for k in given if k in cls.fixed]
        if fixed:
            raise ValueError("%s: %s" % (_snake(cls), "; ".join(
                "%s is fixed at %s" % (k, _word(cls.fixed[k])) for k in fixed)))
    notes: list[str] = []
    data = {k: f.get_default(call_default_factory=True) for k, f in cls.model_fields.items()}
    data.update(given)
    model = _validate(cls, data, {"notes": notes, "places": places, "given": set(given)})
    return Checked(model.model_dump(mode="json"), notes, live_fields(cls))


def check_edit(cls, current: dict, given: dict, *, places: dict | None = None) -> Edit:
    """An edit to a running copy of `cls`: `current` (its values) with `given` over them. Any
    field may change, a fixed one too. Raises ValueError as check_values does."""
    _known(cls, given)
    notes: list[str] = []
    data = {k: f.get_default(call_default_factory=True) for k, f in cls.model_fields.items()}
    data.update({k: v for k, v in (current or {}).items() if k in cls.model_fields})
    was = dict(data)
    data.update(given)
    values = _validate(cls, data, {"notes": notes, "places": places, "given": set(given)}).model_dump(mode="json")
    changed = [k for k in cls.model_fields if k in given and values[k] != to_jsonable_python(was[k])]
    live = set(live_fields(cls))
    return Edit(values, changed, all(k in live for k in changed), notes)


def live_fields(cls) -> list[str]:
    T = _types(cls)
    return [k for k, f in cls.model_fields.items() if T.is_live(f.metadata)]


def _known(cls, given: dict) -> None:
    if not isinstance(given, dict):
        raise ValueError("values are key=value")
    unknown = [k for k in given if k not in cls.model_fields]
    if unknown:
        raise ValueError("%s has no value %s; its values: %s" % (
            _snake(cls), ", ".join(unknown), ", ".join(cls.model_fields) or "none"))


def _validate(cls, data: dict, context: dict):
    try:
        return cls.model_validate(data, context=context)
    except ValidationError as e:
        raise ValueError("; ".join(
            ("%s=%s: %s" % (err["loc"][0], _word(err.get("input")), _msg(err, cls))) if err["loc"] else _msg(err)
            for err in e.errors(include_url=False))) from None


def _msg(err: dict, cls=None) -> str:
    """pydantic's message, or for a pattern the words of what the field takes."""
    msg = err["msg"]
    f = cls.model_fields.get(str(err["loc"][0])) if cls is not None and err["loc"] else None
    if err["type"] == "string_pattern_mismatch" and f is not None:
        k = _types(cls).kind(f.annotation, f.metadata)
        words = KIND_WORDS.get(k[len("list of "):] if k.startswith("list of ") else k)
        if words:
            return ("each should be " if k.startswith("list of ") else "should be ") + words
    return msg[len("Value error, "):] if msg.startswith("Value error, ") else msg


def _snake(cls) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()


def _word(x: Any) -> str:
    """A value as key=<this> carries it: true/false, whole numbers without .0, lists as JSON."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return "%d" % x
    if isinstance(x, str):
        return x
    try:
        return json.dumps(to_jsonable_python(x), ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        return repr(x)


# ------------------------------------------------------------------ rendering

# Each kind of value in words: what describe() says a field takes, and what a refusal says
# it should be.
KIND_WORDS = {
    "int": "a whole number", "number": "a number", "bool": "true or false", "str": "text",
    "player": "a player's name", "players": "player names separated by commas, or @a, @r, @p",
    "place": "a saved place's name, x y z (the_nether or the_end after it), or a player's name",
    "mob": "a mob id, such as zombie", "block": "a block id, such as stone or #logs",
    "item": "an item id, such as diamond_sword", "particle": "a particle id, such as flame",
    "sound": "a sound id, such as entity.wither.spawn", "command": "a server command, no slash",
    "item or empty": "an item id, such as diamond_sword, or empty for none",
    "block mix": "a block id or a mix, such as 70% stone_bricks 30% cobblestone",
    "replace rules": 'rules in order, each "from > to", such as solid > 70% lava 30% keep',
    "replace rule": 'a rule "from > to", such as solid > 70% lava 30% keep',
    "item name": "the name of an item a script gave, such as Spirit Bomb, or empty for none",
}
LIST_WORDS = {
    "int": "whole numbers", "number": "numbers", "bool": "trues and falses", "str": "texts",
    "player": "player names", "players": "player names", "place": "places", "mob": "mob ids",
    "block": "block ids", "item": "item ids", "particle": "particle ids", "sound": "sound ids",
    "command": "server commands", "item or empty": "item ids", "block mix": "blocks or mixes",
    "replace rule": "rules", "item name": "names of items a script gave, such as Spirit Bomb",
}


def menu_line(name: str, cls) -> str:
    """"railgun — Give a player a railgun: ...": the name and the docstring's first line."""
    return "%s — %s" % (name, inspect.cleandoc(cls.__doc__ or "").split("\n", 1)[0].strip())


def describe(name: str, cls, current: dict | None = None) -> str:
    """The whole action, for a brief and for the edit planner: its line, its script and how
    it runs, then a line per field, those the planner fills first, marked *: its type, limits,
    live or not, its default (fixed; or its value now, when `current` is given) and its
    description."""
    T = _types(cls)
    head = ["script %s" % cls.script, "runs %s" % (cls.runs or "?").replace("_", " ")]
    if cls.timeout_s:
        head.append("timeout %d s" % cls.timeout_s)
    lines = [menu_line(name, cls), " · ".join(head), "values (* = the planner fills it):"]
    order = [k for k in cls.takes if k in cls.model_fields] + [k for k in cls.model_fields if k not in cls.takes]
    for key in order:
        f = cls.model_fields[key]
        bits = [_kind_words(f, T)]
        lo, hi = T.limits(f.annotation, f.metadata)
        if lo is not None or hi is not None:
            bits[0] += " %s..%s" % ("" if lo is None else _word(lo), "" if hi is None else _word(hi))
        if T.is_live(f.metadata):
            bits.append("live")
        default = f.get_default(call_default_factory=True)
        if current is not None:
            bits.append("now %s" % _shown(current.get(key, default)))
        elif key in cls.fixed:
            bits.append("fixed %s" % _shown(default))
        else:
            bits.append("default %s" % _shown(default))
        lines.append("%s %s (%s): %s" % ("*" if key in cls.takes else " ", key, "; ".join(bits),
                                         (f.description or "").strip()))
    return "\n".join(lines)


def _kind_words(f, T) -> str:
    k = T.kind(f.annotation, f.metadata)
    opts = T.choices(f.annotation, f.metadata)
    if k == "enum":
        return "one of " + ", ".join(_word(o) for o in opts)
    if k.startswith("list of "):
        item = k[len("list of "):]
        return "a list of " + ("words from " + ", ".join(_word(o) for o in opts) if opts else LIST_WORDS.get(item, item))
    return KIND_WORDS.get(k, k)


def _shown(x: Any) -> str:
    """A value in a description: numbers and true/false bare, text and lists as JSON."""
    if isinstance(x, bool) or isinstance(x, (int, float)):
        return _word(x)
    return json.dumps(to_jsonable_python(x), ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ models for structured()

_planners: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_editors: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


_bases: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _base(cls):
    """The base of the models below: Values' reading (words, clamping), without looking for
    attribute docstrings in source they do not have."""
    T = _types(cls)
    hit = _bases.get(T.Values)
    if hit is None:
        hit = create_model("Made", __base__=T.Values, __module__=__name__)
        hit.model_config = ConfigDict(extra="forbid", use_attribute_docstrings=False)
        hit.model_rebuild(force=True)
        _bases[T.Values] = hit
    return hit


def planner_model(cls) -> type[BaseModel]:
    """A model of only the fields `cls` takes, with their types, defaults, limits, patterns and
    descriptions, all in its JSON schema: what the planner answers in. A number out of range
    in its answer is clamped. Its model_dump(exclude_unset=True) goes to check_values."""
    hit = _planners.get(cls)
    if hit is None:
        spec = {}
        for key in cls.takes:
            f = cls.model_fields[key]
            spec[key] = (_annotated(f), Field(f.get_default(call_default_factory=True), description=f.description))
        hit = create_model(cls.__name__, __base__=_base(cls), __doc__=cls.__doc__, __module__=__name__, **spec)
        _planners[cls] = hit
    return hit


def edit_model(cls) -> type[BaseModel]:
    """A model of every field of `cls`, each optional (null: not changed), with its type,
    limits, patterns and description: what the edit planner answers in."""
    hit = _editors.get(cls)
    if hit is None:
        spec = {key: (Optional[_annotated(f)], Field(None, description=f.description))
                for key, f in cls.model_fields.items()}
        hit = create_model(cls.__name__ + "Edit", __base__=_base(cls), __doc__=cls.__doc__, __module__=__name__, **spec)
        _editors[cls] = hit
    return hit


def _annotated(f) -> Any:
    return Annotated[(f.annotation, *f.metadata)] if f.metadata else f.annotation


# ------------------------------------------------------------------ the cache

def entry(name: str, cls, source: str, file: str) -> dict:
    """One action as actions.json holds it."""
    T = _types(cls)
    fields = {}
    for key, f in cls.model_fields.items():
        lo, hi = T.limits(f.annotation, f.metadata)
        fields[key] = {"type": T.kind(f.annotation, f.metadata),
                       "default": to_jsonable_python(f.get_default(call_default_factory=True)),
                       "ge": lo, "le": hi, "choices": T.choices(f.annotation, f.metadata),
                       "live": T.is_live(f.metadata), "description": (f.description or "").strip()}
    return {"line": menu_line(name, cls).split(" — ", 1)[1], "script": cls.script, "runs": cls.runs,
            "takes": list(cls.takes), "fixed": to_jsonable_python(cls.fixed),
            "defaults": to_jsonable_python(cls.defaults), "descriptions": dict(cls.descriptions),
            "timeout_s": cls.timeout_s, "source": source,
            "file": file, "fields": fields, "describe": describe(name, cls)}


def variables(cls) -> list[dict]:
    """The action's fields as a task's `variables` (Prototype_Named_Tasks): one per field, those it
    takes first, then the rest in the class's order, each with the keys actions.json's fields have,
    plus its name and whether it is fixed."""
    T = _types(cls)
    order = [k for k in cls.takes if k in cls.model_fields] + [k for k in cls.model_fields if k not in cls.takes]
    out = []
    for key in order:
        f = cls.model_fields[key]
        lo, hi = T.limits(f.annotation, f.metadata)
        out.append({"name": key, "type": T.kind(f.annotation, f.metadata),
                    "default": to_jsonable_python(f.get_default(call_default_factory=True)),
                    "ge": lo, "le": hi, "choices": to_jsonable_python(T.choices(f.annotation, f.metadata)),
                    "live": T.is_live(f.metadata), "fixed": key in cls.fixed,
                    "description": (f.description or "").strip()})
    return out


def cache(repo: Path, server: str, home: Path, *, catalog: Catalog | None = None) -> dict:
    """home/actions.json (~/.vfg/actions.json): every action as plain JSON, for everyone who
    reads the actions without loading them:

        {"built_at": epoch, "files": [every action file, from the repo],
         "actions": {name: {"line", "script", "runs", "takes", "fixed", "defaults", "descriptions",
                            "timeout_s", "source", "file", "fields": {field: {"type", "default", "ge",
                            "le", "choices", "live", "description"}}, "describe"}},
         "errors": {file stem: why it was left out}}

    Built again when a file in actions/, made/<server>/actions/ or a presets folder is newer
    than it, or one was added or removed; written atomically under actions.lock. `catalog`
    (this repo's and server's) saves loading the actions again."""
    repo, home = Path(repo).resolve(), Path(home)
    path = home / "actions.json"
    data = _read_cache(path)
    if data is not None and _fresh(data, repo, server):
        return data
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _flocked(home / "actions.lock"):
        data = _read_cache(path)
        if data is not None and _fresh(data, repo, server):
            return data
        built_at = time.time()
        if catalog is None:
            catalog = Catalog(repo, server)
        else:
            catalog.refresh()
        actions = {n: entry(n, catalog.get(n), catalog.source(n), str(catalog.file(n).relative_to(repo)))
                   for n in catalog.names()}
        data = {"built_at": built_at, "files": sorted(str(p.relative_to(repo)) for p in _action_files(repo, server)),
                "actions": actions, "errors": catalog.errors()}
        tmp = path.with_name("actions.json.tmp.%d" % os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
    return data


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        float(data["built_at"])
        if not isinstance(data["actions"], dict) or not isinstance(data.get("files"), list):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _action_files(repo: Path, server: str | None) -> list[Path]:
    out = [p for p in (repo / "actions").glob("*.py") if not p.name.startswith("_")]
    if server:
        out += [p for p in (repo / "made" / server / "actions").glob("*.py") if not p.name.startswith("_")]
    return out


def _fresh(data: dict, repo: Path, server: str | None) -> bool:
    files = _action_files(repo, server)
    if sorted(str(p.relative_to(repo)) for p in files) != data["files"]:
        return False
    deps = files + [repo / "actions" / "_types.py"] + list((repo / "scripts" / "_presets").glob("*.py"))
    if server:
        deps += list((repo / "made" / server / "presets").glob("*.py"))
    built = float(data["built_at"]) - 1  # a file's mtime can lag the clock by a tick
    for p in deps:
        try:
            if p.stat().st_mtime > built:
                return False
        except OSError:
            continue
    return True


class _flocked:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
