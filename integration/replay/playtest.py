"""The 2026-10-08 playtest as the brain would have had it: what the offline replays read.

The data, read and never written:
- the box's vfg.db as it was after the playtest (2.5 h, three players: KrownJM, Eclipszr and
  Styrkaeopteryx), from `~/Documents/MinecraftServers/aws-backup/vfg-backup-2026-10-08-playtest.tgz`
  (`tar xzf … -C DIR vfg/vfg.db`): `lines` (every line said, the AI's chat, Jev's class then and
  when the line was fed) and `feed` (the kept LINE rows, GAME, SYS and ORCH rows). Its classes are
  the old names: `direct_ask` is `action_oriented` now, `not_useful` is `not_relevant`.
- the tasks the conversation wrote, `~/.vfg/orch/inbox.jsonl`, those before 2026-10-09: `{"id",
  "at", "task", "context"}`, the context naming the LINE numbers it came from (`#812`).
- the route labels, by task id: `~/.vfg/dispatch/eval/selector-actions-2026-10-10.jsonl`, on the
  brain's action menu, and `selector.jsonl`, on the menu of 2026-10-09.

THE CONTEXT is every row the brain would have had, in the order it would have arrived, so it only
grows, as live, and the prompt cache sees the same prefix from call to call:
- a kept LINE when Jev fed it (`lines.fed_at`), with its class;
- GAME and SYS rows at their time;
- the AI's chat (`lines` where source='ai') as `AI say` rows;
- each task the conversation wrote as an `AI task` row at its `at`, `T7: <task>`, as the brain
  records a task it opens;
- each ORCH `done` or `failed` as an `AI result` row, `T7 done: <report>`, as the brain records a
  report; ORCH `progress` rows are left out, since an implementer reports once, at its end.
Rows are numbered again in that order; a line's own number in the playtest's feed is kept as `n`.

THE WORLD: who is online comes from the joins and leaves (the game's events before 06:04 were
lost to a bridge bug, so KrownJM alone until then), and the speaker is always online; positions,
items, the time and the weather are unknown in this replay, and the world block says so. Nothing
was a running copy of an action on 2026-10-08, and no place was saved.

THE TRUTH, what the conversation did with a line: it became a task when a task's context names
it; the tasks whose context names it first are its own, and their labels are its route. A line
the conversation answered in chat: see `answered()`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "brain", REPO / "bridge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from brain.context import Context, Row, parse  # noqa: E402
from brain.tasks import Tasks  # noqa: E402
from brain.world import Seen  # noqa: E402
from vfg_feed import clean_text  # noqa: E402

HOME = Path.home()
INBOX = HOME / ".vfg" / "orch" / "inbox.jsonl"
LABELS = HOME / ".vfg" / "dispatch" / "eval" / "selector-actions-2026-10-10.jsonl"
LABELS_OLD = HOME / ".vfg" / "dispatch" / "eval" / "selector.jsonl"
BEFORE = "2026-10-09"  # the playtest's tasks are the ones written before this
OLD_CLASS = {"direct_ask": "action_oriented", "not_useful": "not_relevant", "context": "context"}
KEPT = ("action_oriented", "conversational", "context")
FIRST_PLAYER = "KrownJM"  # online before the first join the bridge recorded (06:04)
RESULT_MAX = 200          # characters of an ORCH report in its `AI result` row
BRIEF_MAX = 160           # characters of a task in its `AI task` row, as act.py's BRIEF_SHORT
REF = re.compile(r"#(\d+)")
FOLLOW_UP = re.compile(r"^\W*(change\s+to|cancel)\s+(T\d+)", re.I)
ORCH = re.compile(r"^ORCH#\d+ \S+ (T\d+) (done|failed|progress) \| (.*)$", re.S)


def when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def queue_usage(out: Path) -> Path:
    """Where a replay's usage rows queue, one per structured() call: VFG_USAGE_QUEUE, set to the
    run's own folder `out` unless the caller set one. Never $VFG_HOME's queue, which the brain's
    next `vfg usage flush` would send into the live usage table: `out`'s usage.pending.jsonl
    reaches the box only when sent on purpose, `VFG_USAGE_QUEUE=OUT vfg usage flush`. VFG_HOME
    stays as it is: it finds the OpenRouter key."""
    if not os.environ.get("VFG_USAGE_QUEUE"):
        os.environ["VFG_USAGE_QUEUE"] = str(Path(out).resolve())
    where = Path(os.environ["VFG_USAGE_QUEUE"]).expanduser()
    print(f"usage rows queue in {where}/usage.pending.jsonl, not sent", flush=True)
    return where


def replayed(ask):
    """An asker (structured() with a model's settings) whose calls' usage rows say they are a
    replay's: the component the brain gives, the call_id `replay <its own>`. Their rows queue where
    queue_usage() says, which each replay calls first."""
    async def asked(*args, **kw):
        kw["call_id"] = f"replay {kw['call_id']}"[:64]
        return await ask(*args, **kw)
    return asked


@dataclass
class Entry:
    """One row of the context and when the brain would first have had it."""
    seen: str               # ISO UTC
    tie: tuple              # order among rows seen at the same moment
    row: Row
    n: int = 0              # a LINE: its number in the playtest's feed
    line_id: str = ""       # a LINE: its id in `lines`


@dataclass
class Line:
    """A line a player said, with what Jev said then and what the conversation did with it."""
    id: str
    n: int                  # its number in the playtest's feed; 0 when Jev dropped it then
    at: str
    who: str
    text: str
    old: str                # Jev's class then, in today's names
    old_conf: float | None
    judged: str             # when Jev judged it then
    fed: str | None         # when it was fed then; None when it was dropped
    peak: float | None
    ended: str              # when it ended: when the bridge stored it
    tasks: list[dict] = field(default_factory=list)   # the tasks whose context names it first
    covered: bool = False   # a task's context names it


class Playtest:
    def __init__(self, db: Path, inbox: Path = INBOX, labels: Path = LABELS, labels_old: Path = LABELS_OLD):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        self.feed = [dict(r) for r in con.execute("SELECT seq, at, line FROM feed ORDER BY seq")]
        said = [dict(r) for r in con.execute(
            "SELECT id, at, ended_at, player_name, source, text, peak_dbfs, jev_class, jev_confidence, jev_judged_at, "
            "fed_at "
            "FROM lines ORDER BY at, id")]
        con.close()
        self.ai = [r for r in said if r["source"] == "ai"]
        by_said = {(r["at"], r["player_name"]): r for r in said if r["source"] != "ai"}
        self.lines: list[Line] = []
        self.by_n: dict[int, Line] = {}
        self.by_id: dict[str, Line] = {}
        for r in said:
            if r["source"] == "ai":
                continue
            ln = Line(r["id"], 0, r["at"], r["player_name"], r["text"], OLD_CLASS[r["jev_class"]],
                      r["jev_confidence"], r["jev_judged_at"], r["fed_at"], r["peak_dbfs"], r["ended_at"] or r["at"])
            self.lines.append(ln)
            self.by_id[ln.id] = ln
        for f in self.feed:
            if f["line"].startswith("LINE#"):
                who = f["line"].split(" |")[0].split()[2]
                ln = self.by_id[by_said[(f["at"], who)]["id"]]
                ln.n = f["seq"]
                self.by_n[ln.n] = ln

        rows = [json.loads(x) for x in inbox.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.tasks = [t for t in rows if t["at"] < BEFORE]
        self.labels = read_labels(labels)
        self.labels_old = read_labels(labels_old)
        for t in self.tasks:
            refs = [int(x) for x in REF.findall(t.get("context", "")) if int(x) in self.by_n]
            t["refs"] = refs
            t["player"] = self.by_n[refs[0]].who if refs else ""
            for n in refs:
                self.by_n[n].covered = True
            if refs:
                self.by_n[refs[0]].tasks.append(t)
        self.task_by_id = {t["id"]: t for t in self.tasks}
        self.closed: dict[str, tuple[str, str, str]] = {}  # id -> (at, done|failed, report)
        for f in self.feed:
            m = ORCH.match(f["line"])
            if m and m.group(2) != "progress" and m.group(1) not in self.closed:
                self.closed[m.group(1)] = (f["at"], m.group(2), m.group(3))
        self.presence = self._presence()

    # ------------------------------------------------------------- the context

    def entries(self, classes: dict[str, tuple[str, str]] | None = None) -> list[Entry]:
        """Every row of the context in the order the brain would have had it, numbered.
        `classes`: {line id: (class, when it was fed)} for a Jev other than the playtest's; its
        kept lines are the LINE rows then. None: the playtest's own kept lines and classes."""
        out: list[Entry] = []
        if classes is None:
            for ln in self.lines:
                if ln.fed:
                    out.append(self._line_entry(ln, ln.old, ln.fed, ln.old_conf))
        else:
            for ln in self.lines:
                cls, fed = classes.get(ln.id, ("not_relevant", ""))
                if cls in KEPT:
                    out.append(self._line_entry(ln, cls, fed, None))
        for f in self.feed:
            kind = f["line"].split("#", 1)[0]
            if kind in ("GAME", "SYS"):
                row = parse(f["line"], seq=f["seq"], at=f["at"])
                out.append(Entry(f["at"], (1, f["seq"]), row))
        for a in self.ai:
            out.append(Entry(a["at"], (2, a["at"]), Row(0, a["at"], "AI", a["text"], kind="say", processed=True)))
        for t in self.tasks:
            text = f"{t['id']}: {short(t['task'], BRIEF_MAX)}"
            out.append(Entry(t["at"], (3, t["at"]), Row(0, t["at"], "AI", text, kind="task", task=t["id"],
                                                         processed=True)))
        for tid, (at, status, report) in self.closed.items():
            text = f"{tid} {status}: {short(report, RESULT_MAX)}"
            out.append(Entry(at, (4, at), Row(0, at, "AI", text, kind="result", task=tid, processed=True)))
        out.sort(key=lambda e: (e.seen, e.tie))
        for i, e in enumerate(out, 1):
            e.row.seq = i
        return out

    @staticmethod
    def _line_entry(ln: Line, cls: str, fed: str, conf) -> Entry:
        peak = "%ddB" % int(ln.peak + 0.5) if ln.peak is not None else "-"
        c = "-" if conf is None else "%.2f" % conf
        text = f"LINE#0 {ln.at[11:19]} {'_'.join(ln.who.split())} voice {cls} {c} {peak} | {clean_text(ln.text)}"
        return Entry(fed, (0, ln.at, ln.id), parse(text, seq=0, at=ln.at), n=ln.n, line_id=ln.id)

    # ------------------------------------------------------------- the world and the tasks

    def _presence(self) -> list[tuple[str, str, str]]:
        """(at, join|leave|reset, player) from the feed's GAME rows."""
        out = []
        for f in self.feed:
            parts = f["line"].split(" |")[0].split()
            if not f["line"].startswith("GAME#") or len(parts) < 4:
                continue
            kind, who = parts[2], parts[3]
            if kind in ("join", "leave"):
                out.append((f["at"], kind, who))
            elif kind == "server":
                out.append((f["at"], "reset", ""))
        return out

    def online(self, at: str, speaker: str = "") -> list[str]:
        names = [FIRST_PLAYER]
        for t, kind, who in self.presence:
            if t > at:
                break
            if kind == "reset":
                names = []
            elif kind == "join" and who not in names:
                names.append(who)
            elif kind == "leave" and who in names:
                names.remove(who)
        if speaker and speaker not in names:
            names.append(speaker)
        return names

    def seen(self, at: str, speaker: str = "", items: bool = True) -> Seen:
        """THE WORLD NOW for a call at `at`: who is online, everything else unknown."""
        what = "position and items" if items else "position"
        players = "\n".join(f"- {p}: {what} unknown" for p in self.online(at, speaker)) or "nobody"
        return Seen(players=f"PLAYERS ONLINE (their {what} unknown in this replay):\n{players}",
                    clock="TIME: unknown. WEATHER: unknown.", running="RUNNING NOW: none",
                    places="SAVED PLACES: none" if items else "", incantations="INCANTATIONS: none")

    def open_tasks(self, at: str) -> str:
        """OPEN TASKS at `at`, as brain.tasks.Tasks shows them: written and not yet closed, each
        with an implementer on it, as the orchestrator was."""
        tasks = Tasks(Path("/nonexistent/tasks.json"))
        for t in self.tasks:
            if t["at"] > at:
                continue
            closed = self.closed.get(t["id"])
            if closed and closed[0] <= at:
                continue
            tasks.tasks[t["id"]] = {"id": t["id"], "at": t["at"], "player": t["player"], "line": "",
                                    "task": t["task"], "status": "open"}
        return tasks.show(list(tasks.tasks))

    # ------------------------------------------------------------- the truth

    def answered(self) -> set[str]:
        """The ids of the lines the conversation answered in chat without a task. A heuristic,
        since nothing records which line a chat line answers: an AI chat line answers every kept
        line not made a task that was fed in the 60 s before it and after the AI's last chat
        line, unless an ORCH row came after those lines and before it (then it tells a result)."""
        kept = sorted((ln for ln in self.lines if ln.fed), key=lambda ln: ln.fed)
        orch = [f["at"] for f in self.feed if f["line"].startswith("ORCH#")]
        out: set[str] = set()
        prev = ""
        for a in self.ai:
            t = a["at"]
            low = iso(when(t) - timedelta(seconds=60))
            since = [ln for ln in kept if max(prev, low) <= ln.fed < t]
            prev = t
            if not since:
                continue
            last = since[-1].fed
            if any(last < o < t for o in orch):
                continue
            out |= {ln.id for ln in since if not ln.covered}
        return out

    def truth(self, ln: Line, chat: set[str]) -> str:
        """task, chat or nothing: what the conversation did with the line; dropped when Jev
        dropped it then, so the conversation never saw it."""
        if ln.covered:
            return "task"
        if not ln.fed:
            return "dropped"
        return "chat" if ln.id in chat else "nothing"


def read_labels(path: Path) -> dict[str, dict]:
    out = {}
    if not path.exists():
        return out
    for x in path.read_text(encoding="utf-8").splitlines():
        if x.strip():
            obj = json.loads(x)
            if "id" in obj:
                out[obj["id"]] = obj
    return out


class Clock:
    """The context growing in time, the reads, and the batch timer as timer.py runs it: a batch
    fires once `batch_s` have passed since the last read of any kind and something is unread, at
    that moment or when the first thing arrives after it. The AI's own rows arrive read."""

    def __init__(self, entries: list[Entry], batch_s: float = 60.0):
        self.entries = entries
        self.ctx = Context()
        self.i = 0
        self.batch_s = timedelta(seconds=batch_s)
        self.last = when(entries[0].seen) if entries else None
        self.arrived: dict[int, datetime] = {}
        self.batches: list[tuple[str, int]] = []   # (when, rows taken in)

    def _batch(self, upto: datetime) -> None:
        waiting = [r for r in self.ctx.unprocessed()]
        if not waiting:
            return
        due = max(self.last + self.batch_s, min(self.arrived[r.seq] for r in waiting))
        if due <= upto:
            taken = self.ctx.take_unprocessed()
            self.last = due
            self.batches.append((iso(due), len(taken)))

    def until(self, at: str) -> None:
        """Every row the brain would have had by `at`, with the batches that fired meanwhile."""
        t = when(at)
        while self.i < len(self.entries) and self.entries[self.i].seen <= at:
            e = self.entries[self.i]
            s = when(e.seen)
            self._batch(s)
            if self.ctx.add(e.row):
                self.arrived[e.row.seq] = s
            self.i += 1
        self._batch(t)

    def read(self, at: str) -> list[Row]:
        """A live read at `at`: what no call has read yet, marked read; the timer starts again."""
        self.until(at)
        self.last = when(at)
        return self.ctx.take_unprocessed()
