"""Loads the actions: one pydantic class per `actions/<name>/action.py`, keyed by its name.

The repo root goes on sys.path and `actions.<name>.action` is imported for every folder
that has an action.py. The class is the one whose `action` field is `Literal["<name>"]`.
A folder that will not import is logged and left out; the dispatcher runs without it.

Also here: the scripts in `scripts/`, each by the first three lines of its main.py's
docstring (its README's, for an old script with none), which the action planner sees for
`run_script` and `stop_script`.
"""

from __future__ import annotations

import ast
import importlib
import inspect
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
    """{name: what it does} for every script folder with a main.py: the first three non-blank
    lines of main.py's docstring, joined; only when it has none, its README's first three."""
    out = {}
    root = repo / "scripts"
    if not root.is_dir():
        return out
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(("_", ".")) or not (folder / "main.py").is_file():
            continue
        lines = [x.strip() for x in docstring(folder / "main.py").splitlines() if x.strip()][:3]
        if not lines:
            try:
                readme = (folder / "README.md").read_text(encoding="utf-8").splitlines()
            except OSError:
                readme = []
            lines = [x.strip() for x in readme[:3] if x.strip()]
        out[folder.name] = " | ".join(lines) or "(no description)"
    return out


def docstring(path: Path) -> str:
    """A module's docstring, read without running it (scripts run on EC2, not here)."""
    try:
        return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, ValueError):
        return ""
