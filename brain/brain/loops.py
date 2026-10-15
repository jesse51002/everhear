"""The brain's loops (Prototype_Conversation_And_Router, _A line added to a loop_): every loop the
router can send a line to, thinking or ended, every player's. In memory only: a restart forgets
them, as it forgets what waited for a turn.

Why a registry: loops hold no turn now, so one player's asks run side by side with each other and
with every other player's. Two asks blind to each other were the night's worst trouble (three
teleports for one ask, each run blind to the last), so the router is shown every loop — whose it
is, the lines it holds, whether it is thinking or has ended, its steps used of its limit, its last
reply — and adds a repeat, a correction, an addition or a "never mind" to the loop already on that
thing, rather than start a second one. The conversation agent is shown them too, briefly, so "is
it done yet?" is answered from what is running rather than guessed.

- The budget: `grant` steps (VFG_LOOP_STEPS, 10) for a loop's first line (or the report it is
  for) and `grant` more for each line added, never past `cap` (VFG_LOOP_CAP, 20): limit = min(cap,
  grant × (lines, a report counted as one)), so every line
  gets about 10 steps of its own and no loop runs on for good. At the cap its last step replies or
  hands off, and it is deleted: it never takes a line again, and the next line on that thing
  starts a new loop.
- A line added to a thinking loop waits in `pending` until its next step reads it (loop.py; when
  the step in flight was its ending, the loop takes one more step for it). A thinking loop takes a
  line however long it has run, unless the step in flight is at the cap: then no step is left to
  read it, and it is gone.
- An ended loop is saved, its prompt kept, and a line added within `take_up_s` (VFG_LOOP_TAKE_UP_S,
  120 s) of its CREATION takes it up again while it has a step left under the cap: a new step from
  where it stopped, every earlier one read from the cache, and the line. Past the window it is gone
  as if deleted, and dropped from memory. Lines added to a loop are mostly direct follow-ups said
  right after the ask, so a short window is enough; the router still sees a loop thinking past it.
- show(): WORK UNDER WAY, in full for the router and briefly for the conversation agent.

Pure: no I/O and no await. A state's check and its change are one step for the event loop, so a
line added in the instant a loop ends is either read by it (settle() sees it pending) or takes it
up (add() sees it ended): never lost.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .act import LANDED, StepRun
from .call import Added, Event, Prompt, one_line
from .context import Row
from .world import Seen

THINKING, ENDED, DELETED = "thinking", "ended", "deleted"
GRANT, CAP, TAKE_UP_S = 10, 20, 120.0  # VFG_LOOP_STEPS, VFG_LOOP_CAP, VFG_LOOP_TAKE_UP_S
WORDS_MAX = 80   # a line's words in WORK UNDER WAY
SAID_MAX = 120   # a loop's last reply or record there


def utc_now() -> datetime:
    return datetime.now(UTC)


def cut(text: str, n: int) -> str:
    text = one_line(text)
    return text if len(text) <= n else text[:n - 1] + "…"


@dataclass
class Loop:
    """One brain loop: what it is for, the lines it holds, its budget and its state, its prompt
    kept for a take-up, and what its current run (its start, or a take-up) spent and how it ended."""
    id: str                         # L<n>, counted per run of the brain
    event: Event                    # its first: a LINE, or what a REPORT left
    player: str                     # whose items the tail shows: the speaker, or a report's task's player
    line: str                       # the first line's words: a new task's `line`
    created: float                  # the brain's clock at its creation: the take-up window counts from it
    created_at: str = ""            # HH:MM:SS UTC, as WORK UNDER WAY shows it
    lines: list[Row] = field(default_factory=list)       # every line it holds, the first included, in order
    pending: list[Added] = field(default_factory=list)   # lines added and not yet read by a step
    limit: int = GRANT              # its last step's number now: min(cap, grant × lines)
    steps: int = 0                  # steps started, over its whole life
    state: str = THINKING           # thinking, ended (saved: its prompt kept) or deleted
    prompt: Prompt | None = None    # its prompt, kept for a take-up
    seen: Seen | None = None        # the world as read at its start; a take-up reuses it
    seen_seq: int = 0               # the highest context seq its model has been shown
    did: bool = False               # an action of it has landed in the game, over its whole life
    last_reply: str = ""            # the last chat line it said
    record: str = ""                # its last record
    # Its current run: what it spent, the time to its reply and how it ended (log_loop's line).
    started: float = 0.0
    runs: list[StepRun] = field(default_factory=list)
    calls: int = 0
    cost: float = 0.0
    cost_known: bool = True
    prompt_tokens: int = 0
    cached_tokens: int = 0
    replied: float | None = None    # seconds from the run's start to the reply said
    recorded: bool = False          # the run's record is written (its `AI action` row)
    how: str = "broke"              # how the run ended: reply, silent, handoff, failed, cut off, or broke

    def begin(self, now: float) -> None:
        """A run starts, at its creation or a take-up: what it spends is counted afresh, so the
        log's loop lines add up to what the loops cost."""
        self.started, self.runs, self.calls, self.cost, self.cost_known = now, [], 0, 0.0, True
        self.prompt_tokens = self.cached_tokens = 0
        self.replied, self.recorded, self.how = None, False, "broke"

    def spend(self, got) -> None:
        """A step's call, answered (an Answer) or failed (an LlmError)."""
        self.calls += 1
        self.cost += got.cost
        self.cost_known = self.cost_known and getattr(got, "cost_known", True)
        self.prompt_tokens += got.prompt_tokens
        self.cached_tokens += got.cached_tokens

    def landed(self) -> list[str]:
        """The instructions of every action of the run that landed, in order."""
        return [r.instruction for run in self.runs for r in run.actions if r.state == LANDED]

    def words(self) -> list[str]:
        """What it is for, in the players' words: each line it holds; a report's, what was left."""
        if self.event.kind == "REPORT":
            name = (self.event.task or {}).get("name") or "a task"
            return [f"what {name}'s report left: {cut(self.event.left, WORDS_MAX)}"]
        return [f"“{cut(r.text, WORDS_MAX)}”" for r in self.lines]


class Loops:
    def __init__(self, grant: int = GRANT, cap: int = CAP, take_up_s: float = TAKE_UP_S,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = utc_now):
        self.grant, self.cap, self.take_up_s = grant, cap, take_up_s
        self.clock = clock
        self.now = now
        self.loops: dict[str, Loop] = {}  # every loop in memory, oldest first: thinking, or ended and saved
        self.count = 0
        self.closed = False               # the brain stops: no line is added, and no loop is saved

    def new(self, event: Event, player: str, line: str) -> Loop:
        """A loop for a LINE the router sent nowhere else, or the work a REPORT left: thinking,
        with the first line's grant."""
        self.count += 1
        loop = Loop(f"L{self.count}", event, player, line, self.clock(),
                    self.now().astimezone(UTC).strftime("%H:%M:%S"),
                    lines=[event.row] if event.kind == "LINE" and event.row is not None else [])
        loop.limit = self.limit(loop)
        loop.begin(loop.created)
        self.loops[loop.id] = loop
        return loop

    def get(self, id: str) -> Loop | None:
        return self.loops.get(id.strip())

    def all(self) -> list[Loop]:
        return list(self.loops.values())

    def limit(self, loop: Loop) -> int:
        """min(cap, grant × what it holds): its lines, and a report's event as one more, so a line
        added to a report's loop gets its own grant too."""
        held = len(loop.lines) + (1 if loop.event.kind == "REPORT" else 0)
        return min(self.cap, self.grant * max(1, held))

    def open(self, loop: Loop, now: float | None = None) -> bool:
        """An ended loop can be taken up: inside the window from its creation, a step left under
        the cap, and its prompt kept."""
        now = self.clock() if now is None else now
        return (loop.state == ENDED and loop.prompt is not None and loop.steps < self.cap
                and now - loop.created < self.take_up_s)

    def offered(self) -> list[Loop]:
        """The loops a line can be added to, oldest first: every one thinking, and every ended one
        that can still be taken up. One past its window, or deleted, is dropped from memory here."""
        now = self.clock()
        for loop in [x for x in self.loops.values() if x.state != THINKING and not self.open(x, now)]:
            loop.state = DELETED
            del self.loops[loop.id]
        return list(self.loops.values())

    def add(self, loop: Loop, row: Row, *, said: str = "", reason: str = "") -> Literal["read next", "taken up",
                                                                                         "gone"]:
        """A line the router sent to `loop`: "read next" (it thinks, and its next step reads the
        line), "taken up" (it had ended: thinking again, and the caller runs it on), or "gone"
        (deleted, past its window, at the cap with no step left, or the brain stopping: the
        caller starts a new loop instead). Each line added grants `grant` more steps, up to the cap."""
        if self.closed or self.loops.get(loop.id) is not loop or loop.state == DELETED:
            return "gone"
        if loop.state == ENDED and not self.open(loop):
            loop.state = DELETED
            del self.loops[loop.id]
            return "gone"
        if loop.state == THINKING and loop.steps >= self.cap:  # its step in flight is its last: none to read it
            return "gone"
        loop.lines.append(row)
        loop.pending.append(Added(row, said, reason))
        loop.limit = self.limit(loop)
        if loop.state == ENDED:
            loop.state = THINKING
            return "taken up"
        return "read next"

    def settle(self, loop: Loop) -> bool:
        """A run's end, once its ending (or its error) went out: True when a line was added while
        it ran, so the loop takes another step; else it is ended and saved, or deleted at the cap
        (or with no prompt to take up, or the brain stopping) and dropped from memory. No await
        between the check and the change, so a line added meanwhile is never lost."""
        if loop.pending and not self.closed:
            return True
        if self.closed or loop.steps >= self.cap or loop.prompt is None:
            self.drop(loop)
        else:
            loop.state = ENDED
        return False

    def drop(self, loop: Loop) -> None:
        """Deleted: it never takes a line again."""
        loop.state = DELETED
        if self.loops.get(loop.id) is loop:
            del self.loops[loop.id]

    def close(self) -> int:
        """At the stop: no line is added from now on, every saved loop is forgotten, and a loop
        still thinking is deleted when its run ends. How many saved loops were forgotten."""
        self.closed = True
        saved = [x for x in self.loops.values() if x.state != THINKING]
        for loop in saved:
            self.drop(loop)
        return len(saved)

    def busy(self) -> set[str]:
        """The players with a loop thinking."""
        return {x.player for x in self.loops.values() if x.state == THINKING and x.player}

    # ------------------------------------------------------------- WORK UNDER WAY

    def show(self, brief: bool) -> str:
        """WORK UNDER WAY: every loop a line can be added to (offered()). In full, the router's:
        each loop's id, player, state, steps of its limit, start, lines (an added one not read yet
        marked so), last reply and record, so it can tell which loop a line is about. Brief, the
        conversation agent's: whose, what, and being done now or done a moment ago. Implementers
        are not here: TASKS marks the tasks one is on."""
        loops = self.offered()
        now = self.clock()
        if brief:
            if not loops:
                return "WORK UNDER WAY: nothing right now"
            return ("WORK UNDER WAY (what is being done for the players now, or was a moment ago):\n"
                    + "\n".join(self._brief(x, now) for x in loops))
        if not loops:
            return "WORK UNDER WAY: no brain loops"
        return ("WORK UNDER WAY (every brain loop a line can be added to, every player's, oldest first; implementers "
                "are under TASKS):\n" + "\n".join(self._full(x, now) for x in loops))

    def _full(self, loop: Loop, now: float) -> str:
        """`- L3, for KrownJM, thinking, step 2 of 10, started 03:09:41 (12 s ago); lines: #58 “…”
        03:09:40, #60 “…” 03:09:50 (added, not read yet); last reply: “…”`."""
        age = now - loop.created
        if loop.state == THINKING:
            state = f"thinking, step {max(1, loop.steps)} of {loop.limit}"
        else:
            left = max(0, self.take_up_s - age)
            state = f"ended, saved: can take a line for {left:.0f} s more; {loop.steps} of {loop.limit} steps used"
        unread = {a.row.seq for a in loop.pending}
        if loop.event.kind == "REPORT":
            what = loop.words()[0]
        else:
            what = "lines: " + ", ".join(f"#{r.seq} “{cut(r.text, WORDS_MAX)}” {r.hms}"
                                         + (" (added, not read yet)" if r.seq in unread else "") for r in loop.lines)
        bits = [f"- {loop.id}, for {loop.player or 'nobody'}, {state}, started {loop.created_at} ({age:.0f} s ago)",
                what]
        if loop.record.strip():
            bits.append(f"did: {cut(loop.record, SAID_MAX)}")
        if loop.last_reply.strip():
            bits.append(f"last reply: “{cut(loop.last_reply, SAID_MAX)}”")
        return "; ".join(bits)

    def _brief(self, loop: Loop, now: float) -> str:
        """`- KrownJM: “can you teleport me to Stirka” — being done now (12 s)`, or `— done a moment
        ago: “<its last reply>”`."""
        head = f"- {loop.player or 'nobody'}: {', then '.join(loop.words())}"
        if loop.state == THINKING:
            return f"{head} — being done now ({now - loop.created:.0f} s)"
        said = f": “{cut(loop.last_reply, SAID_MAX)}”" if loop.last_reply.strip() else ""
        if loop.how == "failed":
            return f"{head} — failed a moment ago{said}"
        if loop.how == "handoff":
            return f"{head} — passed on to be built a moment ago{said}"
        return f"{head} — done a moment ago{said}"
