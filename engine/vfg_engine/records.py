"""What the engine keeps about each running instance: names and plain data only (the game engine
spec, "Live updates"). A Record never holds content code: its listens and timers name methods,
and its content object is rebuilt from (class, config, state) whenever the class is swapped or the
engine restarts. Everything here is saved with the engine's state (to_dict / from_dict).

An ability's casts are Runs inside its Record: each has its own State, its own end, its own
damage count and whatever it made, removed when the run ends.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class Sub:
    kind: str                    # the event kind
    method: str
    about: str = "user"          # "user" | "anyone"
    run: int | None = None       # made during this run (an ability's cast); ends with it


@dataclass
class Timer:
    id: str
    due: float
    method: str
    args: list = field(default_factory=list)
    period: float | None = None  # repeating every `period` seconds; None: once
    run: int | None = None
    tick: bool = False           # the class's own `every` (tick(ctx)), rebuilt on reload


@dataclass
class Owned:
    """What one instance (or one run) made, which goes when it ends."""
    modifiers: dict[str, list] = field(default_factory=dict)   # attribute -> [amount, operation]
    potions: dict[str, list] = field(default_factory=dict)     # keep_potion: effect -> [amplifier, particles, next_due]
    spawned: list[str] = field(default_factory=list)           # uuids
    grants: list[list[str]] = field(default_factory=list)      # [who, ability]
    marks: list[str] = field(default_factory=list)             # mark instance ids (a scenario's)
    holds: list[str] = field(default_factory=list)             # world settings held
    areas: dict[str, dict] = field(default_factory=dict)       # name -> {dim, center, radius, box, inside}

    def empty(self) -> bool:
        return not any((self.modifiers, self.potions, self.spawned, self.grants, self.marks,
                        self.holds, self.areas))


@dataclass
class Run:
    """One cast of an ability."""
    n: int
    started: float               # when the cast was accepted
    fire_at: float               # when the charge is done and on_cast runs
    fired: bool = False
    ends: float | None = None    # set when it fires; None until then, or when it runs until stopped
    cast: dict = field(default_factory=dict)        # the Cast event's fields
    state: Any = None            # the class's State (a model while live; a dict when saved)
    declared: float | None = None   # damage(ctx) when it fired: what one target should take
    dealt: dict[str, float] = field(default_factory=dict)   # target -> damage dealt this run
    flagged: bool = False        # its damage drift was logged
    owned: Owned = field(default_factory=Owned)

    def to_dict(self) -> dict:
        doc = {f.name: getattr(self, f.name) for f in fields(self)}
        doc["state"] = _dump(self.state)
        doc["owned"] = asdict(self.owned)
        return doc


@dataclass
class Record:
    iid: str                     # "ability:fireball#12"
    kind: str                    # item | ability | mark | actor | scenario
    cid: str                     # the class id
    user: str | None             # a player's name or an actor's uuid; None for a scenario
    created: float
    source: str | None = None    # who caused it (a being, or an instance id)
    owner: str | None = None     # the instance whose end ends this one (a lent grant, a spawn)
    args: dict = field(default_factory=dict)        # a scenario's start args; an item's slot
    ends: float | None = None    # when it ends by itself (a mark, a scenario); None: never
    paused_at: float | None = None  # its user went offline: timers and end wait
    stack: int | None = None     # a stacking mark's copy number
    state: Any = None
    subs: list[Sub] = field(default_factory=list)
    timers: list[Timer] = field(default_factory=list)
    owned: Owned = field(default_factory=Owned)
    runs: dict[int, Run] = field(default_factory=dict)
    next_run: int = 1
    next_timer: int = 1
    started: bool = False        # its start(ctx) has run
    ending: bool = False

    # ---- saving --------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Plain data for the saved state. Built field by field: `asdict` would deep-copy every
        State model (once a second, for every instance) only to throw the copy away."""
        doc = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "ending":
                continue
            if f.name == "state":
                doc[f.name] = _dump(value)
            elif f.name == "runs":
                doc[f.name] = {str(n): run.to_dict() for n, run in value.items()}
            elif f.name in ("subs", "timers"):
                doc[f.name] = [asdict(x) for x in value]
            elif f.name == "owned":
                doc[f.name] = asdict(value)
            else:
                doc[f.name] = value
        return doc

    @classmethod
    def from_dict(cls, doc: dict) -> "Record":
        doc = dict(doc)
        doc["subs"] = [Sub(**s) for s in doc.get("subs", [])]
        doc["timers"] = [Timer(**t) for t in doc.get("timers", [])]
        doc["owned"] = Owned(**doc.get("owned", {}))
        runs = {}
        for n, r in (doc.get("runs") or {}).items():
            r = dict(r)
            r["owned"] = Owned(**r.get("owned", {}))
            runs[int(n)] = Run(**r)
        doc["runs"] = runs
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in doc.items() if k in known})


def _dump(state):
    if state is None:
        return None
    if hasattr(state, "model_dump"):
        return state.model_dump(mode="json")
    return state
