"""The batch: handed-on tasks wait VFG_DISPATCH_WINDOW_S, then go in ONE `vfg orch hand`.

The first task handed on starts the window; when it ends, every line collected goes in one
write. A line leaves state.json's batch only once `vfg orch hand` has succeeded, which is
retried until it does (it skips ids already handed, so a retry never doubles a task).
`vfg orch hand` exits 2 only on a line it will never take: the lines are then handed one by
one, and the one refused is given to `on_refused`, which tells the conversation.
"""

from __future__ import annotations

import asyncio
import json
import time

from .config import log
from .inbox import State
from .vfg import Vfg, why

RETRY_S = (1, 2, 4, 8, 15)
BAD_INPUT = 2


class Batch:
    def __init__(self, state: State, vfg: Vfg, window_s: float, *, on_refused=None,
                 retry_s: tuple[float, ...] = RETRY_S):
        self.state = state
        self.vfg = vfg
        self.window_s = window_s
        self.on_refused = on_refused or (lambda line, reason: None)
        self.retry_s = retry_s
        self.timer: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    def add(self, line: dict) -> None:
        """Collect a line. The caller saves state; the window starts with the first line."""
        self.state.batch.append(line)
        log("batch", f"{line['id']} joins the batch ({len(self.state.batch)} waiting)")
        self._arm(self.window_s)

    def start(self) -> None:
        """At start: a batch left waiting is handed at once."""
        if self.state.batch:
            log("batch", f"{len(self.state.batch)} waiting at start: handing them now")
            if self.timer and not self.timer.done():
                self.timer.cancel()  # a window armed by crash recovery: no need to wait it out
            self.timer = None
            self._arm(0)

    def _arm(self, delay: float) -> None:
        if self.timer is None or self.timer.done():
            self.timer = asyncio.create_task(self._window(delay), name="batch window")

    async def _window(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.hand()
        if self.state.batch:  # lines that came while the hand was in flight get their own window
            self.timer = asyncio.create_task(self._window(self.window_s), name="batch window")

    async def hand(self, deadline: float | None = None) -> bool:
        """Hand everything waiting in one write, retrying until it succeeds or `deadline`
        (monotonic) passes. Lines that come while it is in flight wait for their own window."""
        async with self.lock:
            failures = 0
            while self.state.batch:
                lines = list(self.state.batch)
                r = await self.vfg("orch", "hand", json.dumps(lines, ensure_ascii=False), timeout=30)
                if r.code == 0:
                    self._handed(lines, r.stdout)
                    return True
                if r.code == BAD_INPUT:
                    await self._one_by_one(lines)
                    if not self.state.batch:
                        return True
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                delay = self.retry_s[min(failures, len(self.retry_s) - 1)]
                if deadline is not None:
                    delay = max(0.0, min(delay, deadline - time.monotonic()))
                failures += 1
                log("batch", f"vfg orch hand failed ({why(r)}); retrying in {delay:g} s")
                await asyncio.sleep(delay)
            return True

    async def flush(self, timeout: float) -> bool:
        """On stop: cut the window short and hand everything now, for at most `timeout` seconds."""
        if self.timer and not self.timer.done():
            self.timer.cancel()
        deadline = time.monotonic() + timeout
        while self.state.batch:
            if not await self.hand(deadline):
                return False
        return True

    def _handed(self, lines: list[dict], stdout: str) -> None:
        ids = {line["id"] for line in lines}
        self.state.batch[:] = [line for line in self.state.batch if line["id"] not in ids]
        self.state.save()
        said = " ".join(stdout.split())
        log("batch", f"handed {len(lines)} in one write: {', '.join(sorted(ids))}" + (f" ({said})" if said else ""))

    async def _one_by_one(self, lines: list[dict]) -> None:
        for line in lines:
            r = await self.vfg("orch", "hand", json.dumps([line], ensure_ascii=False), timeout=30)
            if r.code == 0:
                self._handed([line], r.stdout)
            elif r.code == BAD_INPUT:
                reason = why(r)
                log("batch", f"{line['id']} REFUSED by vfg orch hand: {reason}")
                self.state.batch[:] = [x for x in self.state.batch if x["id"] != line["id"]]
                self.on_refused(line, reason)
                self.state.save()
            else:
                return  # not the line's fault: the caller retries the lot
