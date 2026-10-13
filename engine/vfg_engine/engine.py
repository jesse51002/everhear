"""The engine: one process, one loop (the game engine spec, "One loop, in order").

    engine = Engine(io, Settings.from_env())
    engine.start()              # io.open(), load content, restore saved state, start-up check
    engine.run(stop)            # the loop, on the main thread (the cut uses a timer signal)
    engine.submit(op, args)     # thread-safe; a Future of the reply, handled in the next tick
    engine.close()              # save state, io.close()

Each tick, in order:
1. Read the queue: the datapack's events since the last tick, this tick's snapshot (with the
   uuids the engine tracks), and every input on the bus (control ops, casts), in order. Events
   handed over again after a crash, stale player input and the engine's own damage reported back
   as a hit are left out (vfg_engine.intake).
2. Scenarios: their ends, then their timers (phases, timelines).
3. Events: joins, then who holds which item (instances made and ended), then the datapack's
   events, then those worked out from the snapshot (held/unheld, sneak, enter/exit, gone), then
   leaves; each handed to whoever listens, then acted on by the engine (death and milk on marks,
   a respawn's modifier check). Then the bus's inputs: ops and casts (a cast's on_cast runs here).
4. Abilities: items', actors' and abilities' timers and ticks; charges done fire; runs end.
5. Marks: their ends, their timers, the potions they keep up.
6. Write: the block queue's share of this tick, world settings, every queued op (a tick's damage on one target by one dealer as one
   op: merge_damage), then the modifiers (read back where due, removes before adds), in one
   Io.flush. Then the slow commands content asked for (self.ask), whose replies come next tick,
   and the answers to `start`s waiting for their scenario's result. State is saved about once a
   second.
"""

from __future__ import annotations

import collections
import concurrent.futures
import dataclasses
import logging
import queue
import sys
import threading
import time

from . import abilities, actors, derive, items, marks, scenarios
from .events import from_raw
from .io import Damage, OpResult
from .intake import Intake
from .ops import Ops
from .reload import attach, reload
from .runtime import Runtime
from .settings import Settings
from .statefile import read_json, write_json

__all__ = ["Engine", "Settings"]

log = logging.getLogger("vfg_engine")

STATE_VERSION = 1


class _Lines(logging.Handler):
    """The engine's recent log lines, for the `log` op."""

    def __init__(self, keep: int):
        super().__init__(logging.DEBUG)
        self.lines: collections.deque = collections.deque(maxlen=keep)

    def emit(self, record):
        try:
            self.lines.append({"t": round(record.created, 3), "level": record.levelname.lower(),
                               "msg": record.getMessage()})
        except Exception:
            pass


class Engine:
    def __init__(self, io, settings: Settings | None = None, clock=time.time):
        self.io = io
        self.settings = settings or Settings()
        self.clock = clock
        self.rt = Runtime(io, self.settings, clock)
        self.ops = Ops(self)
        self.intake = Intake(self.settings.stale_s)
        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self.waits: list[dict] = []          # starts waiting for their scenario's result
        self._mined: list[str] = []          # the block kinds last given to Io.watch_mined
        self.waiting: set = set()            # their futures: answered in a later tick
        self.tick_n = 0
        self.tick_ms: collections.deque = collections.deque(maxlen=200)
        self.started_at: float | None = None
        self._last_save = 0.0
        self._last_check = None
        self._saved: str | None = None
        self._started = False
        self._lines = _Lines(self.settings.log_keep)
        log.addHandler(self._lines)
        if log.level == logging.NOTSET:      # the `log` op keeps INFO and up, unless told otherwise
            log.setLevel(logging.INFO)

    @property
    def log_lines(self):
        return self._lines.lines

    # ---- life ----------------------------------------------------------------------------

    def start(self):
        """Connect, load every content class, rebuild every instance from the saved state; the
        first tick reads the modifiers on everyone back and removes leftovers."""
        rt = self.rt
        rt.now = self.clock()
        doc = None
        if self.settings.state:
            try:
                doc = read_json(self.settings.state)
            except (OSError, ValueError) as exc:
                log.error("the saved state %s cannot be read (%s); starting empty", self.settings.state, exc)
        self.intake.before_open(self.io, int((doc or {}).get("last_n") or 0))
        self.io.open()
        self.intake.after_open(self.io)
        try:
            rt.places.world = str(self.io.world_id())
        except Exception as exc:          # an Io without world_id (or a failed read): one world
            log.warning("world_id not read (%s): places are kept as one world's", exc)
        if doc:
            self._restore(doc)
        result = reload(rt)
        stopped = attach(rt, (doc or {}).get("records") or [])
        rt.block_queue.load((doc or {}).get("blocks"), set(rt.records))
        self._mined = None                   # the first tick tells the Io what is watched
        for uuid, cooldowns in ((doc or {}).get("actor_cooldowns") or {}).items():
            if uuid in rt.actors:
                rt.actors[uuid].cooldowns = {k: float(v) for k, v in cooldowns.items()}
        rt.export_incantations()
        self.started_at = rt.now
        self._last_save = rt.now
        self._started = True
        log.info("engine started: %d classes, %d instances restored, %d stopped, %d errors in content",
                 len(rt.classes), len(rt.records), len(stopped), len(result["errors"]))
        return {"loaded": result["loaded"], "errors": result["errors"], "stopped": stopped}

    def close(self):
        """Save the state (only when start() got as far as restoring it: an engine that failed
        to start, Minecraft not up yet, must not write an empty state over the saved one), then
        close the Io."""
        try:
            if self._started:
                self.save(force=True)
        finally:
            try:
                self.io.close()
            finally:
                self.rt.guard.uninstall()
                for name in self.rt.modules:      # its content modules go with it
                    sys.modules.pop(name, None)
                log.removeHandler(self._lines)

    def run(self, stop: threading.Event):
        """The loop, until `stop` is set; then the state is saved. On the main thread."""
        period = 1.0 / self.settings.tick_hz
        next_at = time.monotonic()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("tick %d failed; the loop goes on", self.tick_n)
            next_at += period
            wait = next_at - time.monotonic()
            if wait < 0:
                next_at = time.monotonic()       # behind: no burst of ticks to catch up
                wait = 0
            stop.wait(wait)
        self.save(force=True)

    def submit(self, op: str, args: dict | None = None) -> concurrent.futures.Future:
        """Thread-safe: the op is handled in the next tick; the Future's result is the reply."""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._inbox.put((op, dict(args or {}), fut))
        return fut

    # ---- one tick ------------------------------------------------------------------------

    def tick(self):
        t0 = time.perf_counter()
        rt = self.rt
        rt.now = self.clock()
        self.tick_n += 1
        rt._mirror = None
        self._failed: list[str] = []
        inputs = []
        while True:
            try:
                inputs.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        try:
            self._phases(inputs)
        finally:
            # every request taken off the bus this tick gets an answer, whatever failed (a start
            # waiting for its scenario's result answers in a later tick)
            for op, _args, fut in inputs:
                if not fut.done() and fut not in self.waiting:
                    why = "; ".join(self._failed) or "it was not handled"
                    _answer(fut, {"ok": False, "error": "the engine's tick failed before answering %s: %s"
                                                        % (op, why)})
            self.tick_ms.append((time.perf_counter() - t0) * 1000.0)

    def _step(self, name, fn, *args):
        """One step of the tick. An error in it (the engine's own, since content's are caught
        where content is called) is logged and the tick goes on to its next step, so one bug
        never skips the write or leaves a request unanswered."""
        try:
            fn(*args)
        except Exception as exc:
            log.exception("tick %d: the %s step failed; the tick goes on", self.tick_n, name)
            self._failed.append("%s: %s: %s" % (name, type(exc).__name__, exc))

    def _phases(self, inputs):
        rt = self.rt
        got = {"raw": [], "joins": [], "middle": [], "leaves": []}

        # 1. read the queue
        def read():
            raw = []
            try:
                raw = self.io.events()
            except Exception as exc:
                log.warning("events not read this tick (%s); they wait in the queue", exc)
            snap = None
            try:
                snap = self.io.snapshot(rt.tracked())
            except Exception as exc:
                log.warning("no snapshot this tick (%s); the last one stands", exc)
            got["raw"] = self.intake.screen(raw, snap.game_tick if snap is not None else None)
            if snap is not None:
                got["joins"], got["middle"], got["leaves"] = derive.take(rt, snap)
        self._step("read", read)

        # 2. scenarios, and the replies to last tick's slow commands (self.ask)
        self._step("scenarios", scenarios.step, rt)
        self._step("answers", self._answers)

        # 3. events, then the bus's inputs
        for ev in got["joins"]:
            self._step("join", self._join, ev)
        self._step("holders", items.reconcile, rt)
        for r in got["raw"]:
            self._step("event %s" % r.kind, self._raw_event, r)
        for ev in got["middle"]:
            self._step("event %s" % ev.kind, self._event, ev)
        self._step("areas", lambda: [self._event(ev) for ev in scenarios.crossings(rt)])
        for ev in got["leaves"]:
            self._step("leave", self._leave, ev)
        for op, args, fut in inputs:
            self._step("op %s" % op, self._input, op, args, fut)

        # 4. abilities (and items' and actors' own ticks)
        self._step("timers", rt.run_timers, ("item", "actor", "ability"))
        self._step("abilities", abilities.step, rt)

        # 5. marks
        self._step("marks", marks.step, rt)

        # 6. write
        check = self._last_check is None or rt.now - self._last_check >= self.settings.check_every_s
        if check:
            self._last_check = rt.now
            self._step("sweep", actors.sweep, rt)
        self._step("blocks", rt.block_queue.step)
        self._step("mined", self._watch_mined, check)
        self._step("write", self._write, check)
        self._step("asks", self._asks)
        self._step("waits", self._waits)
        if self.settings.state and rt.now - self._last_save >= self.settings.save_every_s:
            self._step("save", self.save)

    def _watch_mined(self, again: bool = False):
        """Tell the Io which block kinds' breaking is a `mine` event: when the set the live
        instances watch changes, and with every periodic check (so a reconnect or a new world
        hears it again within check_every_s)."""
        want = sorted(self.rt.mined_blocks())
        if want == self._mined and not (again and want):
            return
        if not callable(getattr(self.io, "watch_mined", None)):
            self._mined = want
            return
        unknown = self.io.watch_mined(want)
        if unknown:
            log.warning("mining not watched for %s: not blocks in Minecraft 26.3", ", ".join(unknown))
        self._mined = want

    def _asks(self):
        """The slow commands content asked for (self.ask), after the batch and outside any call's
        cut; each reply is handed over next tick."""
        rt = self.rt
        asks, rt.asks = rt.asks, []
        for iid, command, method in asks:
            try:
                reply = self.io.query(command)
            except Exception as exc:
                log.warning("%s: its slow command failed (%s): %s", iid, exc, command[:80])
                reply = ""
            rt.answers.append((iid, method, reply))

    def _answers(self):
        rt = self.rt
        answers, rt.answers = rt.answers, []
        for iid, method, reply in answers:
            rec = rt.records.get(iid)
            if rec is not None and not rec.ending:
                rt.call(rec, method, reply)

    def wait_for(self, iid: str, seconds: float, reply, fut=None):
        """Answer a `start` once its scenario ends, or after `seconds` with "still running"."""
        self.waits.append({"instance": iid, "until": self.rt.now + seconds, "reply": reply, "fut": fut})
        if fut is not None:
            self.waiting.add(fut)

    def _waits(self):
        rt = self.rt
        keep = []
        for w in self.waits:
            iid = w["instance"]
            rec = rt.records.get(iid)
            if rec is None or rec.ending:
                got = rt.results.get(iid) or {}
                result = {k: got[k] for k in ("summary", "facts") if k in got} or None
                w["reply"]({"ok": True, "instance": iid, "ended": got.get("reason", "ended"), "result": result})
            elif rec.notes.get("result") is not None:     # it reported, and goes on
                w["reply"]({"ok": True, "instance": iid, "running": True, "result": rec.notes["result"]})
            elif rt.now >= w["until"]:
                w["reply"]({"ok": True, "instance": iid, "running": True, "note": "still running",
                            "result": None})
            else:
                keep.append(w)
                continue
            self.waiting.discard(w["fut"])
        self.waits = keep

    def _join(self, ev):
        self._joined(ev.who)
        self.rt.deliver(ev)

    def _raw_event(self, r):
        ev = self.intake.mark(from_raw(r), self.rt.now)
        if ev is not None:                       # None: the engine's own damage, reported back as a hit
            self._event(ev)

    def _event(self, ev):
        self.rt.deliver(ev)
        self._react(ev)

    def _leave(self, ev):
        self.rt.deliver(ev)
        self._left(ev.who)

    def _input(self, op, args, fut):
        # a request its asker gave up on (the socket cancelled it on its timeout) is never
        # carried out: a cast that waited past its second must not fire late
        if not fut.set_running_or_notify_cancel():
            log.info("op %s skipped: its asker cancelled it before it ran", op)
            return
        self.ops.current = fut
        try:
            self.ops.handle(op, args, _replier(fut))
        finally:
            self.ops.current = None

    def _react(self, ev):
        rt, kind, who = self.rt, ev.kind, ev.who
        if kind == "death":
            marks.died(rt, who)
            for rec in rt.records_of(who, "ability"):
                for n in list(rec.runs):
                    rt.end_run(rec, n, "died")
        elif kind == "respawn":
            rt.read_back_due.add(who)
        elif kind == "milk":
            marks.drank_milk(rt, who)
        elif kind == "gone":
            actors.gone(rt, who)

    def _joined(self, name):
        rt = self.rt
        rt.read_back_due.add(name)
        for rec in list(rt.records.values()):
            if rec.user != name or rec.paused_at is None:
                continue
            shift = rt.now - rec.paused_at
            rec.paused_at = None
            if rec.ends is not None:
                rec.ends += shift
            for t in rec.timers:
                t.due += shift
            for owned in [rec.owned] + [r.owned for r in rec.runs.values()]:
                for keep in owned.potions.values():
                    keep[2] += shift
            if not rec.started:
                rt.start(rec)
        log.info("%s joined", name)

    def _left(self, name):
        rt = self.rt
        player = rt.player(name)
        if player is not None:
            player.online = False
            player.snap = None
        for rec in list(rt.records.values()):
            if rec.user != name or rec.ending:
                continue
            if rec.kind == "item":
                rt.end_record(rec, "left")
            elif rec.kind in ("ability", "mark"):
                for n in list(rec.runs):
                    rt.end_run(rec, n, "left")
                if rec.iid in rt.records:
                    rec.paused_at = rt.now
        log.info("%s left", name)

    def _write(self, check: bool = False):
        """The tick's one batch: world settings, every queued op, then the modifiers (read back
        first where due, and for everyone when `check`), removes before adds."""
        rt = self.rt
        ops, callbacks = [], []
        for name, value, op in rt.world_settings.plan(rt.io.read_gamerule):
            ops.append(op)
            callbacks.append(lambda op, res, name=name, value=value:
                             rt.world_settings.took(name, value, res.ok, res.error))
        for op, cb in merge_damage(rt.ops):
            ops.append(op)
            callbacks.append(cb)
        rt.ops = []

        desired = rt.desired()
        due = set(rt.read_back_due)
        if check:
            due |= {n for n, p in rt.snapshot.players.items() if p.alive}
            due |= {u for u in rt.actors if u in rt.snapshot.tracked}
        rt.read_back_due = set()
        for target in sorted(due):
            if not rt.online(target):
                if rt.player(target) is not None or target in rt.actors:
                    rt.read_back_due.add(target)     # dead or not here yet: when they are
                continue
            try:
                found = self.io.read_modifiers(target)
            except Exception as exc:
                log.warning("modifiers on %s not read back (%s); trying again next tick", target, exc)
                rt.read_back_due.add(target)
                continue
            rt.modifiers.read_back(target, found, desired)
        for op in rt.modifiers.plan(desired, rt.online):
            ops.append(op)
            callbacks.append(rt.modifiers.took)
        if not ops:
            return
        try:
            results = list(self.io.flush(ops))
        except Exception as exc:
            log.error("the tick's batch of %d ops failed (%s); modifiers and settings are tried again "
                      "next tick", len(ops), exc)
            results = []
        if len(results) < len(ops):
            results += [OpResult(False, "", "no result: the batch stopped")] * (len(ops) - len(results))
        for op, res, cb in zip(ops, results, callbacks):
            if res.ok:
                self.intake.sent(op, rt.now)
            if cb is not None:
                try:
                    cb(op, res)
                except Exception:
                    log.exception("handling the result of %s failed", type(op).__name__)
            elif not res.ok:
                log.warning("%s did not take: %s", op, res.error or res.reply)
        rt.drain()

    # ---- saved state ---------------------------------------------------------------------

    def state(self) -> dict:
        rt = self.rt
        return {"version": STATE_VERSION, "saved_at": rt.now, "counter": rt.counter,
                "players": {n: {"uuid": p.uuid, "online": p.online, "cooldowns": p.cooldowns}
                            for n, p in rt.players.items()},
                "actor_cooldowns": {u: b.cooldowns for u, b in rt.actors.items() if b.cooldowns},
                "records": [r.to_dict() for r in rt.records.values() if not r.ending] + rt.orphans,
                "raw_spawned": rt.raw_spawned, "confirmed": sorted(rt.confirmed),
                "last_n": self.intake.last_n, "modifiers": rt.modifiers.to_list(), "world": rt.world_settings.to_dict(),
                "incantations": rt.bindings.to_dict(), "broken": rt.broken,
                "places": rt.places.to_dict(), "blocks": rt.block_queue.to_list()}

    def save(self, force: bool = False):
        """Write the state if it changed (atomically: a crash leaves the old or the new file).
        Nothing before start() has restored it."""
        if not self.settings.state or not self._started:
            return
        import json
        doc = self.state()
        doc_saved_at = doc.pop("saved_at")
        text = json.dumps(doc, sort_keys=True, default=str)
        self._last_save = self.rt.now
        if text == self._saved and not force:
            return
        try:
            write_json(self.settings.state, {**doc, "saved_at": doc_saved_at})
            self._saved = text
        except (OSError, ValueError) as exc:
            log.error("state not saved to %s: %s", self.settings.state, exc)

    def _restore(self, doc: dict):
        from .beings import Player
        rt = self.rt
        if doc.get("version") != STATE_VERSION:
            log.warning("the saved state is version %r, not %d; reading what fits", doc.get("version"), STATE_VERSION)
        rt.counter = int(doc.get("counter") or 0)
        for name, p in (doc.get("players") or {}).items():
            player = rt.players[name] = Player(rt, name, p.get("uuid") or "")
            player.online = bool(p.get("online"))
            player.cooldowns = {k: float(v) for k, v in (p.get("cooldowns") or {}).items()}
        rt.raw_spawned = dict(doc.get("raw_spawned") or {})
        rt.confirmed = set(doc.get("confirmed") or ())
        rt.modifiers.load(doc.get("modifiers"))
        rt.world_settings.load(doc.get("world"))
        rt.bindings.load(doc.get("incantations"))
        rt.broken = dict(doc.get("broken") or {})
        rt.places.load(doc.get("places"))


def merge_damage(queued: list) -> list:
    """The tick's queued (op, callback) pairs with every Damage on one target by one dealer made one
    op where the first was: their amounts summed, the largest part's damage type. Minecraft keeps a
    target unhurtable for 10 game ticks after a hurt, and inside them takes only what a new hurt
    has over the last one: Fireball's last burn and its explosion, sent in one tick, dealt 8.4 of
    their 9.45 (engine_e2e.py, 2026-10-13). Hits on one target in different ticks under 0.5 s
    apart are still Minecraft's to cut."""
    groups: dict = {}
    for i, (op, _cb) in enumerate(queued):
        if isinstance(op, Damage):
            groups.setdefault((str(op.target).lower(), str(op.by or "").lower()), []).append(i)
    out = []
    for i, (op, cb) in enumerate(queued):
        if not isinstance(op, Damage):
            out.append((op, cb))
            continue
        parts = groups[(str(op.target).lower(), str(op.by or "").lower())]
        if len(parts) == 1:
            out.append((op, cb))
        elif parts[0] == i:
            pairs = [queued[j] for j in parts]
            biggest = max((p[0] for p in pairs), key=lambda d: d.amount)
            merged = dataclasses.replace(pairs[0][0], amount=sum(p[0].amount for p in pairs),
                                         damage_type=biggest.damage_type)
            calls = [p[1] for p in pairs if p[1] is not None]
            out.append((merged, (lambda op, res, calls=calls: [c(op, res) for c in calls]) if calls else None))
    return out


def _answer(fut: concurrent.futures.Future, doc):
    try:
        if not fut.done():
            fut.set_result(doc)
    except concurrent.futures.InvalidStateError:     # cancelled by its asker meanwhile
        pass


def _replier(fut: concurrent.futures.Future):
    def reply(doc):
        _answer(fut, doc)
    return reply
