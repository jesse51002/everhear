"""The brain's loop: every row of the feed into the shared context, every live line through its
player's turn, and every event an agentic loop of steps (Prototype_Brain_Loop).

A row from `vfg watch` goes into the context; then, by what it is:
- a LINE Jev classed action_oriented: the brain's loop (below), in its player's turn;
- a LINE classed conversational: a Respond call (respond.py), in its player's turn, which replies,
  or hands the line up to the brain, whose loop then runs in the same turn, with what Respond read;
- a LINE classed context: kept, and marked processed at once (`vfg context mark`, nobody
  waiting); no model is shown it (context.py);
- anything else (game events, system notices, the brain's own AI rows): context only, for the
  next call to read.
A LINE already in the feed when the context was read (at or below its highest seq then, `start`)
and said more than HISTORY_S before that is history: context, marked processed, never acted on. A
LINE that arrives on the watch is live whatever its HH:MM:SS: a box clock a minute off the PC's
would otherwise make every line history. When the feed restarts (vfg watch says so, or gives a seq
at or below the one it follows from), the context is read again and followed from its new start.

One turn per player (turns.py): a player's lines run one at a time, whichever agent takes them,
while different players' turns run at once. At most VFG_TURN_WAITING lines wait; one more is
dropped, marked processed and never answered, and that player alone is told which lines still wait
(`vfg say --to`). An implementer's report waits for its task's player's turn too, at the front of
their queue. A turn ends when its run ends, and its run ends only once the AI rows it wrote (the
reply, the record, the task rows) came back on the watch, at most VFG_TURN_SETTLE_S, so the next
loop reads what this one did. A stop drops what waits.

A loop, per event: what no call has read yet is taken (`vfg context mark`, nobody waiting), the
actions reloaded, the world read once; then steps (call.py, act.py) until one ends it. A step that
lands and ends with a reply, silence or a hand-off is the last: code opens the hand-offs' tasks and
the follow-ups', says the reply, writes the loop's ONE record (`AI action -` for a line; for a
report, its `AI result` row and the task closed, or kept open while a follow-up runs), then starts
the implementers, and the turn ends with them started. A step that does not land, or ends `next`,
sends its results back: the next step's prompt holds every earlier step and what it came to. At
most VFG_LOOP_STEPS steps and VFG_LOOP_SECONDS: the step that starts at either limit is the last,
and must end with a reply or a hand-off. A step whose call fails ends the loop: the player gets
the error line, and code writes the record of what earlier steps did. Every step's usage row is
structured()'s, `#<seq> s<n>` (a report's: `<task> s<n>`); after each the brain logs what it spent
and sends the queue (`vfg usage flush`), nobody waiting, never two at once; and a log line closes
each loop: its steps, its cost, its cache rate, the time to its reply and how it ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from vfg_actions import menu_line
from vfg_llm import LlmError

from . import actions
from .act import LANDED, WROTE, Acts, StepRun, record_of, short, spent
from .call import NEXT, Call, Event, StepBase, describe, step_type
from .config import log
from .context import LIVE, Context, Row, parse
from .respond import Respond, promises
from .tasks import Tasks, board
from .turns import Turns, turned_away_line
from .vfg import Later
from .world import World

HISTORY_S = 120  # a line older than this when it arrives is history
MARK_CHUNK = 500  # seqs in one `vfg context mark`
ERRORED_MAX = 180  # the error line's reason, in characters
LANDED_MAX = 90   # what landed, in an error line
LINE_MAX = 200    # a chat line written here
MISMATCH = "failed to match"  # in structured()'s LlmError for an answer that failed its checks twice
STEPS, SECONDS = 10, 120.0  # a loop's limits (VFG_LOOP_STEPS, VFG_LOOP_SECONDS)
WAITING, SETTLE_S = 3, 3.0  # lines that wait for a turn, and a turn's wait for its rows (VFG_TURN_*)
TOLD = "the player was told"  # a note on an exception already said in chat, so it is never said twice


def errored(who: str, e: Exception, landed: list[str] = ()) -> str:
    """The chat line for a loop that failed for good: what its earlier steps did land (`landed`,
    their instructions), then that it errored out, and why, short. An answer that failed its
    checks twice says so plainly, never the check's words, which are for the model (the log keeps
    them); a timeout or a box out of reach keeps its short reason. One chat line, LINE_MAX at most."""
    done = short("; ".join(landed), LANDED_MAX) if landed else ""
    if MISMATCH in str(e):
        if done:
            return short(f"{who}: done: {done} — then I got tangled up on the rest, say it again?", LINE_MAX)
        return f"{who}: sorry, I got tangled up on that one — say it again?"
    lines = [x.strip() for x in str(e).splitlines() if x.strip()] or ["no reason given"]
    why = lines[-1]
    if not done:
        return f"{who}: that errored out — {short(why, ERRORED_MAX)}"
    return short(f"{who}: done: {done} — then that errored out: {why}", LINE_MAX)


WATCH_AGAIN_S = 2.0  # after `vfg watch` exits, or the feed restarted, before it is followed again
RESTARTED = "feed restarted"  # in vfg watch's notice of a new feed.log
RECONNECTED = "reconnected"  # vfg watch's notice once EC2 answers again after a drop


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Loop:
    """One event's loop as it runs: its steps, what they spent, and how it ended."""
    event: Event
    player: str                     # whose items the tail shows: the speaker, or a report's task's player
    line: str                       # the words that started it: a new task's `line`
    started: float                  # the brain's clock at its start
    runs: list[StepRun] = field(default_factory=list)
    calls: int = 0
    cost: float = 0.0
    cost_known: bool = True
    prompt_tokens: int = 0
    cached_tokens: int = 0
    replied: float | None = None    # seconds from its start to the reply said
    recorded: bool = False          # its record is written: a line's `AI action` row, a report's `AI result`
    how: str = "broke"              # how it ended: reply, silent, handoff, failed, cut off, or broke

    def spend(self, got) -> None:
        """A step's call, answered (an Answer) or failed (an LlmError)."""
        self.calls += 1
        self.cost += got.cost
        self.cost_known = self.cost_known and getattr(got, "cost_known", True)
        self.prompt_tokens += got.prompt_tokens
        self.cached_tokens += got.cached_tokens

    def landed(self) -> list[str]:
        """The instructions of every action of the loop that landed, in order."""
        return [r.instruction for run in self.runs for r in run.actions if r.state == LANDED]


class Brain:
    def __init__(self, *, ctx: Context, call: Call, respond: Respond, acts: Acts, world: World, tasks: Tasks,
                 implementers, later: Later, catalog, refs=None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic,
                 menu: Callable[[str], object] | None = None, start: int = 0,
                 load: Callable[[], Awaitable[tuple[Context, int, list[dict]]]] | None = None,
                 steps: int = STEPS, seconds: float = SECONDS, waiting: int = WAITING, settle_s: float = SETTLE_S):
        self.ctx = ctx
        self.start = start            # the feed's highest seq when the context was read: what is new is past it
        self.call = call
        self.respond = respond
        self.acts = acts
        self.world = world
        self.tasks = tasks
        self.implementers = implementers
        self.later = later
        self.catalog = catalog
        self.refs = refs              # the command references, once loaded (None until then)
        self.now = now
        self.clock = clock            # a loop's seconds, against its limit
        self.menu = menu              # sets Jev's menu again when the actions change
        self.load = load              # reads the context again, (ctx, start, task rows), after the feed restarted
        self.steps, self.seconds = steps, seconds
        self.settle_s = settle_s
        self.turns = Turns(waiting_max=waiting, spawn=self.spawn, turned_away=self.turned_away)
        self.inflight: set[asyncio.Task] = set()
        self.waiting: dict[int, Row] = {}  # the lines waiting for their player's turn, by seq
        self.queued: set[str] = set()      # the runs that waited for a turn, by their log label
        self.stopping = False
        self.restarted = False        # the watch said the feed restarted, or gave a seq it had passed
        self.catching_up = False      # after a reconnect: the backlog is judged by its age

    # ------------------------------------------------------------- rows in

    def spawn(self, coro, name: str, who: str = "") -> asyncio.Task:
        """An event as its own task, kept until it ends; what it raises is logged, never lost, and
        told in chat to `who`, the player whose line it was."""
        async def guarded() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one event's bug never stops the others
                log("error", f"{name} failed: {type(e).__name__}: {e} | {traceback.format_exc(limit=-4)}")
                if who and TOLD not in getattr(e, "__notes__", ()):
                    with contextlib.suppress(Exception):
                        await self.acts.say(errored(who, e))

        t = asyncio.create_task(guarded(), name=name)
        self.inflight.add(t)
        t.add_done_callback(self.inflight.discard)
        return t

    def feed(self, text: str) -> str | None:
        """One line from `vfg watch`: into the context, then to its player's turn; what the turn
        said of it ("started", "waiting", "turned away"), if it is a live line."""
        row = parse(text, now=self.now())
        if row is None:
            return None
        if row.seq == 0:  # vfg watch's own notices: EC2 unreachable, reconnected, lines skipped, feed restarted
            log("watch", row.text)
            self.restarted = self.restarted or RESTARTED in row.text
            # What the watch prints after a drop is a backlog: its lines are judged by their age,
            # as a start's are, until one of the last HISTORY_S arrives.
            self.catching_up = self.catching_up or row.text.startswith(RECONNECTED)
            return None
        if row.seq <= self.position():  # `vfg watch --after` gives none but after the feed restarted
            log("watch", f"#{row.seq} is not past #{self.position()}: the feed restarted")
            self.restarted = True
            return None
        if not self.ctx.add(row):
            return None
        if row.type == "AI":  # a row a turn wrote, come back: the turn may end (settle)
            self.acts.echoes.seen(row.kind, row.text)
        if not row.shown:  # a context line: no model reads it, so nothing waits on it
            row.processed = True
            self.later("context", "mark", str(row.seq))
            return None
        return self.route(row)

    def route(self, row: Row) -> str | None:
        if self.stopping or row.type != "LINE" or row.cls not in LIVE or row.processed:
            return None
        old = row.at < iso(self.now() - timedelta(seconds=HISTORY_S))
        if not old:
            self.catching_up = False
        if old and (row.seq <= self.start or self.catching_up):
            self.mark(row)
            log("line", f"#{row.seq} {row.who} from {row.hms} is history, not acted on: {row.text[:120]!r}")
            return None
        acting = row.cls == "action_oriented"
        name = f"{'line' if acting else 'respond'} #{row.seq}"
        label = f"#{row.seq} {row.who}"
        got = self.turns.line(row.who, row.text, functools.partial(
            self.turn, self.line if acting else self.conversational, row, label=label, queued=self.clock()), name)
        if got == "turned away":
            self.mark(row)
            log("turn", f"#{row.seq} {row.who} turned away, {len(self.turns.waiting(row.who))} waiting: "
                        f"{row.text[:120]!r}")
        elif got == "waiting":
            self.waiting[row.seq] = row
            self.queued.add(label)
            log("turn", f"#{row.seq} {row.who} waits for their turn: {row.text[:120]!r}")
        return got

    def mark(self, row: Row) -> None:
        """A line never answered (history, or turned away): marked read, nobody waiting."""
        row.processed = True
        self.later("context", "mark", str(row.seq))

    async def turned_away(self, player: str, words: list[str]) -> None:
        """The private message to a player whose line was turned away, naming the lines still waiting."""
        await self.acts.say(turned_away_line(words), to=player)

    def position(self) -> int:
        """Where the watch follows from: the context's last row, or the feed's highest seq when the
        context was read, whichever is later; never 0 for a feed with rows in it."""
        return max(self.ctx.last_seq, self.start)

    def catch_up(self) -> list[Row]:
        """The context as read at the start: what is unread and older than HISTORY_S is history,
        marked read, never acted on; a context line still unread is marked read, whatever its
        age; a line of the last HISTORY_S still unread is answered. Returns the history."""
        history = self.ctx.take_before(iso(self.now() - timedelta(seconds=HISTORY_S)))
        marks = sorted(history + self.ctx.take_quiet(), key=lambda r: r.seq)
        for i in range(0, len(marks), MARK_CHUNK):
            self.later("context", "mark", *(str(r.seq) for r in marks[i:i + MARK_CHUNK]), timeout=60)
        for row in self.ctx.unprocessed():
            if row.type == "LINE" and row.cls in LIVE:
                self.route(row)
        return history

    def take(self) -> list[Row]:
        """A read: what no call has read yet, marked processed."""
        new = self.ctx.take_unprocessed()
        if new:
            self.later("context", "mark", *(str(r.seq) for r in new))
        return new

    def usage(self, component: str, answer, ref: str) -> None:
        """A call's log line, from its Answer; its usage row structured() queued, sent here."""
        log(component, f"{ref} {spent(answer)}")
        self.flush()

    def flush(self) -> None:
        """The usage rows structured() queued, this call's among them, sent nobody waiting: never
        two `vfg usage flush` at once, one more after the one running when asked meanwhile
        (Later.flush_usage), so a burst of calls with the box down is one SSH attempt, then one."""
        self.later.flush_usage()

    def reload(self) -> list[str]:
        """Before each loop: every action file that changed is loaded again (a coding session's edit),
        and Jev's menu with it. The names that changed."""
        try:
            changed = actions.refresh(self.catalog)
        except Exception as e:  # noqa: BLE001 — the actions as they were are still good
            log("actions", f"reloading failed ({type(e).__name__}: {e}); the menu is as it was")
            return []
        if changed and self.menu is not None:
            self.menu(jev_menu(self.catalog))
        return changed

    # ------------------------------------------------------------- a turn

    async def turn(self, run: Callable[..., Awaitable[None]], *args, label: str = "",
                   queued: float | None = None) -> None:
        """One turn's run: `run(*args, started=…)`, the brain's clock as the turn began, which the
        loop's limits and its time to the reply count from; then the wait for the AI rows it wrote
        to come back on the watch, at most settle_s, so the player's next turn reads what this one
        did. The wait is skipped when the run raised or was cancelled. A run that waited for its
        turn logs how long (`turn #36 Steve waited 4.2 s for their turn`), from `queued`."""
        started = self.clock()
        if label in self.queued:
            self.queued.discard(label)
            log("turn", f"{label} waited {started - (queued or started):.1f} s for their turn")
        wrote: list[asyncio.Future] = []
        token = WROTE.set(wrote)
        try:
            await run(*args, started=started)
        except BaseException:
            self.acts.echoes.forget(wrote)
            raise
        finally:
            WROTE.reset(token)
        await self.settle(wrote)

    async def settle(self, wrote: list[asyncio.Future]) -> None:
        if not wrote or self.stopping:
            return
        try:
            if self.settle_s > 0:
                await asyncio.wait_for(asyncio.gather(*wrote), self.settle_s)
        except TimeoutError:
            log("turn", f"{sum(not f.done() for f in wrote)} of {len(wrote)} AI rows not back on the watch in "
                        f"{self.settle_s:g} s: the next turn goes on")
        finally:
            self.acts.echoes.forget(wrote)

    # ------------------------------------------------------------- events

    async def line(self, row: Row, new: list[Row] | None = None, handed_up: str = "",
                   started: float | None = None) -> None:
        """An action_oriented line, or one Respond handed up with what it read: the brain's loop,
        its clock from `started` (the turn's start; else now). The line itself is never among its
        NEW, though it may already be marked read: it waited for its turn while an earlier loop
        read it."""
        self.waiting.pop(row.seq, None)
        started = self.clock() if started is None else started
        if new is None:
            new = self.take()
        self.reload()
        seen = await self.world.seen(items=[row.who])
        event = Event("LINE", row, handed_up=handed_up)
        await self.run_loop(event, seen, [r for r in new if r.seq != row.seq], player=row.who, line=row.text,
                            started=started)

    async def conversational(self, row: Row, started: float | None = None) -> None:
        """A conversational line: Respond replies, or hands it up to the brain, whose loop runs in
        this same turn, its clock from the turn's start. The actions are reloaded first."""
        self.waiting.pop(row.seq, None)
        started = self.clock() if started is None else started
        new = self.take()
        self.reload()
        seen = await self.world.glance()
        try:
            answer = await self.respond.answer(row, self.ctx, seen, self.tasks.show(self.implementers.running()),
                                               [r for r in new if r.seq != row.seq])
        except LlmError as e:
            log("respond", f"#{row.seq} FAILED ({spent(e)}; {e}): handed up")
            self.flush()
            return await self.line(row, new, handed_up="Respond could not answer", started=started)
        self.usage("respond", answer, f"#{row.seq}")
        said = answer.value
        if said.hand_up:
            log("respond", f"#{row.seq} handed up: {said.reason}")
            return await self.line(row, new, handed_up=said.reason or "it may ask for something", started=started)
        log("respond", f"#{row.seq} {row.who}: {row.text[:80]!r} -> {said.reply!r} ({said.reason})")
        if said.reply.strip():
            await self.acts.say(said.reply)

    async def report(self, name: str, status: str, text: str, description: str = "", made: dict | None = None,
                     following: bool = False, started: float | None = None) -> None:
        """An implementer ended: its task takes what it made and its DESCRIPTION line (when it gave
        one; else the description it had stays); then a loop reads
        the report whole, writes the player's line and the record, and may act on it, reading what
        no call has read yet, as every loop does. When a follow-up to the task already runs
        (`following`), the task stays open with the result so far, and the loop is told, so its
        reply never says it is all done. The task never stays open for an error: the player then
        hears a line written here. Its loop's clock is from `started`, the turn's start (else now)."""
        started = self.clock() if started is None else started
        self.keep_session(name)
        self.tasks.merge_made(name, made)
        if description.strip():
            self.tasks.describe(name, description)
        loop: Loop | None = None
        closed = False  # the task closed here, on an error

        def recorded() -> bool:
            return closed or (loop is not None and loop.recorded)

        try:
            self.reload()
            task = self.tasks.get(name) or {"name": name, "player": None, "line": "", "brief": "", "description": ""}
            player = task.get("player") or ""
            new = self.take()
            seen = await self.world.seen(items=[player] if player else False)
            event = Event("REPORT", task=task, status=status, report=text, following=following)
            loop = Loop(event, player, task.get("line") or "", started)
            await self.run_loop(event, seen, new, player=player, line=loop.line, loop=loop)
        except Exception:  # raised on, for the event's own log; the task never stays open for it
            if not recorded():
                kept = self.result(name, status, self.written(name, status, loop), following)
                closed = True
                await self.acts.say(self.line_for(name, status, kept), name)
            raise
        finally:
            if not recorded():  # cancelled: the brain stopped while it ran
                self.result(name, status, self.written(name, status), following)

    # ------------------------------------------------------------- the loop

    async def run_loop(self, event: Event, seen, new: list[Row], *, player: str, line: str,
                       loop: Loop | None = None, started: float | None = None) -> Loop:
        """Steps until one ends the loop, at most `steps` of them and `seconds` from `started` (the
        turn's start; else now); see the module's docstring. The world (`seen`), TASKS and the
        conversation are the prompt's as they are now, for every step. When something raises that
        nobody expected, a line's loop still leaves the record of what its steps did, then raises
        on (a report's: report() closes its task with it)."""
        loop = loop or Loop(event, player, line, self.clock() if started is None else started)
        report = event.kind == "REPORT"
        tasks = board(self.tasks.all(), seen.instances, self.implementers.running(),
                      today=self.now().astimezone(UTC).strftime("%Y-%m-%d"))
        prompt = self.call.prompt(event, self.ctx, seen, tasks, new, self.refs)
        ref = event.ref()
        # a reply that promises with nothing started is refused, but for a status: of the speaker's open task, or
        # of the follow-up a report's loop is told already runs
        promise = None if event.following else functools.partial(
            promises, tasks=self.tasks.show(self.implementers.running()), who=player, offers=False)
        try:
            n = 0
            while True:
                n += 1
                used = self.clock() - loop.started
                last = n >= self.steps or used >= self.seconds
                output = step_type(self.catalog, last=last, report=report, tasks=self.tasks.names(),
                                   promise=promise)
                trailer = self.call.trailer(n, self.steps, used, self.seconds, last)
                try:
                    answer = await self.call.step(prompt, trailer, output, f"{ref} s{n}")
                except LlmError as e:
                    loop.spend(e)
                    log("brain", f"{ref} s{n} FAILED ({spent(e)}): {e}")
                    self.flush()
                    await self.failed(loop, e)
                    return loop
                loop.spend(answer)
                self.flush()
                step = answer.value
                run = await self.acts.run(step, n=n, player=player, seen=seen, refs=self.refs, ref=f"{ref} s{n}",
                                          earlier=loop.runs)
                loop.runs.append(run)
                log("brain", f"{ref} s{n} {spent(answer)} | {describe(step)} -> {run.summary()}")
                if run.landed and step.end != NEXT:
                    await self.end(loop, step)
                    return loop
                if last:  # the last step's ending did not land: nothing comes after it
                    await self.failed(loop, None)
                    return loop
                prompt.steps.append(run.show())
        except asyncio.CancelledError:
            loop.how = "cut off"
            raise
        except Exception as e:
            if event.kind == "LINE":
                if loop.replied is None:  # the error line, after what landed, as failed() says it: once
                    with contextlib.suppress(Exception):
                        await self.acts.say(errored(event.row.who, e, loop.landed()))
                e.add_note(TOLD)          # said here, or the reply went out already: the guard says nothing
                if not loop.recorded and (done := record_of(loop.runs)):
                    self.acts.post("action", "", done)
                    loop.recorded = True
            raise
        finally:
            self.log_loop(loop)

    async def end(self, loop: Loop, step: StepBase) -> None:
        """The loop's ending, once its last step landed: each hand-off's task opened and each
        follow-up's opened again, with their `AI task` rows; the reply (a report's: written here
        when the step gave none); the record; then each implementer started and each follow-up
        sent, the turn waiting for none of them to finish. A task cut off before its implementer
        had it (the brain stopped) fails by name."""
        event, report = loop.event, loop.event.kind == "REPORT"
        name = str((event.task or {}).get("name") or "") if report else ""
        starts: list[tuple[str, str]] = []
        sends: list[tuple[str, str]] = []
        unopened: list[str] = []  # the player's words of each hand-off whose task would not open
        cut: dict[str, str] = {}
        try:
            for h in step.hand_offs:
                opened = self.acts.open_task(h, player=loop.player, line=loop.line)
                if opened is not None:
                    starts.append((opened, h.brief()))
                    cut[opened] = f"{opened} failed: cut off, the brain stopped before its implementer started"
                else:
                    unopened.append(h.words.strip().strip("“”\"'") or h.name)
            for f in step.follow_ups:
                text = self.acts.reopen_task(f)
                if text is not None:
                    sends.append((f.task.strip(), text))
                    cut[f.task.strip()] = (f"{f.task.strip()} failed: cut off, the brain stopped before its "
                                           "implementer got the follow-up")
            task = name or (starts[0][0] if starts else sends[0][0] if sends else "")
            record = step.record.strip() or record_of(loop.runs)
            reply = step.reply.strip()
            if unopened:  # the reply promises what never started: a line written here says so instead
                said = ", ".join(f"“{short(w, 60)}”" for w in unopened)
                done = short("; ".join(loop.landed()), LANDED_MAX)
                head = (f"done: {done} — " if done else "") + ("the rest is on its way, but " if starts or sends
                                                               else "but " if done else "sorry, ")
                reply = short(f"{head}I couldn't start {said} — say it again?", LINE_MAX)
                record = short(f"{record + '; ' if record else ''}could not start {said}", 400)
                log("task", f"{event.ref()} {len(unopened)} hand-off(s) did not open: said {reply!r} instead of "
                            f"{step.reply.strip()!r}")
            if report:
                final = step.status or event.status
                following = event.following or name in {n for n, _ in sends}
                record = record or self.written(name, final)
                kept = self.result(name, final, record, following)
                loop.recorded = True
                reply = reply or self.line_for(name, final, kept)
            if reply:
                await self.acts.say(reply, task)
                loop.replied = self.clock() - loop.started
            if not report and record:
                self.acts.post("action", "", record)
                loop.recorded = True
            await asyncio.sleep(0)  # the rows' writes go out first; nothing waits for them
            for opened, brief in starts:
                await self.acts.start(opened, brief, loop.player)
                cut.pop(opened, None)
            for followed, text in sends:
                await self.acts.send(followed, text)
                cut.pop(followed, None)
            loop.how = step.end
        finally:
            for left, why in cut.items():
                self.acts.fail(left, why)

    async def failed(self, loop: Loop, e: Exception | None) -> None:
        """A loop with no ending: its step's call failed for good (`e`), or its last step's ending
        did not land. A line's player hears the error line, and the record of what its steps did
        goes in; a report's task is closed on the implementer's status with a record written here,
        and its player hears a line written here."""
        loop.how = "failed"
        event = loop.event
        why = e if e is not None else Exception("it reached its limit with nothing it could end on")
        if event.kind == "LINE":
            await self.acts.say(errored(event.row.who, why, loop.landed()))
            loop.replied = self.clock() - loop.started
            if done := record_of(loop.runs):
                self.acts.post("action", "", done)
                loop.recorded = True
            return
        name = str((event.task or {}).get("name") or "")
        kept = self.result(name, event.status, self.written(name, event.status, loop), event.following)
        loop.recorded = True
        await self.acts.say(self.line_for(name, event.status, kept), name)
        loop.replied = self.clock() - loop.started

    def log_loop(self, loop: Loop) -> None:
        """The loop's line: its steps, what it cost, how much of it the cache read, the time to its
        reply and how it ended."""
        rate = f"{loop.cached_tokens / loop.prompt_tokens:.0%}" if loop.prompt_tokens else "no"
        reply = f"reply at {loop.replied:.1f} s" if loop.replied is not None else "no reply"
        who = loop.player or "nobody"
        log("loop", f"{loop.event.ref()} {who}: {loop.calls} step(s), ${loop.cost:.6f}"
                    f"{'' if loop.cost_known else '+'}, {rate} cached, {reply}, "
                    f"{self.clock() - loop.started:.1f} s, ended {loop.how}")

    # ------------------------------------------------------------- reports

    def keep_session(self, name: str) -> None:
        """The task's implementer session into its row, in case on_session missed it."""
        session = getattr(self.implementers, "session", None)
        if callable(session):
            self.tasks.set_session(name, session(name))

    def result(self, name: str, status: str, record: str, following: bool) -> bool:
        """A report's record, an `AI result` row: the task closed with it, or kept open with it as
        the result so far while a follow-up to it runs (one sent while the loop ran included).
        True when kept open."""
        kept = following or name in self.implementers.running()
        if kept:
            self.tasks.note(name, record)
            log("task", f"{name} stays open: a follow-up to it runs")
        else:
            self.tasks.close(name, status, record)
        self.acts.post("result", name, record)
        return kept

    async def on_report(self, name: str, status: str, text: str, description: str = "",
                        made: dict | None = None) -> None:
        """What the implementers call when one ends (their `on_report`), with its report, its
        DESCRIPTION line taken off it, and what it made: a loop of its own in the task's player's
        turn, at the front of their queue, and the implementer waits for none of it. A task whose
        implementers still name it as running has a follow-up queued or started behind this run.
        While the brain stops, or when the stop drops the report before it ran, the task closes on
        the implementer's status, in code."""
        following = name in self.implementers.running()
        closing = functools.partial(self.closed_in_code, name, status, text, description, made, following)
        if self.stopping:
            return closing()
        player = str((self.tasks.get(name) or {}).get("player") or "")
        label = f"report {name}"
        got = self.turns.report(player, functools.partial(self.turn, self.report, name, status, text, description,
                                                          made, following, label=label, queued=self.clock()),
                                label, dropped=closing)
        if got == "closed":
            closing()
        elif got == "first in line":
            self.queued.add(label)
            log("turn", f"report {name} waits for {player or 'its lane'}'s turn, first in line")

    def closed_in_code(self, name: str, status: str, text: str, description: str, made: dict | None,
                       following: bool) -> None:
        """A report no loop reads (the brain stops): its task takes what the implementer made and
        closes on its status, with a record written in code and no model call."""
        log("task", f"{name} reported {status} while the brain stops: {short(text, 200)}")
        self.keep_session(name)
        self.tasks.merge_made(name, made)
        if description.strip():
            self.tasks.describe(name, description)
        self.result(name, status, self.written(name, status), following)

    def written(self, name: str, status: str, loop: Loop | None = None) -> str:
        """A task's record written in code, never the implementer's words: `<name> done: <what it
        is>`, its description (a brief is in parts under headings now, and long); then what the
        report's loop ran, when it ran something before it ended without its own record."""
        what = short((self.tasks.get(name) or {}).get("description") or "", 120)
        done = record_of(loop.runs) if loop is not None else ""
        return short(f"{name} {status}" + (f": {what}" if what else "") + (f"; then {done}" if done else ""), 400)

    def line_for(self, name: str, status: str, following: bool = False) -> str:
        """A report's chat line written in code, when the loop gave none: the player's own words,
        never the brief nor the description, which players are not shown."""
        t = self.tasks.get(name) or {}
        what = short(t.get("line") or "", 80) or "that one"
        if following:
            return f"still on it: {what}"
        return f"done: {what}" if status == "done" else f"sorry, that one failed: {what}"

    # ------------------------------------------------------------- running and stopping

    async def follow(self, watch: Callable[[int], AsyncIterator[str]]) -> None:
        """Feed every line of `vfg watch --after <position>`; follow again whenever it exits, and
        when the feed restarted, from the context read again."""
        while not self.stopping:
            self.restarted = False
            async with contextlib.aclosing(watch(self.position())) as lines:
                async for text in lines:
                    if self.stopping:
                        return
                    try:
                        self.feed(text)
                    except Exception as e:  # noqa: BLE001 — a line it cannot read never stops the watch
                        log("error", f"feeding {text.strip()[:200]!r}: {type(e).__name__}: {e}")
                    if self.restarted:
                        break
            if self.stopping:
                return
            if self.restarted:
                log("watch", f"the feed restarted: reading the context again in {WATCH_AGAIN_S:g} s")
                await asyncio.sleep(WATCH_AGAIN_S)
                await self.reread()
                continue
            log("watch", f"vfg watch ended; following again in {WATCH_AGAIN_S:g} s from #{self.position()}")
            await asyncio.sleep(WATCH_AGAIN_S)

    async def reread(self) -> None:
        """After the feed restarted (a new feed.log, its seqs from 1 again): the context read
        again, with the start seq it gives, and caught up as at a start, so the new feed's rows
        are heard and none of it older than two minutes is acted on; and the tasks read with it
        merged into the memory's (Tasks.merge)."""
        if self.load is None:
            log("watch", "nothing to read the context again with: following the new feed from its start")
            self.ctx, self.start = Context(), 0
            return
        ctx, start, rows = await self.load()
        self.ctx, self.start = ctx, max(start, ctx.last_seq)
        self.tasks.merge(rows)
        history = self.catch_up()
        log("watch", f"the context read again: {len(ctx.rows)} rows, following from #{self.position()} "
                     f"({len(history)} history marked read)")

    async def stop(self, grace_s: float, implementers_grace_s: float = 2.0) -> None:
        """Read nothing new and drop what waits for a turn (a report dropped closes its task in
        code); let the loops in flight finish for up to `grace_s` (a report that comes in meanwhile
        closes its task in code, on_report()); stop the implementers and fail their tasks as
        stopped."""
        self.stopping = True
        dropped = self.turns.close()  # first: a run that ends in the grace starts nothing after it
        if dropped:
            log("stop", f"dropped {dropped} line(s) and report(s) waiting for a turn")
        for row in self.waiting.values():  # one rule: a line dropped here is read, never answered, after a restart too
            if not row.processed:
                self.mark(row)
            log("stop", f"#{row.seq} {row.who} dropped at the stop, marked read: {row.text[:120]!r}")
        self.waiting.clear()
        self.acts.echoes.release()  # the watch is done: no turn waits for its rows to come back
        pending = list(self.inflight)
        if pending:
            log("stop", f"waiting up to {grace_s:g} s for {len(pending)} call(s) in flight")
            _, left = await asyncio.wait(pending, timeout=grace_s)
            for t in left:
                t.cancel()
            await asyncio.gather(*left, return_exceptions=True)
        try:
            stopped = await self.implementers.close(implementers_grace_s)
        except Exception as e:  # noqa: BLE001 — the stop goes on; the next start fails what stayed open
            log("error", f"stopping the implementers failed: {type(e).__name__}: {e} | "
                         f"{traceback.format_exc(limit=-4)}")
            stopped = []
        for name in stopped:
            try:
                self.keep_session(name)  # a follow-up after the restart resumes it
                record = f"{name} failed: stopped, the brain stopped while it ran"
                self.tasks.close(name, "failed", record)
                self.acts.post("result", name, record)
            except Exception as e:  # noqa: BLE001 — one task's trouble never keeps the rest open
                log("error", f"closing {name} at the stop failed: {type(e).__name__}: {e}")
        if stopped:
            log("stop", f"stopped the implementers of {', '.join(stopped)}")


def jev_menu(catalog) -> str:
    """Jev's menu (`vfg jev menu set`): one `name — what it does` line per action."""
    return "\n".join(menu_line(n, catalog.get(n)) for n in catalog.names())
