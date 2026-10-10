"""The batch timer: a dynamic timer, not a cron (Prototype_Brain, the Context_Batch idea).

A batch call fires once VFG_BRAIN_BATCH_S (60 s) have passed since the last read of any kind
(a brain call, a Respond call, a batch) and something is still unprocessed: at that moment, or
when the first thing arrives after it. Every live read pushes it back, so in busy play it rarely
fires, and in a quiet spell it catches up. The clock is injected, so a test moves time by hand
and calls check().
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable


class Timer:
    def __init__(self, wait_s: float, waiting: Callable[[], bool], fire: Callable[[], asyncio.Task], *,
                 clock: Callable[[], float] = time.monotonic):
        self.wait_s = wait_s
        self.waiting = waiting  # is anything unprocessed?
        self.fire = fire        # starts the batch call as a task the brain keeps, and returns it
        self.clock = clock
        self.last = clock()     # the last read of any kind
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.firing: asyncio.Task | None = None

    def read(self) -> None:
        """A read of any kind: the timer starts again."""
        self.last = self.clock()
        self.wake.set()

    def arrived(self) -> None:
        """Something new may be waiting."""
        self.wake.set()

    def due(self) -> float | None:
        """When the batch fires: `wait_s` after the last read, and not before something waits."""
        return self.last + self.wait_s if self.waiting() else None

    def check(self) -> bool:
        """Fire the batch when it is due, as its own task; True when it fired."""
        due = self.due()
        if due is None or self.clock() < due or (self.firing is not None and not self.firing.done()):
            return False
        self.last = self.clock()
        self.firing = self.fire()
        return True

    async def run(self) -> None:
        while True:
            self.wake.clear()
            self.check()
            due = self.due()
            delay = None if due is None else max(due - self.clock(), 0.05)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), delay)

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="timer")

    async def stop(self) -> None:
        """No more batches; one under way is the brain's to wait for."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.task = None
