"""The brain's tasks (Prototype_Named_Tasks): one per implementer's work, keyed by its name, kept
in memory and written through to the `tasks` table of the box's vfg.db, which only the brain
writes, through `vfg task set` (Contracts §4, §8). There is no file.

    {"name": "galick_gun_styrk", "description": "A Galick Gun for Styrkaeopteryx: …",
     "player": "Styrkaeopteryx", "status": "open|done|failed", "line": "<the player's words>",
     "brief": "<the latest brief>", "made": {"scripts": [], "actions": [], "presets": [], "copies": []},
     "variables": [{"name": "cooldown", …}] or null, "session": null, "result": "<the brain's line>",
     "created_at": "<iso>", "updated_at": "<iso>", "closed_at": null}

The tasks come in at the start from `vfg context --json`, every row of the table. Every change
writes the whole row in the background (`send`), and the writes of one task are chained, so they
go out in the order they were made; the toolkit keeps the newer `updated_at` should two ever
cross. `vfg route --task` reads a row back, and `vfg brain status` lists the open ones.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from . import names
from .config import log, now_iso

Send = Callable[[dict], Awaitable[bool]]   # True when `vfg task set` exited 0
MADE_KINDS = ("scripts", "actions", "presets", "copies")
STATUSES = ("open", "done", "failed")
DESCRIPTION_MAX = 160  # characters of a description in TASKS


def no_made() -> dict[str, list[str]]:
    return {k: [] for k in MADE_KINDS}


def made_of(made: Any) -> dict[str, list[str]]:
    """`made` with all four kinds, each a list of names; anything else in it is left out."""
    out = no_made()
    if isinstance(made, dict):
        for k in MADE_KINDS:
            got = made.get(k)
            if isinstance(got, list):
                out[k] = [str(x) for x in got if str(x).strip()]
    return out


def one_line(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


class Tasks:
    def __init__(self, rows: Iterable[dict] = (), send: Send | None = None, clock=now_iso):
        self.send = send
        self.clock = clock
        self.tasks: dict[str, dict[str, Any]] = {}
        self.writes: dict[str, asyncio.Task] = {}   # each task's last write, the next one chained to it
        for row in rows:
            row = self._row(row)
            if row is not None:
                self.tasks[row["name"]] = row

    @staticmethod
    def _row(row: Any) -> dict[str, Any] | None:
        """A row as `vfg context --json` gives it, with every column; None for one with no name."""
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            return None
        out = {k: row.get(k) for k in ("name", "description", "player", "status", "line", "brief", "made",
                                       "variables", "session", "result", "created_at", "updated_at",
                                       "closed_at")}
        out["name"] = str(out["name"]).strip()
        out["description"] = str(out["description"] or "")
        out["status"] = out["status"] if out["status"] in STATUSES else "open"
        out["made"] = made_of(out["made"]) if out["made"] is not None else no_made()
        if not isinstance(out["variables"], list):
            out["variables"] = None
        return out

    # ------------------------------------------------------------- reading

    def names(self) -> set[str]:
        return set(self.tasks)

    def get(self, name: str) -> dict[str, Any] | None:
        """A copy of the task's row, or None."""
        t = self.tasks.get(name)
        return copy.deepcopy(t) if t is not None else None

    def all(self) -> list[dict[str, Any]]:
        """Every task, a copy of each, oldest created first."""
        return [copy.deepcopy(t) for t in sorted(self.tasks.values(),
                                                  key=lambda t: (str(t.get("created_at") or ""), t["name"]))]

    def open_ones(self) -> list[dict[str, Any]]:
        return [t for t in self.all() if t.get("status") == "open"]

    def show(self, running: Iterable[str] = ()) -> str:
        """TASKS, the brain's and Respond's: every task, one line each, oldest first."""
        running = set(running)
        rows = []
        for t in self.all():
            state = "an implementer is on it" if t["name"] in running else t.get("status") or "open"
            rows.append(f"- {t['name']} for {t.get('player') or 'nobody'}, {state}: "
                        f"{one_line(t.get('description'), DESCRIPTION_MAX)}")
        if not rows:
            return "TASKS: none yet"
        return ("TASKS (every task on this server, oldest first: its name, for whom, its status, what it is "
                "now; you never name one):\n" + "\n".join(rows))

    # ------------------------------------------------------------- changing

    def open(self, name: str, player: str, line: str, brief: str, description: str) -> None:
        """A new task, open: ValueError when the name is taken or is not a task's name."""
        if not names.NAME.match(name or "") or name in names.RESERVED:
            raise ValueError(f"{name!r} is not a task's name")
        if name in self.tasks:
            raise ValueError(f"{name} is a task already")
        now = self.clock()
        self.tasks[name] = {"name": name, "description": description, "player": player or None,
                            "status": "open", "line": line, "brief": brief, "made": no_made(), "variables": None,
                            "session": None, "result": None, "created_at": now, "updated_at": now,
                            "closed_at": None}
        self._write(name)

    def follow_up(self, name: str, brief: str, description: str) -> None:
        """A follow-up: the task is open again with its new brief and description, until its
        implementer reports."""
        self._change(name, status="open", closed_at=None, brief=brief,
                     **({"description": description} if description.strip() else {}))

    def describe(self, name: str, description: str) -> None:
        """What the thing is now, from the implementer's DESCRIPTION line."""
        t = self.tasks.get(name)
        if t is not None and description.strip() and description != t.get("description"):
            self._change(name, description=description)
        elif t is None:
            self._unknown(name, "describe")

    def merge_made(self, name: str, made: dict | None) -> None:
        """What the implementer made, added to what the row has: each kind's names once, in order."""
        t = self.tasks.get(name)
        if t is None:
            return self._unknown(name, "merge what it made into")
        if not made:
            return None
        now, more = made_of(t.get("made")), made_of(made)
        merged = {k: now[k] + [n for n in dict.fromkeys(more[k]) if n not in now[k]] for k in MADE_KINDS}
        if merged != t.get("made"):
            self._change(name, made=merged)
        return None

    def set_variables(self, name: str, variables: list | None) -> None:
        """The variables of the task's action, as vfg_actions.variables() reads them; a write only
        when they changed."""
        t = self.tasks.get(name)
        if t is None:
            return self._unknown(name, "set the variables of")
        if variables != t.get("variables"):
            self._change(name, variables=variables)
        return None

    def set_session(self, name: str, session: str | None) -> None:
        """The implementer's session id, so a follow-up after a restart resumes it."""
        t = self.tasks.get(name)
        if t is not None and session and t.get("session") != session:
            self._change(name, session=session)

    def note(self, name: str, result: str) -> None:
        """A result so far, the task kept open: a follow-up to it runs."""
        self._change(name, result=result)

    def close(self, name: str, status: str, result: str) -> None:
        self._change(name, status=status, result=result, closed_at=self.clock())

    def merge(self, rows: Iterable[dict]) -> None:
        """After the feed restarted, the tasks read again: a row the memory lacks is added, and a
        task the memory has that the read lacked (a box provisioned again) is written through.
        For a name in both, the memory wins."""
        read = {}
        for row in rows:
            row = self._row(row)
            if row is not None:
                read[row["name"]] = row
        added = [n for n in read if n not in self.tasks]
        for n in added:
            self.tasks[n] = read[n]
        missing = [n for n in self.tasks if n not in read]
        for n in missing:
            self._write(n)
        if added or missing:
            log("tasks", f"read again: {len(added)} added, {len(missing)} written back to the box")

    # ------------------------------------------------------------- writing

    def _unknown(self, name: str, what: str) -> None:
        log("tasks", f"cannot {what} {name}: not a task here")

    def _change(self, name: str, **fields: Any) -> None:
        t = self.tasks.get(name)
        if t is None:
            return self._unknown(name, f"set {', '.join(fields)} on")
        t.update(fields, updated_at=self.clock())
        self._write(name)
        return None

    def _write(self, name: str) -> None:
        """The whole row, sent after the task's last write has gone out."""
        if self.send is None:
            return
        row = copy.deepcopy(self.tasks[name])
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log("tasks", f"{name} not written: no event loop to send it from")
            return
        prev = self.writes.get(name)
        t = loop.create_task(self._send(prev, row), name=f"task set {name}")
        self.writes[name] = t
        t.add_done_callback(lambda done: self.writes.pop(name, None) if self.writes.get(name) is done else None)

    async def _send(self, prev: asyncio.Task | None, row: dict) -> bool:
        if prev is not None:
            await asyncio.gather(prev, return_exceptions=True)
        try:
            return bool(await self.send(row))
        except Exception as e:  # noqa: BLE001 — a write that broke is a failed write, logged
            log("tasks", f"writing {row['name']} broke: {type(e).__name__}: {e}")
            return False

    async def drain(self, timeout: float) -> None:
        """At the stop: wait up to `timeout` for the writes still going out; cancel the rest."""
        pending = list(self.writes.values())
        if not pending:
            return
        _, left = await asyncio.wait(pending, timeout=timeout)
        for t in left:
            t.cancel()
        await asyncio.gather(*left, return_exceptions=True)
        if left:
            log("tasks", f"{len(left)} task write(s) still going out at the stop: cut off")
