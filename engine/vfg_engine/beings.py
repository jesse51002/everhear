"""Players and actors' bodies: what `ctx.user` is (the game engine spec, "Actors, players and
scenarios": "Player and Actor share a small base, so an ability or a mark does not care whether
it is on Steve or on the Ice Queen").

A Being is the engine's, never content's: its grants, its marks, its cooldowns, its power. A
Player is one per player the engine has seen; an ActorBeing is the body of one spawned Actor
(the Actor class is the code driving it). Both read their position and look from this tick's
snapshot, so every read is instant.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .io import EntitySnap, Look, PlayerSnap, Vec

if TYPE_CHECKING:
    from .runtime import Runtime


class Being:
    is_player = False

    def __init__(self, rt: "Runtime", key: str):
        self._rt = rt
        self.key = key                               # the name (a player) or the uuid (an actor)
        self.grants: dict[str, str] = {}             # ability id -> its instance id
        self.cooldowns: dict[str, float] = {}        # ability id -> when it can be cast again

    # ---- read ----------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.key

    @property
    def target(self) -> str:
        """What an op names it by: a player's name, an actor's uuid."""
        return self.key

    def power(self) -> float:
        """1 for everyone for now (the spec: how power is earned is for later)."""
        return 1.0

    @property
    def dim(self) -> str:
        raise NotImplementedError

    @property
    def pos(self) -> Vec:
        raise NotImplementedError

    @property
    def eye(self) -> Vec:
        return self.pos

    @property
    def facing(self) -> Vec:
        return (0.0, 0.0, 1.0)

    @property
    def look(self) -> Look:
        raise NotImplementedError

    def looking_at(self) -> Vec:
        """The point this one's look hits this tick (for a player, the datapack's raycast)."""
        return self.look.pos

    @property
    def health(self) -> float | None:
        return None

    @property
    def alive(self) -> bool:
        return True

    # ---- act -----------------------------------------------------------------------------

    def has(self, ability: str) -> bool:
        iid = self.grants.get(ability)
        rec = self._rt.records.get(iid) if iid else None
        return rec is not None and not rec.args.get("release_after_runs")

    def cast(self, ability: str) -> str:
        """Cast an ability by name: "fired", "charging", "cooldown", "not_granted" (only when the
        caller is not an instance that can lend it), "unknown" or "failed". One this being has not
        been granted is lent for as long as the calling instance lives (a held item's ability goes
        when the item is let go). The ability runs right after the calling code returns."""
        return self._rt.cast_from_content(self, ability)

    def marks(self) -> list[str]:
        """The ids of the marks on this one."""
        return [r.cid for r in self._rt.records_of(self.key, "mark")]

    def __repr__(self):
        return "<%s %s>" % (type(self).__name__, self.key)


class Player(Being):
    """The engine's own class, one per player: their abilities, incantations, marks and power."""
    is_player = True

    def __init__(self, rt, name: str, uuid: str = ""):
        super().__init__(rt, name)
        self.uuid = uuid
        self.online = False
        self.snap: PlayerSnap | None = None

    def _snap(self) -> PlayerSnap:
        if self.snap is None:
            raise LookupError("%s is not online" % self.key)
        return self.snap

    @property
    def dim(self):
        return self._snap().dim

    @property
    def pos(self):
        return self._snap().pos

    @property
    def eye(self):
        return self._snap().eye

    @property
    def facing(self):
        return self._snap().facing

    @property
    def yaw(self):
        return self._snap().yaw

    @property
    def pitch(self):
        return self._snap().pitch

    @property
    def look(self):
        return self._snap().looking_at

    @property
    def health(self):
        return self._snap().health

    @property
    def alive(self):
        return self.snap is not None and self.snap.alive

    @property
    def sneaking(self):
        return self._snap().sneaking


class ActorBeing(Being):
    """A spawned actor's body. It has no raycast: it looks where `aim` points it (a player's name,
    a uuid, or a position), and `looking_at()` is that target's position, else its own."""

    def __init__(self, rt, uuid: str, instance: str, dim: str, pos: Vec, size: float = 1.0):
        super().__init__(rt, uuid)
        self.uuid = uuid
        self.instance = instance
        self.size = size
        self.aim_at: str | list | None = None
        self.snap: EntitySnap | None = None
        self._dim, self._pos = dim, pos

    @property
    def name(self):
        return self.snap.name if self.snap is not None and self.snap.name else self.key

    @property
    def dim(self):
        return self.snap.dim if self.snap is not None else self._dim

    @property
    def pos(self):
        return self.snap.pos if self.snap is not None else self._pos

    @property
    def eye(self):
        x, y, z = self.pos
        return (x, y + 1.6 * self.size, z)

    @property
    def health(self):
        return self.snap.health if self.snap is not None else None

    def aim(self, target):
        """Point this actor's look at a player (name), an entity (uuid) or a position; None: ahead."""
        self.aim_at = list(target) if isinstance(target, (tuple, list)) else target

    def _aim_pos(self) -> Vec | None:
        t = self.aim_at
        if t is None:
            return None
        if isinstance(t, list):
            return tuple(float(v) for v in t)
        return self._rt.position_of(t)

    @property
    def facing(self):
        to = self._aim_pos()
        if to is None:
            return (0.0, 0.0, 1.0)
        ex, ey, ez = self.eye
        d = (to[0] - ex, to[1] - ey, to[2] - ez)
        n = math.sqrt(sum(c * c for c in d)) or 1.0
        return (d[0] / n, d[1] / n, d[2] / n)

    @property
    def look(self):
        to = self._aim_pos()
        if to is None:
            return Look(kind="none", pos=self.pos, distance=0.0)
        return Look(kind="entity" if isinstance(self.aim_at, str) else "block", pos=to,
                    distance=math.dist(self.eye, to),
                    entity=self.aim_at if isinstance(self.aim_at, str) else None)
