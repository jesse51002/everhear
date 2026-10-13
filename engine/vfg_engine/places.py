"""Places: named spots, the engine's own data (DESIGN2 §2).

Kept in the engine's state per world (`Io.world_id()`): a fresh world starts with none. A place
is {dim, pos, yaw, by, at}. Content reads them through `ctx.places` and writes them with
`self.save_place(name, at, by=None)` and `self.forget_place(name)`; the brain names a place with
the library's `save_place` scenario. The `places` op lists them, with a version that goes up on
every change; `places_import` takes the old `everhear:places` list once.

A name is lowercase letters, digits and _ (spaces and hyphens become _), at most 32.
"""

from __future__ import annotations

from .args import place_name


class Places:
    def __init__(self):
        self.worlds: dict[str, dict] = {}     # world id -> {"places": {name: doc}, "imported": bool}
        self.world = "default"
        self.version = 0

    def _here(self) -> dict:
        return self.worlds.setdefault(self.world, {"places": {}, "imported": False})

    # ---- reading (ctx.places) ------------------------------------------------------------

    def get(self, name) -> dict | None:
        doc = self._here()["places"].get(place_name(name))
        return dict(doc) if doc is not None else None

    def all(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._here()["places"].items()}

    # ---- writing -------------------------------------------------------------------------

    def save(self, name, dim: str, pos, yaw: float = 0.0, by: str | None = None, now: float = 0.0) -> str:
        key = place_name(name)
        if not key:
            raise ValueError("%r cannot be a place's name: letters, digits and _, at most 32" % (name,))
        self._here()["places"][key] = {"dim": dim, "pos": [float(c) for c in pos], "yaw": float(yaw),
                                       "by": by, "at": now}
        self.version += 1
        return key

    def forget(self, name) -> bool:
        if self._here()["places"].pop(place_name(name), None) is None:
            return False
        self.version += 1
        return True

    def import_old(self, items, now: float = 0.0, force: bool = False) -> dict:
        """The old `everhear:places` list, once per world: a list of {name, dim, pos | x y z, yaw}
        or a dict of name -> the same. Names already saved are kept and reported."""
        here = self._here()
        if here["imported"] and not force:
            raise ValueError("this world's places were imported already (force to import again)")
        if isinstance(items, dict):
            items = [dict(v, name=k) for k, v in items.items()]
        imported, skipped, refused = [], [], []
        for it in items or ():
            try:
                name = place_name(it.get("name"))
                pos = it.get("pos") or [it["x"], it["y"], it["z"]]
                pos = [float(c) for c in pos]
                dim = str(it.get("dim") or "minecraft:overworld")
                dim = dim if ":" in dim else "minecraft:" + dim
                if not name or len(pos) != 3:
                    raise ValueError("no name or no position")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                refused.append({"place": it, "error": str(exc)})
                continue
            if name in here["places"]:
                skipped.append(name)
                continue
            here["places"][name] = {"dim": dim, "pos": pos, "yaw": float(it.get("yaw") or 0.0),
                                    "by": it.get("by") or "import", "at": now}
            imported.append(name)
        here["imported"] = True
        if imported:
            self.version += 1
        return {"imported": imported, "skipped": skipped, "refused": refused}

    # ---- saving --------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"version": self.version, "worlds": self.worlds}

    def load(self, doc):
        doc = doc or {}
        self.version = int(doc.get("version", 0))
        self.worlds = dict(doc.get("worlds") or {})
