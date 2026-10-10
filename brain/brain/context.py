"""The shared context: every feed row the brain keeps, in seq order, each with its processed
mark, and how the conversation is shown to a model. Pure: no I/O, so a test builds one by hand.

The rows (Contracts §7, and the brain plan's AI type):

    LINE#812 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle
    GAME#814 18:22:40 death Steve | Steve was slain by Zombie
    SYS#816 18:25:00 | jev unavailable — passing every line as context
    AI#817 18:25:02 action T7 | command: give Steve diamond_pickaxe — ok: Gave 1 [Diamond Pickaxe] to Steve

A line of a type it does not know (an old ORCH line) is kept as its plain text.

A row is processed once a call has read it: a live read (a brain call for a line, a Respond
call) takes in everything not yet processed, and the batch call catches up what no live read
took. The AI's own rows are processed as they arrive: the brain wrote them.

The conversation goes to a model as minute-blocks: a block is a run of consecutive rows sharing
a minute, and once the next minute's row arrives it never changes. Every `checkpoint`-th closed
block is marked for the prompt cache, the last three of them with the opening as the fourth
mark, so each call's prompt matches the last call's up to its last mark (Prototype_Brain:
98% of a batch call's prompt cached, at $0.0047).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

AI_KINDS = ("say", "action", "task", "result", "note")
LIVE = ("action_oriented", "conversational")  # the classes that get a live call of their own
MARKS = 3  # conversation marks; the opening is the fourth
HEAD = re.compile(r"^(?P<type>[A-Z]+)#(?P<seq>\d+) (?P<hms>\d\d:\d\d:\d\d)(?: (?P<fields>.*))?$")


@dataclass
class Row:
    seq: int
    at: str                  # ISO UTC, when it was said or happened
    type: str                # LINE, GAME, SYS or AI; "" for a line of a type not known here
    text: str                # what follows `|`; the whole line for an unknown type
    who: str = ""            # LINE: the player; GAME: the player, or ""
    source: str = ""         # LINE: voice or chat
    cls: str = ""            # LINE: Jev's class (action_oriented, conversational, context, not_relevant)
    kind: str = ""           # GAME: its kind (death, join, cast …); AI: say, action, task, result or note
    task: str = ""           # AI: the task it is about, or ""
    processed: bool = False

    @property
    def minute(self) -> str:
        return self.at[:16]

    @property
    def hms(self) -> str:
        return self.at[11:19]

    def show(self) -> str:
        """The row as a model reads it, on one line."""
        t = self.hms
        if self.type == "LINE":
            how = f"typed, {self.cls}" if self.source == "chat" else self.cls
            return f"{t} {self.who} ({how}): {self.text}"
        if self.type == "GAME":
            return f"{t} game, {self.kind}: {self.text}"
        if self.type == "SYS":
            return f"{t} system: {self.text}"
        if self.type == "AI":
            head = "AI" if self.kind == "say" else f"AI {self.kind}"
            if self.task and not self.text.startswith(self.task):
                head += f" [{self.task}]"
            return f"{t} {head}: {self.text}"
        return self.text


def at_of(hms: str, now: datetime) -> str:
    """A feed line's HH:MM:SS (UTC) as a full time: today's, or yesterday's when that would be
    more than a minute in the future (a line from just before midnight)."""
    h, m, s = (int(x) for x in hms.split(":"))
    when = now.astimezone(UTC).replace(hour=h, minute=m, second=s, microsecond=0)
    if when - now > timedelta(minutes=1):
        when -= timedelta(days=1)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse(line: str, *, seq: int | None = None, at: str | None = None, processed: bool = False,
          now: datetime | None = None) -> Row | None:
    """One feed line as a Row. `seq` and `at` are the row's own when they are known (`vfg context
    --json`); else they are read from the line, its HH:MM:SS against `now`. None for a blank line,
    or one with no seq of its own that is not a feed line."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    head, bar, rest = line.partition(" |")
    text = rest[1:] if rest.startswith(" ") else rest
    m = HEAD.match(head) if bar else None
    if m is None:
        return None if seq is None else Row(seq, at or "", "", line, processed=processed)
    seq, kind = int(m.group("seq")) if seq is None else seq, m.group("type")
    when = at or at_of(m.group("hms"), now or datetime.now(UTC))
    fields = (m.group("fields") or "").split()
    if kind == "LINE" and len(fields) >= 3:
        return Row(seq, when, "LINE", text, who=fields[0], source=fields[1], cls=fields[2], processed=processed)
    if kind == "GAME" and fields:
        who = fields[1] if len(fields) > 1 and fields[1] != "-" else ""
        return Row(seq, when, "GAME", text, who=who, kind=fields[0], processed=processed)
    if kind == "SYS":
        return Row(seq, when, "SYS", text, processed=processed)
    if kind == "AI" and fields:
        task = fields[1] if len(fields) > 1 and fields[1] != "-" else ""
        return Row(seq, when, "AI", text, kind=fields[0], task=task, processed=True)
    return Row(seq, when, "", line, processed=processed)


class Context:
    """The rows, in seq order. Only the brain's watch adds to it."""

    def __init__(self, rows: list[Row] | None = None):
        self.rows: list[Row] = []
        for r in rows or []:
            self.add(r)

    @property
    def last_seq(self) -> int:
        return self.rows[-1].seq if self.rows else 0

    def add(self, row: Row) -> bool:
        """Append a row; False for one already here, a seq at or below the last (and vfg
        watch's own notices, SYS#0)."""
        if row.seq <= self.last_seq:
            return False
        self.rows.append(row)
        return True

    def unprocessed(self) -> list[Row]:
        return [r for r in self.rows if not r.processed]

    def take_unprocessed(self) -> list[Row]:
        """What no call has read yet, marked processed here; the caller marks it in the database."""
        out = self.unprocessed()
        for r in out:
            r.processed = True
        return out

    def take_before(self, at: str) -> list[Row]:
        """The unprocessed rows from before `at`, marked processed: history, never acted on."""
        out = [r for r in self.rows if not r.processed and r.at < at]
        for r in out:
            r.processed = True
        return out

    def blocks(self) -> list[list[Row]]:
        """The rows as minute-blocks: each a run of consecutive rows sharing a minute."""
        out: list[list[Row]] = []
        for r in self.rows:
            if out and out[-1][-1].minute == r.minute:
                out[-1].append(r)
            else:
                out.append([r])
        return out

    def cached(self, opening: str, checkpoint: int) -> list[tuple[str, bool]]:
        """What structured() caches: the opening (marked), then the conversation a minute-block at
        a time, every `checkpoint`-th closed block marked, the last MARKS of them. The last block
        may still grow, so it is never marked."""
        blocks = self.blocks()
        closed = len(blocks) - 1
        marks = [i for i in range(1, closed + 1) if i % checkpoint == 0][-MARKS:]
        out = [(opening, True)]
        out += [("".join(r.show() + "\n" for r in b), i in marks) for i, b in enumerate(blocks, 1)]
        return out


def show(rows: list[Row]) -> str:
    return "\n".join(r.show() for r in rows)
