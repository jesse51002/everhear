"""The engine's books: every running instance, every being, and the one way content is called
(the game engine spec, "One loop, in order", "Live updates" and "When something goes wrong").

- `records` holds each instance as names and data (vfg_engine.records); `objects` holds its
  content object, rebuilt from (class, config, state) whenever the class is swapped.
- `call` is the only way content runs: through the Guard (timed, cut over the limit), and an error
  ends only that instance (in an ability's cast, only that run), with its cleanup.
- Work a call asks for that runs content itself (a cast, a mark's start, a spawned actor's
  start) is queued with `later` and done right after the call returns, so content never runs
  inside content and each piece is timed on its own.
- Ending anything removes what it made: modifiers (by leaving `desired`), listens, timers,
  potions it keeps up, world-setting holds, spawned entities, grants it lent and, for a scenario,
  the marks it applied.

The loop's phases (vfg_engine.engine) call into here and into the kind modules: items (holders),
abilities (casts, runs), marks, actors, scenarios.
"""

from __future__ import annotations

import collections
import logging
import math
import re
from typing import Callable

from . import abilities, actors, marks, scenarios
from .beings import ActorBeing, Being, Player
from .config import value_of
from .content import ticks_every
from .ctx import Ctx, RunView
from .events import Event, event_kind
from .guard import Guard
from .handle import Handle
from .incantations import Bindings
from .io import (Bossbar, Damage, EntitySnap, Forceload, Look, Particle, PlayerSnap, Remove, Snapshot,
                 Sound)
from .places import Places
from .modifiers import Modifiers
from .records import Owned, Record
from .worldsettings import WorldSettings

log = logging.getLogger("vfg_engine")

RUN_ENDS_KEPT = 500       # why recent runs ended, for the cast op's answer
PLAYER = "minecraft:player"
OVERWORLD = "minecraft:overworld"
EMPTY = Snapshot(game_tick=0, players={}, tracked={})


class _NoMirror:
    age_s = math.inf
    radius = 0.0

    def entities_near(self, dim, pos, radius):
        return []

    def get(self, uuid):
        return None


class Runtime:
    def __init__(self, io, settings, clock: Callable[[], float]):
        self.io = io
        self.settings = settings
        self.clock = clock
        self.now = clock()
        self.guard = Guard(settings.slow_ms, settings.cut_ms, getattr(settings, "io_ms", 250.0))
        self.snapshot: Snapshot = EMPTY
        self._mirror = None

        self.classes = {}                        # id -> loader.ClassInfo
        self.files: dict[str, set[str]] = {}     # file -> the ids loaded from it
        self.candidates: dict[str, dict] = {}    # id -> {file: ClassInfo}: every folder's version;
                                                 # the latest folder's runs (reload.effective)
        self.modules: set[str] = set()           # the content modules this engine put in sys.modules

        self.records: dict[str, Record] = {}
        self.objects: dict[str, object] = {}
        self.handles: dict[str, Handle] = {}
        self.orphans: list[dict] = []            # saved records whose class is not loaded now
        self.players: dict[str, Player] = {}
        self.actors: dict[str, ActorBeing] = {}  # uuid -> body
        self.raw_spawned: dict[str, str] = {}    # uuid of a vanilla entity spawned -> its owner
        self.confirmed: set[str] = set()         # uuids whose summon took
        self.removing: dict[str, float] = {}     # uuid -> when its Remove was sent (actors.sweep)
        self.mobs: dict[str, ActorBeing] = {}    # uuid -> body of a vanilla mob that bears a mark
        self.seen: set[str] = set()              # watched uuids seen at least once (then missing: gone)
        self.places = Places()
        self.asks: list = []                     # (instance, command, method) to run after the batch
        self.answers: list = []                  # (instance, method, reply) handed over next tick
        self.results: collections.OrderedDict = collections.OrderedDict()   # ended scenarios' results
        self.read_costs: dict[str, tuple[int, float]] = {}   # live read kind -> (count, total ms)
        self._mspt = (0.0, -math.inf)            # (value, when read)
        from .blocks import BlockQueue
        self.block_queue = BlockQueue(self)

        self.modifiers = Modifiers()
        self.world_settings = WorldSettings()
        self.bindings = Bindings(settings.export)
        self.broken: dict[str, str] = {}         # "cid|user" -> why its instance ended in error
        self.run_ends: collections.OrderedDict = collections.OrderedDict()
        self.read_back_due: set[str] = set()     # targets whose modifiers are read back this tick

        self.ops: list = []                      # (op, callback) queued this tick
        self._later: collections.deque = collections.deque()
        self._draining = False
        self.counter = 0
        self.current: str | None = None          # the instance whose content is running now

    # ---- lookups -------------------------------------------------------------------------

    def io_mirror(self):
        if self._mirror is None:
            try:
                self._mirror = self.io.mirror()
            except Exception as exc:
                log.warning("no mirror: %s", exc)
                return _NoMirror()
        return self._mirror

    def player(self, name) -> Player | None:
        if name in self.players:
            return self.players[name]
        low = str(name).lower()
        return next((p for k, p in self.players.items() if k.lower() == low), None)

    def ensure_player(self, name: str) -> Player:
        p = self.player(name)
        if p is None:
            p = self.players[name] = Player(self, name)
        return p

    def being(self, key) -> Being | None:
        if key is None:
            return None
        return self.actors.get(key) or self.mobs.get(key) or self.player(key)

    def target_of(self, x) -> str | None:
        """What an op names someone by, from a name, a uuid, a being, a snapshot or a look."""
        if x is None:
            return None
        if isinstance(x, Being):
            return x.key
        if isinstance(x, PlayerSnap):
            return x.name
        if isinstance(x, EntitySnap):
            return self.player_name(x.uuid) or (x.name if x.type == PLAYER and x.name else x.uuid)
        if isinstance(x, Look):
            return (self.player_name(x.entity) or x.entity) if x.entity else None
        if isinstance(x, str) and x:
            p = self.player(x)
            return p.key if p is not None else (self.player_name(x) or x)
        return None

    def player_name(self, uuid) -> str | None:
        """The name of the player with this uuid, if one is known: a player is always named by
        name (the mirror lists players by uuid too)."""
        if not uuid:
            return None
        low = str(uuid).lower()
        for p in self.snapshot.players.values():
            if p.uuid and p.uuid.lower() == low:
                return p.name
        for name, p in self.players.items():
            if p.uuid and p.uuid.lower() == low:
                return name
        return None

    def position_of(self, key) -> tuple | None:
        p = self.snapshot.players.get(key)
        if p is not None:
            return p.pos
        b = self.being(key)
        if isinstance(b, Player) and b.snap is not None:
            return b.snap.pos
        e = self.snapshot.tracked.get(key) or self.io_mirror().get(key)
        return e.pos if e is not None else None

    def records_of(self, user, kind=None) -> list[Record]:
        return [r for r in self.records.values()
                if r.user == user and (kind is None or r.kind == kind) and not r.ending]

    def tracked(self) -> list[str]:
        """The uuids the snapshot reports on: every actor, every entity content spawned, every mob
        bearing a mark and every entity an instance holds a modifier on."""
        out = dict.fromkeys(self.actors)
        out.update(dict.fromkeys(self.raw_spawned))
        out.update(dict.fromkeys(self.mobs))
        for rec in self.records.values():
            for owned in [rec.owned] + [r.owned for r in rec.runs.values()]:
                for key in owned.modifiers:
                    if "|" in key:
                        target = key.split("|", 1)[0]
                        if self.player(target) is None:
                            out[target] = None
        for target, _attr, _id in self.modifiers.actual:   # until what we put on it is taken off
            if self.player(target) is None:
                out[target] = None
        return list(out)

    def online(self, key) -> bool:
        """Whether ops can reach this target now: a player online and alive, or an existing entity."""
        p = self.snapshot.players.get(key)
        if p is not None:
            return p.alive
        return key in self.snapshot.tracked

    # ---- instances -----------------------------------------------------------------------

    def new_iid(self, kind: str, cid: str) -> str:
        self.counter += 1
        return "%s:%s#%d" % (kind, cid, self.counter)

    def add(self, rec: Record) -> Record:
        """Put a new instance in the books. ValueError, in words, when its State cannot be made:
        nothing is added then."""
        info = self.classes[rec.cid]
        if rec.state is None:
            rec.state = self.new_state(info, rec.iid)
        self.records[rec.iid] = rec
        self._make_object(rec, info)
        return rec

    def new_state(self, info, label: str):
        """A fresh State of `info`'s class. Content's own code may run (defaults, validators), so it
        is a content call: inside another content call it counts against that one, otherwise it is
        timed and cut on its own. ValueError, in words, when it fails; it never raises anything
        else into the engine."""
        if self.guard.active is not None:
            try:
                return info.cls.State()
            except Exception as exc:
                raise ValueError("%s.State() could not be made: %s" % (info.name, _one_line(exc))) from None
        out = self.guard.call("%s.State" % label, info.cls.State)
        if not out.ok:
            raise ValueError("%s.State() could not be made: %s" % (info.name, _one_line(out.error)))
        return out.value

    def args_of(self, rec: Record):
        """The instance's Args model, from its saved args (a Place arg saved as its Spot)."""
        handle = self.handles.get(rec.iid)
        if handle is None:
            return None
        cached = getattr(handle, "args_model", None)
        if cached is None or cached[0] is not rec.args:
            cls = getattr(self.classes[rec.cid].cls, "Args", None)
            model = cls.model_validate(rec.args) if cls is not None else None
            handle.args_model = cached = (rec.args, model)
        return cached[1]

    def _make_object(self, rec, info):
        obj = info.cls.__new__(info.cls)
        handle = self.handles.get(rec.iid) or Handle(self, rec)
        handle.record = rec
        handle.args_model = None
        object.__setattr__(obj, "_h", handle)
        self.objects[rec.iid] = obj
        self.handles[rec.iid] = handle

    def later(self, fn):
        self._later.append(fn)
        if not self._draining and self.guard.active is None:
            self.drain()

    def drain(self):
        if self._draining:
            return
        self._draining = True
        try:
            while self._later:
                fn = self._later.popleft()
                try:
                    fn()
                except Exception:
                    log.exception("engine work after a call failed")
        finally:
            self._draining = False

    def ctx_for(self, rec: Record, run=None) -> Ctx:
        user = self.being(rec.user)
        source = self.being(rec.source) if rec.source and rec.source != rec.user else None
        running = []
        if run is not None:
            running = [RunView(self, rec, r) for n, r in rec.runs.items() if n != run.n]
        return Ctx(self, user, source, running)

    def call(self, rec: Record, method: str, *args, run=None):
        """Call content: `method(ctx, *args)` on the instance. None when there is no such
        method; else the Guard's Outcome. A failure ends the instance (or, in a cast, its run)."""
        obj = self.objects.get(rec.iid)
        fn = getattr(obj, method, None) if obj is not None else None
        if fn is None or not callable(fn):
            return None
        handle = self.handles[rec.iid]
        handle.run = run
        self.current = rec.iid
        try:
            ctx = self.ctx_for(rec, run)
            out = self.guard.call("%s.%s" % (rec.iid, method), fn, ctx, *args)
        finally:
            handle.run = None
            self.current = None
        self._after_call(rec, method, out, run)
        return out

    def formula(self, rec: Record, name: str, run=None):
        """A Config formula (damage, duration_s) worked out with this instance's ctx, as a content
        call. (ok, value or error words)."""
        config = self.classes[rec.cid].config
        out = self.guard.call("%s.Config.%s" % (rec.iid, name),
                              lambda: value_of(config, name, self.ctx_for(rec, run)))
        self._after_call(rec, "Config." + name, out, run)
        return (True, out.value) if out.ok else (False, out.error)

    def guarded(self, label: str, fn):
        """Run a piece of content code the engine needs a value from (a Config formula for
        `meta`, an actor's damage at its summon): inside a content call it counts against that
        call; otherwise it is a call of its own, cut at the limit. ValueError when it fails."""
        if self.guard.active is not None:
            return fn()
        out = self.guard.call(label, fn)
        if not out.ok:
            raise ValueError("%s failed: %s" % (label, out.error))
        return out.value

    def _after_call(self, rec, method, out, run):
        if out.ms > self.settings.slow_ms:
            log.info("slow call: %s.%s took %.1f ms%s", rec.iid, method, out.ms,
                     " (and %.1f ms waiting on Minecraft)" % out.io_ms if out.io_ms >= 1 else "")
            if self.guard.note_slow(rec.iid, self.now):
                log.warning("flagged: %s keeps going slow (%s)", rec.iid, self.guard.flagged[rec.iid])
        if not out.ok:
            log.error("%s.%s failed: %s%s", rec.iid, method, out.error,
                      " (at %s)" % out.detail if out.detail else "")
            reason = out.error if out.cut else "error: %s" % out.error
            if run is not None and run.n in rec.runs:
                self.end_run(rec, run.n, reason)
            else:
                if rec.kind in ("item", "ability"):
                    self.broken["%s|%s" % (rec.cid, rec.user)] = reason
                self.end_record(rec, reason)
        self.drain()

    def start(self, rec: Record):
        """Start an instance: its tick timer, its end time, then its start(ctx)."""
        if rec.iid not in self.records or rec.started or rec.ending:
            return
        user = self.being(rec.user)
        if isinstance(user, Player) and not user.online:
            rec.paused_at = rec.paused_at or self.now      # started when they come back
            return
        rec.started = True
        info = self.classes[rec.cid]
        every = ticks_every(info.cls)
        if every and not (rec.kind == "ability" and abilities.triggered(info.cls)):
            self.add_tick_timer(rec, None, every)
        if rec.kind == "mark":
            marks.set_end(self, rec)
        elif rec.kind == "scenario":
            scenarios.set_end(self, rec)
        if rec.iid in self.records:
            self.call(rec, "start")

    def add_tick_timer(self, rec, run, period):
        from .records import Timer
        rec.timers.append(Timer("tick" if run is None else "tick/%d" % run, self.now + period, "tick",
                                [], period, run, tick=True))

    # ---- ending --------------------------------------------------------------------------

    def end_record(self, rec: Record, reason: str):
        if rec.ending or rec.iid not in self.records:
            return
        rec.ending = True
        stopped = reason.startswith("stopped")
        for n in list(rec.runs):
            self.end_run(rec, n, reason)
        if rec.started:
            self.call(rec, "stop", reason)
        self.release(rec.iid, rec.owned, stopped)
        self.block_queue.drop(rec.iid)
        for other in list(self.records.values()):
            if other.owner == rec.iid:
                self.end_record(other, ("stopped: its owner %s was stopped" if stopped
                                        else "its owner %s ended") % rec.iid)
        if rec.kind == "scenario" or rec.notes.get("result") is not None:
            self.results[rec.iid] = dict(rec.notes.get("result") or {}, reason=reason, at=self.now)
            while len(self.results) > RUN_ENDS_KEPT:
                self.results.popitem(last=False)
        self.records.pop(rec.iid, None)
        self.objects.pop(rec.iid, None)
        self.handles.pop(rec.iid, None)
        self.guard.forget(rec.iid)
        being = self.being(rec.user)
        if rec.kind == "ability" and being is not None and being.grants.get(rec.cid) == rec.iid:
            del being.grants[rec.cid]
        if rec.kind == "actor":
            actors.ended(self, rec, reason)
        if rec.user in self.mobs and not self.records_of(rec.user):
            self.mobs.pop(rec.user, None)          # a mob that bears no mark any more
        log.info("%s ended (%s)", rec.iid, reason)

    def end_run(self, rec: Record, n: int, reason: str):
        run = rec.runs.pop(n, None)
        if run is None:
            return
        rec.subs = [s for s in rec.subs if s.run != n]
        rec.timers = [t for t in rec.timers if t.run != n]
        self.release("%s/run%d" % (rec.iid, n), run.owned)
        abilities.run_ended(self, rec, run, reason)
        self.run_ends[(rec.iid, n)] = reason
        while len(self.run_ends) > RUN_ENDS_KEPT:
            self.run_ends.popitem(last=False)
        if not rec.runs and rec.notes.get("release_after_runs") and not rec.ending:
            self.end_record(rec, "%s; its last cast is done" % rec.notes["release_after_runs"])

    def release(self, holder: str, owned: Owned, stopped: bool = False):
        """Take away what an instance (or a run) made. Spawns made with keep=True stay in the
        world unless it was stopped."""
        owned.modifiers.clear()
        owned.potions.clear()
        owned.areas.clear()
        for uuid in list(owned.kept):
            if stopped:
                actors.unspawn(self, uuid, "its owner %s was stopped" % holder)
            else:
                self.raw_spawned.pop(uuid, None)      # left in the world, no longer tracked
                self.confirmed.discard(uuid)
        owned.kept.clear()
        for bar in owned.bossbars:
            self.queue(Bossbar(bar, remove=True))
        owned.bossbars.clear()
        for dim, x1, z1, x2, z2 in owned.forceloads:
            self.queue(Forceload(dim, (x1, z1), (x2, z2), add=False))
        owned.forceloads.clear()
        if owned.undo:
            self.block_queue.undo(owned.undo)
            owned.undo.clear()
        if owned.holds:
            self.world_settings.release_prefix(holder)
            owned.holds.clear()
        for uuid in list(owned.spawned):
            actors.unspawn(self, uuid, "its owner %s ended" % holder)
        owned.spawned.clear()
        for who, ability in list(owned.grants):
            being = self.being(who)
            iid = being.grants.get(ability) if being is not None else None
            lent = self.records.get(iid) if iid else None
            if lent is None:
                continue
            if lent.runs and not lent.ending:
                # a cast already made finishes (a fireball thrown, then the staff dropped); the
                # grant goes when its last run ends, and casts nothing new meanwhile
                lent.owner = None
                lent.notes["lent_by"] = holder
                lent.notes["release_after_runs"] = "lent by %s, which ended" % holder
            else:
                self.end_record(lent, "lent by %s, which ended" % holder)
        owned.grants.clear()
        for iid in list(owned.marks):
            if iid in self.records:
                self.end_record(self.records[iid], "applied by %s, which ended" % holder)
        owned.marks.clear()

    # ---- events and timers ---------------------------------------------------------------

    def mined_blocks(self) -> set[str]:
        """Every block kind a live instance's Mine listen watches."""
        return {b for rec in self.records.values() if not rec.ending
                for s in rec.subs if s.kind == "mine" and s.blocks for b in s.blocks}

    def hears(self, rec: Record, sub, ev: Event) -> bool:
        if getattr(sub, "blocks", None) and getattr(ev, "block", None) not in sub.blocks:
            return False
        if rec.kind == "scenario" or sub.about == "anyone":
            return True
        if getattr(ev, "instance", None) == rec.iid:          # an area this instance watches
            return True
        if event_kind(ev) == "gone" and _spawned_by(rec, ev.who):
            return True
        if ev.who is None or ev.who != rec.user:
            return False
        if rec.kind == "item" and event_kind(ev) in ("use", "held", "unheld", "land"):
            return getattr(ev, "item", None) == rec.cid          # this item's, not another's
        return True

    def deliver(self, ev: Event):
        kind = event_kind(ev)
        for rec in list(self.records.values()):
            if rec.iid not in self.records or rec.paused_at is not None or not rec.started or rec.ending:
                continue
            if rec.notes.get("lingering") is not None and kind != "land":
                continue                       # a let-go item hears only its projectiles' Land
            for sub in list(rec.subs):
                if sub.kind != kind or not self.hears(rec, sub, ev):
                    continue
                run = rec.runs.get(sub.run) if sub.run is not None else None
                if sub.run is not None and (run is None or not run.fired):
                    continue
                self.call(rec, sub.method, ev, run=run)
                if rec.iid not in self.records:
                    break

    def run_timers(self, kinds):
        for rec in list(self.records.values()):
            if (rec.kind not in kinds or rec.paused_at is not None or not rec.started or rec.ending
                    or rec.notes.get("lingering") is not None):
                continue
            for timer in sorted(rec.timers, key=lambda t: t.due):
                if rec.iid not in self.records or rec.ending:
                    break
                if timer.due > self.now or timer not in rec.timers:
                    continue
                run = rec.runs.get(timer.run) if timer.run is not None else None
                if timer.run is not None and run is None:
                    rec.timers.remove(timer)
                    continue
                if timer.period:
                    timer.due += timer.period
                    if timer.due <= self.now:          # behind: no burst to catch up
                        timer.due = self.now + timer.period
                else:
                    rec.timers.remove(timer)
                out = self.call(rec, timer.method, *timer.args, run=run)
                if out is None and timer in rec.timers:
                    log.warning("%s: timer %s names %r, which %s does not have; dropped",
                                rec.iid, timer.id, timer.method, self.classes[rec.cid].name)
                    rec.timers.remove(timer)

    # ---- writes --------------------------------------------------------------------------

    def queue(self, op, callback=None):
        self.ops.append((op, callback))

    def query(self, command: str) -> str:
        """ctx.query: one command now. The cut waits for the reply (Guard.engine_code), and the
        time counts against the call."""
        return self.read("query", lambda io: io.query(str(command)))

    def read(self, kind: str, fn):
        """A live read for content (ctx.query, ctx.world.probe...): the call's cut pauses while it
        waits (the read has the call's own I/O budget instead: Guard.io_wait), and what each kind
        costs is kept for `status`."""
        import time
        start = time.perf_counter()
        with self.guard.io_wait(kind):
            try:
                return fn(self.io)
            finally:
                n, total = self.read_costs.get(kind, (0, 0.0))
                self.read_costs[kind] = (n + 1, total + (time.perf_counter() - start) * 1000.0)

    def potion_reply(self, op, result):
        # Potions are given and forgotten: the reply is only kept, in the log ("Applied effect",
        # or refused: "has something stronger").
        if result.ok:
            log.debug("potion %s %d on %s: %s", op.effect, op.amplifier, op.target, result.reply)
        else:
            log.info("potion %s %d on %s did not take: %s", op.effect, op.amplifier, op.target,
                     result.error or result.reply)

    def desired(self) -> dict:
        out = {}
        for rec in self.records.values():
            if rec.ending or rec.notes.get("lingering"):
                continue
            for run, owned in [(None, rec.owned)] + [(r, r.owned) for r in rec.runs.values()]:
                for key, (amount, op) in owned.modifiers.items():
                    if "|" in key:                   # on another entity
                        target, attr = key.split("|", 1)
                        out[(target, attr, modifier_id(rec, run, on=True))] = (amount, op)
                    elif rec.user is not None:
                        out[(rec.user, key, modifier_id(rec, run))] = (amount, op)
        return out

    # ---- damage --------------------------------------------------------------------------

    def deal(self, rec, run, target: str, amount: float, damage_type: str):
        user = rec.user
        self.queue(Damage(target, amount, damage_type, by=user))
        abilities.count_damage(self, rec, run, target, amount)

    def explode(self, rec, run, at, radius: float, damage: float) -> list[str]:
        from .handle import _pos
        pos = _pos(at)
        user = self.being(rec.user)
        dim = "minecraft:overworld"
        if user is not None:
            try:
                dim = user.dim
            except LookupError:
                pass
        config = self.classes[rec.cid].config
        self_damage = bool(getattr(config, "self_damage", True))
        hit = []
        for p in self.snapshot.players.values():
            if p.alive and p.dim == dim and math.dist(p.pos, pos) <= radius and p.gamemode in ("survival", "adventure"):
                if p.name == rec.user and not self_damage:
                    continue
                hit.append(p.name)
        seen = set()
        for e in list(self.snapshot.tracked.values()) + self.io_mirror().entities_near(dim, pos, radius):
            # players come from the snapshot above (with their gamemode); the mirror lists them too
            if (e.uuid in seen or e.type == PLAYER or e.health is None or e.dim != dim
                    or math.dist(e.pos, pos) > radius):
                continue
            seen.add(e.uuid)
            if e.uuid == rec.user and not self_damage:
                continue
            hit.append(e.uuid)
        self.queue(Particle("minecraft:explosion_emitter", dim, pos))
        self.queue(Sound("minecraft:entity.generic.explode", dim, pos))
        for target in hit:
            self.deal(rec, run, target, damage, "minecraft:explosion")
        return hit

    # ---- making things (the kind modules do the work) --------------------------------------

    def spawn(self, what, at, dim, owner=None, source=None, nbt=None, keep=False) -> str:
        return actors.spawn(self, what, at, dim, owner, source, nbt=nbt, keep=keep)

    def apply_mark(self, who, mark, seconds, source=None, owner=None, args=None) -> str:
        return marks.apply(self, who, mark, seconds, source, owner, args=args)

    # ---- places and names ------------------------------------------------------------------

    def where(self, at, dim: str | None = None) -> tuple[str, tuple, float]:
        """(dim, pos, yaw) of a Spot, a being, a snapshot, a position (in `dim`, else the
        overworld), a saved place's name, a player's name or "x y z [dim]"."""
        from .args import Spot, resolve_place, _COORDS, DIMS
        if isinstance(at, Spot):
            return at.dim, tuple(at.pos), at.yaw
        if isinstance(at, dict) and "pos" in at:
            return at.get("dim") or dim or OVERWORLD, tuple(float(c) for c in at["pos"]), float(at.get("yaw") or 0.0)
        if isinstance(at, Being):
            try:
                return at.dim, tuple(at.pos), float(getattr(at, "yaw", 0.0) or 0.0)
            except LookupError:
                raise ValueError("%s is not online" % at.key) from None
        if isinstance(at, PlayerSnap):
            return at.dim, tuple(at.pos), float(at.yaw)
        if isinstance(at, EntitySnap):
            return at.dim, tuple(at.pos), 0.0
        if isinstance(at, Look):
            return dim or OVERWORLD, tuple(at.pos), 0.0
        if isinstance(at, str):
            s = at.strip()
            m = _COORDS.match(s.lower())
            if m:
                return (DIMS.get(m.group(4) or "overworld", OVERWORLD),
                        tuple(float(g) for g in m.groups()[:3]), 0.0)
            b = self.being(self.target_of(s))
            if b is not None and not b.is_player:
                return self.where(b)
            spot = resolve_place(self, s)
            return spot.dim, tuple(spot.pos), spot.yaw
        if isinstance(at, (tuple, list)) and len(at) == 3:
            return dim or OVERWORLD, tuple(float(c) for c in at), 0.0
        raise ValueError("%r is not a place: a Spot, a player, a position or \"x y z [dim]\"" % (at,))

    def player_names(self, who) -> list[str]:
        """Names from a Players arg ("@a", "@r", "Steve,Alex") or a list of names or beings."""
        if isinstance(who, str):
            from .args import players
            from .ctx import Ctx
            return players(Ctx(self), who)
        return [n for n in (self.target_of(p) for p in who) if n]

    def bossbar_id(self, rec: Record, bar: str) -> str:
        clean = re.sub(r"[^a-z0-9_.-]+", "_", str(bar).lower()).strip("_") or "bar"
        return "everhear:%s_%s_%s" % (rec.cid, rec.iid.rsplit("#", 1)[-1], clean)

    def mspt(self) -> float:
        """The server's milliseconds per tick, read at most every half second (0.0 when unknown)."""
        value, at = self._mspt
        if self.now - at >= 0.5:
            try:
                value = float(self.io.mspt())
            except Exception as exc:
                log.debug("mspt not read: %s", exc)
            self._mspt = (value, self.now)
        return value

    def grant(self, who, ability, owner=None):
        return abilities.grant(self, who, ability, owner)

    def cooldown_of(self, ability) -> float | None:
        info = self.classes.get(ability)
        return float(info.config.cooldown_s) if info is not None and info.kind == "ability" else None

    def export_incantations(self):
        try:
            if self.bindings.export(self.cooldown_of):
                log.info("incantations exported, version %d", self.bindings.version)
        except OSError as exc:
            log.error("could not write the incantation export %s: %s", self.bindings.export_path, exc)

    def cast_from_content(self, being, ability) -> str:
        """ctx.user.cast(name): the calling instance lends the ability when the user lacks it. A
        CastResult: the status (a str), with .remaining_s and .error."""
        with self.guard.engine_code():
            status, extra = abilities.cast(self, being, ability, by="content", lend_owner=self.current)
            return CastResult(status, extra)


class CastResult(str):
    """What ctx.user.cast() answers: the status itself ("fired", "cooldown"...), so `== "fired"`
    works, with what goes with it: `.remaining_s` (the cooldown left, else 0.0), `.error` (why it
    failed, else None), `.instance` and `.run` (the cast's, when it fired or is charging)."""

    def __new__(cls, status: str, extra: dict | None = None):
        self = super().__new__(cls, status)
        extra = extra or {}
        self.remaining_s = float(extra.get("remaining_s") or 0.0)
        self.error = extra.get("error")
        self.instance = extra.get("instance")
        self.run = extra.get("run")
        return self

    @property
    def status(self) -> str:
        return str(self)


def _one_line(x) -> str:
    return " ".join(str(x).split()).split(" For further information")[0][:300]


def modifier_id(rec: Record, run=None, on: bool = False) -> str:
    """everhear:item/<id>, everhear:mark/<id> (or /<n> for a stacking mark's copies),
    everhear:ability/<id> (or /<run> for one cast's), everhear:actor/<id>; on another entity
    (on=), the instance's number too (/i<n>), so two instances never share one there."""
    base = "everhear:%s/%s" % (rec.kind, rec.cid)
    if on:
        base = "%s/i%s" % (base, rec.iid.rsplit("#", 1)[-1])
    if rec.kind == "mark" and rec.stack is not None:
        return "%s/%d" % (base, rec.stack)
    if run is not None:
        return "%s/%d" % (base, run.n)
    return base


def _spawned_by(rec: Record, uuid) -> bool:
    return uuid in rec.owned.spawned or any(uuid in r.owned.spawned for r in rec.runs.values())


def remove_op(uuid: str):
    return Remove(uuid)
