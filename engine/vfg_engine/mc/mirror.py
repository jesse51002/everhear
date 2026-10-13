"""The mirror: a second thread's copy of the entities around players, at most about 0.1 s old (the
game engine spec, "Reading the world without waiting", its second layer; Jesse's idea).

Every `interval` seconds the thread runs the datapack's everhear:io/mirror (each entity within
`radius` of any player, read once) and swaps the whole copy in at once, so a reader on the main
thread never sees half a pass. Players are not in it: they come from the snapshot, and the pass
would otherwise save each player's whole data (recipe book and all, up to 0.37 ms) every time. A
pack from before that change still sends them, and they are dropped here: the same pack version,
since nothing the engine reads changed shape. It shares McIo's one RCON connection; each pass
holds it for one function and one read. A failed pass is logged (once a minute at most) and the
copy kept; `age_s` then grows, and says how stale it is.

A pass costs the server about 25 µs per entity (each entity's data is saved once to read it;
measured on 26.3), on the server's main thread. So the mirror keeps to a budget: it waits at least
`interval`, and longer when a pass's function took more than `budget` of the time between passes
(VFG_ENGINE_MIRROR_BUDGET, default 0.1: a tenth of the server's thread). With 60 mobs near the
players a pass is ~1.7 ms and the mirror stays at 0.1 s; with 600 it slows to ~0.15 s.

Standard library only, besides vfg_engine.io.
"""

from __future__ import annotations

import logging
import math
import signal
import threading
import time

from ..io import EntitySnap, Vec
from . import reading, snbt

log = logging.getLogger("vfg_engine.mc")

INTERVAL = 0.1
RADIUS = 32
BUDGET = 0.1


class McMirror:
    """vfg_engine.io.Mirror over the datapack. McIo makes one and starts it in open()."""

    def __init__(self, io, interval: float = INTERVAL, radius: int = RADIUS, budget: float = BUDGET):
        self._io = io
        self.interval, self.radius = float(interval), int(radius)
        self.budget = max(0.01, float(budget))
        self.server_ms = 0.0           # the last pass's function: the server's own time, about
        self.wait_s = self.interval    # how long the thread now waits between passes
        self._by_uuid: dict[str, EntitySnap] = {}
        self._by_dim: dict[str, list[EntitySnap]] = {}
        self._at = 0.0                 # monotonic time of the last good pass
        self.game_tick = None
        self.passes = self.failures = 0
        self.last_ms = 0.0
        self.last_error = None
        self._logged_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- the Mirror protocol
    @property
    def age_s(self) -> float:
        """Seconds since the copy was read (infinite before the first pass)."""
        return math.inf if not self._at else time.monotonic() - self._at

    def entities_near(self, dim: str, pos: Vec, radius: float) -> list[EntitySnap]:
        """Every entity in `dim` within `radius` of `pos`, nearest first (never a player: those are
        the snapshot's)."""
        r2 = float(radius) ** 2
        px, py, pz = pos
        found = []
        for e in self._by_dim.get(dim, ()):
            d2 = (e.pos[0] - px) ** 2 + (e.pos[1] - py) ** 2 + (e.pos[2] - pz) ** 2
            if d2 <= r2:
                found.append((d2, e))
        found.sort(key=lambda t: t[0])
        return [e for _, e in found]

    def get(self, uuid: str) -> EntitySnap | None:
        return self._by_uuid.get(uuid.lower())

    def all(self) -> list[EntitySnap]:
        return list(self._by_uuid.values())

    # ---- the thread
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vfg-engine-mirror", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 2.0):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(wait)
        self._thread = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        # The engine's 50 ms cut is a SIGALRM (setitimer) for the main thread: Python runs handlers
        # there anyway, but blocked here no syscall of this thread is ever interrupted by one.
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        except (AttributeError, OSError, ValueError):
            pass
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.refresh()
            except Exception as e:          # the thread never dies of a bad pass
                self.failures += 1
                self.last_error = "%s: %s" % (type(e).__name__, e)
                now = time.monotonic()
                if now - self._logged_at > 60:
                    self._logged_at = now
                    log.warning("mirror: a pass failed, the copy is kept (%.1f s old): %s",
                                self.age_s, self.last_error)
            self.wait_s = max(self.interval, self.server_ms / 1000.0 / self.budget)
            self._stop.wait(max(0.0, self.wait_s - (time.monotonic() - started)))

    def refresh(self):
        """One pass, now (the thread's; also callable from a test)."""
        start = time.perf_counter()
        io = self._io
        with io.rcon.held():
            f0 = time.perf_counter()
            io.rcon.command("function everhear:io/mirror {r:%d}" % self.radius)
            self.server_ms = (time.perf_counter() - f0) * 1000
            reply = io.rcon.command("data get storage everhear:mirror")
        doc = snbt.data_reply(reply)
        by_uuid, by_dim = {}, {}
        for e in doc.get("e") or []:
            snap = reading.entity(e, io.tables)
            if snap is None or snap.type == "minecraft:player":     # a player: an older pack's
                continue
            by_uuid[snap.uuid] = snap
            by_dim.setdefault(snap.dim, []).append(snap)
        self._by_uuid, self._by_dim = by_uuid, by_dim       # swapped whole: readers see one pass
        self._at = time.monotonic()
        self.game_tick = doc.get("time")
        self.passes += 1
        self.last_ms = (time.perf_counter() - start) * 1000
