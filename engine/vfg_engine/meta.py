"""`engine meta`: every class as one table (the game engine spec, "Abilities and their config":
"the required fields, damage at power 0, 10 and 20, then each class's own fields").

A row is JSON-able: a formula is shown by its value at power 1, and UNTIL_STOPPED as
"until stopped".
"""

from __future__ import annotations

import math

from .config import engine_fields, own_fields, value_of
from .ctx import ProbeCtx


def _plain(value):
    if isinstance(value, float) and math.isinf(value):
        return "until stopped"
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def row(info, guarded=None) -> dict:
    """One class's row; `guarded(label, fn)` runs a formula as a content call (Runtime.guarded)."""
    config = info.config
    guarded = guarded or (lambda label, fn: fn())
    required = {}
    for name in type(config).REQUIRED:
        try:
            value = guarded("%s.Config.%s" % (info.id, name), lambda name=name: value_of(config, name, ProbeCtx(1)))
        except Exception as exc:          # it passed its checks when it loaded
            value = "failed: %s" % exc
        required[name] = _plain(value)
    for name, value in engine_fields(config).items():
        required.setdefault(name, _plain(value))
    return {"id": info.id, "kind": info.kind, "class": info.name, "summary": config.summary,
            "required": required,
            "damage": {str(p): round(v, 4) for p, v in info.damage.items()} if info.damage else None,
            "fields": {k: _plain(v) for k, v in own_fields(config).items()}}


def rows(classes, cid: str | None = None, guarded=None) -> list[dict]:
    infos = [classes[cid]] if cid else [classes[k] for k in sorted(classes, key=lambda k: (classes[k].kind, k))]
    return [row(i, guarded) for i in infos]
