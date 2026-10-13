"""Every content call is wrapped (the game engine spec, "One loop, in order", its rules).

A call over VFG_ENGINE_SLOW_MS (5) is logged as slow; one over VFG_ENGINE_CUT_MS (50) is cut: a
timer signal (signal.setitimer, ITIMER_REAL) raises CallCut inside it, so only that instance ends
and the loop goes on. CallCut is a BaseException, so content's own `except Exception` cannot keep
a stuck call alive, and it comes again every 10 ms after the first, so neither can a bare
`except:`; a call that caught it and returned anyway still counts as cut. The cut waits while the engine's own code runs inside the call (a `self.*`
method, ctx.query talking to Minecraft): it lands the moment that returns, so the engine's books
and the RCON connection are never left half-done.

A signal can only be handled on the main thread; a call made on any other thread is timed but
cannot be cut (Engine.run runs the loop on the main thread).
"""

from __future__ import annotations

import collections
import contextlib
import signal
import threading
import time
import traceback
from dataclasses import dataclass


class CallCut(BaseException):
    """Raised inside a content call that ran past the cut."""


@dataclass
class Outcome:
    ok: bool
    value: object = None
    error: str | None = None     # in words, for the log and the instance's end reason
    ms: float = 0.0
    cut: bool = False
    detail: str | None = None    # the traceback's last lines


RECUT_S = 0.01         # after the first cut, another this often until the call returns
SLOW_WINDOW_S = 60.0   # an instance slow this many times within this window is flagged
SLOW_FLAG_AT = 3


class Guard:
    def __init__(self, slow_ms: float, cut_ms: float):
        self.slow_ms = slow_ms
        self.cut_ms = cut_ms
        self.active: str | None = None
        self.limit = cut_ms
        self.fired = False
        self._live = False                 # content is running now: an alarm may cut it
        self.critical = 0
        self.pending_cut = False
        self._installed = False
        self._previous = None
        self._slow: dict[str, collections.deque] = {}
        self.flagged: dict[str, str] = {}    # instance -> why

    # ---- the signal ----------------------------------------------------------------------

    def _can_cut(self) -> bool:
        if threading.current_thread() is not threading.main_thread():
            return False
        if not self._installed:
            self._previous = signal.signal(signal.SIGALRM, self._on_alarm)
            self._installed = True
        return True

    def uninstall(self):
        if self._installed and threading.current_thread() is threading.main_thread():
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._previous or signal.SIG_DFL)
            self._installed = False

    def _on_alarm(self, signum, frame):
        if not self._live:                 # the call is over (or never was): nothing to cut
            return
        self.fired = True
        if self.critical:
            self.pending_cut = True
            return
        raise CallCut("over %g ms" % self.limit)

    @contextlib.contextmanager
    def engine_code(self):
        """Wraps the engine's own work done inside a content call: a cut waits until it is done."""
        self.critical += 1
        try:
            yield
        finally:
            self.critical -= 1
            if self.critical == 0 and self.pending_cut and self._live:
                self.pending_cut = False
                raise CallCut("over %g ms" % self.limit)

    # ---- one call ------------------------------------------------------------------------

    def call(self, label: str, fn, *args, cut_ms: float | None = None) -> Outcome:
        """Run fn(*args) as one content call: timed, cut past `cut_ms` (default the setting)."""
        limit = self.cut_ms if cut_ms is None else cut_ms
        if self.active is not None:
            # By construction the engine never calls content from inside content (work a call
            # asks for is done after it returns); this is a bug in the engine, not in content.
            raise RuntimeError("content call %s inside %s" % (label, self.active))
        cut = limit > 0 and self._can_cut()
        self.active, self.pending_cut, self.limit, self.fired = label, False, limit, False
        start = time.perf_counter()
        self._live = True
        if cut:
            # then again every RECUT_S: a bare `except:` in content that swallows one cut is
            # given another, until the call returns
            signal.setitimer(signal.ITIMER_REAL, limit / 1000.0, RECUT_S)
        try:
            try:
                value = self._run(fn, args)
            finally:
                # disarmed before any bookkeeping: no cut can land in the engine's own code below
                self._live = False
                if cut:
                    signal.setitimer(signal.ITIMER_REAL, 0)
            out = Outcome(True, value)
            if self.fired:
                out = Outcome(False, error="cut: the call ran over %g ms (and caught the cut)" % limit,
                              cut=True)
        except CallCut:
            out = Outcome(False, error="cut: the call ran over %g ms" % limit, cut=True)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:     # SystemExit from content ends the instance, not the engine
            words = "%s: %s" % (type(exc).__name__, exc)
            if self.fired:
                out = Outcome(False, error="cut: the call ran over %g ms (caught the cut, then %s)"
                              % (limit, words), cut=True, detail=_short_trace(exc))
            else:
                out = Outcome(False, error=words, detail=_short_trace(exc))
        finally:
            self.active, self.pending_cut, self.critical = None, False, 0
        out.ms = (time.perf_counter() - start) * 1000.0
        return out

    def _run(self, fn, args):
        """fn(*args); the moment it returns or raises, an alarm does nothing any more (a plain
        attribute store: no signal handler can run between the call ending and it)."""
        try:
            return fn(*args)
        finally:
            self._live = False

    # ---- slow calls ----------------------------------------------------------------------

    def note_slow(self, instance: str, now: float) -> bool:
        """Count one slow call; True the first time the instance has been slow SLOW_FLAG_AT times
        within SLOW_WINDOW_S (it is then flagged)."""
        times = self._slow.setdefault(instance, collections.deque(maxlen=SLOW_FLAG_AT))
        times.append(now)
        if (len(times) == SLOW_FLAG_AT and now - times[0] <= SLOW_WINDOW_S
                and instance not in self.flagged):
            self.flagged[instance] = "slow %d times in %g s" % (SLOW_FLAG_AT, SLOW_WINDOW_S)
            return True
        return False

    def forget(self, instance: str):
        self._slow.pop(instance, None)


def _short_trace(exc: BaseException, lines: int = 3) -> str:
    frames = traceback.extract_tb(exc.__traceback__)[-lines:]
    return " | ".join("%s:%d in %s" % (f.filename.rsplit("/", 1)[-1], f.lineno, f.name) for f in frames)
