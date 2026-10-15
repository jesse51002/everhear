"""FakeIo: a fake Minecraft for the core's tests, behaving as 26.3 does where the engine cares
(DESIGN, "26.3 facts already measured"):

- attribute modifiers are keyed by id: adding an id already there is refused ("already present"),
  removing one leaves the others, and the value is vanilla's sum (`attribute_value`);
- potions stack as measured: a stronger one goes on top and the old one waits underneath with its
  own time (which keeps running); a weaker, longer one waits underneath; weaker and shorter is
  refused ("has something stronger"); the same strength and longer replaces; milk and death clear
  them all; modifiers survive a relog (and, by default, a death; `death_drops_modifiers`);
- players join, leave, die, respawn, hold, drop, pick up and use EverHear items; entities exist
  and disappear; every datapack event is numbered and handed over once (`last_n`, as McIo's: a
  counter behind it when opened is a new world);
- the engine's `Damage` with `by=<player>` comes back as that player's own `hit` on the target,
  and a player target's `hurt`, as 26.3 reports it;
- (part 2) a block world: `fill`/`setblock` (32,768 blocks at most, only loaded chunks), stone
  up to `ground` and bedrock at the floor below an empty sky, `probe`, `ground_y`, `ray`,
  `loaded` and `forceload`; motion, entity data, fire, boss bars, spawn points; `entities`,
  `mspt` and `world_id`; and the land, mine, ranged-hit and death-place events.

Tests drive it with the helpers (join, hold, throw, use, kill, advance, ...) and read what the
engine sent in `sent`.
"""

from __future__ import annotations

import contextlib
import math
import time
import uuid as uuidlib
from dataclasses import dataclass, field, replace

from . import registries
from .io import (IoDeadline, AddModifier, BlockData, Bossbar, Command, Damage, Effect, EntityData, EntitySnap, Fill, Forceload,
                 GiveEffect, GiveItem, Gamerule, Ignite, Look, Message, Motion, OpResult, Particle,
                 PlayerSnap, RawEvent, Remove, RemoveModifier, SetBlock, Snapshot, Sound, Spawnpoint,
                 StopSound, Summon, TakeItem, Teleport)

SLOTS = ("mainhand", "offhand", "head", "chest", "legs", "feet")
_AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
_NOT_SOLID = _AIR | {"minecraft:water", "minecraft:lava", "minecraft:short_grass", "minecraft:snow"}
_NO_MOTION = _AIR | {"minecraft:short_grass", "minecraft:tall_grass", "minecraft:snow"}   # the heightmap passes these


@dataclass
class FakePlayer:
    name: str
    uuid: str
    dim: str = "minecraft:overworld"
    pos: tuple = (0.0, 64.0, 0.0)
    yaw: float = 0.0
    pitch: float = 0.0
    health: float = 20.0
    max_health: float = 20.0
    alive: bool = True
    sneaking: bool = False
    gamemode: str = "survival"
    online: bool = True
    on_ground: bool = True
    sprinting: bool = False
    slots: dict = field(default_factory=dict)          # slot -> EverHear item id
    inventory: list = field(default_factory=list)      # the other stacks' EverHear ids
    effects: dict = field(default_factory=dict)        # effect id -> the top Effect (its chain under it)
    modifiers: dict = field(default_factory=dict)      # (attribute, id) -> (amount, operation)
    look: Look | None = None


@dataclass
class FakeEntity:
    uuid: str
    type: str
    dim: str
    pos: tuple
    health: float | None = 20.0
    tags: frozenset = frozenset()
    name: str | None = None
    modifiers: dict = field(default_factory=dict)
    nbt: str | None = None                # the SNBT it was summoned with
    data: list = field(default_factory=list)     # every `data merge` it was given
    motion: tuple | None = None
    owner: str | None = None
    in_ground: bool = False
    effects: dict = field(default_factory=dict)        # effect id -> the top Effect, as a player's


class FakeMirror:
    def __init__(self, io):
        self._io = io
        self.radius = io.mirror_radius

    @property
    def age_s(self):
        return 0.0

    def _all(self):
        """Every entity (players are the snapshot's, as with McMirror)."""
        return [self._io._esnap(e) for e in self._io.by_uuid.values()]

    def entities_near(self, dim, pos, radius):
        return [e for e in self._all() if e.dim == dim and math.dist(e.pos, pos) <= radius]

    def get(self, uuid):
        return next((e for e in self._all() if e.uuid == uuid), None)


def _ticks(seconds: float) -> int:
    return -1 if math.isinf(seconds) else int(round(seconds * 20))


def _longer(a: int, b: int) -> bool:
    """Whether a duration in ticks `a` is longer than `b` (-1 is infinite)."""
    if a == -1:
        return b != -1
    return b != -1 and a > b


def stack_effect(current: Effect | None, new: Effect) -> tuple[Effect | None, bool]:
    """Vanilla's MobEffectInstance.update, as measured on 26.3: (the new top, whether it took)."""
    if current is None:
        return new, True
    if new.amplifier > current.amplifier:
        return replace(new, hidden=current), True
    if new.amplifier < current.amplifier:
        if not _longer(new.ticks, current.ticks):
            return current, False
        under, took = stack_effect(current.hidden, new)
        return replace(current, hidden=under), took
    if _longer(new.ticks, current.ticks):
        return replace(current, ticks=new.ticks), True
    return current, False


def _tick_down(e: Effect | None, ticks: int) -> Effect | None:
    """`ticks` game ticks later: every layer counts down; an expired top lets the next one up."""
    if e is None:
        return None
    hidden = _tick_down(e.hidden, ticks)
    if e.ticks == -1:
        return replace(e, hidden=hidden)
    left = e.ticks - ticks
    if left <= 0:
        return hidden
    return replace(e, ticks=left, hidden=hidden)


class FakeIo:
    """An Io (vfg_engine.io) over an in-memory world."""

    death_drops_modifiers = False

    def __init__(self):
        self.players: dict[str, FakePlayer] = {}
        self.by_uuid: dict[str, FakeEntity] = {}     # every entity but players, by uuid
        self.gamerules: dict[str, str] = {"mob_griefing": "true", "keep_inventory": "false",
                                          "pvp": "true", "fall_damage": "true"}
        self.game_tick = 0
        self.opened = False
        self.closed = False
        self.sent: list = []                 # every op flushed, in order
        self.batches: list[list] = []        # every flush, as a batch
        self.queries: list[str] = []
        self.query_reply = lambda command: ""
        self.query_delay_s = 0.0
        self.read_delay_s = 0.0              # each probe/ground_y/ray/entities/loaded waits this long
        self.deadlines: list[float] = []     # every deadline() given, in seconds
        self._deadline_end: float | None = None
        self._events: list[RawEvent] = []
        self._n = 0
        self.last_n: int | None = 0          # the last event handed over; None: no dedupe at all
        self._fail: list[tuple[type, str, int]] = []
        self.flush_raises: Exception | None = None
        self.snapshot_raises: Exception | None = None
        self.read_backs: list[str] = []
        # part 2: the block world and the rest
        self.ground = 63                     # the top of the stone; the floor is bedrock at -64
        self.blocks: dict[tuple, str] = {}   # (dim, x, y, z) -> block id, where it differs
        self.unloaded: set[tuple] = set()    # (dim, chunk x, chunk z) not loaded
        self.forceloaded: set[tuple] = set()
        self.block_tags: dict[str, set] = {"minecraft:logs": {"minecraft:oak_log", "minecraft:spruce_log"}}
        self.fire: dict[str, float] = {}     # target -> seconds it was set on fire for
        self.bossbars: dict[str, dict] = {}
        self.spawnpoints: dict[str, tuple] = {}
        self.stopped_sounds: list = []
        self.mspt_value = 20.0
        self.world = "fake-world-1"
        self.watched_mined: list[str] = []
        self.block_data: dict[tuple, list] = {}      # (dim, x, y, z) -> [(path, how, snbt)] in order
        self.weather = "clear"                       # "clear", "rain" or "thunder"
        self.mirror_radius = 32
        self.watch_calls = 0

    # ---- Io ------------------------------------------------------------------------------

    def open(self):
        self.opened = True
        if self.last_n is not None and self._n < self.last_n:    # a new world: count from there
            self.last_n = self._n

    def close(self):
        self.closed = True

    def events(self):
        out = [e for e in self._events if self.last_n is None or e.n > self.last_n]
        self._events = []
        if out and self.last_n is not None:
            self.last_n = out[-1].n
        return out

    def snapshot(self, track=()):
        if self.snapshot_raises is not None:
            raise self.snapshot_raises
        self.game_tick += 2
        players = {p.name: self._psnap(p) for p in self.players.values() if p.online}
        tracked = {u: self._esnap(self.by_uuid[u]) for u in track if u in self.by_uuid}
        return Snapshot(self.game_tick, players, tracked)

    def mirror(self):
        return FakeMirror(self)

    @contextlib.contextmanager
    def deadline(self, seconds):
        """As McIo's: the reads inside answer within `seconds`, else IoDeadline. Kept in
        `deadlines`; a read slower than what is left (query_delay_s, read_delay_s) waits it out and
        raises, as a socket timeout would."""
        self.deadlines.append(seconds)
        before = self._deadline_end
        end = time.monotonic() + seconds
        self._deadline_end = end if before is None else min(before, end)
        try:
            yield self
        finally:
            self._deadline_end = before

    def _wait(self, delay, what):
        if not delay:
            return
        left = None if self._deadline_end is None else self._deadline_end - time.monotonic()
        if left is not None and delay > left:
            time.sleep(max(left, 0.0))
            raise IoDeadline("%s did not answer within its caller's deadline" % what)
        time.sleep(delay)

    def query(self, command):
        self.queries.append(command)
        self._wait(self.query_delay_s, repr(command[:60]))
        if command.startswith("execute if predicate") and "weather_check" in command:
            now = {"raining": self.weather in ("rain", "thunder"), "thundering": self.weather == "thunder"}
            asked = "thundering" if "thundering" in command else "raining"
            return "Test passed" if now[asked] else "Test failed"
        return self.query_reply(command)

    def flush(self, ops):
        if self.flush_raises is not None:
            raise self.flush_raises
        self.batches.append(list(ops))
        out = []
        for op in ops:
            self.sent.append(op)
            failed = self._take_failure(op)
            out.append(OpResult(False, "", failed) if failed else self._apply(op))
        return out

    def read_modifiers(self, target):
        self.read_backs.append(target)
        holder = self._holder(target)
        if holder is None:
            raise LookupError("no such target %s" % target)
        return {k: v[0] for k, v in holder.modifiers.items() if k[1].startswith("everhear:")}

    def read_gamerule(self, name):
        return self.gamerules.get(name)

    # ---- failures ------------------------------------------------------------------------

    def fail_next(self, op_type: type, error: str = "fake failure", count: int = 1):
        """The next `count` ops of `op_type` fail with `error`."""
        self._fail.append((op_type, error, count))

    def _take_failure(self, op):
        for i, (t, error, count) in enumerate(self._fail):
            if isinstance(op, t):
                if count <= 1:
                    self._fail.pop(i)
                else:
                    self._fail[i] = (t, error, count - 1)
                return error
        return None

    # ---- applying ops --------------------------------------------------------------------

    def _holder(self, target):
        p = self.players.get(target)
        if p is not None:
            return p if p.online else None
        return self.by_uuid.get(target)

    def _apply(self, op) -> OpResult:
        if isinstance(op, AddModifier):
            h = self._holder(op.target)
            if h is None:
                return OpResult(False, "", "no player or entity %s" % op.target)
            if op.attribute not in registries.ATTRIBUTES:
                return OpResult(False, "", "unknown attribute %s" % op.attribute)
            key = (op.attribute, op.id)
            if key in h.modifiers:
                return OpResult(False, "Modifier %s is already present" % op.id, "already present")
            h.modifiers[key] = (op.amount, op.operation)
            return OpResult(True, "Added modifier %s" % op.id)
        if isinstance(op, RemoveModifier):
            h = self._holder(op.target)
            if h is None:
                return OpResult(False, "", "no player or entity %s" % op.target)
            if h.modifiers.pop((op.attribute, op.id), None) is None:
                return OpResult(False, "", "no modifier %s (not present)" % op.id)
            return OpResult(True, "Removed modifier %s" % op.id)
        if isinstance(op, GiveEffect):
            p = self._holder(op.target)                 # a player or a mob, as on 26.3
            if p is None:
                return OpResult(False, "", "no entity found")
            new = Effect(op.effect, op.amplifier, _ticks(op.seconds))
            top, took = stack_effect(p.effects.get(op.effect), new)
            if not took:
                return OpResult(False, "", "has something stronger")
            p.effects[op.effect] = top
            return OpResult(True, "Applied effect %s to %s" % (op.effect, op.target))
        if isinstance(op, GiveItem):
            p = self.players.get(op.player)
            if p is None or not p.online:
                return OpResult(False, "", "no player")
            if "mainhand" not in p.slots:
                p.slots["mainhand"] = op.item_id
            else:
                p.inventory.append(op.item_id)
            return OpResult(True, "Gave 1 [%s] to %s" % (op.name or op.item_id, op.player))
        if isinstance(op, TakeItem):
            p = self.players.get(op.player)
            if p is None or not p.online:
                return OpResult(False, "", "no player")
            for s in [s for s, i in p.slots.items() if i == op.item_id]:
                del p.slots[s]
            p.inventory = [i for i in p.inventory if i != op.item_id]
            return OpResult(True, "Removed items from %s" % op.player)
        if isinstance(op, Summon):
            if op.uuid in self.by_uuid:
                return OpResult(False, "", "an entity with that uuid exists")
            if op.entity_type not in registries.ENTITY_TYPES:
                return OpResult(False, "", "unknown entity type")
            health = 20.0
            if op.nbt and "Health:" in op.nbt:
                health = float(op.nbt.split("Health:", 1)[1].split("f", 1)[0])
            self.by_uuid[op.uuid] = FakeEntity(op.uuid, op.entity_type, op.dim, op.pos, health,
                                                frozenset(op.tags), nbt=op.nbt)
            return OpResult(True, "Summoned new %s" % op.entity_type)
        if isinstance(op, Remove):
            if self.by_uuid.pop(op.uuid, None) is None:
                return OpResult(False, "", "no entity found")
            return OpResult(True, "Removed 1 entity")
        if isinstance(op, Damage):
            return self._damage(op)
        if isinstance(op, Teleport):
            h = self._holder(op.target)
            if h is None:
                return OpResult(False, "", "no entity found")
            h.pos, h.dim = op.pos, op.dim
            return OpResult(True, "Teleported")
        if isinstance(op, Gamerule):
            if op.name not in registries.GAME_RULES:
                return OpResult(False, "", "unknown gamerule %s" % op.name)
            self.gamerules[op.name] = op.value
            return OpResult(True, "Gamerule %s is now set to: %s" % (op.name, op.value))
        if isinstance(op, Command) and op.text.split()[:1] == ["weather"]:
            self.weather = op.text.split()[1]
            return OpResult(True, "Changing to %s" % self.weather)
        if isinstance(op, (Particle, Sound, Message, Command)):
            return OpResult(True, "")
        if isinstance(op, Fill):
            return self._fill(op)
        if isinstance(op, SetBlock):
            return self._fill(Fill(op.dim, op.pos, op.pos, op.block, None, op.strict, op.mode))
        if isinstance(op, BlockData):
            if not self._chunk_loaded(op.dim, op.pos[0], op.pos[2]):
                return OpResult(False, "", "That position is not loaded")
            text = "data modify block %d %d %d %s %s value %s" % (*op.pos, op.path, op.how, op.snbt)
            if len(text.encode()) > 1446:
                return OpResult(False, "", "the command is over 1446 bytes, RCON's limit")
            self.block_data.setdefault((op.dim, *op.pos), []).append((op.path, op.how, op.snbt))
            return OpResult(True, "Modified block data of %d, %d, %d" % tuple(op.pos))
        if isinstance(op, Forceload):
            chunks = {(op.dim, cx, cz) for cx in range(min(op.a[0], op.b[0]) >> 4, (max(op.a[0], op.b[0]) >> 4) + 1)
                      for cz in range(min(op.a[1], op.b[1]) >> 4, (max(op.a[1], op.b[1]) >> 4) + 1)}
            if op.add:
                self.forceloaded |= chunks
            else:
                self.forceloaded -= chunks
            return OpResult(True, "Marked %d chunk(s)" % len(chunks))
        if isinstance(op, Motion):
            h = self._holder(op.target)
            if h is None:
                return OpResult(False, "", "no entity found")
            h.motion = tuple(op.vec)
            return OpResult(True, "Modified entity data")
        if isinstance(op, EntityData):
            e = self.by_uuid.get(op.target)
            if e is None:
                return OpResult(False, "", "no entity found")
            e.data.append(op.snbt)
            return OpResult(True, "Modified entity data of %s" % op.target)
        if isinstance(op, Ignite):
            if self._holder(op.target) is None:
                return OpResult(False, "", "no entity found")
            self.fire[op.target] = op.seconds
            return OpResult(True, "Set %s on fire" % op.target)
        if isinstance(op, Bossbar):
            if op.remove:
                if self.bossbars.pop(op.id, None) is None:
                    return OpResult(False, "", "no bossbar %s" % op.id)
                return OpResult(True, "Removed bossbar")
            bar = self.bossbars.setdefault(op.id, {"name": op.id, "color": "white", "value": 0, "max": 100,
                                                   "players": ()})
            for key in ("name", "color", "value", "max", "players"):
                if getattr(op, key) is not None:
                    bar[key] = getattr(op, key)
            return OpResult(True, "Bossbar %s set" % op.id)
        if isinstance(op, StopSound):
            self.stopped_sounds.append((op.players, op.name))
            return OpResult(True, "Stopped sound")
        if isinstance(op, Spawnpoint):
            if op.player not in self.players:
                return OpResult(False, "", "no player")
            self.spawnpoints[op.player] = (op.dim, tuple(op.pos))
            return OpResult(True, "Set spawn point")
        return OpResult(False, "", "unknown op %r" % (op,))

    # ---- blocks (part 2) -----------------------------------------------------------------

    def block_at(self, dim, x, y, z) -> str:
        got = self.blocks.get((dim, x, y, z))
        if got is not None:
            return got
        if y == -64:
            return "minecraft:bedrock"
        if y < -64 or y > 319:
            return "minecraft:void_air"
        return "minecraft:stone" if y <= self.ground else "minecraft:air"

    def set_block(self, dim, pos, block):
        self.blocks[(dim, int(pos[0]), int(pos[1]), int(pos[2]))] = block

    def _chunk_loaded(self, dim, x, z) -> bool:
        key = (dim, int(x) >> 4, int(z) >> 4)
        return key not in self.unloaded or key in self.forceloaded

    def _fill(self, op: Fill) -> OpResult:
        lo = [min(op.a[i], op.b[i]) for i in range(3)]
        hi = [max(op.a[i], op.b[i]) for i in range(3)]
        volume = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
        if volume > 32768:
            return OpResult(False, "", "Too many blocks in the specified area (maximum 32768, specified %d)" % volume)
        for x in (lo[0], hi[0]):
            for z in (lo[2], hi[2]):
                if not self._chunk_loaded(op.dim, x, z):
                    return OpResult(False, "", "That position is not loaded")
        changed = 0
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    edge = x in (lo[0], hi[0]) or y in (lo[1], hi[1]) or z in (lo[2], hi[2])
                    now = self.block_at(op.dim, x, y, z)
                    want = op.block
                    if op.mode == "keep" and now != "minecraft:air":
                        continue
                    if op.mode == "outline" and not edge:
                        continue
                    if op.mode == "hollow" and not edge:
                        want = "minecraft:air"
                    if op.replace is not None and not self._matches(now, op.replace):
                        continue
                    if now != want:
                        self.blocks[(op.dim, x, y, z)] = want
                        changed += 1
        if not changed:
            return OpResult(False, "", "No blocks were filled")
        return OpResult(True, "Successfully filled %d block(s)" % changed)

    def _matches(self, block, test) -> bool:
        if test.startswith("#"):
            return block in self.block_tags.get(test[1:], set())
        return block == (test if ":" in test else "minecraft:" + test)

    # ---- reads (part 2) ------------------------------------------------------------------

    def probe(self, dim, points, test):
        self._wait(self.read_delay_s, "probe")
        out = []
        for x, y, z in points:
            block = self.block_at(dim, x, y, z)
            if test == "solid":
                out.append(block not in _NOT_SOLID)
            elif test == "open":
                out.append(block in _AIR and self.block_at(dim, x, y + 1, z) in _AIR)
            else:
                out.append(self._matches(block, test))
        return out

    def ground_y(self, dim, x, z):
        """Where feet stand on column x, z, as 26.3's motion_blocking_no_leaves heightmap gives it:
        one above the highest block that blocks movement (fluids count, leaves and plants do not)."""
        self._wait(self.read_delay_s, "ground_y")
        if not self._chunk_loaded(dim, x, z):
            return None
        for y in range(319, -65, -1):
            block = self.block_at(dim, x, y, z)
            if block not in _NO_MOTION and not block.endswith("_leaves"):
                return y + 1
        return None

    def ray(self, dim, origin, direction, length, through_blocks=False):
        self._wait(self.read_delay_s, "ray")
        n = math.sqrt(sum(c * c for c in direction)) or 1.0
        d = tuple(c / n for c in direction)
        steps = int(length / 0.1)
        for i in range(1, steps + 1):
            p = tuple(origin[k] + d[k] * i * 0.1 for k in range(3))
            cell = tuple(math.floor(c) for c in p)
            block = self.block_at(dim, *cell)
            if not through_blocks and block not in _NOT_SOLID:
                return Look("block", p, i * 0.1, cell, block)
        end = tuple(origin[k] + d[k] * length for k in range(3))
        return Look("none", end, float(length))

    def entities(self, dim, center, radius, types=None):
        self._wait(self.read_delay_s, "entities")
        return [self._esnap(e) for e in self.entities_by_type(types)
                if e.dim == dim and math.dist(e.pos, center) <= radius and self._chunk_loaded(dim, e.pos[0], e.pos[2])]

    def entities_by_type(self, types):
        return [e for e in self.by_uuid.values() if not types or e.type in types]

    def loaded(self, dim, x, z):
        self._wait(self.read_delay_s, "loaded")
        return self._chunk_loaded(dim, x, z)

    def mspt(self):
        return self.mspt_value

    def world_id(self):
        return self.world

    def _damage(self, op: Damage) -> OpResult:
        attacker = self.players.get(op.by) if op.by else None
        attacker = attacker if attacker is not None and attacker.online and attacker.alive else None
        p = self.players.get(op.target)
        if p is not None and p.online:
            if not p.alive:
                return OpResult(False, "", "no entity found")
            if attacker is not None:       # 26.3 reports damage by a player as that player's hit
                self._event("hit", attacker.name, {"target": p.name, "target_type": "minecraft:player",
                                                   "item": attacker.slots.get("mainhand")})
            self._event("hurt", p.name, {"amount": op.amount, "attacker": op.by,
                                         "attacker_type": "minecraft:player" if attacker else None})
            p.health = max(0.0, p.health - op.amount)
            if p.health <= 0:
                self.kill(p.name)
            return OpResult(True, "Applied %g damage to %s" % (op.amount, op.target))
        e = self.by_uuid.get(op.target)
        if e is None:
            return OpResult(False, "", "no entity found")
        dies = e.health is not None and e.health - op.amount <= 0
        if e.health is not None:
            e.health -= op.amount
        if dies:
            del self.by_uuid[op.target]
            if attacker is not None:
                # a mob is dead before the datapack reads it: its kill, and the killing blow's hit
                # after it, name no target (datapack/README.md)
                self._event("kill", attacker.name, {"target_type": e.type})
                self._event("hit", attacker.name, {"target_type": e.type,
                                                   "item": attacker.slots.get("mainhand")})
        elif attacker is not None:
            self._event("hit", attacker.name, {"target": e.uuid, "target_type": e.type,
                                               "item": attacker.slots.get("mainhand")})
        return OpResult(True, "Applied %g damage to %s" % (op.amount, op.target))

    # ---- snapshots -----------------------------------------------------------------------

    def _psnap(self, p: FakePlayer) -> PlayerSnap:
        yaw, pitch = math.radians(p.yaw), math.radians(p.pitch)
        facing = (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        eye = (p.pos[0], p.pos[1] + 1.62, p.pos[2])
        look = p.look or Look("none", tuple(eye[i] + 5 * facing[i] for i in range(3)), 5.0)
        carried = tuple(list(p.slots.values()) + list(p.inventory))
        return PlayerSnap(name=p.name, uuid=p.uuid, dim=p.dim, pos=p.pos, eye=eye, yaw=p.yaw,
                          pitch=p.pitch, facing=facing, health=p.health, max_health=p.max_health,
                          alive=p.alive, sneaking=p.sneaking, gamemode=p.gamemode,
                          held=dict(p.slots), carried=carried, effects=tuple(p.effects.values()),
                          looking_at=look, on_ground=p.on_ground, sprinting=p.sprinting)

    def _esnap(self, e: FakeEntity) -> EntitySnap:
        return EntitySnap(e.uuid, e.type, e.dim, e.pos, e.health, e.tags, e.name, e.owner, e.in_ground)

    # ---- test helpers --------------------------------------------------------------------

    def _event(self, kind, player, data=None):
        self._n += 1
        self._events.append(RawEvent(self._n, kind, player, dict(data or {}), self.game_tick))

    def join(self, name, pos=(0.0, 64.0, 0.0), **kw) -> FakePlayer:
        p = self.players.get(name)
        if p is None:
            p = self.players[name] = FakePlayer(name, str(uuidlib.uuid4()), pos=pos, **kw)
        p.online = True
        return p

    def leave(self, name):
        self.players[name].online = False

    def hold(self, name, item, slot="mainhand"):
        self.players[name].slots[slot] = item

    def unhold(self, name, slot="mainhand") -> str | None:
        return self.players[name].slots.pop(slot, None)

    def throw(self, giver, taker, slot="mainhand", to_slot="mainhand"):
        """`giver` drops what is in `slot` and `taker` picks it up into `to_slot`."""
        item = self.unhold(giver, slot)
        if item is None:
            raise LookupError("%s holds nothing in %s" % (giver, slot))
        self.players[taker].slots[to_slot] = item

    def use(self, name, slot="mainhand"):
        item = self.players[name].slots.get(slot)
        if item is None:
            raise LookupError("%s holds no EverHear item in %s" % (name, slot))
        self._event("use", name, {"item": item, "slot": slot})

    def hit(self, name, target, target_type="minecraft:zombie"):
        self._event("hit", name, {"target": target, "target_type": target_type,
                                  "item": self.players[name].slots.get("mainhand")})

    def sneak(self, name, on=True):
        self.players[name].sneaking = on

    def look_at(self, name, pos, kind="block"):
        p = self.players[name]
        eye = (p.pos[0], p.pos[1] + 1.62, p.pos[2])
        p.look = Look(kind, tuple(float(c) for c in pos), math.dist(eye, pos))

    def move(self, name, pos):
        self.players[name].pos = tuple(float(c) for c in pos)

    def kill(self, name):
        """The player dies: effects go (as in vanilla), held items drop, a death event (with where)."""
        p = self.players[name]
        p.alive, p.health = False, 0.0
        p.effects.clear()
        p.slots.clear()
        p.inventory.clear()
        if self.death_drops_modifiers:
            p.modifiers.clear()
        self._event("death", name, {"pos": list(p.pos), "dim": p.dim})

    def respawn(self, name):
        p = self.players[name]
        p.alive, p.health = True, p.max_health
        self._event("respawn", name)

    def drink_milk(self, name):
        self.players[name].effects.clear()
        self._event("milk", name)

    def add_entity(self, type="minecraft:zombie", pos=(0.0, 64.0, 0.0), health=20.0, dim="minecraft:overworld"):
        u = str(uuidlib.uuid4())
        self.by_uuid[u] = FakeEntity(u, type, dim, tuple(pos), health)
        return u

    def kill_entity(self, uuid):
        self.by_uuid.pop(uuid, None)

    def land(self, name, item=None, pos=(0.0, 64.0, 0.0), target=None, target_type=None,
             projectile_type="minecraft:arrow") -> str:
        """`name`'s arrow or trident (shot from EverHear `item`, or none) landed at `pos` or hit
        `target`; its uuid."""
        u = str(uuidlib.uuid4())
        data = {"item": item, "projectile": u, "projectile_type": projectile_type, "pos": list(pos),
                "dim": self.players[name].dim, "hit": "block" if target is None else "entity"}
        if target is not None:
            data.update(target=target, target_type=target_type)
        self._event("land", name, data)
        return u

    def watch_mined(self, blocks):
        self.watched_mined = list(blocks)
        self.watch_calls += 1
        return [b for b in self.watched_mined if b not in registries.BLOCKS]

    def mine(self, name, block, pos) -> bool:
        """`name` broke a `block` at `pos`: a `mine` event only when that kind is watched (as on
        26.3, where each watched kind is a minecraft.mined objective). Whether it was reported."""
        if block not in self.watched_mined:
            return False
        self._event("mine", name, {"block": block, "pos": list(pos), "count": 1})
        return True

    def shoot(self, name, target, target_type="minecraft:zombie"):
        """A ranged hit: `name`'s arrow hit `target`."""
        self._event("hit", name, {"target": target, "target_type": target_type, "ranged": True,
                                  "item": self.players[name].slots.get("mainhand")})

    def new_world(self):
        """The world is replaced: the datapack's counter starts again at 0, its queue empty."""
        self._n = 0
        self._events = []

    def hand_again(self, events):
        """After a crash, events already handed over come again (a fresh McIo has no memory)."""
        self._events = list(events) + self._events
        self.last_n = 0

    def advance(self, seconds: float):
        """Game time passes: effects count down (every layer)."""
        ticks = int(round(seconds * 20))
        self.game_tick += ticks
        for p in list(self.players.values()) + list(self.by_uuid.values()):
            for k in list(p.effects):
                e = _tick_down(p.effects[k], ticks)
                if e is None:
                    del p.effects[k]
                else:
                    p.effects[k] = e

    def attribute_value(self, target, attribute, base=1.0) -> float:
        """Vanilla's value: base plus add_value, times (1 + add_multiplied_base), times each
        (1 + add_multiplied_total)."""
        h = self.players.get(target) or self.by_uuid.get(target)
        mods = [v for (a, _), v in h.modifiers.items() if a == attribute]
        value = base + sum(m[0] for m in mods if m[1] == "add_value")
        value *= 1 + sum(m[0] for m in mods if m[1] == "add_multiplied_base")
        for m in mods:
            if m[1] == "add_multiplied_total":
                value *= 1 + m[0]
        return value

    def ops_of(self, op_type) -> list:
        return [op for op in self.sent if isinstance(op, op_type)]
