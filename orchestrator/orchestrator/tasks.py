"""Task states and state.json, rewritten whole (temp file, then rename) after every change.

state.json keeps the TypeScript runner's shape, so either one loads what the other wrote:
{"inboxOffset", "usage": {model: {"in", "out", "cost", "cacheRead", "cacheWrite"}}, "tasks": {id: {"at", "task", "context",
"seenAt", "status", "nudged", "replies": [{"status", "text", "sent", "n"}], "closedAt",
"implementers": [{"description", "at", "route", "loaded", ...}]}}, "nextReply", "sessionId"}.
"implementers" is new with the script library (route.record writes it) and left out while empty.
"""
import functools
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Literal, TypedDict

from .config import iso, js_number, log, now_ms, parse_time

TaskStatus = Literal["open", "done", "failed"]
ReplyStatus = Literal["progress", "done", "failed"]

# The usage snapshot of one model: tokens in (fresh, cache read, cache written), out, cost in USD,
# and of the tokens in, those read from and written to the cache (absent in a snapshot from before).
ModelTotals = TypedDict("ModelTotals", {"in": int, "out": int, "cost": float, "cacheRead": int, "cacheWrite": int},
                        total=False)


@dataclass
class Reply:
    status: ReplyStatus
    text: str
    sent: bool
    # Global order of replies, for the outbox.
    n: int

    def to_json(self) -> dict:
        return {"status": self.status, "text": self.text, "sent": self.sent, "n": self.n}

    @staticmethod
    def from_json(d: dict) -> "Reply":
        return Reply(status=d.get("status"), text=d.get("text", ""), sent=bool(d.get("sent")), n=d.get("n") or 0)


@dataclass
class Task:
    at: str
    task: str
    context: str
    seen_at: str
    status: TaskStatus
    nudged: bool
    replies: list[Reply]
    closed_at: str | None = None
    # One entry per implementer briefed for this task: what the router picked for it (route.record).
    implementers: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {"at": self.at, "task": self.task, "context": self.context, "seenAt": self.seen_at,
             "status": self.status, "nudged": self.nudged, "replies": [r.to_json() for r in self.replies]}
        if self.closed_at is not None:
            d["closedAt"] = self.closed_at
        if self.implementers:
            d["implementers"] = self.implementers
        return d

    @staticmethod
    def from_json(d: dict) -> "Task":
        return Task(at=d.get("at"), task=d.get("task", ""), context=d.get("context", ""),
                    seen_at=d.get("seenAt"), status=d.get("status"), nudged=bool(d.get("nudged")),
                    replies=[Reply.from_json(r) for r in d.get("replies") or []], closed_at=d.get("closedAt"),
                    implementers=[i for i in d.get("implementers") or [] if isinstance(i, dict)])


@dataclass
class State:
    session_id: str | None = None
    inbox_offset: int = 0
    usage: dict[str, ModelTotals] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    next_reply: int = 1

    def to_json(self) -> dict:
        d = {"inboxOffset": self.inbox_offset, "usage": self.usage,
             "tasks": {k: t.to_json() for k, t in self.tasks.items()}, "nextReply": self.next_reply}
        if self.session_id is not None:
            d["sessionId"] = self.session_id
        return d


MAX_TEXT = 300


def clean_text(text) -> str:
    """Newlines to spaces, trimmed, cut to 300 characters with "…"."""
    t = re.sub(r"[\r\n]+", " ", "" if text is None else str(text)).strip()
    return t[:MAX_TEXT - 1] + "…" if len(t) > MAX_TEXT else t


@dataclass
class ReplyOutcome:
    ok: bool
    reply: Reply | None = None
    error: str | None = None


class Store:
    def __init__(self, file: str):
        self.file = file
        self.state = State()
        # Called after a reply is recorded (the outbox listens).
        self.on_reply: Callable[[], None] = lambda: None

    def load(self) -> None:
        if not os.path.exists(self.file):
            self.state = State()
            return
        try:
            with open(self.file, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            tasks = raw.get("tasks") or {}
            state = State(
                session_id=raw.get("sessionId"),
                inbox_offset=raw.get("inboxOffset") or 0,
                usage=raw.get("usage") or {},
                tasks={k: Task.from_json(t) for k, t in tasks.items()},
                next_reply=raw.get("nextReply") or 1,
            )
            top = max((r.n for t in state.tasks.values() for r in t.replies), default=0)
            state.next_reply = max(state.next_reply, top + 1)
            self.state = state
        except Exception as e:
            log("state", "state.json unreadable, starting empty: %s" % e)
            self.state = State()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        tmp = "%s.tmp-%d" % (self.file, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.state.to_json(), indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, self.file)

    def drop_closed_before(self, hours: float, now: float | None = None) -> list[str]:
        """Drop tasks closed more than `hours` ago whose replies are all sent."""
        now = now_ms() if now is None else now
        dropped = []
        for tid, t in list(self.state.tasks.items()):
            if t.status == "open":
                continue
            when = t.closed_at if t.closed_at is not None else t.seen_at if t.seen_at is not None else t.at
            closed = parse_time(when)
            if closed is not None and now - closed > hours * 3600_000 and all(r.sent for r in t.replies):
                del self.state.tasks[tid]
                dropped.append(tid)
        return dropped

    def get(self, tid: str) -> Task | None:
        return self.state.tasks.get(tid)

    def has(self, tid: str) -> bool:
        return tid in self.state.tasks

    def open_ids(self) -> list[str]:
        ids = [tid for tid, t in self.state.tasks.items() if t.status == "open"]
        return sorted(ids, key=functools.cmp_to_key(by_task_number))

    def add(self, tid: str, at: str, task: str, context: str, now: float | None = None) -> Task:
        """Records a task as open."""
        t = Task(at=at, task=task, context=context, seen_at=iso(now_ms() if now is None else now),
                 status="open", nudged=False, replies=[])
        self.state.tasks[tid] = t
        return t

    def reply(self, tid: str, status: ReplyStatus, text, now: float | None = None) -> ReplyOutcome:
        """The one path every answer takes, the model's and the runner's: it refuses an unknown
        task, a closed one, and empty text; records the reply; closes the task on done or failed.
        Exactly once is held here."""
        t = self.state.tasks.get(tid)
        if t is None:
            return ReplyOutcome(False, error="%s is not a task I know." % tid)
        if t.status != "open":
            return ReplyOutcome(False, error="%s is already closed as %s." % (tid, t.status))
        clean = clean_text(text)
        if not clean:
            return ReplyOutcome(False, error="The text is empty.")
        r = Reply(status=status, text=clean, sent=False, n=self.state.next_reply)
        self.state.next_reply += 1
        t.replies.append(r)
        if status != "progress":
            t.status = status
            t.closed_at = iso(now_ms() if now is None else now)
        self.save()
        self.on_reply()
        return ReplyOutcome(True, reply=r)

    def fail(self, tid: str, text: str) -> bool:
        """The runner closes a task as failed; nothing happens when it is already closed."""
        return self.reply(tid, "failed", text).ok

    def unsent(self) -> list[tuple[str, Reply]]:
        """Every unsent reply, in the order they were made."""
        out = [(tid, r) for tid, t in self.state.tasks.items() for r in t.replies if not r.sent]
        return sorted(out, key=lambda x: x[1].n)


def by_task_number(a: str, b: str) -> int:
    na = js_number(re.sub(r"^\D+", "", a))
    nb = js_number(re.sub(r"^\D+", "", b))
    if math.isfinite(na) and math.isfinite(nb) and na != nb:
        return -1 if na < nb else 1
    return (a > b) - (a < b)
