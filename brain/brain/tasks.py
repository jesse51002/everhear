"""The brain's tasks: ~/.vfg/brain/tasks.json, which only the brain writes, always whole.

    {"next": 184, "tasks": {"T183": {"id": "T183", "at": "<iso>", "player": "Steve",
     "line": "<the player's words>", "task": "<the brief>", "status": "open|done|failed",
     "result": "", "session": null, "closed_at": null}}}

Every line the brain acts on gets one: the line's actions run under one id, and each new
implementer part gets its own, which stays open until the implementer reports. `vfg route
--task` reads `task` and `line` from here, and `vfg brain status` lists the open ones.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import log, now_iso

KEEP_CLOSED = 300  # the newest closed tasks kept in the file; open ones are always kept
TASK_ID = re.compile(r"\bT(\d+)\b")
BRIEF_MAX = 160    # characters of a brief in OPEN TASKS


class Tasks:
    def __init__(self, path: Path, clock=now_iso):
        self.path = path
        self.clock = clock
        self.next = 1
        self.tasks: dict[str, dict[str, Any]] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.next = max(1, int(data.get("next") or 1))
            self.tasks = {k: v for k, v in (data.get("tasks") or {}).items() if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log("tasks", f"{path} is unreadable ({type(e).__name__}: {e}); starting with no tasks")

    def at_least(self, text: str) -> None:
        """Keep new ids past every T<n> in `text` (old feed rows, running copies, old inboxes),
        so an id never means two things."""
        seen = [int(n) for n in TASK_ID.findall(text)]
        if seen and max(seen) >= self.next:
            self.next = max(seen) + 1

    def open(self, player: str, line: str, task: str) -> str:
        tid = f"T{self.next}"
        self.next += 1
        self.tasks[tid] = {"id": tid, "at": self.clock(), "player": player, "line": line, "task": task,
                           "status": "open", "result": "", "session": None, "closed_at": None}
        self.save()
        return tid

    def get(self, tid: str) -> dict[str, Any] | None:
        return self.tasks.get(tid)

    def reopen(self, tid: str) -> None:
        """A follow-up: the task is open again until its implementer reports."""
        t = self.tasks.get(tid)
        if t is not None and t["status"] != "open":
            t.update(status="open", closed_at=None)
            self.save()

    def set_session(self, tid: str, session: str | None) -> None:
        """The implementer's session id, so a follow-up after a restart resumes it."""
        t = self.tasks.get(tid)
        if t is not None and session and t.get("session") != session:
            t["session"] = session
            self.save()

    def note(self, tid: str, result: str) -> None:
        """A result so far, the task kept open: a follow-up to it runs."""
        t = self.tasks.get(tid)
        if t is not None:
            t["result"] = result
            self.save()

    def close(self, tid: str, status: str, result: str) -> None:
        t = self.tasks.get(tid)
        if t is None:
            return
        t.update(status=status, result=result, closed_at=self.clock())
        self.save()

    def open_ones(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks.values() if t.get("status") == "open"]

    def show(self, running: list[str] = ()) -> str:
        """OPEN TASKS, one line each, for a model."""
        rows = []
        for t in self.open_ones():
            brief = " ".join(str(t.get("task") or "").split())
            if len(brief) > BRIEF_MAX:
                brief = brief[:BRIEF_MAX - 1] + "…"
            state = "an implementer is on it" if t["id"] in running else "open"
            rows.append(f"- {t['id']} for {t.get('player') or 'nobody'}, since {str(t.get('at'))[11:19]}, "
                        f"{state}: {brief}")
        return "OPEN TASKS:\n" + ("\n".join(rows) or "none")

    def save(self) -> None:
        """The whole file, written beside and moved over it: never half a file."""
        closed = sorted((t for t in self.tasks.values() if t.get("status") != "open"),
                        key=lambda t: str(t.get("closed_at") or ""))
        for t in closed[:max(0, len(closed) - KEEP_CLOSED)]:
            del self.tasks[t["id"]]
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"next": self.next, "tasks": self.tasks}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)
