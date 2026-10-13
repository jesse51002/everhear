"""The datapack's compounds as the contract's dataclasses (vfg_engine.io): events, players, entities
and the raycast (the game engine spec, "the datapack" and "Reading the world without waiting").

The datapack names types and blocks by number and players by UUID (commands cannot write either as
text); `Tables` turns the numbers back into ids (everhear:meta's types and blocks) and `Names` the
UUIDs into player names (`list uuids`). What each compound holds is in engine/datapack/README.md.

Standard library only, besides vfg_engine.io.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..io import Effect, EntitySnap, Look, PlayerSnap, RawEvent
from .commands import uuid_str

GAMEMODES = {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}
SLOTS = ("mainhand", "offhand", "head", "chest", "legs", "feet")
# The mirror's own tag, which the datapack puts on each entity it reads in a pass.
OWN_TAGS = frozenset({"everhear.m", "everhear.hit", "everhear.me", "everhear.ray_self", "everhear.ours",
                      "everhear.done", "everhear.ign", "everhear.lava", "everhear.bb"})
RAY_UNIT = 32.0          # the raycast's distances are in 1/32 of a block


@dataclass
class Tables:
    """Index -> id for entity types and blocks, as everhear:meta has them (index 0: not known)."""
    types: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)

    def type(self, i) -> str:
        return _at(self.types, i, "unknown")

    def block(self, i) -> str:
        return _at(self.blocks, i, "unknown")


def _at(table, i, default):
    try:
        i = int(i)
    except (TypeError, ValueError):
        return default
    if 0 < i < len(table) and table[i]:
        return table[i]
    return default


class Names:
    """Player UUID -> name, from `list uuids`; a name once seen is kept, for an event that comes
    after its player left."""

    LINE = re.compile(r"([A-Za-z0-9_]{1,16}) \(([0-9a-f-]{36})\)")

    def __init__(self):
        self.online: dict[str, str] = {}
        self.seen: dict[str, str] = {}
        # UUIDs asked about that were no player's then (mobs), so they are not asked about again
        self.not_players: set[str] = set()

    def update(self, reply: str):
        """From `list uuids`: "There are 1 of a max of 20 players online: Steve (8667ba71-…)"."""
        _, _, rest = reply.partition("online:")
        self.online = {u.lower(): n for n, u in self.LINE.findall(rest)}
        self.seen.update(self.online)
        if len(self.not_players) > 10000:
            self.not_players.clear()

    def name(self, uuid: str) -> str | None:
        return self.online.get(uuid) or self.seen.get(uuid)

    def uuid_of(self, name: str) -> str | None:
        for u, n in self.online.items():
            if n == name:
                return u
        return None


def facing(yaw: float, pitch: float) -> tuple[float, float, float]:
    """Minecraft's look vector for (yaw, pitch) in degrees: yaw 0 faces +z (south), 90 faces -x."""
    y, p = math.radians(yaw), math.radians(pitch)
    return (-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def _vec(v, default=(0.0, 0.0, 0.0)):
    try:
        return (float(v[0]), float(v[1]), float(v[2]))
    except (TypeError, ValueError, IndexError):
        return default


def effect(doc) -> Effect:
    """One active_effects entry (and what waits under it) as an Effect."""
    hidden = doc.get("hidden_effect")
    return Effect(id=str(doc.get("id", "")), amplifier=int(doc.get("amplifier", 0)),
                  ticks=int(doc.get("duration", 0)),
                  hidden=effect(hidden) if isinstance(hidden, dict) else None)


def text_of(component) -> str | None:
    """A text component's plain text: a string, {text, extra}, or a list of them."""
    if component is None:
        return None
    if isinstance(component, str):
        return component
    if isinstance(component, list):
        return "".join(text_of(c) or "" for c in component)
    if isinstance(component, dict):
        out = str(component.get("text", ""))
        for c in component.get("extra", []) or []:
            out += text_of(c) or ""
        return out
    return str(component)


def who(ints, names: Names) -> str | None:
    """A UUID from the datapack as a player's name when it is a player we know, else the uuid."""
    if not isinstance(ints, list) or len(ints) != 4:
        return None
    u = uuid_str(ints)
    return names.name(u) or u


def event(doc: dict, tables: Tables, names: Names) -> RawEvent:
    """One queue entry ({n, k, u, t, d}) as a RawEvent, its data in the design's field names."""
    kind = str(doc.get("k", ""))
    d = dict(doc.get("d") or {})
    data: dict = {}
    u = doc.get("u")
    player_uuid = uuid_str(u) if isinstance(u, list) and len(u) == 4 else None
    if player_uuid:
        data["uuid"] = player_uuid
    if kind == "use":
        data.update(item=d.get("item"), slot=d.get("slot", "mainhand"), count=int(d.get("count", 1) or 1))
    elif kind in ("hit", "kill"):
        if "target" in d:
            data["target"] = who(d["target"], names)
        data["target_type"] = tables.type(d.get("target_type"))
        if d.get("item"):
            data["item"] = d["item"]
        if kind == "hit":
            data["ranged"] = bool(d.get("ranged", 0))
    elif kind == "land":
        data.update(item=d.get("item"), hit=str(d.get("hit", "block")), pos=_vec(d.get("pos"), None),
                    dim=d.get("dim"), projectile_type=tables.type(d.get("projectile_type")))
        if isinstance(d.get("projectile"), list) and len(d["projectile"]) == 4:
            data["projectile"] = uuid_str(d["projectile"])
        if d.get("block"):
            data["block"] = str(d["block"])
        if "target" in d:
            data["target"] = who(d["target"], names)
            data["target_type"] = tables.type(d.get("target_type"))
    elif kind == "mine":
        data.update(block=str(d.get("block", "")), count=int(d.get("count", 1) or 1))
    elif kind == "death":
        if isinstance(d.get("pos"), list) and len(d["pos"]) == 3:
            data["pos"] = tuple(int(c) for c in d["pos"])
        if d.get("dim"):
            data["dim"] = str(d["dim"])
    elif kind == "hurt":
        data["amount"] = round(float(d.get("amount", 0.0)), 3)
        data["absorbed"] = round(float(d.get("absorbed", 0.0)), 3)
        if "attacker" in d:
            data["attacker"] = who(d["attacker"], names)
            data["attacker_type"] = tables.type(d.get("attacker_type"))
    else:
        data.update(d)
    return RawEvent(n=int(doc["n"]), kind=kind, player=names.name(player_uuid) if player_uuid else None,
                    data=data, game_tick=int(doc.get("t", 0)))


def look(doc: dict | None, eye, face, tables: Tables) -> Look:
    """The raycast's compound ({k, d, p?, b?, e?, y?}) as a Look."""
    doc = doc or {}
    kind = str(doc.get("k", "none"))
    distance = float(doc.get("d", 0)) / RAY_UNIT
    if "p" in doc:
        pos = _vec(doc["p"])
    else:
        pos = tuple(e + f * distance for e, f in zip(eye, face))
    if kind == "block":
        return Look("block", pos, distance, block=tuple(math.floor(c) for c in pos),
                    block_id=tables.block(doc.get("b")))
    if kind == "entity" and isinstance(doc.get("e"), list):
        return Look("entity", pos, distance, entity=uuid_str(doc["e"]), entity_type=tables.type(doc.get("y")))
    return Look("none", pos, distance)


def player(doc: dict, name: str, tables: Tables) -> PlayerSnap:
    """One player's compound from everhear:io/player as a PlayerSnap."""
    pos = _vec(doc.get("p"))
    eye = _vec(doc.get("e"), pos)
    rot = doc.get("r") or [0.0, 0.0]
    yaw, pitch = float(rot[0]), float(rot[1])
    face = facing(yaw, pitch)
    health = float(doc.get("h", 0.0))
    held = {slot: str(item) for slot, item in (doc.get("hd") or {}).items() if slot in SLOTS and item}
    carried = [str(i) for i in (doc.get("inv") or []) if i]
    carried += [held[s] for s in SLOTS[1:] if s in held]       # the main hand is in the 36 slots already
    return PlayerSnap(
        name=name, uuid=uuid_str(doc["u"]), dim=str(doc.get("d", "unknown")), pos=pos, eye=eye,
        yaw=yaw, pitch=pitch, facing=face, health=health, max_health=float(doc.get("mh", 20.0)),
        alive=health > 0, sneaking=bool(doc.get("s", 0)), gamemode=GAMEMODES.get(int(doc.get("g", 0)), "survival"),
        on_ground=bool(doc.get("og", 0)), sprinting=bool(doc.get("sp", 0)),
        held=held, carried=tuple(carried), effects=tuple(effect(e) for e in doc.get("fx") or [] if isinstance(e, dict)),
        looking_at=look(doc.get("l"), eye, face, tables))


def entity(doc: dict, tables: Tables, names: Names | None = None) -> EntitySnap | None:
    """One entity's compound from everhear:io/entity as an EntitySnap; None when it is dead (a dying
    mob stays in the world, and selectable by its UUID, for a second: to the engine it is gone)."""
    if not isinstance(doc.get("u"), list):
        return None
    health = doc.get("h")
    if health is not None:
        health = float(health)
        if health <= 0:
            return None
    u = uuid_str(doc["u"])
    kind = tables.type(doc.get("y"))
    name = text_of(doc.get("c"))
    if kind == "minecraft:player" and names is not None:
        name = names.name(u) or name
    tags = frozenset(str(t) for t in doc.get("g") or []) - OWN_TAGS
    owner = who(doc.get("o"), names) if names is not None else (
        uuid_str(doc["o"]) if isinstance(doc.get("o"), list) and len(doc["o"]) == 4 else None)
    return EntitySnap(uuid=u, type=kind, dim=str(doc.get("d", "unknown")), pos=_vec(doc.get("p")),
                      health=health, tags=tags, name=name, owner=owner, in_ground=bool(doc.get("ig", 0)))
