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


@dataclass(frozen=True)
class EntitySnap:
    uuid: str
    type: str                    # "minecraft:zombie"
    dim: str
    pos: Vec
    health: float | None
    tags: frozenset[str] = frozenset()
    name: str | None = None


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

@dataclass(frozen=True)
class Sound:
    name: str
    dim: str
    pos: Vec
    volume: float = 1.0
    pitch: float = 1.0

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

Op = (AddModifier | RemoveModifier | GiveEffect | GiveItem | TakeItem | Summon | Remove | Damage
      | Particle | Sound | Message | Teleport | Gamerule | Command)


@dataclass(frozen=True)
class OpResult:
    ok: bool
    reply: str                   # Minecraft's reply, as sent back
    error: str | None = None     # why it did not take, in words ("already present", "has something stronger", "no player")


class Mirror(Protocol):
    """The second thread's copy of the entities around players, at most about 0.1 s old."""
    @property
    def age_s(self) -> float: ...
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
