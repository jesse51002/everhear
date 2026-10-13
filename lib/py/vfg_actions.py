"""The actions on the PC: load them, check them and their values, render them, and keep
~/.vfg/actions.json.

Every action is one of the game engine's operations on the brain's menu (the game engine spec):
one pydantic class in actions/<name>.py, named as its file in CamelCase, whose ClassVar `engine`
names the control op its values go to; actions/_types.py has the Action base and the value types,
actions/README.md how to write one. vfg and the brain go through here:

    cat = Catalog(repo)                       every action; cat.refresh() reloads what changed
    cat.names(), cat.get(name), cat.file(name), cat.errors()
    check_action(name, cls)                   what `vfg action check` says; [] when it is fine
    engine_op(cls)                            the control op an action's values go to
    check_values(cls, {"player": "Steve"})    -> Checked(values, notes)
    menu_line(name, cls), describe(name, cls)
    cache(repo, home)                         home/actions.json, built again when it is stale

Loading. The repo goes first on sys.path and an action is imported as actions.<name>. refresh()
looks at every file's mtime and size, and loads everything again when one changed. A file that
will not import, holds no class named as the file in CamelCase (or more than one action class),
or fails check_action is logged and left out, with why in errors().

The values: a scenario's or a mark's args (Args) go to the engine as an object, which checks them
against the class's own Args; a rewritten Config (Python) is text whole, its lines and comments
kept; every other value is read as its field's type (actions/_types.py) and checked here.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ENGINE_OP_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _types(cls=None):
    """actions/_types.py: the one `cls` was made with (it is loaded again when it changes),
    else the one `import actions._types` gives."""
    for k in getattr(cls, "__mro__", ()):
        if k.__name__ == "Values" and k.__module__.endswith("_types") and k.__module__ in sys.modules:
            return sys.modules[k.__module__]
    return importlib.import_module("actions._types")


# ------------------------------------------------------------------ loading

class Catalog:
    """Every action of a repo: actions/*.py."""

    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve()
        self._classes: dict[str, type] = {}
        self._files: dict[str, Path] = {}
        self._errors: dict[str, str] = {}
        self._stamps: dict[Path, tuple] = {}
        self._loaded = False
        self.refresh()

    def names(self) -> list[str]:
        return sorted(self._classes)

    def get(self, name: str):
        return self._classes.get(name)

    def file(self, name: str) -> Path:
        return self._files[name]

    def errors(self) -> dict[str, str]:
        """{file stem: why it was left out}."""
        return dict(self._errors)

    def refresh(self) -> list[str]:
        """Load every file again when one changed since the last load (the first time: all of
        them). Returns the names that are new, gone or loaded again."""
        stamps = _stamps(self.repo / "actions")
        if self._loaded and stamps == self._stamps:
            return []
        before = dict(self._classes)
        self._load(fresh_types=self._loaded and stamps.get(self._types_py) != self._stamps.get(self._types_py))
        self._stamps, self._loaded = stamps, True
        return sorted(n for n in set(before) | set(self._classes) if before.get(n) is not self._classes.get(n))

    @property
    def _types_py(self) -> Path:
        return self.repo / "actions" / "_types.py"

    def _load(self, fresh_types: bool) -> None:
        _use_repo(self.repo)
        importlib.invalidate_caches()
        stems = sorted(p.stem for p in (self.repo / "actions").glob("*.py") if not p.name.startswith("_"))
        for key in ["actions." + n for n in set(stems) | set(self._classes)] + (["actions._types"] if fresh_types else []):
            sys.modules.pop(key, None)
        self._classes, self._files, self._errors = {}, {}, {}
        for stem in stems:
            try:
                module = importlib.import_module("actions." + stem)
            except Exception as e:  # noqa: BLE001 — one broken action never stops the rest
                self._fail(stem, "failed to import: %s: %s" % (type(e).__name__, e))
                continue
            self._take(stem, module, self.repo / "actions" / (stem + ".py"))

    def _take(self, stem: str, module, path: Path) -> None:
        T = _types()
        if not NAME_RE.match(stem):
            return self._fail(stem, "%s is not an action name: a-z, 0-9 and _, starting with a letter" % stem)
        mine = [v for v in vars(module).values()
                if isinstance(v, type) and issubclass(v, T.Action) and v.__module__ == module.__name__]
        cls = getattr(module, T.camel(stem), None)
        if cls not in mine:
            return self._fail(stem, "no class %s(Action) in it, named as the file" % T.camel(stem))
        if len(mine) > 1:
            return self._fail(stem, "one action class per file, not %s" % ", ".join(sorted(c.__name__ for c in mine)))
        problems = check_action(stem, cls)
        if problems:
            return self._fail(stem, "; ".join(problems))
        self._classes[stem], self._files[stem] = cls, path

    def _fail(self, stem: str, why: str) -> None:
        logger.warning("action %s left out: %s", stem, why)
        self._errors[stem] = why


def _stamps(folder: Path) -> dict[Path, tuple]:
    out = {}
    if not folder.is_dir():
        return out
    for p in folder.glob("*.py"):
        if p.name == "__init__.py":
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out[p] = (st.st_mtime_ns, st.st_size)
    return out


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

def engine_op(cls) -> str | None:
    """The engine's control op an action's values go to (grant, start, bind, config ...), or
    None for a class that names none."""
    op = getattr(cls, "engine", None)
    return op if isinstance(op, str) and op else None


def python_fields(cls) -> list[str]:
    """The fields of `cls` that hold Python source (actions/_types.py's Python)."""
    T = _types(cls)
    return [k for k, f in getattr(cls, "model_fields", {}).items() if T.kind(f.annotation, f.metadata) == "python"]


def args_fields(cls) -> list[str]:
    """The fields of `cls` that hold a scenario's or a mark's args (actions/_types.py's Args)."""
    T = _types(cls)
    return [k for k, f in getattr(cls, "model_fields", {}).items() if T.kind(f.annotation, f.metadata) == "args"]


def check_action(name: str, cls) -> list[str]:
    """What is wrong with an action, in words, as `vfg action check <name>` prints it; [] when
    it is fine."""
    T = _types(cls)
    if not (isinstance(cls, type) and issubclass(cls, T.Action)):
        return ["%s is not an Action" % getattr(cls, "__name__", cls)]
    out = []
    if not NAME_RE.match(name):
        out.append("%s is not an action name: a-z, 0-9 and _, starting with a letter" % name)
    elif cls.__name__ != T.camel(name):
        out.append("the class is named %s; in %s.py it is %s" % (cls.__name__, name, T.camel(name)))
    doc = inspect.cleandoc(vars(cls).get("__doc__") or "")
    if not doc.split("\n", 1)[0].strip():
        out.append("no docstring: its first line is the action's line on the menu, in players' words")
    op = engine_op(cls)
    if op is None:
        out.append("engine names none of the engine's control ops: every action is one (engine = \"grant\")")
    elif not ENGINE_OP_RE.match(op):
        out.append("engine is the engine's control op its values go to, not %r" % (op,))
    if cls.timeout_s is not None and (not isinstance(cls.timeout_s, int) or cls.timeout_s <= 0):
        out.append("timeout_s is a whole number of seconds, or None")
    if not isinstance(cls.takes, list):
        return out + ["takes is a list of field names"]
    fields = cls.model_fields
    for key in cls.takes:
        if key not in fields:
            out.append("takes names %s, which is not one of its fields" % key)
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
        try:   # every default is one its own field takes
            cls.model_validate({k: f.get_default(call_default_factory=True) for k, f in fields.items()},
                               context={"notes": [], "clamp": False})
        except ValidationError as e:
            for err in e.errors(include_url=False):
                key = str(err["loc"][0]) if err["loc"] else ""
                out.append("%s=%s in its default: %s" % (key, _word(err.get("input")), _msg(err, cls)))
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
                out.append("%s.%s is code: an action holds none, the engine does" % (k.__name__, attr))
    return out


# ------------------------------------------------------------------ checking values

@dataclass
class Checked:
    values: dict           # every field: its default <- given, clamped, JSON-able
    notes: list[str] = field(default_factory=list)   # "seconds=90000 is over its max, clamped to 86400"


def check_values(cls, given: dict) -> Checked:
    """The values an op of action `cls` gets: every field, its default, then `given` (words from
    the command line, or what a model wrote). A number out of range is clamped and a note says so.
    Raises ValueError, saying what is wrong, for an unknown key or a value its field refuses."""
    if not isinstance(given, dict):
        raise ValueError("values are key=value")
    unknown = [k for k in given if k not in cls.model_fields]
    if unknown:
        raise ValueError("%s has no value %s; its values: %s" % (
            _snake(cls), ", ".join(unknown), ", ".join(cls.model_fields) or "none"))
    notes: list[str] = []
    data = {k: f.get_default(call_default_factory=True) for k, f in cls.model_fields.items()}
    data.update(given)
    try:
        model = cls.model_validate(data, context={"notes": notes})
    except ValidationError as e:
        raise ValueError("; ".join(
            ("%s=%s: %s" % (err["loc"][0], _word(err.get("input")), _msg(err, cls))) if err["loc"] else _msg(err)
            for err in e.errors(include_url=False))) from None
    return Checked(model.model_dump(mode="json"), notes)


def _msg(err: dict, cls=None) -> str:
    """pydantic's message, or for a pattern the words of what the field takes."""
    msg = err["msg"]
    f = cls.model_fields.get(str(err["loc"][0])) if cls is not None and err["loc"] else None
    if err["type"] == "string_pattern_mismatch" and f is not None:
        words = KIND_WORDS.get(_types(cls).kind(f.annotation, f.metadata))
        if words:
            return "should be " + words
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

# Each kind of value in words: what describe() says a field takes, and what a refusal says it
# should be.
KIND_WORDS = {
    "int": "a whole number", "number": "a number", "bool": "true or false", "str": "text",
    "player": "one player's exact name (letters, digits and _, at most 16)",
    "class id": "a name as ENGINE shows it (a-z, 0-9 and _, starting with a letter), such as fireball",
    "instance": "a run's name as RUNNING NOW shows it, such as scenario:horde#3",
    "args": "the class's args, each by its name as ENGINE lists them",
    "python": "Python source, every line, its indentation and its comments as written",
}


def menu_line(name: str, cls) -> str:
    """"engine_grant — Give a player one of the engine's abilities ...": the name and the
    docstring's first line."""
    return "%s — %s" % (name, inspect.cleandoc(cls.__doc__ or "").split("\n", 1)[0].strip())


def describe(name: str, cls) -> str:
    """The whole action: its line, the op it goes to, then a line per field, those it takes
    first, marked * (the brain fills it): its type, limits, default and description."""
    T = _types(cls)
    head = ["engine %s" % (engine_op(cls) or "?")]
    if cls.timeout_s:
        head.append("timeout %d s" % cls.timeout_s)
    lines = [menu_line(name, cls), " · ".join(head), "values (* = the brain fills it):"]
    order = [k for k in cls.takes if k in cls.model_fields] + [k for k in cls.model_fields if k not in cls.takes]
    for key in order:
        f = cls.model_fields[key]
        bits = [_kind_words(f, T)]
        lo, hi = T.limits(f.annotation, f.metadata)
        if lo is not None or hi is not None:
            bits[0] += " %s..%s" % ("" if lo is None else _word(lo), "" if hi is None else _word(hi))
        bits.append("default %s" % _shown(f.get_default(call_default_factory=True)))
        lines.append("%s %s (%s): %s" % ("*" if key in cls.takes else " ", key, "; ".join(bits),
                                         (f.description or "").strip()))
    return "\n".join(lines)


def _kind_words(f, T) -> str:
    k = T.kind(f.annotation, f.metadata)
    opts = T.choices(f.annotation, f.metadata)
    if k == "enum":
        return "one of " + ", ".join(_word(o) for o in opts)
    if k.startswith("list of "):
        return "a list of " + ("words from " + ", ".join(_word(o) for o in opts) if opts else k[len("list of "):])
    return KIND_WORDS.get(k, k)


def _shown(x: Any) -> str:
    """A value in a description: numbers and true/false bare, text and lists as JSON."""
    if isinstance(x, bool) or isinstance(x, (int, float)):
        return _word(x)
    return json.dumps(to_jsonable_python(x), ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ the cache

def entry(name: str, cls, file: str) -> dict:
    """One action as actions.json holds it."""
    T = _types(cls)
    fields = {}
    for key, f in cls.model_fields.items():
        lo, hi = T.limits(f.annotation, f.metadata)
        fields[key] = {"type": T.kind(f.annotation, f.metadata),
                       "default": to_jsonable_python(f.get_default(call_default_factory=True)),
                       "ge": lo, "le": hi, "choices": T.choices(f.annotation, f.metadata),
                       "description": (f.description or "").strip()}
    return {"line": menu_line(name, cls).split(" — ", 1)[1], "engine": engine_op(cls), "takes": list(cls.takes),
            "timeout_s": cls.timeout_s, "file": file, "fields": fields, "describe": describe(name, cls)}


def cache(repo: Path, home: Path, *, catalog: Catalog | None = None) -> dict:
    """home/actions.json (~/.vfg/actions.json): every action as plain JSON, for everyone who reads
    the actions without loading them:

        {"built_at": epoch, "files": [every action file, from the repo],
         "actions": {name: {"line", "engine", "takes", "timeout_s", "file", "fields": {field: {"type",
                            "default", "ge", "le", "choices", "description"}}, "describe"}},
         "errors": {file stem: why it was left out}}

    Built again when a file in actions/ is newer than it, or one was added or removed; written
    atomically under actions.lock. `catalog` (this repo's) saves loading the actions again."""
    repo, home = Path(repo).resolve(), Path(home)
    path = home / "actions.json"
    data = _read_cache(path)
    if data is not None and _fresh(data, repo):
        return data
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _flocked(home / "actions.lock"):
        data = _read_cache(path)
        if data is not None and _fresh(data, repo):
            return data
        built_at = time.time()
        if catalog is None:
            catalog = Catalog(repo)
        else:
            catalog.refresh()
        actions = {n: entry(n, catalog.get(n), str(catalog.file(n).relative_to(repo))) for n in catalog.names()}
        data = {"built_at": built_at, "files": sorted(str(p.relative_to(repo)) for p in _action_files(repo)),
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


def _action_files(repo: Path) -> list[Path]:
    return [p for p in (repo / "actions").glob("*.py") if not p.name.startswith("_")]


def _fresh(data: dict, repo: Path) -> bool:
    files = _action_files(repo)
    if sorted(str(p.relative_to(repo)) for p in files) != data["files"]:
        return False
    built = float(data["built_at"]) - 1  # a file's mtime can lag the clock by a tick
    for p in files + [repo / "actions" / "_types.py"]:
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
