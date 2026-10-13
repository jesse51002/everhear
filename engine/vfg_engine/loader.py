"""Loading content files into the registry (the game engine spec, "Live updates").

`engine reload` imports each file as a fresh module, under a name of its own
(`vfg_content.<stem>_<n>`), never with importlib.reload, which leaves old references behind.
Every class the file defines then goes through the checks (vfg_engine.checks); only those that
pass are offered to the registry, and a file that fails to import offers nothing, so whatever was
loaded from it before keeps running.

A class's id is its name in snake_case (FireStaff -> fire_staff) unless it sets `id = "..."`.
One file may hold several classes; the implementer's convention is one class per file, named for
its id (made/<server>/content/<id>.py).
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import os
import re
import sys
from dataclasses import dataclass, field

from . import checks
from .config import BaseConfig

IMPORT_LIMIT_MS = 2000.0     # a file's top level gets this long (pydantic builds its models then)
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")   # as every vfg command and engine action takes an id
_counter = itertools.count(1)


@dataclass
class ClassInfo:
    id: str
    kind: str
    cls: type
    name: str                    # the Python class name
    file: str
    module: str
    config: BaseConfig
    loaded_at: float = 0.0
    damage: dict[int, float] | None = None   # damage(ctx) at power 0, 10, 20


@dataclass
class Loaded:
    file: str
    module: str | None = None
    classes: list[ClassInfo] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)      # {file, check, error, class?}


def content_files(folders) -> list[str]:
    """Every content file in the folders: *.py, not starting with _ or ., sorted."""
    out = []
    for folder in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if name.endswith(".py") and not name.startswith(("_", ".")):
                out.append(os.path.abspath(os.path.join(folder, name)))
    return out


def load_file(path: str, guard, now: float) -> Loaded:
    path = os.path.abspath(path)
    out = Loaded(path)
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError as exc:
        out.errors.append({"file": path, "check": "imports", "error": "cannot read it: %s" % exc})
        return out
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        out.errors.append({"file": path, "check": "imports",
                           "error": "SyntaxError: %s (line %s)" % (exc.msg, exc.lineno)})
        return out

    stem = re.sub(r"\W", "_", os.path.splitext(os.path.basename(path))[0])
    name = "vfg_content.%s_%d" % (stem, next(_counter))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # pydantic resolves the annotations of the file's models here

    # compiled from the text just read, never from a cached .pyc: a file rewritten within the same
    # second at the same size would otherwise run its old code
    code = compile(source, path, "exec", dont_inherit=True)
    result = guard.call("import %s" % os.path.basename(path), exec, code, module.__dict__,
                        cut_ms=IMPORT_LIMIT_MS)
    if not result.ok:
        sys.modules.pop(name, None)
        out.errors.append({"file": path, "check": "imports", "error": result.error})
        return out
    out.module = name

    def call(fn):
        r = guard.call("check %s" % os.path.basename(path), fn)
        return (True, r.value) if r.ok else (False, r.error)

    found = [v for v in vars(module).values() if checks.is_content(v, name)]
    if not found:
        out.errors.append({"file": path, "check": "base",
                           "error": "no content class in it (a subclass of Item, Ability, Mark, Actor or Scenario)"})
    for cls in found:
        info, error = _check(cls, tree, path, name, call, now)
        if error:
            out.errors.append({"file": path, "class": cls.__name__, "id": getattr(cls, "id", None),
                               "check": error[0], "error": error[1]})
        else:
            out.classes.append(info)
    return out


def _check(cls, tree, path, module, call, now):
    why = checks.check_base(cls)
    if why:
        return None, ("base", why)
    kind = checks.kind_of(cls)
    cid = cls.id
    if not isinstance(cid, str) or not ID_RE.match(cid):
        return None, ("base", "%s's id %r must start with a letter, then up to 63 of a-z, 0-9 and _"
                      % (cls.__name__, cid))
    why = checks.check_code(cls, tree)
    if why:
        return None, ("code", why)
    ok, made = call(cls.State)
    if not ok:
        words = " ".join(str(made).split()).split(" For further information")[0]
        return None, ("state", "%s.State() cannot be made from its defaults alone (every State field "
                               "needs a default): %s" % (cls.__name__, words))
    config, error = checks.check_config(cls)
    if error:
        return None, error
    why = checks.check_duration(config, call)
    if why:
        return None, ("bounds", why)
    damage = None
    if "damage" in type(config).REQUIRED:
        why = checks.check_damage(config, call)
        if why:
            return None, ("damage", why)
        damage, _ = checks.damage_at(config, call)
    if kind == "item":
        why = checks.check_triggers(config, checks.class_node(tree, cls.__name__))
        if why:
            return None, ("triggers", why)
    if kind == "actor":
        why = checks.check_entity_type(config.entity_type)
        if why:
            return None, ("config", why)
    return ClassInfo(id=cid, kind=kind, cls=cls, name=cls.__name__, file=path, module=module,
                     config=config, loaded_at=now, damage=damage), None
