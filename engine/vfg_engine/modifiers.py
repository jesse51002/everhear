"""Attribute modifiers, desired against actual (the game engine spec, "Potions and marks" and
"The datapack": "the engine compares what should be true with what is, every tick").

Every modifier the engine writes is `/attribute <target> <attr> modifier add everhear:<family>/...`,
one id per item, mark, ability or actor, and Minecraft adds them up with every other modifier on
that attribute. The engine never sets a base and never touches an id it does not own.

- `desired` is worked out each tick from the live instances (what each one set, on its user).
- `actual` is what the engine knows to be on Minecraft: an id goes in when its add took, and out
  when its remove did. A failed op is simply tried again next tick, because the difference is
  still there.
- An id is removed before it is added again: 26.3 refuses a duplicate id ("already present").
- Every few seconds, and when a player joins or respawns, the modifiers on the target are read
  back (`Io.read_modifiers`) and `actual` is corrected, so drift (a lost command, a death that
  dropped them) is put right. A leftover of one of the engine's own families (`everhear:item/`,
  `everhear:mark/`, `everhear:ability/`, `everhear:actor/`) that no instance wants is removed: at
  start-up this is the spec's "removes every everhear:item/* modifier nobody is holding the item
  for". Any other `everhear:` id is someone else's and is left alone.
"""

from __future__ import annotations

import logging

from .io import AddModifier, OpResult, RemoveModifier

log = logging.getLogger("vfg_engine")

Key = tuple[str, str, str]           # (target, attribute, id)
OPERATIONS = ("add_value", "add_multiplied_base", "add_multiplied_total")
FAMILIES = ("everhear:item/", "everhear:mark/", "everhear:ability/", "everhear:actor/")
EPS = 1e-9


def attribute_name(attr: str) -> str:
    attr = str(attr).strip().lower()
    return attr if ":" in attr else "minecraft:" + attr


def _same(a, b) -> bool:
    return a is not None and b is not None and a[0] is not None and abs(a[0] - b[0]) <= EPS and a[1] == b[1]


class Modifiers:
    def __init__(self):
        self.actual: dict[Key, list] = {}          # key -> [amount (None: unknown), operation]
        self._foreign_seen: set[Key] = set()

    # ---- what to send ------------------------------------------------------------------

    def plan(self, desired: dict[Key, tuple[float, str]], reachable) -> list:
        """The ops that make Minecraft match `desired`, removes first, for targets `reachable(t)`."""
        removes, adds = [], []
        for key, have in self.actual.items():
            if reachable(key[0]) and not _same(have, desired.get(key)):
                removes.append(RemoveModifier(*key))
        for key, want in desired.items():
            if reachable(key[0]) and not _same(self.actual.get(key), want):
                adds.append(AddModifier(key[0], key[1], key[2], want[0], want[1]))
        return removes + adds

    def took(self, op, result: OpResult):
        key = (op.target, op.attribute, op.id)
        words = (result.error or result.reply or "").lower()
        if isinstance(op, RemoveModifier):
            if result.ok or "no modifier" in words or "not present" in words or "does not have" in words:
                self.actual.pop(key, None)
            else:
                log.warning("modifier %s on %s %s not removed (%s); trying again next tick",
                            op.id, op.target, op.attribute, result.error or result.reply)
        elif isinstance(op, AddModifier):
            if result.ok:
                self.actual[key] = [op.amount, op.operation]
            elif "already present" in words:
                # something is there under our id: its amount is not known, so it is removed
                # and added again next tick
                self.actual[key] = [None, op.operation]
            else:
                log.warning("modifier %s on %s %s not added (%s); trying again next tick",
                            op.id, op.target, op.attribute, result.error or result.reply)

    # ---- what is there -----------------------------------------------------------------

    def read_back(self, target: str, found: dict[tuple[str, str], float], desired: dict[Key, tuple[float, str]]):
        """Correct `actual` for `target` from what Minecraft says is on it."""
        for key in [k for k in self.actual if k[0] == target]:
            amount = found.get((key[1], key[2]))
            if amount is None:
                del self.actual[key]
            else:
                self.actual[key][0] = amount
        for (attr, mod_id), amount in found.items():
            key = (target, attr, mod_id)
            if key in self.actual:
                continue
            if key in desired:
                self.actual[key] = [amount, desired[key][1]]
            elif mod_id.startswith(FAMILIES):
                log.info("modifier %s on %s %s is a leftover nothing holds; removing it", mod_id, target, attr)
                self.actual[key] = [None, "add_value"]
            elif key not in self._foreign_seen:
                self._foreign_seen.add(key)
                log.info("modifier %s on %s %s is not the engine's; leaving it", mod_id, target, attr)

    def forget(self, target: str):
        """A target that is gone for good (an actor removed): nothing more to send it."""
        for key in [k for k in self.actual if k[0] == target]:
            del self.actual[key]

    # ---- saving ------------------------------------------------------------------------

    def to_list(self) -> list:
        return [[*k, v[0], v[1]] for k, v in self.actual.items()]

    def load(self, rows):
        self.actual = {(r[0], r[1], r[2]): [r[3], r[4]] for r in rows or ()}
