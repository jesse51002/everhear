"""Actors: custom mobs and bosses, one instance per spawned entity (the game engine spec,
"Actors, players and scenarios").

A spawn picks the entity's uuid itself, so the instance exists, and is tracked in every snapshot,
before Minecraft answers. The summon sets the actor's own base attributes (max health, scale,
attack damage) from its Config: it is ours, so that is fine; a player's base is never set. The
actor's body (vfg_engine.beings.ActorBeing) is granted the Config's abilities, and its class's
`tick()` runs at its `every`. When the entity no longer exists (killed, despawned), a `gone`
event goes out and the instance ends; when the instance ends any other way (its owner stopped,
an error, the `stop` op), the entity is removed, without loot.

`self.spawn("minecraft:zombie", at)` spawns a plain vanilla entity the same way, with no
instance: it is tracked, and removed when whatever spawned it ends.

**Orphans are swept.** An actor in a chunk that unloads is missing from the snapshot, so it is
taken as gone and its instance ends; when the chunk loads again the entity (it persists) is back
with nothing driving it. So every few seconds (`check_every_s`) the engine looks at what the
mirror sees around players, and removes every entity tagged `everhear.actor` or `everhear.spawn`
that no live actor, vanilla spawn or kept-aside record owns, with a log line. An entity removed
in the last few seconds is not removed again.
"""

from __future__ import annotations

import json
import logging
import uuid as uuidlib

from . import abilities
from .beings import ActorBeing
from .checks import check_entity_type
from .config import value_of
from .ctx import ProbeCtx
from .io import Remove, Summon
from .records import Record

log = logging.getLogger("vfg_engine")

TAG = "everhear"
SPAWNED_TAGS = frozenset({TAG + ".actor", TAG + ".spawn"})   # what the engine's summons carry
SWEEP_RADIUS = 64.0      # around each player; the mirror itself reads 32 blocks round
REMOVED_KEEP_S = 10.0    # a Remove just sent is not sent again by the sweep for this long


def _pos(at) -> tuple[float, float, float]:
    from .handle import _pos as pos
    return pos(at)


def spawn(rt, what: str, at, dim, owner=None, source=None) -> str:
    """Spawn an Actor class (by id) or a vanilla entity ("minecraft:zombie") at `at`; its uuid."""
    if at is None:
        raise ValueError("spawn needs a place (at=(x, y, z), a player or a position)")
    if isinstance(at, str):
        pos = rt.position_of(rt.target_of(at))
        if pos is None:
            raise ValueError("spawn at %r: no such player or entity in this tick's world" % at)
    else:
        pos = _pos(at)
    dim = dim or _dim_of(rt, at) or "minecraft:overworld"
    uuid = str(uuidlib.uuid4())
    info = rt.classes.get(what)
    if info is not None and info.kind == "actor":
        return _spawn_actor(rt, info, uuid, dim, pos, owner, source)
    if ":" not in str(what):
        raise ValueError("%r is not a loaded actor (or a vanilla entity type like minecraft:zombie)" % what)
    why = check_entity_type(what)
    if why:
        raise ValueError(why)
    rt.raw_spawned[uuid] = owner or ""
    rt.queue(Summon(str(what), dim, pos, uuid, None, (TAG, TAG + ".spawn")),
             lambda op, res: _summoned(rt, op, res))
    return uuid


def _spawn_actor(rt, info, uuid, dim, pos, owner, source):
    config = info.config
    damage = float(rt.guarded("%s.Config.damage" % info.id, lambda: value_of(config, "damage", ProbeCtx(1))))
    nbt = {"Health": "%gf" % config.health, "PersistenceRequired": "1b",
           "attributes": "[%s]" % ",".join(
               '{id:"minecraft:%s",base:%g}' % (a, v)
               for a, v in (("max_health", config.health), ("scale", config.size), ("attack_damage", damage)))}
    if config.name:
        nbt["CustomName"] = json.dumps(config.name)
    if config.equipment:
        nbt["equipment"] = "{%s}" % ",".join('%s:{id:"%s",count:1}' % (slot, item)
                                            for slot, item in sorted(config.equipment.items()))
    snbt = "{%s}" % ",".join("%s:%s" % kv for kv in nbt.items())
    rec = rt.add(Record(rt.new_iid("actor", info.id), "actor", info.id, uuid, rt.now, source=source,
                        owner=owner, args={"dim": dim, "pos": list(pos), "size": config.size}))
    rt.actors[uuid] = ActorBeing(rt, uuid, rec.iid, dim, pos, config.size)
    rt.queue(Summon(config.entity_type, dim, pos, uuid, snbt, (TAG, TAG + ".actor", "%s.actor.%s" % (TAG, info.id))),
             lambda op, res: _summoned(rt, op, res))
    for ability in config.abilities:
        try:
            abilities.grant(rt, uuid, ability)
        except ValueError as exc:
            log.warning("%s: its ability %s was not granted: %s", rec.iid, ability, exc)
    rt.later(lambda: rt.start(rec))
    return uuid


def _summoned(rt, op: Summon, result):
    if result.ok:
        rt.confirmed.add(op.uuid)
        return
    why = result.error or result.reply or "no reply"
    log.warning("summon of %s (%s) failed: %s", op.entity_type, op.uuid, why)
    rt.raw_spawned.pop(op.uuid, None)
    body = rt.actors.get(op.uuid)
    if body is not None and body.instance in rt.records:
        rt.end_record(rt.records[body.instance], "its summon failed: %s" % why)


def unspawn(rt, uuid: str, reason: str):
    """Remove something spawned: an actor's instance ends (and removes it), a vanilla one goes."""
    body = rt.actors.get(uuid)
    if body is not None and body.instance in rt.records:
        rt.end_record(rt.records[body.instance], reason)
    elif uuid in rt.raw_spawned:
        del rt.raw_spawned[uuid]
        rt.confirmed.discard(uuid)
        rt.removing[uuid] = rt.now
        rt.queue(Remove(uuid))


def ended(rt, rec: Record, reason: str):
    """An actor's instance ended: its body's grants and marks go, and the entity unless it is gone."""
    body = rt.actors.pop(rec.user, None)
    if body is not None:
        for iid in list(body.grants.values()):
            if iid in rt.records:
                rt.end_record(rt.records[iid], "its actor ended")
    for mark in rt.records_of(rec.user, "mark"):
        rt.end_record(mark, "its actor ended")
    if reason != "gone":
        rt.removing[rec.user] = rt.now
        rt.queue(Remove(rec.user))
    rt.confirmed.discard(rec.user)
    rt.modifiers.forget(rec.user)


def body_of(rt, rec: Record) -> ActorBeing:
    """An actor's body rebuilt from its record (a restart, a reload of a class not loaded before)."""
    a = rec.args
    return ActorBeing(rt, rec.user, rec.iid, a.get("dim", "minecraft:overworld"),
                      tuple(a.get("pos", (0.0, 0.0, 0.0))), float(a.get("size", 1.0)))


def sweep(rt) -> list[str]:
    """Remove the engine's spawns that nothing owns any more (see the module); their uuids."""
    owned = set(rt.actors) | set(rt.raw_spawned)
    owned |= {d.get("user") for d in rt.orphans if d.get("kind") == "actor"}
    rt.removing = {u: t for u, t in rt.removing.items() if rt.now - t < REMOVED_KEEP_S}
    mirror = rt.io_mirror()
    found = {}
    for p in rt.snapshot.players.values():
        for e in mirror.entities_near(p.dim, p.pos, SWEEP_RADIUS):
            if e.uuid not in owned and e.uuid not in rt.removing and e.tags & SPAWNED_TAGS:
                found[e.uuid] = e
    for uuid, e in found.items():
        log.warning("removing %s (%s%s): an EverHear spawn nothing owns (its chunk unloaded and it "
                    "was taken as gone, or the engine lost track of it)", uuid, e.type,
                    ", " + e.name if e.name else "")
        rt.removing[uuid] = rt.now
        rt.queue(Remove(uuid))
    return list(found)


def missing(rt) -> list[str]:
    """Tracked uuids whose summon took that the snapshot no longer has: gone."""
    return [u for u in rt.tracked() if u in rt.confirmed and u not in rt.snapshot.tracked]


def gone(rt, uuid: str):
    body = rt.actors.get(uuid)
    if body is not None and body.instance in rt.records:
        rt.end_record(rt.records[body.instance], "gone")
    owner = rt.raw_spawned.pop(uuid, None)
    rt.confirmed.discard(uuid)
    for rec in rt.records.values():
        for owned in [rec.owned] + [r.owned for r in rec.runs.values()]:
            if uuid in owned.spawned:
                owned.spawned.remove(uuid)
    return owner


def _dim_of(rt, at):
    if isinstance(at, str):
        b = rt.being(at)
        try:
            return b.dim if b is not None else None
        except LookupError:
            return None
    return getattr(at, "dim", None)
