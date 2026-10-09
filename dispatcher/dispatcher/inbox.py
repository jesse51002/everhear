"""Follows ~/.vfg/dispatch/inbox.jsonl from a byte offset, and keeps state.json.

state.json is everything the dispatcher must not lose:
- `offset`: the inbox byte offset read so far, saved in the same write as the tasks it covered;
- `recent`: the last ids taken, so a line is never run twice;
- `tasks`: in-flight tasks, each with its picks and what was done or dropped so far;
- `batch`: task lines waiting for `vfg orch hand`, removed only once it succeeds;
- `outbox`: replies waiting for `vfg orch reply`, removed only once it succeeds.
Every write is atomic (a temporary file, then a rename).
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import log, now_iso

TASK_ID = re.compile(r"^T[1-9][0-9]*$")
RECENT = 1000


class State:
    def __init__(self, path: Path, data: dict[str, Any] | None = None):
        self.path = path
        self.data: dict[str, Any] = {"offset": 0, "recent": [], "tasks": {}, "batch": [], "outbox": []}
        self.data.update(data or {})

    @classmethod
    def load(cls, path: Path, inbox: Path | None = None) -> State:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError as e:
            # The offset is gone. Reading the inbox again from 0 would run old tasks twice,
            # so start at its end; the bad file is kept for a person to read.
            aside = path.with_name(f"{path.name}.bad-{int(time.time())}")
            path.rename(aside)
            size = inbox.stat().st_size if inbox and inbox.exists() else 0
            log("state", f"{path} is unreadable ({e}); kept as {aside.name}; reading the inbox from its end ({size})")
            state = cls(path, {"offset": size})
            state.save()
            return state
        return cls(path, data)

    @property
    def offset(self) -> int:
        return int(self.data["offset"])

    @offset.setter
    def offset(self, value: int) -> None:
        self.data["offset"] = value

    @property
    def tasks(self) -> dict[str, dict[str, Any]]:
        return self.data["tasks"]

    @property
    def batch(self) -> list[dict[str, Any]]:
        return self.data["batch"]

    @property
    def outbox(self) -> list[dict[str, Any]]:
        return self.data["outbox"]

    @property
    def recent(self) -> list[str]:
        return self.data["recent"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


def new_task(line: dict[str, Any]) -> dict[str, Any]:
    """An in-flight task as state.json keeps it."""
    return {
        "id": line["id"], "at": line["at"], "task": line["task"], "context": line["context"],
        "picks": None,          # the selector's picks, once it has answered
        "orchestrator": "",     # the selector's orchestrator part
        "next": 0,              # the pick running or about to run
        "done": [],             # [{"instruction", "report"}]
        "dropped": [],          # [{"instruction", "why"}]
    }


def waited_minutes(at: str, now: datetime | None = None) -> float:
    """Minutes since a task line's `at`; 0 when it cannot be read."""
    try:
        sent = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    return ((now or datetime.now(UTC)) - sent).total_seconds() / 60


class Inbox:
    """Reads only whole lines past the offset. Each new task is recorded in `state.tasks` in
    the same save as the offset that covers it, so a task is never lost nor run twice.

    A task that waited more than `stale_minutes` (VFG_ORCH_STALE_MINUTES, as the orchestrator)
    is never run: `on_expired(line, minutes)` answers it, before that same save."""

    def __init__(self, path: Path, state: State, *, stale_minutes: int = 10,
                 on_expired: Callable[[dict[str, Any], int], None] | None = None):
        self.path = path
        self.state = state
        self.stale_minutes = stale_minutes
        self.on_expired = on_expired or (lambda line, minutes: None)

    def poll(self) -> list[dict[str, Any]]:
        st = self.state
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size < st.offset:
            log("inbox", f"the inbox is smaller than the offset ({size} < {st.offset}); reading it again from 0")
            st.offset = 0
            st.save()
        if size == st.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(st.offset)
            buf = f.read(size - st.offset)
        end = buf.rfind(b"\n")
        if end < 0:
            return []  # a half-written line: wait for its newline
        new = []
        for raw in buf[: end + 1].decode("utf-8", "replace").splitlines():
            if line := self._take(raw):
                st.recent.append(line["id"])
                waited = waited_minutes(line["at"])
                if waited > self.stale_minutes:
                    minutes = math.floor(waited + 0.5)  # rounded as the orchestrator rounds
                    log("inbox", f"{line['id']} waited {minutes} minutes: failed as expired")
                    self.on_expired(line, minutes)
                    continue
                st.tasks[line["id"]] = new_task(line)
                new.append(line)
        del st.recent[:-RECENT]
        st.offset += end + 1
        st.save()
        return new

    def _take(self, raw: str) -> dict[str, Any] | None:
        if not raw.strip():
            return None
        try:
            obj = json.loads(raw)
        except ValueError:
            log("inbox", f"bad JSON, skipped: {raw[:200]}")
            return None
        tid = obj.get("id") if isinstance(obj, dict) else None
        if not isinstance(tid, str) or not TASK_ID.match(tid.strip()):
            log("inbox", f"no task id, skipped: {raw[:200]}")
            return None
        tid = tid.strip()
        if tid in self.state.tasks or tid in self.state.recent:
            log("inbox", f"{tid} is already known, skipped")
            return None
        task = obj.get("task") if isinstance(obj.get("task"), str) else ""
        if not task.strip():
            log("inbox", f"{tid} has no task, skipped")
            return None
        context = obj.get("context") if isinstance(obj.get("context"), str) else ""
        at = obj.get("at") if isinstance(obj.get("at"), str) and obj.get("at") else now_iso()
        log("inbox", f"{tid} new: {' '.join(task.split())[:160]}")
        return {"id": tid, "at": at, "task": task.strip(), "context": context.strip()}
