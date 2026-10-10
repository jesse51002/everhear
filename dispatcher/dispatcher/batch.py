"""The batch: handed-on tasks wait VFG_DISPATCH_WINDOW_S, then go in ONE `vfg orch hand`.

The first task handed on starts the window; when it ends, every line collected goes in one
write. A line leaves state.json's batch only once `vfg orch hand` has succeeded, which is
retried until it does (it skips ids already handed, so a retry never doubles a task).
`vfg orch hand` exits 2 only on a line it will never take: the lines are then handed one by
one, and the one refused is given to `on_refused`, which tells the conversation.

A line is handed only once its task has nothing left in state.json's outbox: the
conversation must hear "handed on to the orchestrator" before the orchestrator can answer.
Until then it keeps its place, tried again every HELD_S, and never past a stop's deadline.
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
HELD_S = 1.0  # a line whose task still has a reply to send is tried again after this


class Batch:
    def __init__(self, state: State, vfg: Vfg, window_s: float, *, on_refused=None,
                 retry_s: tuple[float, ...] = RETRY_S, held_s: float = HELD_S):
        self.state = state
        self.vfg = vfg
        self.window_s = window_s
        self.on_refused = on_refused or (lambda line, reason: None)
        self.retry_s = retry_s
        self.held_s = held_s
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

    def held(self, line: dict) -> bool:
        """True while the task still has a reply waiting in the outbox: its "handed on" line
        must reach the feed before the orchestrator can answer, or a retried reply of ours
        could land after the orchestrator's `done`."""
        return any(o["id"] == line["id"] for o in self.state.outbox)

    async def _window(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.hand()
        if self.state.batch:
            # a line held for its reply tries again shortly; lines that came while the hand was
            # in flight get their own window
            again = self.held_s if any(self.held(x) for x in self.state.batch) else self.window_s
            self.timer = asyncio.create_task(self._window(again), name="batch window")

    async def hand(self, deadline: float | None = None) -> bool:
        """Hand, in one write, every line whose task has no reply left to send, retrying until
        it succeeds or `deadline` (monotonic) passes. A held line keeps its place. True when
        the batch is empty."""
        async with self.lock:
            failures = 0
            while self.state.batch:
                lines = [x for x in self.state.batch if not self.held(x)]
                if not lines:
                    return False  # every line waits for its reply: the caller tries again shortly
                r = await self.vfg("orch", "hand", json.dumps(lines, ensure_ascii=False), timeout=30)
                if r.code == 0:
                    self._handed(lines, r.stdout)
                    return not self.state.batch
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
        """On stop: cut the window short and hand everything now, a line held for its reply
        included once that reply is out, for at most `timeout` seconds."""
        if self.timer and not self.timer.done():
            self.timer.cancel()
        deadline = time.monotonic() + timeout
        while self.state.batch:
            if await self.hand(deadline):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(min(self.held_s, max(0.0, deadline - time.monotonic())))
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
