"""The actions as the brain sees them: lib/py/vfg_actions.py's Catalog, kept in memory and
refreshed before each loop, so an action file written or changed a moment ago (an implementer's,
in made/<server>/actions/) is on the menu at the next loop, with no restart.

Also here: what the brain is shown of an action (its docstring and the values it takes), the
fields that hold server commands, and the words a value becomes on `vfg action run`'s command
line.
"""

from __future__ import annotations

import inspect
import json
import re
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import vfg_actions
from vfg_actions import Catalog, describe

from .config import log

# stop's and edit's field `copy` is the contract's name for it; pydantic warns that it hides
# BaseModel.copy(), which nothing here calls.
warnings.filterwarnings("ignore", message=r'Field name "copy" in "\w+" shadows an attribute in parent')

COMMAND_KINDS = ("command", "list of command")


def load(repo: Path, server: str | None = "main") -> Catalog:
    """Every library action, and server's made ones. One that will not load is logged (by
    vfg_actions) and left out; the dispatcher runs without it."""
    catalog = Catalog(repo, server)
    names = catalog.names()
    log("actions", f"loaded {len(names)}: {', '.join(names) or 'none'}")
    return catalog


def refresh(catalog: Catalog) -> list[str]:
    """Load again every action file that changed since the last ask; the names that changed."""
    changed = catalog.refresh()
    if changed:
        log("actions", f"reloaded {', '.join(changed)}; {len(catalog.names())} on the menu")
    return changed


def hidden_commands() -> frozenset[str]:
    """The commands the drafting model never sees in the list: those that place or remove
    blocks and the owner's, from actions/_types.py (BLOCK_COMMANDS, REFUSED)."""
    try:
        T = vfg_actions._types()
        return frozenset(T.BLOCK_COMMANDS) | frozenset(T.REFUSED)
    except Exception as e:  # noqa: BLE001 — without _types there is no action either
        log("actions", f"actions/_types.py gives no BLOCK_COMMANDS and REFUSED ({type(e).__name__}: {e})")
        return frozenset()


def doc(cls) -> str:
    return inspect.cleandoc(cls.__doc__ or "")


def runs_words(cls) -> str:
    if cls.runs == "until_stopped":
        return "It runs until stopped: it keeps going after it reports, as a copy that stop ends and edit changes."
    return "It runs once and reports what it did."


def view(name: str, cls) -> str:
    """What the brain sees of one action: its whole docstring, how it runs, and each value it
    fills (its `takes`), with its type, limits, default and description as describe() gives them."""
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
    return (f"ACTION: {name}\n{doc(cls)}\n\n{runs_words(cls)}\n\n"
            f"FIELDS (fill each one; one the instruction does not give keeps its default):\n{values}\n")


def variables_of(catalog: Catalog, name: str) -> list[dict] | None:
    """A task's `variables` (Prototype_Named_Tasks): the fields of the action `name`, as
    vfg_actions.variables() reads them; None when the action is not loaded."""
    cls = catalog.get(name)
    return None if cls is None else vfg_actions.variables(cls)


def command_fields(model) -> list[str]:
    """The fields of an action, or of a model made from one, that hold server commands: a
    Command, or a list of them."""
    T = vfg_actions._types(model)
    return [k for k, f in model.model_fields.items() if T.kind(f.annotation, f.metadata) in COMMAND_KINDS]


def commands_in(values: dict[str, Any], fields: Iterable[str]) -> list[str]:
    """Every command in those fields of `values`, in order."""
    out: list[str] = []
    for k in fields:
        v = values.get(k)
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


def name_pattern(name: str) -> re.Pattern[str]:
    """How a sentence names a copy: its name, with a space allowed for each _, so "galick gun
    2" names galick_gun_2."""
    return re.compile(r"\b" + r"[_ ]".join(map(re.escape, name.split("_"))) + r"\b", re.IGNORECASE)


def named(text: str, names: Iterable[str]) -> list[str]:
    """The names `text` says, in the order of `names`. "galick_gun_2" says galick_gun_2 alone;
    "galick gun 2" says galick_gun too."""
    return [n for n in names if name_pattern(n).search(text)]
