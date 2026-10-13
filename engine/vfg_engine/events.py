"""The events content listens for (the game engine spec, "Ticks and subscriptions").

`self.listen(Hit, "on_hit")` names one of these classes (or its kind, "hit"). Each event is about
one being, `ev.who`: a player's name, or an actor's uuid. Where an event comes from:

- the datapack, as numbered RawEvents (vfg_engine.mc): use, hit, hurt, kill, death, respawn, milk,
  and (part 2) land and mine;
- the engine, worked out from snapshots: join, leave, held, unheld, sneak, unsneak, enter, exit, gone;
- the event bus: cast (an incantation heard, an item's or an actor's cast, the `cast` op).

What each carries is provisional (the spec leaves the full list for later); a kind the engine does
not know arrives as a plain Event whose `data` is the datapack's.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Mapping

from .io import RawEvent


@dataclass(frozen=True)
class Event:
    kind: ClassVar[str] = "event"
    who: str | None = None                     # the player's name, or an actor's uuid
    data: Mapping[str, Any] = field(default_factory=dict)   # everything the source sent
    n: int | None = None                       # the datapack's number, for its events
    game_tick: int | None = None
    engine: bool = False                       # caused by the engine's own damage (a hurt, a kill)

    @property
    def player(self) -> str | None:
        """The player it is about (for an event about an actor, its uuid)."""
        return self.who


@dataclass(frozen=True)
class Use(Event):
    """A right-click with an EverHear item."""
    kind: ClassVar[str] = "use"
    item: str = ""
    slot: str = "mainhand"


@dataclass(frozen=True)
class Hit(Event):
    """`who` hit something in melee: `target` is a uuid or a player's name."""
    kind: ClassVar[str] = "hit"
    target: str = ""
    target_type: str = ""
    item: str | None = None                    # the EverHear item in the main hand, if any
    ranged: bool = False                       # an arrow or trident's hit, not a melee one


@dataclass(frozen=True)
class Hurt(Event):
    """`who` took damage."""
    kind: ClassVar[str] = "hurt"
    amount: float | None = None
    attacker: str | None = None
    attacker_type: str | None = None


@dataclass(frozen=True)
class Kill(Event):
    kind: ClassVar[str] = "kill"
    target: str | None = None
    target_type: str = ""


@dataclass(frozen=True)
class Death(Event):
    kind: ClassVar[str] = "death"
    pos: tuple | None = None                   # where they died
    dim: str | None = None


@dataclass(frozen=True)
class Respawn(Event):
    kind: ClassVar[str] = "respawn"


@dataclass(frozen=True)
class Milk(Event):
    kind: ClassVar[str] = "milk"


@dataclass(frozen=True)
class Join(Event):
    kind: ClassVar[str] = "join"


@dataclass(frozen=True)
class Leave(Event):
    kind: ClassVar[str] = "leave"


@dataclass(frozen=True)
class Held(Event):
    """An EverHear item came into one of `who`'s slots."""
    kind: ClassVar[str] = "held"
    item: str = ""
    slot: str = ""


@dataclass(frozen=True)
class Unheld(Event):
    kind: ClassVar[str] = "unheld"
    item: str = ""
    slot: str = ""


@dataclass(frozen=True)
class Sneak(Event):
    kind: ClassVar[str] = "sneak"


@dataclass(frozen=True)
class Unsneak(Event):
    kind: ClassVar[str] = "unsneak"


@dataclass(frozen=True)
class Enter(Event):
    """`who` came into an area an instance watches (`self.watch`)."""
    kind: ClassVar[str] = "enter"
    area: str = ""
    instance: str = ""


@dataclass(frozen=True)
class Exit(Event):
    kind: ClassVar[str] = "exit"
    area: str = ""
    instance: str = ""


@dataclass(frozen=True)
class Gone(Event):
    """A tracked entity (an actor, or anything `self.spawn` made) no longer exists; `who` is its uuid."""
    kind: ClassVar[str] = "gone"
    type: str = ""
    actor: str | None = None                   # the actor class's id, when it was one


@dataclass(frozen=True)
class Land(Event):
    """`who`'s arrow or trident landed in a block or hit something. Heard by the thrower's
    instances, the item it came from included, even after that item was let go."""
    kind: ClassVar[str] = "land"
    item: str | None = None                    # the EverHear id of the bow or trident it came from
    projectile: str = ""                       # the projectile's uuid
    projectile_type: str = ""                  # "minecraft:arrow", "minecraft:trident"
    pos: tuple | None = None                   # where it landed or hit
    dim: str | None = None
    hit: str = "block"                         # "block" (it stuck) or "entity" (it hit something)
    block: str | None = None                   # the block it stuck in, when known
    target: str | None = None                  # what it hit: a uuid or a player's name; None for a block
    target_type: str | None = None


@dataclass(frozen=True)
class Mine(Event):
    """`who` broke a block of a kind the datapack watches."""
    kind: ClassVar[str] = "mine"
    block: str = ""
    pos: tuple | None = None
    count: int = 1                             # blocks of that kind broken since the last tick


@dataclass(frozen=True)
class Cast(Event):
    """An ability cast by `who`; what `on_cast(ctx, ev)` is given."""
    kind: ClassVar[str] = "cast"
    ability: str = ""
    phrase: str | None = None                  # the incantation heard, when it came from one
    cast_id: str | None = None
    by: str = "op"                             # "incantation" | "content" | "op"
    progress: float = 1.0                      # in on_charge: how far the charge is, 0 to 1


KINDS: dict[str, type[Event]] = {
    cls.kind: cls for cls in (Use, Hit, Hurt, Kill, Death, Respawn, Milk, Join, Leave, Held,
                              Unheld, Sneak, Unsneak, Enter, Exit, Gone, Land, Mine, Cast)}


def kind_of(event) -> str:
    """"hit" for Hit or "hit"; ValueError for anything else."""
    if isinstance(event, str):
        return event.strip().lower()
    if isinstance(event, type) and issubclass(event, Event):
        return event.kind
    raise ValueError("listen takes an event class (Hit) or its kind (\"hit\"), not %r" % (event,))


def make(kind: str, who: str | None, data: Mapping[str, Any] | None = None, *, n=None,
         game_tick=None, **known) -> Event:
    """The event of `kind`, its own fields taken from `known`, else from `data` by name."""
    cls = KINDS.get(kind)
    data = dict(data or {})
    if cls is None:
        ev = Event(who=who, data=data, n=n, game_tick=game_tick)
        object.__setattr__(ev, "_kind", kind)
        return ev
    own = {}
    for f in fields(cls):
        if f.name in ("who", "data", "n", "game_tick", "engine"):
            continue
        if f.name in known:
            own[f.name] = known[f.name]
        elif f.name in data:
            own[f.name] = data[f.name]
        if f.name == "pos" and isinstance(own.get("pos"), list):
            own["pos"] = tuple(own["pos"])
    return cls(who=who, data=data, n=n, game_tick=game_tick, **own)


def event_kind(ev: Event) -> str:
    """An event's kind; an unknown datapack kind keeps its own."""
    return getattr(ev, "_kind", None) or ev.kind


def from_raw(raw: RawEvent) -> Event:
    return make(raw.kind, raw.player, raw.data, n=raw.n, game_tick=raw.game_tick)
