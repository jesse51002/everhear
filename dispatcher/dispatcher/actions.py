"""Loads the actions: one pydantic class per `actions/<name>/action.py`, keyed by its name.

The repo root goes on sys.path and `actions.<name>.action` is imported for every folder
that has an action.py. The class is the one whose `action` field is `Literal["<name>"]`.
A folder that will not import is logged and left out; the dispatcher runs without it.

Also here: the scripts in `scripts/`, each by its main.py's docstring's first line and one
line per entry of its `VALUES` (its README's first three lines, for an old script with no
`VALUES`), which the action planner sees for `run_script` and `stop_script`. A script is
read, never run: scripts run on EC2, not here.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
import typing
from pathlib import Path

from pydantic import BaseModel

from .config import log

Catalog = dict[str, type[BaseModel]]


def load(repo: Path, package: str = "actions") -> Catalog:
    root = repo / package
    if not root.is_dir():
        log("actions", f"no {root}: no actions loaded")
        return {}
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    catalog: Catalog = {}
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(("_", ".")) or not (folder / "action.py").is_file():
            continue
        try:
            module = importlib.import_module(f"{package}.{folder.name}.action")
        except Exception as e:  # noqa: BLE001 — one broken action never stops the rest
            log("actions", f"{folder.name}: failed to import: {type(e).__name__}: {e}")
            continue
        cls = find_class(module, folder.name)
        if cls is None:
            log("actions", f"{folder.name}: no class with action: Literal[{folder.name!r}]")
            continue
        catalog[folder.name] = cls
    log("actions", f"loaded {len(catalog)}: {', '.join(catalog) or 'none'}")
    return catalog


def find_class(module, name: str) -> type[BaseModel] | None:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseModel) and obj.__module__ == module.__name__ and action_name(obj) == name:
            return obj
    return None


def action_name(cls: type[BaseModel]) -> str | None:
    field = cls.model_fields.get("action")
    if field is None or typing.get_origin(field.annotation) is not typing.Literal:
        return None
    args = typing.get_args(field.annotation)
    return args[0] if len(args) == 1 else None


def doc(cls: type[BaseModel]) -> str:
    return inspect.cleandoc(cls.__doc__ or "")


def hidden_commands(repo: Path, package: str = "actions") -> frozenset[str]:
    """The commands the drafting model never sees in the list: those that place or remove
    blocks and the owner's, from actions/_types.py (BLOCK_COMMANDS, REFUSED)."""
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        types = importlib.import_module(f"{package}._types")
        return frozenset(types.BLOCK_COMMANDS) | frozenset(types.REFUSED)
    except Exception as e:  # noqa: BLE001 — without _types there is no command action either
        log("actions", f"{package}/_types.py gives no BLOCK_COMMANDS and REFUSED ({type(e).__name__}: {e})")
        return frozenset()


def menu_line(cls: type[BaseModel]) -> str:
    """The docstring's first line: all the complexity selector sees of an action."""
    return doc(cls).split("\n", 1)[0].strip()


def scripts(repo: Path) -> dict[str, str]:
    """{name: what it does} for every script folder with a main.py.

    The first line says what it does; each line after it is indented. A script with
    `VALUES`: its docstring's first line (`<Title>: what it does.`), then one line per value,
    `key (type, default …, min..max or one of …)`, the keys exactly as its VALUES has them.
    An old script with no `VALUES`: its README's first three non-blank lines (what it does,
    its usage, how it ends), else its docstring's."""
    out = {}
    root = repo / "scripts"
    if not root.is_dir():
        return out
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(("_", ".")) or not (folder / "main.py").is_file():
            continue
        out[folder.name] = describe_script(folder)
    return out


def describe_script(folder: Path) -> str:
    tree = parse(folder / "main.py")
    doc_lines = [x.strip() for x in docstring_of(tree).splitlines() if x.strip()]
    values = script_values(tree)
    if values is not None:
        lines = [doc_lines[0] if doc_lines else "(no description)",
                 *([value_line(k, e) for k, e in values.items()] or ["(takes no values)"])]
    else:
        try:
            readme = (folder / "README.md").read_text(encoding="utf-8").splitlines()
        except OSError:
            readme = []
        lines = [x.strip() for x in readme[:3] if x.strip()] or doc_lines[:3] or ["(no description)"]
    return "\n  ".join(lines)


def parse(path: Path) -> ast.Module | None:
    """A script's module, parsed without running it (scripts run on EC2, not here)."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None


def docstring_of(tree: ast.Module | None) -> str:
    return (ast.get_docstring(tree) or "") if tree is not None else ""


def script_values(tree: ast.Module | None) -> dict[str, dict] | None:
    """The module's top-level `VALUES = {...}`, by ast.literal_eval; None when there is none,
    or it is not a literal dict of entries."""
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            named = any(isinstance(t, ast.Name) and t.id == "VALUES" for t in node.targets)
        elif isinstance(node, ast.AnnAssign):
            named = isinstance(node.target, ast.Name) and node.target.id == "VALUES"
        else:
            continue
        if not named or node.value is None:
            continue
        try:
            values = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return None
        if not isinstance(values, dict) or not all(isinstance(k, str) for k in values):
            return None
        return values
    return None


def value_line(key: str, entry: object) -> str:
    """One VALUES entry as `key (type, default D, lo..hi)` or `key (enum, default D, one of a, b)`;
    a list's `of` is its items' type (`list of player`) or their options (`each one of a, b`)."""
    if not isinstance(entry, dict):
        return key
    typ, of = entry.get("type", "?"), entry.get("of")
    bits = [f"list of {of}" if typ == "list" and isinstance(of, str) else str(typ)]
    if "default" in entry:
        bits.append("default " + show_value(entry["default"]))
    lo, hi = entry.get("min"), entry.get("max")
    if lo is not None or hi is not None:
        bits.append(("" if lo is None else show_value(lo)) + ".." + ("" if hi is None else show_value(hi)))
    if isinstance(of, (list, tuple)):
        bits.append(("each one of " if typ == "list" else "one of ") + ", ".join(show_value(o) for o in of))
    return f"{key} ({', '.join(bits)})"


def show_value(x: object) -> str:
    """A value as `key=<this>` carries it, as vfg script run reads it back: true/false, a whole
    number without .0, a list as compact JSON, a word as it is ("" when empty)."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return "%d" % x
    if isinstance(x, (list, tuple, dict)):
        return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    if x == "":
        return '""'
    return str(x)
