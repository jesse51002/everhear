"""The actions as the brain sees them: lib/py/vfg_actions.py's Catalog, the game engine's ops on
the brain's menu (actions/engine_*.py), kept in memory and refreshed before each loop.

Also here: what the brain is shown of an action (its docstring and the values it takes), the
commands a start of the command scenario carries (for their reference, when Minecraft refuses
one), and the words a value becomes on `vfg action run`'s command line.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vfg_actions import Catalog, describe

from .config import log
from .implementers.guard import refused


def load(repo: Path) -> Catalog:
    """Every action. One that will not load is logged (by vfg_actions) and left out; the brain runs
    without it."""
    catalog = Catalog(repo)
    names = catalog.names()
    log("actions", f"loaded {len(names)}: {', '.join(names) or 'none'}")
    return catalog


def refresh(catalog: Catalog) -> list[str]:
    """Load again every action file that changed since the last ask; the names that changed."""
    changed = catalog.refresh()
    if changed:
        log("actions", f"reloaded {', '.join(changed)}; {len(catalog.names())} on the menu")
    return changed


# Commands that place or remove blocks: building is a scenario's that builds, else an implementer's.
# TODO(library): the command scenario's own list of them, once the library has one.
BLOCK_COMMANDS = frozenset(["setblock", "fill", "clone", "place", "fillbiome"])


def hidden_commands() -> frozenset[str]:
    """The commands the brain never sees in its command list: those that place or remove blocks
    (BLOCK_COMMANDS), and the owner's, the engine's list (engine/vfg_engine/mc/commands.py's
    REFUSED, which it refuses every command it sends against)."""
    try:
        return BLOCK_COMMANDS | refused()
    except (OSError, SyntaxError, RuntimeError, ValueError) as e:
        log("actions", f"the engine's REFUSED could not be read ({type(e).__name__}: {e})")
        return BLOCK_COMMANDS


def doc(cls) -> str:
    return inspect.cleandoc(cls.__doc__ or "")


def view(name: str, cls) -> str:
    """What the brain sees of one action: its whole docstring, and each value it fills (its
    `takes`), with its type, limits, default and description as describe() gives them."""
    lines = describe(name, cls).splitlines()[3:]
    takes, keep = [], False
    for line in lines:
        m = re.match(r"^([* ]) (\w+) \(", line)
        if m and m.group(2) in cls.model_fields:
            keep = m.group(1) == "*"
            if keep:
                takes.append("- " + line[2:])
        elif keep and takes:
            takes[-1] += " " + line.strip()  # a description on more than one line
    values = "\n".join(takes) or "(none)"
    return (f"ACTION: {name}\n{doc(cls)}\n\n"
            f"FIELDS (fill each one; one the instruction does not give keeps its default):\n{values}\n")


def commands_in(values: dict[str, Any], classes: Iterable[dict]) -> list[str]:
    """Every server command an engine_start's args carry, in order: the args whose type, as the
    scenario's Args in `classes` (ENGINE's) name it, is a command or a list of them."""
    args = values.get("args") if isinstance(values.get("args"), dict) else {}
    scenario = str(values.get("scenario") or "")
    spec = next((c.get("args") for c in classes if isinstance(c, dict) and c.get("id") == scenario
                 and c.get("kind") == "scenario"), None) or []
    kinds = dict((str(x.get("name")), str(x.get("type") or "").lower()) for x in spec if isinstance(x, dict))
    out: list[str] = []
    for k, v in args.items():
        if "command" not in kinds.get(k, ""):
            continue
        out += [str(c) for c in v] if isinstance(v, list) else [str(v)] if v else []
    return out


def word(v: Any) -> str:
    """A value as `key=<this>` carries it on vfg's command line: true/false, a whole number
    without .0, a list or a dict as compact JSON, text as it is."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return "%d" % v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)
