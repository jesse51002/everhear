"""One turn per player: a player's lines one at a time and in the order they came, players side
by side, the line past three waiting turned away with a private message naming them, no report
in any queue, a turn given on whatever ends its run, a stop dropping what waits, and the
message's words cut short. Each run waits for a future the test resolves; nothing sleeps but a
yield."""

import asyncio
import re

import pytest

from brain.turns import STILL_ON_NONE, WORDS_CAP, Turns, turned_away_line

CASTLE = "build me a castle"
SPEC = ["a meteor staff", "and a shield", "and a bow"]  # the spec's turned-away example


async def ticks(n: int = 10) -> None:
    """Let everything that can move move: a run's end, its done callback, the next run's start."""
    for _ in range(n):
        await asyncio.sleep(0)


class Kit:
    """Turns over a spawn like Brain.spawn (its own task, what it raises kept, never raised), with
    runs that start, then wait for the test to end them."""

    def __init__(self, waiting_max: int = 3):
        self.seen: list[str] = []                   # "start <name>", "end <name>", as they happened
        self.made: list[str] = []                   # whose coroutine was made: only when its turn came
        self.gates: dict[str, asyncio.Future] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.spawned: list[tuple[str, str]] = []    # (name, who)
        self.errors: list[str] = []
        self.told: list[tuple[str, list[str]]] = []
        self.turns = Turns(waiting_max=waiting_max, spawn=self.spawn, turned_away=self.turned_away)

    def spawn(self, coro, name: str, who: str) -> asyncio.Task:
        async def guarded():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — as Brain.spawn: logged, never raised
                self.errors.append(f"{name}: {e}")

        self.spawned.append((name, who))
        self.tasks[name] = asyncio.create_task(guarded(), name=name)
        return self.tasks[name]

    async def turned_away(self, player: str, words: list[str]) -> None:
        self.told.append((player, words))

    def run(self, name: str):
        gate = asyncio.get_running_loop().create_future()
        self.gates[name] = gate

        async def go():
            self.seen.append(f"start {name}")
            try:
                await gate
            finally:
                self.seen.append(f"end {name}")

        def make():
            self.made.append(name)
            return go()

        return make

    def line(self, player: str, words: str) -> str:
        return self.turns.line(player, words, self.run(words), words)

    def started(self) -> list[str]:
        return [s.removeprefix("start ") for s in self.seen if s.startswith("start ")]

    def running(self) -> list[str]:
        ended = {s.removeprefix("end ") for s in self.seen if s.startswith("end ")}
        return sorted(n for n in self.started() if n not in ended)

    async def end(self, name: str, error: Exception | None = None) -> None:
        if error is None:
            self.gates[name].set_result(None)
        else:
            self.gates[name].set_exception(error)
        await ticks()


@pytest.fixture
async def kit():
    """A Kit, and at the test's end a stop as Brain.stop makes one: what waits dropped first, so
    cancelling a run going starts nothing; then every run left going cancelled."""
    kits: list[Kit] = []

    def new(waiting_max: int = 3) -> Kit:
        kits.append(Kit(waiting_max))
        return kits[-1]

    yield new
    for k in kits:
        k.turns.close()
    left = [t for k in kits for t in k.tasks.values() if not t.done()]
    for t in left:
        t.cancel()
    await asyncio.gather(*left, return_exceptions=True)


async def test_one_players_lines_take_turns_in_the_order_they_came(kit):
    k = kit()
    assert [k.line("Alex", w) for w in (CASTLE, "and a moat", "make it day")] == ["started", "waiting", "waiting"]
    await ticks()
    assert k.running() == [CASTLE] and k.made == [CASTLE]  # a waiting run is not even made yet
    assert k.turns.busy("Alex") and k.turns.waiting("Alex") == ["and a moat", "make it day"]
    await k.end(CASTLE)
    assert k.running() == ["and a moat"] and k.turns.waiting("Alex") == ["make it day"]
    await k.end("and a moat")
    await k.end("make it day")
    assert k.seen == [f"{e} {n}" for n in (CASTLE, "and a moat", "make it day") for e in ("start", "end")]
    assert not k.turns.busy("Alex") and k.turns.waiting("Alex") == [] and k.turns.lanes == {}  # forgotten
    assert k.line("Alex", "thanks") == "started"  # the turn is free: the next line starts at once
    assert k.spawned[0] == (CASTLE, "Alex")       # a line's run is spawned for its player: an error is told


async def test_two_players_run_at_once_and_a_turn_ending_moves_nobody_elses(kit):
    k = kit()
    assert k.line("Alex", "a meteor staff") == "started"
    assert k.line("Steve", "make it day") == "started"
    assert k.line("Alex", "and a shield") == "waiting"
    await ticks()
    assert k.running() == ["a meteor staff", "make it day"]
    await k.end("make it day")  # Steve's turn ending moves nobody else's
    assert k.running() == ["a meteor staff"] and k.turns.waiting("Alex") == ["and a shield"]
    assert ("make it day", "Steve") in k.spawned  # every run is a line's, spawned for its player


def test_reports_never_queue():
    """An implementer's report goes to the conversation agent at once (loop.py): the turns hold lines alone."""
    assert not hasattr(Turns, "report")


async def test_the_line_past_three_waiting_is_turned_away_and_its_player_alone_told_which_wait(kit):
    k = kit()
    took = [k.line("Alex", w) for w in (CASTLE, *SPEC, "and a horse")]
    assert took == ["started", "waiting", "waiting", "waiting", "turned away"]
    assert k.told == []  # line() never waits for the message: it is a task of its own
    await ticks()
    assert k.told == [("Alex", SPEC)] and ("and a horse turned away", "") in k.spawned
    assert turned_away_line(k.told[0][1]) == ("Still on: “a meteor staff”, “and a shield”, “and a bow”. "
                                              "I can't take any more right now — wait a bit, then ask again.")
    assert "and a horse" not in k.made and k.turns.waiting("Alex") == SPEC  # never queued
    assert k.line("Steve", "make it day") == "started"  # another player's lines are counted apart
    await k.end(CASTLE)  # the next line takes the turn, and a place frees
    assert k.line("Alex", "and a horse") == "waiting"
    assert k.turns.waiting("Alex") == ["and a shield", "and a bow", "and a horse"]

    k = kit(waiting_max=1)  # VFG_TURN_WAITING
    assert [k.line("Alex", w) for w in (CASTLE, *SPEC)] == ["started", "waiting", "turned away", "turned away"]
    await ticks()
    assert k.told == [("Alex", ["a meteor staff"])] * 2


async def test_a_run_that_raises_or_is_cancelled_still_gives_the_turn_on(kit):
    k = kit()
    for w in ("give me a sword", "and a shield", "and a bow"):
        k.line("Alex", w)
    await ticks()
    await k.end("give me a sword", RuntimeError("the box is down"))
    assert k.errors == ["give me a sword: the box is down"] and k.running() == ["and a shield"]
    k.tasks["and a shield"].cancel()
    await ticks()
    assert k.running() == ["and a bow"]
    await k.end("and a bow")
    assert not k.turns.busy("Alex") and k.turns.lanes == {}


async def test_a_run_ended_but_not_yet_handled_gives_its_turn_on_before_a_line_is_judged():
    """A run's done callback comes a moment after it ends; a line in between queues behind the
    waiting ones, never turned away for a place that is free, and the late callback moves nothing."""
    loop = asyncio.get_running_loop()
    ends: dict[str, asyncio.Future] = {}

    def spawn(coro, name, who):
        coro.close()  # never run: each turn here ends by hand
        ends[name] = loop.create_future()
        return ends[name]

    async def turned_away(player, words):
        raise AssertionError("nobody is turned away")

    async def run():
        pass

    turns = Turns(spawn=spawn, turned_away=turned_away)
    for w in (CASTLE, *SPEC):
        turns.line("Alex", w, run, w)
    ends[CASTLE].set_result(None)  # done; its callback not run yet
    assert turns.line("Alex", "and a horse", run, "and a horse") == "waiting"
    assert list(ends) == [CASTLE, "a meteor staff"]  # the next took the turn, once
    await ticks()
    assert list(ends) == [CASTLE, "a meteor staff"]
    assert turns.waiting("Alex") == ["and a shield", "and a bow", "and a horse"]


async def test_close_drops_what_waits_and_starts_nothing_after(kit):
    k = kit()
    for w in (CASTLE, "and a moat", "and a bridge"):
        k.line("Alex", w)
    k.line("Steve", "make it day")
    k.line("Steve", "and rain")
    await ticks()
    assert k.turns.close() == 3
    assert k.turns.waiting("Alex") == [] and k.turns.busy("Alex")  # a run going goes on: the stop's to end
    assert k.line("Alex", "and a bow") == "closed"
    for w in ("one", "two", "three", "four"):
        assert k.line("Steve", w) == "closed"  # nobody turned away either
    await k.end(CASTLE)
    k.tasks["make it day"].cancel()
    await ticks()
    assert k.made == [CASTLE, "make it day"]  # what was dropped was never made: nothing left unawaited
    assert k.running() == [] and k.told == [] and k.turns.lanes == {}
    assert not k.turns.busy("Alex") and not k.turns.busy("Steve")


def test_the_turned_away_message_cuts_each_line_short_and_stays_one_line_under_200():
    long = "can you make me a staff that shoots meteors whenever I right click with it"
    assert turned_away_line([long]) == ("Still on: “can you make me a staff that shoots…”. "
                                        "I can't take any more right now — wait a bit, then ask again.")
    msg = turned_away_line([long, "and a\n  shield.", "x" * 80])
    assert msg.startswith("Still on: “can you make me a staff that shoots…”, “and a shield”, “xxx")
    assert "\n" not in msg and len(msg) < 200
    for n in range(1, 13):  # however many wait (VFG_TURN_WAITING), one line under 200
        msg = turned_away_line([long] * n)
        assert len(msg) < 200 and msg.count("“") == min(n, 5), n
        assert all(len(w) <= WORDS_CAP for w in re.findall("“([^”]*)”", msg)), n
    assert turned_away_line([long] * 8).endswith("”, and 3 more. I can't take any more right now — wait a bit, "
                                                 "then ask again.")
    assert turned_away_line([]) == STILL_ON_NONE  # waiting_max 0: a busy player's every line turned away
