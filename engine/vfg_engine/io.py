"""What the engine's core needs from Minecraft (the game engine spec). The core never builds a
command string: it reads snapshots and events, and queues writes as the ops below; vfg_engine.mc
turns them into 26.3 commands, and vfg_engine.testing fakes them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Effect:
    id: str                      # "minecraft:poison"
    amplifier: int               # 0 is level I
    ticks: int                   # remaining game ticks; -1 is infinite
    hidden: "Effect | None" = None   # the one waiting underneath (vanilla's hidden_effect)


@dataclass(frozen=True)
class Look:
    """What a player's raycast hit this tick (the datapack runs one per player)."""
    kind: str                    # "block" | "entity" | "none"
    pos: Vec                     # the point hit; for "none", the ray's end at its range
    distance: float
    block: tuple[int, int, int] | None = None
    block_id: str | None = None
    entity: str | None = None    # uuid, hyphenated lower-case
    entity_type: str | None = None


@dataclass(frozen=True)
class PlayerSnap:
    name: str
    uuid: str
    dim: str                     # "minecraft:overworld"
    pos: Vec                     # feet
    eye: Vec
    yaw: float
    pitch: float
    facing: Vec                  # unit vector of the look direction
    health: float
    max_health: float
    alive: bool
    sneaking: bool
    gamemode: str                # "survival" | "creative" | "adventure" | "spectator"
    held: Mapping[str, str]      # slot -> EverHear item id, only slots holding one:
                                 # "mainhand", "offhand", "head", "chest", "legs", "feet"
    carried: tuple[str, ...]     # EverHear item ids of every stack anywhere in the inventory (held ones too)
    effects: tuple[Effect, ...]
    looking_at: Look
    on_ground: bool = True       # (part 2) standing on a block
    sprinting: bool = False      # (part 2)


@dataclass(frozen=True)
class EntitySnap:
    uuid: str
    type: str                    # "minecraft:zombie"
    dim: str
    pos: Vec
    health: float | None
    tags: frozenset[str] = frozenset()
    name: str | None = None
    owner: str | None = None     # (part 2) a tamed mob's or a projectile's owner (a player's name or a uuid)
    in_ground: bool = False      # (part 2) an arrow or trident stuck in a block


@dataclass(frozen=True)
class Snapshot:
    game_tick: int
    players: Mapping[str, PlayerSnap]      # every online player, by name
    tracked: Mapping[str, EntitySnap]      # of the uuids asked for, those that exist now; one missing is gone


@dataclass(frozen=True)
class RawEvent:
    """One event the datapack noticed, in the order it happened."""
    n: int                       # the datapack's number: strictly increasing, each handed over once
    kind: str                    # see EVENT KINDS in the design
    player: str | None           # the player it is about, by name
    data: Mapping[str, Any]
    game_tick: int


# ---- writes: queued during a tick, sent in order at its end as one batch -------------------------

@dataclass(frozen=True)
class AddModifier:
    target: str                  # a player name or an entity uuid
    attribute: str               # "minecraft:scale"
    id: str                      # always "everhear:..."
    amount: float
    operation: str               # "add_value" | "add_multiplied_base" | "add_multiplied_total"

@dataclass(frozen=True)
class RemoveModifier:
    target: str
    attribute: str
    id: str

@dataclass(frozen=True)
class GiveEffect:
    target: str
    effect: str                  # "minecraft:speed"
    seconds: float               # rounded to ticks; float("inf") is infinite
    amplifier: int = 0
    particles: bool = True

@dataclass(frozen=True)
class GiveItem:
    player: str
    base: str                    # "minecraft:carrot_on_a_stick"
    item_id: str                 # the EverHear id, into custom_data={everhear:{item:"<id>"}}
    name: str | None = None      # item_name
    components: Mapping[str, str] = field(default_factory=dict)   # extra components, raw SNBT values
    count: int = 1

@dataclass(frozen=True)
class TakeItem:
    player: str
    item_id: str                 # clears every stack carrying this EverHear id

@dataclass(frozen=True)
class Summon:
    entity_type: str
    dim: str
    pos: Vec
    uuid: str                    # chosen by the engine, so it is known before the reply
    nbt: str | None = None       # extra SNBT, merged
    tags: tuple[str, ...] = ()

@dataclass(frozen=True)
class Remove:
    uuid: str                    # gone without loot or a death message

@dataclass(frozen=True)
class Damage:
    target: str                  # a player name or an entity uuid
    amount: float
    damage_type: str = "minecraft:generic"
    by: str | None = None        # who dealt it (name or uuid)

@dataclass(frozen=True)
class Particle:
    name: str                    # "minecraft:flame"
    dim: str
    pos: Vec
    delta: Vec = (0.0, 0.0, 0.0)
    speed: float = 0.0
    count: int = 1
    force: bool = False          # (part 2) seen from far away ("force" mode)
    to: tuple[str, ...] = ()     # (part 2) only these players; () = everyone near

@dataclass(frozen=True)
class Sound:
    name: str
    dim: str
    pos: Vec
    volume: float = 1.0
    pitch: float = 1.0
    to: tuple[str, ...] = ()     # (part 2) players; () = everyone near

@dataclass(frozen=True)
class Message:
    players: tuple[str, ...]     # () is everyone
    text: str
    where: str = "chat"          # "chat" | "actionbar" | "title" | "subtitle"

@dataclass(frozen=True)
class Teleport:
    target: str
    dim: str
    pos: Vec
    yaw: float | None = None
    pitch: float | None = None

@dataclass(frozen=True)
class Gamerule:
    name: str                    # 26.3 snake_case
    value: str

@dataclass(frozen=True)
class Command:
    text: str                    # anything else, as a raw command (content's escape hatch)

# ---- part 2 (DESIGN2 §6) ------------------------------------------------------------------------

@dataclass(frozen=True)
class Fill:            # fill a b block [replace filter] ; strict: no block updates
    dim: str; a: tuple[int, int, int]; b: tuple[int, int, int]; block: str
    replace: str | None = None; strict: bool = True; mode: str = "replace"   # "replace"|"keep"|"destroy"|"hollow"|"outline"
@dataclass(frozen=True)
class SetBlock:
    dim: str; pos: tuple[int, int, int]; block: str; strict: bool = True; mode: str = "replace"
@dataclass(frozen=True)
class Forceload:
    dim: str; a: tuple[int, int]; b: tuple[int, int]; add: bool = True     # chunk-aligned x z corners
@dataclass(frozen=True)
class Motion:
    target: str; vec: Vec
@dataclass(frozen=True)
class EntityData:
    target: str; snbt: str                 # data merge entity
@dataclass(frozen=True)
class Bossbar:
    id: str; name: str | None = None; color: str | None = None; value: int | None = None
    max: int | None = None; players: tuple[str, ...] | None = None; remove: bool = False
@dataclass(frozen=True)
class StopSound:
    players: tuple[str, ...] = (); name: str | None = None
@dataclass(frozen=True)
class Spawnpoint:
    player: str; dim: str; pos: Vec
@dataclass(frozen=True)
class BlockData:
    dim: str; pos: tuple[int, int, int]; path: str; snbt: str
    how: str = "append"                    # data modify block x y z <path> <how> value <snbt>: "append"|"prepend"|"merge"|"set"
@dataclass(frozen=True)
class Ignite:
    target: str; seconds: float            # a player or an entity uuid set on fire for this long
# Sound gains `to: tuple[str, ...] = ()` (players; () = everyone near); Particle gains `force: bool = False`
# and `to: tuple[str, ...] = ()`. Summon.nbt is passed through by self.spawn.

Op = (AddModifier | RemoveModifier | GiveEffect | GiveItem | TakeItem | Summon | Remove | Damage
      | Particle | Sound | Message | Teleport | Gamerule | Command
      | Fill | SetBlock | Forceload | Motion | EntityData | Bossbar | StopSound | Spawnpoint | Ignite
      | BlockData)


@dataclass(frozen=True)
class OpResult:
    ok: bool
    reply: str                   # Minecraft's reply, as sent back
    error: str | None = None     # why it did not take, in words ("already present", "has something stronger", "no player")


class Mirror(Protocol):
    """The second thread's copy of the entities around players, at most about 0.1 s old."""
    @property
    def age_s(self) -> float: ...
    radius: float                # (part 2) how far round each player it reaches, in blocks
    def entities_near(self, dim: str, pos: Vec, radius: float) -> list[EntitySnap]: ...
    def get(self, uuid: str) -> EntitySnap | None: ...


class Io(Protocol):
    def open(self) -> None: ...                       # connect; check the datapack is loaded and its version
    def close(self) -> None: ...
    def events(self) -> list[RawEvent]: ...           # every event since the last call, in order; taken off the queue
    def snapshot(self, track: Iterable[str] = ()) -> Snapshot: ...
    def mirror(self) -> Mirror: ...
    def query(self, command: str) -> str: ...         # one command now, and its reply (content's ctx.query)
    def flush(self, ops: Sequence[Op]) -> list[OpResult]: ...   # in order, one batch, one result per op
    def read_modifiers(self, target: str) -> dict[tuple[str, str], float]: ...
        # every everhear:* modifier on the target: (attribute, id) -> amount
    def read_gamerule(self, name: str) -> str | None: ...
    # part 2 (DESIGN2 §6): reads on demand, each counted against the calling content's 50 ms
    def probe(self, dim: str, points: Sequence[tuple[int, int, int]], test: str) -> list[bool]: ...
    def ground_y(self, dim: str, x: int, z: int) -> int | None: ...
    def ray(self, dim: str, origin: Vec, direction: Vec, length: float, through_blocks: bool = False) -> Look: ...
    def entities(self, dim: str, center: Vec, radius: float, types: Sequence[str] | None = None) -> list[EntitySnap]: ...
    def loaded(self, dim: str, x: int, z: int) -> bool: ...
    def mspt(self) -> float: ...
    def world_id(self) -> str: ...
    def watch_mined(self, blocks: Iterable[str]) -> list[str]: ...
        # the block kinds whose breaking is a `mine` event, all of them (replaces the last list);
        # returns the ids that are no 26.3 block
