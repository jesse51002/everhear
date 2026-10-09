"""Follows inbox.jsonl from a byte offset: every 250 ms it compares the file's size with
state.inbox_offset and reads only whole lines (up to the last newline)."""
import asyncio
import json
import os
import re
import traceback
from typing import Callable

from .config import hms, iso, js_round, log, now_ms, parse_time
from .tasks import Store


def task_message(tid: str, at: float, task: str, context: str) -> str:
    msg = "TASK %s at %s UTC\n%s" % (tid, hms(at), task.strip())
    if context.strip():
        msg += "\n\nContext from the conversation:\n" + context.strip()
    return msg


class InboxFollower:
    def __init__(self, inbox: str, store: Store, stale_minutes: int,
                 on_task: Callable[[str, str], None], now: Callable[[], float] | None = None):
        self.inbox = inbox
        self.store = store
        self.stale_minutes = stale_minutes
        # A task was recorded open: push it to the session.
        self.on_task = on_task
        self.now = now or now_ms
        self._timer: asyncio.Task | None = None

    def start(self, interval_ms: float = 250) -> None:
        self.poll()
        self._timer = asyncio.get_running_loop().create_task(self._every(interval_ms))

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = None

    async def _every(self, interval_ms: float) -> None:
        while True:
            await asyncio.sleep(interval_ms / 1000)
            try:
                self.poll()
            except Exception:
                log("main", "uncaught exception: %s" % traceback.format_exc())

    def poll(self) -> list[str]:
        """One pass. Returns the ids recorded open."""
        store, inbox = self.store, self.inbox
        try:
            size = os.stat(inbox).st_size if os.path.exists(inbox) else 0
        except OSError:
            size = 0
        offset = store.state.inbox_offset or 0
        if size < offset:
            log("inbox", "inbox is smaller than the offset (%d < %d); reading it again from 0" % (size, offset))
            offset = 0
            store.state.inbox_offset = 0
            store.save()
        if size == offset:
            return []
        with open(inbox, "rb") as f:
            f.seek(offset)
            buf = f.read(size - offset)
        end = buf.rfind(b"\n")
        if end < 0:
            return []  # a half-written line: wait for its newline
        text = buf[:end + 1].decode("utf-8", "replace")
        store.state.inbox_offset = offset + end + 1
        opened, pushes = [], []
        for line in text.split("\n"):
            if not line.strip():
                continue
            r = self._take(line)
            if r:
                opened.append(r[0])
                pushes.append(r)
        store.save()
        for tid, msg in pushes:
            self.on_task(tid, msg)
        return opened

    def _take(self, line: str) -> tuple[str, str] | None:
        store = self.store
        try:
            obj = json.loads(line)
        except ValueError:
            log("inbox", "bad JSON, skipped: %s" % line[:200])
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not obj["id"].strip():
            log("inbox", "no task id, skipped: %s" % line[:200])
            return None
        tid = obj["id"].strip()
        if store.has(tid):
            log("inbox", "%s is already known, skipped" % tid)
            return None
        now = self.now()
        parsed = parse_time(obj.get("at"))
        at = parsed if parsed is not None else now
        task = obj["task"] if isinstance(obj.get("task"), str) else ""
        context = obj["context"] if isinstance(obj.get("context"), str) else ""
        store.add(tid, at=iso(at), task=task, context=context, now=now)
        if not task.strip():
            log("inbox", "%s has no task: failed as malformed" % tid)
            store.fail(tid, "malformed task")
            return None
        waited = (now - at) / 60_000
        if waited > self.stale_minutes:
            n = js_round(waited)
            log("inbox", "%s waited %d minutes: failed as expired" % (tid, n))
            store.fail(tid, "Expired: it waited %d minutes for the orchestrator." % n)
            return None
        log("inbox", "%s open: %s" % (tid, re.sub(r"\s+", " ", task)[:120]))
        return tid, task_message(tid, at, task, context)
