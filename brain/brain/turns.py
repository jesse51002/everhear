"""One turn per player: a player's lines to the AI run one at a time, in the order they came,
while different players' turns run at once (Prototype_Conversation_And_Router, _The queue, in
front of the conversation agent and the router_).

What holds a turn is two quick calls: the conversation agent's answer (respond.py) and, when it
hands the line off, the router's (router.py), which places the work. A turn ends once the router
has placed the line, so the player's next line is routed knowing where the last one went: three
asks for one thing in a row land in one loop. It never waits for the work: brain loops hold no
turn and run side by side, a player's too, and an implementer runs on its own. On the 2026-10-12
night the queue sat in front of the brain, and one player's asks waited up to 45 s behind their
own loop, each then running blind to the last; a turn now lasts a few seconds.
- A live line takes its player's turn, or waits for it. At most `waiting_max` lines wait
  (VFG_TURN_WAITING, 3); one more is turned away, never queued, and that player alone is told
  which lines are still waiting (`turned_away`, the message written by turned_away_line()).
- An implementer's report never queues: it goes to the conversation agent at once, beside
  whatever the player's turn is doing (loop.py, on_report).
- A turn ends when its run ends, whatever ends it — it returns, raises or is cancelled — and the
  next one starts then: a turn never sticks.
- close(), at a stop, drops everything waiting; after it nothing starts.

Each run starts through `spawn(coro, name, who)`, Brain.spawn: its own asyncio task, kept until
it ends, what it raises logged, and told to `who`, the line's player. A run's coroutine is made
only when its turn comes, so one dropped leaves nothing unawaited. Pure asyncio, no I/O of its
own: everything a turn does is in its run, the wait for its AI rows to come back on the watch
(the settle) included. line() never blocks, since the feed calls it on a sync path:
`turned_away` is spawned, not awaited.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

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
    words: str  # the line's words


@dataclass
class Lane:
    running: asyncio.Future | None = None                     # the run holding the turn
    queue: deque[Waiting] = field(default_factory=deque)      # the lines waiting, in order


class Turns:
    def __init__(self, *, waiting_max: int = WAITING_MAX, spawn: Spawn, turned_away: TurnedAway):
        self.waiting_max = waiting_max
        self.spawn = spawn
        self.turned_away = turned_away
        self.lanes: dict[str, Lane] = {}  # a player's while a run holds their turn; forgotten when idle
        self.closed = False

    def line(self, player: str, words: str, run: Run,
             name: str) -> Literal["started", "waiting", "turned away", "closed"]:
        """A live line: it takes the player's turn ("started"), or waits behind the lines before
        it ("waiting"). With `waiting_max` lines waiting already it is turned away
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

    def busy(self, player: str) -> bool:
        """A run holds the player's turn."""
        lane = self.lanes.get(player)
        return lane is not None and lane.running is not None

    def waiting(self, player: str) -> list[str]:
        """The words of the player's lines waiting for their turn, in order."""
        lane = self.lanes.get(player)
        return [w.words for w in lane.queue] if lane else []

    def close(self) -> int:
        """A stop: everything waiting dropped, its runs never made, and nothing started after this,
        even when a run still going ends. How many were dropped."""
        self.closed = True
        waited = sum(len(lane.queue) for lane in self.lanes.values())
        for lane in self.lanes.values():
            lane.queue.clear()
        return waited

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
        lane.running = self.spawn(w.run(), w.name, player)
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
