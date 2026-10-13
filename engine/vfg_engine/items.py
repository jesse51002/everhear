"""Items: whoever holds one has an instance of it (the game engine spec, "Items").

The stack carries only an id (`custom_data={everhear:{item:"<id>"}}`); the code is the class.
Each tick the snapshot says which ids each player has where (`PlayerSnap.held` by slot, and
`carried` for the whole inventory); for each item class, `active_in` says which of those count:

    mainhand   the main hand          offhand    the off hand
    hands      either hand            worn       an armour slot (head, chest, legs, feet)
    inventory  anywhere on the player

One instance per (item, holder): made when they start holding it there, ended when they stop
("unheld"), leave ("left") or die. Three players holding Fire Staffs are three instances, each
with its own state, and a staff thrown to someone else ends one and starts another. The item's
modifiers are `everhear:item/<id>` on the holder while held.

A give is built from the class: its base, its name, a stack of one, and the id. A give naming
another base is refused, naming the class's base. Our items never carry vanilla's
`attribute_modifiers`, so the item's code is the one way it changes an attribute.
"""

from __future__ import annotations

import logging

from . import bases
from .io import GiveItem, TakeItem
from .records import Record

log = logging.getLogger("vfg_engine")

SLOTS = {
    "mainhand": ("mainhand",),
    "offhand": ("offhand",),
    "hands": ("mainhand", "offhand"),
    "worn": ("head", "chest", "legs", "feet"),
}


def active_ids(snap, active_in: str) -> set[str]:
    if active_in == "inventory":
        return set(snap.carried) | set(snap.held.values())
    return {snap.held[s] for s in SLOTS.get(active_in, ()) if s in snap.held}


def slot_of(snap, item: str, active_in: str) -> str | None:
    for s in SLOTS.get(active_in, ("mainhand", "offhand", "head", "chest", "legs", "feet")):
        if snap.held.get(s) == item:
            return s
    return "inventory" if item in snap.carried else None


def reconcile(rt):
    """Make an instance for each new holder and end the ones whose holder let go."""
    holding: dict[tuple[str, str], str] = {}     # (item id, player) -> the slot
    for name, snap in rt.snapshot.players.items():
        if not snap.alive:
            continue
        for cid, info in rt.classes.items():
            if info.kind != "item":
                continue
            if cid in active_ids(snap, info.config.active_in):
                holding[(cid, name)] = slot_of(snap, cid, info.config.active_in) or "inventory"

    for rec in [r for r in rt.records.values() if r.kind == "item"]:
        if (rec.cid, rec.user) in holding:
            continue
        p = rt.snapshot.players.get(rec.user)
        reason = "left" if p is None else "died" if not p.alive else "unheld"
        rt.end_record(rec, reason)
    for key in list(rt.broken):                  # a broken item is tried again once let go
        cid, _, user = key.partition("|")
        if cid in rt.classes and rt.classes[cid].kind == "item" and (cid, user) not in holding:
            del rt.broken[key]

    have = {(r.cid, r.user) for r in rt.records.values() if r.kind == "item"}
    for (cid, name), slot in holding.items():
        if (cid, name) in have or "%s|%s" % (cid, name) in rt.broken:
            continue
        try:
            rec = rt.add(Record(rt.new_iid("item", cid), "item", cid, name, rt.now, source=name,
                                args={"slot": slot}))
        except ValueError as exc:
            # like any other failure of the item's own code: not made again until let go or reloaded
            rt.broken["%s|%s" % (cid, name)] = str(exc)
            log.error("item %s for %s not made: %s", cid, name, exc)
            continue
        rt.start(rec)


def give_op(rt, player: str, item: str, base: str | None = None) -> GiveItem:
    """The give for `item`, built from its class. ValueError when there is no such item, or
    when `base` is not the class's base."""
    info = rt.classes.get(item)
    if info is None or info.kind != "item":
        raise ValueError("%r is not a loaded item" % item)
    own = bases.normal(info.config.base)
    if base is not None and bases.normal(base) != own:
        raise ValueError("%s is made on %s, not %s: a give on the wrong base is refused"
                         % (item, own, bases.normal(base)))
    return GiveItem(player=player, base=own, item_id=item, name=info.config.name, count=1)


def take_op(player: str, item: str) -> TakeItem:
    return TakeItem(player=player, item_id=item)
