"""One turn per player: a player's lines run one at a time, in the order they came, whichever
agent takes them, while different players' turns run at once (Prototype_Brain_Loop, _One turn
per player_).

Why: the brain is a loop now, and loops that run async make one player's asks overlap more — two
calls at once for one player, each blind to what the other is doing. So the queue sits in front
of both agents, the conversation agent's and the brain's:
- A live line takes its player's turn, or waits for it. At most `waiting_max` lines wait
  (VFG_TURN_WAITING, 3); one more is turned away, never queued, and that player alone is told
  which lines are still waiting (`turned_away`, the message written by turned_away_line()).
- An implementer's report waits for its task's player's turn too, but at the FRONT of their
  queue, ahead of every waiting line and behind any report already there; it is never turned
  away and does not count toward the limit. A task with no player ("") has a lane of its own,
  as a player has.
- A turn ends when its run ends, whatever ends it — it returns, raises or is cancelled — and the
  next one starts then: a turn never sticks.
- close(), at a stop, drops everything waiting; after it nothing starts. A report dropped before
  it ran has its `dropped` called, so the brain closes its task in code, as at a stop today.

Each run starts through `spawn(coro, name, who)`, Brain.spawn: its own asyncio task, kept until
it ends, what it raises logged, and told to `who` — the line's player; nobody for a report, whose
run tells its player itself, as today. A run's coroutine is made only when its turn comes, so one
dropped leaves nothing unawaited. Pure asyncio, no I/O of its own: everything a turn does is in
its run, a hand-up to the brain in the same turn and the wait for its AI rows to come back on the
watch (the settle) included. line() and report() never block, since the feed calls them on a
sync path: `turned_away` is spawned, not awaited. Its one line of its own is the log of a
`dropped` that raised.
"""

from __future__ import annotations

import asyncio
import traceback
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from .config import log

WAITING_MAX = 3   # lines that wait for a player's turn; one more is turned away (VFG_TURN_WAITING)
WORDS_CAP = 40    # a waiting line's words in the turned-away message, at most, "…" included
NAMED_MAX = 5     # waiting lines the message names; the rest it counts
LINE_MAX = 199    # the turned-away message, at most: one chat line under 200 characters
STILL_ON = "Still on: {}. I can't take any more right now — wait a bit, then ask again."
STILL_ON_NONE = "Still on your last one. I can't take any more right now — wait a bit, then ask again."

Run = Callable[[], Awaitable[None]]
Spawn = Callable[[Coroutine[Any, Any, None], str, str], asyncio.Future]
TurnedAway = Callable[[str, list[str]], Awaitable[None]]


@dataclass
class Waiting:
    name: str
    run: Run
    words: str | None  # a line's words; None for a report
    dropped: Callable[[], None] | None = None  # a report's: called when close() drops it before it ran


@dataclass
class Lane:
    running: asyncio.Future | None = None                     # the run holding the turn
    queue: deque[Waiting] = field(default_factory=deque)      # the reports first, then the lines, each in order


class Turns:
    def __init__(self, *, waiting_max: int = WAITING_MAX, spawn: Spawn, turned_away: TurnedAway):
        self.waiting_max = waiting_max
        self.spawn = spawn
        self.turned_away = turned_away
        self.lanes: dict[str, Lane] = {}  # a player's while a run holds their turn; forgotten when idle
        self.closed = False

    def line(self, player: str, words: str, run: Run,
             name: str) -> Literal["started", "waiting", "turned away", "closed"]:
        """A live line: it takes the player's turn ("started"), or waits behind the reports and the
        lines before it ("waiting"). With `waiting_max` lines waiting already it is turned away
        ("turned away"): never run, and the player told which lines still wait, nobody waiting
        for it. After close(): "closed", nothing started."""
        if self.closed:
            return "closed"
        lane = self.lane(player)
        if lane.running is None:
            self.start(player, lane, Waiting(name, run, words))
            return "started"
        lines = self.waiting(player)
        if len(lines) >= self.waiting_max:
            self.spawn(self.turned_away(player, lines), f"{name} turned away", "")
            return "turned away"
        lane.queue.append(Waiting(name, run, words))
        return "waiting"

    def report(self, player: str, run: Run, name: str, *,
               dropped: Callable[[], None] | None = None) -> Literal["started", "first in line", "closed"]:
        """An implementer's report: it takes the player's turn ("started"), or waits at the front
        of their queue, behind only the reports already there ("first in line"). Never turned
        away. When close() drops it before it ran, `dropped()` is called then; after close(),
        "closed": nothing started, and `dropped` not called (the caller knows from the answer)."""
        if self.closed:
            return "closed"
        lane = self.lane(player)
        if lane.running is None:
            self.start(player, lane, Waiting(name, run, None))
            return "started"
        reports = sum(1 for w in lane.queue if w.words is None)  # all at the front
        lane.queue.insert(reports, Waiting(name, run, None, dropped))
        return "first in line"

    def busy(self, player: str) -> bool:
        """A run holds the player's turn."""
        lane = self.lanes.get(player)
        return lane is not None and lane.running is not None

    def waiting(self, player: str) -> list[str]:
        """The words of the player's lines waiting for their turn, in order (a report is no line)."""
        lane = self.lanes.get(player)
        return [w.words for w in lane.queue if w.words is not None] if lane else []

    def close(self) -> int:
        """A stop: everything waiting dropped, its runs never made, and nothing started after this,
        even when a run still going ends. Each report dropped has its `dropped()` called, in the
        order it waited; one that raises is logged, and the rest are called all the same. How
        many were dropped, reports included."""
        self.closed = True
        waited = [w for lane in self.lanes.values() for w in lane.queue]
        for lane in self.lanes.values():
            lane.queue.clear()
        for w in waited:
            if w.dropped is None:
                continue
            try:
                w.dropped()
            except Exception as e:  # noqa: BLE001 — one report's trouble never stops the stop
                log("error", f"{w.name} dropped at the stop: {type(e).__name__}: {e} | "
                             f"{traceback.format_exc(limit=-4)}")
        return len(waited)

    # ------------------------------------------------------------- the turn passing on

    def lane(self, player: str) -> Lane:
        """The player's lane. A run that ended before its end was handled (its done callback runs a
        moment later) gives the turn on here, so a line that comes meanwhile queues behind the ones
        already waiting and is never turned away for a place that is free."""
        lane = self.lanes.setdefault(player, Lane())
        if lane.running is not None and lane.running.done():
            self.advance(player, lane)
            lane = self.lanes.setdefault(player, Lane())
        return lane

    def start(self, player: str, lane: Lane, w: Waiting) -> None:
        lane.running = self.spawn(w.run(), w.name, "" if w.words is None else player)
        lane.running.add_done_callback(lambda t: self.ended(player, t))

    def ended(self, player: str, run: asyncio.Future) -> None:
        """A run ended, however: the next waiting one takes the turn."""
        lane = self.lanes.get(player)
        if lane is not None and lane.running is run:  # else lane() gave the turn on already
            self.advance(player, lane)

    def advance(self, player: str, lane: Lane) -> None:
        lane.running = None
        if lane.queue and not self.closed:
            self.start(player, lane, lane.queue.popleft())
        elif not lane.queue:
            del self.lanes[player]


def one_line(words: str, n: int) -> str:
    """A line's words on one line, without the full stop that ends them, cut to `n` characters,
    "…" included, at a word's end when one is in reach."""
    text = " ".join(str(words).split()).rstrip(" .,;:")
    if len(text) <= n:
        return text
    cut = text[:n - 1]
    if text[n - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def turned_away_line(words: list[str]) -> str:
    """The private message to a player whose line was turned away: the lines still waiting, each
    cut short so the whole is one chat line under 200 characters, however many wait —
    `Still on: “a meteor staff”, “and a shield”, “and a bow”. I can't take any more right now —
    wait a bit, then ask again.`"""
    if not words:
        return STILL_ON_NONE
    named = words[:NAMED_MAX]
    more = f", and {len(words) - len(named)} more" if len(words) > len(named) else ""
    room = LINE_MAX - len(STILL_ON.format("")) - len(more)
    each = min(WORDS_CAP, room // len(named) - 4)  # 4: its two quotes, and the ", " after it
    return STILL_ON.format(", ".join(f"“{one_line(w, each)}”" for w in named) + more)
