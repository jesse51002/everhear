"""Items: whoever holds one has an instance of it (the game engine spec, "Items").

The stack carries only an id (`custom_data={everhear:{item:"<id>"}}`); the code is the class.
Each tick the snapshot says which ids each player has where (`PlayerSnap.held` by slot, and
`carried` for the whole inventory); for each item class, `active_in` says which of those count:

    mainhand   the main hand          offhand    the off hand
    hands      either hand            worn       an armour slot (head, chest, legs, feet)
    inventory  anywhere on the player

One instance per (item, holder): made when they start holding it there, ended when they stop
("unheld"), leave ("left") or die. An item that listens for Land (a bow, a trident: what it shot
is still flying) lingers for LINGER_S after it is let go: it hears only Land then, its modifiers
are off and its timers wait; held again, it carries on; else it ends "unheld". Three players holding Fire Staffs are three instances, each
with its own state, and a staff thrown to someone else ends one and starts another. The item's
modifiers are `everhear:item/<id>` on the holder while held.

A give is built from the class: its base, its name, a stack of one, and the id. A give naming
another base is refused, naming the class's base. Our items never carry vanilla's
`attribute_modifiers`, so the item's code is the one way it changes an attribute. A give that took
starts its holder's instance at once, so a click right after it is heard; if the stack did not
land where the item counts, the next snapshots end it after GIVE_GRACE_S.
"""

from __future__ import annotations

import json
import logging

from . import bases
from .io import GiveItem, TakeItem
from .records import Record

log = logging.getLogger("vfg_engine")

LINGER_S = 15.0     # an item that listens for Land keeps hearing it this long after it is let go
GIVE_GRACE_S = 1.0  # a given item's instance waits this long for the snapshot to show it held

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
            if rec.notes.pop("lingering", None) is not None:
                log.info("%s is held again", rec.iid)
            rec.notes.pop("given_until", None)
            continue
        p = rt.snapshot.players.get(rec.user)
        reason = "left" if p is None else "died" if not p.alive else "unheld"
        if reason == "unheld" and rec.notes.get("given_until", 0) > rt.now:
            continue                         # just given: the snapshot may not show it yet
        if reason == "unheld" and any(s.kind == "land" for s in rec.subs):
            # its arrow or trident may still be flying: it hears that Land for LINGER_S more
            until = rec.notes.setdefault("lingering", rt.now + LINGER_S)
            if rt.now < until:
                continue
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
                                notes={"slot": slot}))
        except ValueError as exc:
            # like any other failure of the item's own code: not made again until let go or reloaded
            rt.broken["%s|%s" % (cid, name)] = str(exc)
            log.error("item %s for %s not made: %s", cid, name, exc)
            continue
        rt.start(rec)


def given(rt, player: str, item: str):
    """A give took: start the holder's instance at once, so a click right after the give finds
    it. If the stack did not land where the item counts (a full main hand), the next snapshots
    end it after GIVE_GRACE_S, as any item let go."""
    info = rt.classes.get(item)
    p = rt.snapshot.players.get(player)
    if info is None or info.kind != "item" or p is None or not p.alive:
        return
    if "%s|%s" % (item, player) in rt.broken:
        return
    if any(r.kind == "item" and r.cid == item and r.user == player for r in rt.records.values()):
        return
    try:
        rec = rt.add(Record(rt.new_iid("item", item), "item", item, player, rt.now, source=player,
                            notes={"slot": "given", "given_until": rt.now + GIVE_GRACE_S}))
    except ValueError as exc:
        rt.broken["%s|%s" % (item, player)] = str(exc)
        log.error("item %s for %s not made: %s", item, player, exc)
        return
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
    components = look(info.config)
    name = None if "minecraft:item_name" in components else info.config.name
    return GiveItem(player=player, base=own, item_id=item, name=name, components=components, count=1)


def stack_snbt(rt, item: str, count: int = 1) -> str:
    """An EverHear item's stack as SNBT, as a give builds it: for a chest's Items or a mob's
    equipment."""
    if not 1 <= count <= 99:
        raise ValueError("count is 1 to 99, not %r" % count)
    op = give_op(rt, "nobody", item)
    components = {"minecraft:custom_data": "{everhear:{item:%s}}" % json.dumps(op.item_id)}
    if op.name:
        components["minecraft:item_name"] = _text(op.name)
    components.update(op.components)
    return "{id:%s,count:%d,components:{%s}}" % (json.dumps(op.base), count, ",".join(
        "%s:%s" % (json.dumps(k), v) for k, v in components.items()))


def _text(text: str, color: str | None = None) -> str:
    """A text component as SNBT: {text:"Fire Staff",color:"gold",italic:false}."""
    parts = ["text:%s" % json.dumps(text)]
    if color:
        parts.append("color:%s" % json.dumps(color))
    parts.append("italic:false")
    return "{%s}" % ",".join(parts)


def look(config) -> dict[str, str]:
    """An item's look from its Config, as the stack's components (raw SNBT values)."""
    out = {}
    if config.model:
        model = config.model if ":" in config.model else "minecraft:" + config.model
        out["minecraft:item_model"] = json.dumps(model)
    if config.color:
        out["minecraft:item_name"] = _text(config.name, config.color)
    if config.lore:
        out["minecraft:lore"] = "[%s]" % ",".join(_text(line, "gray") for line in config.lore)
    if config.glint is not None:
        out["minecraft:enchantment_glint_override"] = "true" if config.glint else "false"
    if config.enchantments:
        out["minecraft:enchantments"] = "{%s}" % ",".join(
            "%s:%d" % (json.dumps(k if ":" in k else "minecraft:" + k), v) for k, v in sorted(config.enchantments.items()))
    if config.unbreakable:
        out["minecraft:unbreakable"] = "{}"
    return out


def take_op(player: str, item: str) -> TakeItem:
    return TakeItem(player=player, item_id=item)
