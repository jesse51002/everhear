"""Events the engine works out from snapshots, for what Minecraft does not report as one (the game
engine spec, "Ticks and subscriptions"): join, leave, held, unheld, sneak, unsneak, gone. (Enter
and exit, for watched areas, are vfg_engine.scenarios.crossings.)

`take(rt, snap)` puts the new snapshot in place and returns the events in three groups, in the
order the loop hands them out: joins (before anything else of that player's), the rest, and
leaves (after everything else of that player's this tick). Held, unheld, sneak and unsneak are
changes between two snapshots of a player in one session: none comes with a join, or after a
restart.
"""

from __future__ import annotations

from . import actors
from .events import make


def take(rt, snap) -> tuple[list, list, list]:
    joins, middle, leaves = [], [], []
    for name, p in snap.players.items():
        player = rt.ensure_player(name)
        before = player.snap if player.online else None
        if not player.online:
            joins.append(make("join", name, {}))
        player.uuid = p.uuid or player.uuid
        if before is not None:
            for slot in sorted(set(before.held) | set(p.held)):
                old, new = before.held.get(slot), p.held.get(slot)
                if old == new:
                    continue
                if old:
                    middle.append(make("unheld", name, {"item": old, "slot": slot}))
                if new:
                    middle.append(make("held", name, {"item": new, "slot": slot}))
            if p.sneaking != before.sneaking:
                middle.append(make("sneak" if p.sneaking else "unsneak", name, {}))
        # With no snapshot of theirs before this one (they joined, or the engine restarted with
        # them online), nothing came into their hands in front of the engine: no held events. The
        # item's instance starting is how content learns it is held.
        player.snap = p
        player.online = True
    for name, player in rt.players.items():
        if player.online and name not in snap.players:
            leaves.append(make("leave", name, {}))
    rt.snapshot = snap
    for uuid, body in rt.actors.items():
        body.snap = snap.tracked.get(uuid) or body.snap
    for uuid in actors.missing(rt):
        info = rt.actors.get(uuid)
        cid = rt.records[info.instance].cid if info is not None and info.instance in rt.records else None
        etype = info.snap.type if info is not None and info.snap is not None else ""
        middle.append(make("gone", uuid, {"type": etype, "actor": cid}, type=etype, actor=cid))
    return joins, middle, leaves
