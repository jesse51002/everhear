"""A task's name, and the names a new one must not take (Prototype_Named_Tasks).

The orchestrator names a task from what is made and for whom (`galick_gun_styrk`); code adds
`_2`, `_3` … when the name is taken. The name is the task's, its script's, its action's and its
copy's, so it must be free as each of those: a task, an action (the library's or this server's),
a script folder, a running copy, and never `cast`, the task the bridge gives an incantation's
copies.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
MOST = 32                       # characters in a task's name
RESERVED = frozenset({"cast"})  # never a task's name: the bridge's task for a cast's copy


def unique(name: str, taken: Iterable[str], most: int = MOST) -> str:
    """`name`, else `name_2`, `name_3` …, the base cut so the whole fits `most` characters: the
    first that is not in `taken`."""
    taken = set(taken)
    if name not in taken:
        return name
    n = 2
    while True:
        suffix = "_%d" % n
        candidate = name[:most - len(suffix)].rstrip("_") + suffix
        if candidate not in taken:
            return candidate
        n += 1


def script_names(repo: Path, server: str) -> set[str]:
    """The script folders in scripts/ and made/<server>/scripts/: the names `vfg script new`
    refuses."""
    out: set[str] = set()
    for root in (repo / "scripts", repo / "made" / server / "scripts"):
        try:
            out |= {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))}
        except OSError:
            pass
    return out
