"""Reply: one `vfg orch reply <T7> <status> <text>` per answer, retried until it succeeds.

Every answer is first put in state.json's outbox, and leaves it only once `vfg orch reply`
has succeeded. Each task has its own sender, which sends that task's answers strictly in
order, so one task's `done` never overtakes its `progress`; different tasks' answers go
side by side. A failure is retried after 1, 2, 4, 8 and then every 15 s.
"""

from __future__ import annotations

import asyncio
import time

from .config import log
from .inbox import State
from .vfg import Vfg, why

RETRY_S = (1, 2, 4, 8, 15)
MAX_TEXT = 1000
BAD_ARGUMENTS = 2  # vfg's exit code for bad arguments: no retry can fix it


def clean(text: str) -> str:
    """One line, not read as an option or as `-` (stdin) by vfg."""
    text = " ".join(text.split()).lstrip("-").strip() or "(no text)"
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


class Reply:
    def __init__(self, state: State, vfg: Vfg, retry_s: tuple[float, ...] = RETRY_S):
        self.state = state
        self.vfg = vfg
        self.retry_s = retry_s
        self.senders: dict[str, asyncio.Task] = {}

    def send(self, task_id: str, status: str, text: str) -> None:
        """Queue an answer. The caller saves state (with whatever else changed alongside), and
        the sender starts at the next await, so the answer is on disk before it is sent."""
        self.state.outbox.append({"id": task_id, "status": status, "text": clean(text)})
        self._kick(task_id)

    def start(self) -> None:
        """At start: send what a stopped or crashed run left unsent."""
        for task_id in dict.fromkeys(o["id"] for o in self.state.outbox):
            self._kick(task_id)

    def pending(self) -> int:
        return len(self.state.outbox)

    async def drain(self, timeout: float) -> bool:
        """Waits until everything is sent or the time is up. True when nothing is left."""
        end = time.monotonic() + timeout
        while self.state.outbox and time.monotonic() < end:
            await asyncio.sleep(0.05)
        return not self.state.outbox

    async def stop(self) -> None:
        for sender in self.senders.values():
            sender.cancel()
        await asyncio.gather(*self.senders.values(), return_exceptions=True)
        self.senders.clear()

    def _kick(self, task_id: str) -> None:
        sender = self.senders.get(task_id)
        if sender is None or sender.done():
            self.senders[task_id] = asyncio.create_task(self._sender(task_id), name=f"reply {task_id}")

    def _next(self, task_id: str):
        return next((o for o in self.state.outbox if o["id"] == task_id), None)

    async def _sender(self, task_id: str) -> None:
        failures = 0
        while (item := self._next(task_id)) is not None:
            t0 = time.perf_counter()
            r = await self.vfg("orch", "reply", task_id, item["status"], item["text"], timeout=60)
            ms = int((time.perf_counter() - t0) * 1000)
            if r.code == 0 or r.code == BAD_ARGUMENTS:
                self.state.outbox.remove(item)
                self.state.save()
                failures = 0
                if r.code == 0:
                    log("reply", f"{task_id} {item['status']} sent ({ms} ms): {item['text'][:200]}")
                else:
                    log("reply", f"{task_id} {item['status']} REFUSED by vfg, not retried ({why(r)}): {item['text'][:200]}")
                continue
            delay = self.retry_s[min(failures, len(self.retry_s) - 1)]
            failures += 1
            log("reply", f"{task_id} {item['status']} failed ({why(r)}); retrying in {delay:g} s")
            await asyncio.sleep(delay)
        if self.senders.get(task_id) is asyncio.current_task():
            del self.senders[task_id]
