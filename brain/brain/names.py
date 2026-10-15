"""A task's name, and the names a new one must not take (Prototype_Named_Tasks).

The brain's hand-off names a task from what is made and for whom (`galick_gun_styrk`); code adds
`_2`, `_3` … when the name is taken. The name is the task's and the id of the class it makes (the
game engine spec: made/<server>/content/<id>.py), so it must be free as each of those: a task, a
class the engine has, a file in engine/library/ or made/<server>/content/, an action's name, and
never `cast`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
MOST = 32                       # characters in a task's name
RESERVED = frozenset({"cast"})  # never a task's name: the bridge's task for a cast


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


def content_names(repo: Path, server: str) -> set[str]:
    """The classes written as files, engine/library/<id>.py and made/<server>/content/<id>.py, by
    their file names: the ids a new task's class must not take."""
    out: set[str] = set()
    for root in (repo / "engine" / "library", repo / "made" / server / "content"):
        try:
            out |= {p.stem for p in root.glob("*.py") if not p.name.startswith(("_", "."))}
        except OSError:
            pass
    return out
