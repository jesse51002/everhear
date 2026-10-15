"""The brain's loop: every row of the feed into the shared context, every line to the AI through its
player's turn to the conversation agent and the router, and the work they place: brain loops that
run side by side, and implementers (Prototype_Brain_Loop, then Prototype_Conversation_And_Router).

A row from `vfg watch` goes into the context; then, by what it is:
- a LINE said to the AI (Jev's live classes, context.LIVE): the front turn (below), in its
  player's turn. Jev's class no longer picks the agent: every line to the AI takes the same path;
- a LINE Jev kept as context: kept, and marked processed at once (`vfg context mark`, nobody
  waiting); no model is shown it (context.py);
- anything else (game events, system notices, the brain's own AI rows): context only, for the
  next call to read.
A LINE already in the feed when the context was read (at or below its highest seq then, `start`)
and said more than HISTORY_S before that is history: context, marked processed, never acted on. A
LINE that arrives on the watch is live whatever its HH:MM:SS: a box clock a minute off the PC's
would otherwise make every line history. When the feed restarts (vfg watch says so, or gives a seq
at or below the one it follows from), the context is read again and followed from its new start.

The front turn, one per player at a time (turns.py; at most VFG_TURN_WAITING lines wait, one more
is turned away with a private message naming them): what no call has read yet is taken (`vfg
context mark`, nobody waiting), the actions reloaded, the world glanced at; then two quick calls.
The conversation agent (respond.py) answers at once: a reply, silence, or a reply and a hand-off to
be looked up or done; it fails into a hand-off (to be done). Its line goes out the moment it
answers, every time, hand-off or not: there is no hold. A hand-off goes to the router (router.py),
told which of the two it is, which picks who does the work, and code places it: a new brain loop;
a loop already on that thing (loops.py: one thinking reads the line before its next step, an ended
one is taken up again); a new implementer, started on the router's brief; an implementer already
on it, followed up (a message, its session resumed, or a cancel); or nowhere, with a line of the
router's own. The router failing starts a new loop. The router's own line (nowhere's reply, or
code's apology when a task would not open) goes out after the agent's, when there is one. The turn
ends once the line is placed, then waits, at most VFG_TURN_SETTLE_S, for the AI rows it wrote to
come back on the watch, so the player's next line is routed knowing where this one went. It never
waits for the work.

Brain loops hold no turn: each is an asyncio task of its own, so they run side by side, a player's
too. A loop reads the world and TASKS once at its start; then steps (call.py, act.py) until one
ends it. A step that lands and ends with a reply, silence or a hand-off is the last: code opens
the hand-offs' tasks and the follow-ups', says the reply, writes the loop's ONE record (`AI action
-`), then starts the implementers. A step that does not land, or ends `next`, sends its results
back: the next step's prompt holds every earlier step and what it came to. A line the router adds
to a loop while it runs is read before its next step, with the rows the loop has not read; when the
step that ran meanwhile was its ending, the loop takes one more step for the line, the ended step
shown as SENT. Then the loop ends and is saved, its prompt kept: a line added within
VFG_LOOP_TAKE_UP_S of its creation takes it up again, from where it stopped, read from the cache.
A loop's budget is VFG_LOOP_STEPS for its first line and as many more for each line added, never
past VFG_LOOP_CAP; the step at the limit is the last and must end with a reply or a hand-off, and a
loop that reaches the cap is deleted. There is no time limit: the steps and the step timeout bound
a loop. A step whose call fails ends the run: the player gets the error line, and code writes the
record of what earlier steps did. Every step's usage row is structured()'s, `#<seq> s<n>` (a
report's loop: `<task> s<n>`); after each the brain logs what it spent and sends the queue (`vfg
usage flush`), nobody waiting, never two at once; and a log line closes each run of a loop: its
steps, its cost, its cache rate, the time to its reply, how it ended, its id, lines and state.

An implementer's report never queues: it goes to the conversation agent at once, beside whatever
the player's turn is doing. The task first takes what the implementer made, its DESCRIPTION line
and its action's variables; the agent tells the player, writes the record and the status (the task
closed, or kept open while a follow-up runs or a hand-off is on its way) and hands what is left, if
anything, to the router: back to an implementer, to a loop that does it with actions, or nowhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

from vfg_actions import menu_line
from vfg_llm import LlmError

from . import actions
from .act import LANDED, LOOP_SO_FAR, WROTE, Acts, StepRun, record_of, short, spent
from .actions import variables_of
from .call import NEXT, Call, Event, FollowUp, StepBase, added, describe, step_type
from .config import log
from .context import LIVE, Context, Row, parse
from .loops import CAP, DELETED, ENDED, GRANT, TAKE_UP_S, Loop, Loops
from .respond import ReportEvent, Respond, promises
from .router import Router, RouterEvent
from .running import full as running_in_full
from .tasks import Tasks, board
from .turns import Turns, turned_away_line
from .vfg import Later
from .world import Seen, World

HISTORY_S = 120  # a line older than this when it arrives is history
MARK_CHUNK = 500  # seqs in one `vfg context mark`
ERRORED_MAX = 180  # the error line's reason, in characters
LANDED_MAX = 90   # what landed, in an error line
LINE_MAX = 200    # a chat line written here
MISMATCH = "failed to match"  # in structured()'s LlmError for an answer that failed its checks twice
WAITING, SETTLE_S = 3, 3.0  # lines that wait for a turn, and a turn's wait for its rows (VFG_TURN_*)
TOLD = "the player was told"  # a note on an exception already said in chat, so it is never said twice
# Where the router sends the work (router.Route.to).
NEW_LOOP, LOOP, NEW_IMPLEMENTER, IMPLEMENTER, NOWHERE = "new_loop", "loop", "new_implementer", "implementer", "nowhere"


def errored(who: str, e: Exception, landed: list[str] = ()) -> str:
    """The chat line for a loop that failed for good: what its earlier steps did land (`landed`,
    their instructions), then that it errored out, and why, short. An answer that failed its
    checks twice says so plainly, never the check's words, which are for the model (the log keeps
    them); a timeout or a box out of reach keeps its short reason. One chat line, LINE_MAX at most,
    led by the player's name when there is one (a report's task may have none)."""
    to = f"{who}: " if who else ""
    done = short("; ".join(landed), LANDED_MAX) if landed else ""
    if MISMATCH in str(e):
        if done:
            return short(f"{to}done: {done} — then I got tangled up on the rest, say it again?", LINE_MAX)
        return f"{to}sorry, I got tangled up on that one — say it again?"
    lines = [x.strip() for x in str(e).splitlines() if x.strip()] or ["no reason given"]
    why = lines[-1]
    if not done:
        return f"{to}that errored out — {short(why, ERRORED_MAX)}"
    return short(f"{to}done: {done} — then that errored out: {why}", LINE_MAX)


def failed_step(n: int, why: Exception, said: str) -> str:
    """A step whose call failed for good, as THE LOOP SO FAR shows it to a step after it (a line
    was added to the loop meanwhile): it ran nothing, and the player got the error line."""
    reason = ([x.strip() for x in str(why).splitlines() if x.strip()] or ["no reason given"])[-1]
    return (f"{LOOP_SO_FAR if n == 1 else ''}STEP {n}:\n- your answer to it failed ({short(reason, ERRORED_MAX)}), so "
            f"nothing of it ran\nSENT INSTEAD: “{said}”\n\n")


WATCH_AGAIN_S = 2.0  # after `vfg watch` exits, or the feed restarted, before it is followed again
RESTARTED = "feed restarted"  # in vfg watch's notice of a new feed.log
RECONNECTED = "reconnected"  # vfg watch's notice once EC2 answers again after a drop


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Brain:
    def __init__(self, *, ctx: Context, call: Call, respond: Respond, router: Router, acts: Acts, world: World,
                 tasks: Tasks, implementers, later: Later, catalog, refs=None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic,
                 menu: Callable[[str], object] | None = None, start: int = 0,
                 load: Callable[[], Awaitable[tuple[Context, int, list[dict]]]] | None = None,
                 steps: int = GRANT, cap: int = CAP, take_up_s: float = TAKE_UP_S,
                 waiting: int = WAITING, settle_s: float = SETTLE_S):
        self.ctx = ctx
        self.start = start            # the feed's highest seq when the context was read: what is new is past it
        self.call = call
        self.respond = respond        # the conversation agent
        self.router = router
        self.acts = acts
        self.world = world
        self.tasks = tasks
        self.implementers = implementers
        self.later = later
        self.catalog = catalog
        self.refs = refs              # the command references, once loaded (None until then)
        self.now = now
        self.clock = clock            # a loop's seconds, and its take-up window
        self.menu = menu              # sets Jev's menu again when the actions change
        self.load = load              # reads the context again, (ctx, start, task rows), after the feed restarted
        self.settle_s = settle_s
        self.loops = Loops(steps, cap, take_up_s, clock=clock, now=now)
        self.turns = Turns(waiting_max=waiting, spawn=self.spawn, turned_away=self.turned_away)
        self.inflight: set[asyncio.Task] = set()
        self.waiting: dict[int, Row] = {}  # the lines waiting for their player's turn, by seq
        self.queued: set[str] = set()      # the turns that waited, by their log label
        self.stopping = False
        self.restarted = False        # the watch said the feed restarted, or gave a seq it had passed
        self.catching_up = False      # after a reconnect: the backlog is judged by its age

    # ------------------------------------------------------------- rows in

    def spawn(self, coro, name: str, who: str = "") -> asyncio.Task:
        """A turn, a loop or a report as its own task, kept until it ends; what it raises is
        logged, never lost, and told in chat to `who`, the player it is for."""
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
        """A line to the AI, whatever Jev's live class: its player's front turn, now or when the
        lines before it have had theirs."""
        if self.stopping or row.type != "LINE" or row.cls not in LIVE or row.processed:
            return None
        old = row.at < iso(self.now() - timedelta(seconds=HISTORY_S))
        if not old:
            self.catching_up = False
        if old and (row.seq <= self.start or self.catching_up):
            self.mark(row)
            log("line", f"#{row.seq} {row.who} from {row.hms} is history, not acted on: {row.text[:120]!r}")
            return None
        label = f"#{row.seq} {row.who}"
        got = self.turns.line(row.who, row.text, functools.partial(
            self.turn, self.front, row, label=label, queued=self.clock()), f"front #{row.seq}")
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
        """Before each front turn and report: every action file that changed is loaded again, so
        an action an implementer saved a moment ago is on the menu, and Jev's menu with it, and
        every task whose action changed has its variables read again. The names that changed."""
        try:
            changed = actions.refresh(self.catalog)
        except Exception as e:  # noqa: BLE001 — the actions as they were are still good
            log("actions", f"reloading failed ({type(e).__name__}: {e}); the menu is as it was")
            return []
        if changed and self.menu is not None:
            self.menu(jev_menu(self.catalog))
        for t in self.tasks.all() if changed else []:
            if self.action_of(t) in changed:
                self.refresh_variables(t["name"])
        return changed

    def action_of(self, task: dict) -> str | None:
        """The action a task made: the one named after it, else the last it made that is loaded."""
        if self.catalog.get(task["name"]) is not None:
            return task["name"]
        made = (task.get("made") or {}).get("actions") or []
        return next((a for a in reversed(made) if self.catalog.get(a) is not None), None)

    def refresh_variables(self, name: str) -> None:
        """The task's `variables`, read again from its action (none loaded: left as they are)."""
        task = self.tasks.get(name)
        action = self.action_of(task) if task is not None else None
        if action is None:
            return
        try:
            self.tasks.set_variables(name, variables_of(self.catalog, action))
        except Exception as e:  # noqa: BLE001 — the variables as they were are still good
            log("tasks", f"{name}: reading {action}'s variables failed ({type(e).__name__}: {e})")

    def board(self, seen: Seen) -> str:
        """TASKS in full (tasks.board): the brain's and the router's."""
        return board(self.tasks.all(), seen.copies, self.implementers.running(),
                     today=self.now().astimezone(UTC).strftime("%Y-%m-%d"))

    def in_full(self, seen: Seen) -> Seen:
        """A glance with RUNNING NOW in full, every value of every copy, for the router: "make it
        bigger" may be one value of a running copy, a brain's edit rather than an implementer's
        build. No I/O: the copies are the glance's."""
        return dataclasses.replace(seen, running=running_in_full(seen.copies, self.catalog))

    def busy(self) -> set[str]:
        """The players with work under way: a loop thinking, or an implementer running on one of
        their tasks. The conversation agent's "coming up" is a status for them, not a promise."""
        players = {str((self.tasks.get(n) or {}).get("player") or "") for n in self.implementers.running()}
        return (self.loops.busy() | players) - {""}

    def under_way(self, player: str) -> bool:
        """Whether the player has work under way: a loop thinking, an implementer running, or a
        task of theirs open. The agent's "still on it" is then a status, and the router may send a
        line that only presses for it nowhere behind that line."""
        return player in self.busy() or any(t.get("player") == player and t.get("status") == "open"
                                            for t in self.tasks.all())

    # ------------------------------------------------------------- a turn

    async def turn(self, run: Callable[..., Awaitable[None]], *args, label: str = "",
                   queued: float | None = None) -> None:
        """One turn's run, `run(*args)` (the front turn); then the wait for the AI rows it wrote to
        come back on the watch, at most settle_s, so the player's
        next turn reads what this one did. The wait is skipped when the run raised or was
        cancelled. A run that waited for its turn logs how long (`turn #36 Steve waited 4.2 s for
        their turn`), from `queued`."""
        started = self.clock()
        if label in self.queued:
            self.queued.discard(label)
            log("turn", f"{label} waited {started - (queued or started):.1f} s for their turn")
        wrote: list[asyncio.Future] = []
        token = WROTE.set(wrote)
        try:
            await run(*args)
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

    # ------------------------------------------------------------- the front turn

    async def front(self, row: Row) -> None:
        """A line to the AI, in its player's turn: the conversation agent answers at once, and its
        line goes out the moment it answers, every time, hand-off or not. A hand-off goes to the
        router, told the agent's choice (`look` or `do`), which picks who does the work; code
        places it (place()). The router's own line (nowhere's reply, or code's apology when a task
        would not open) goes out after the agent's, when it has one. The module's docstring says
        the rest. The line itself is never among the NEW rows either call reads, though it may
        already be marked read: it waited for its turn while an earlier call read it."""
        self.waiting.pop(row.seq, None)
        new = [r for r in self.take() if r.seq != row.seq]
        self.reload()
        seen = await self.world.glance()
        running = self.implementers.running()
        reply, hand, reason, choice = "", True, "", "do"
        try:
            answer = await self.respond.answer(row, self.ctx, seen, self.tasks.show(running),
                                               self.loops.show(brief=True), new, busy=self.busy())
        except LlmError as e:  # unsure, failing: the router places it all the same, and the work answers
            log("respond", f"#{row.seq} FAILED ({spent(e)}; {e}): handed off")
            self.flush()
            agent = f"failed {e.ms} ms"
        else:
            self.usage("respond", answer, f"#{row.seq}")
            said = answer.value
            reply, hand, reason, choice = said.reply.strip(), said.hand_off, said.reason, said.choice
            agent = f"{'handoff' if hand else 'reply' if reply else 'silent'} {answer.ms} ms"
            log("respond", f"#{row.seq} {row.who}: {row.text[:80]!r} -> {reply!r}"
                           f"{', handed off' if hand else ''} ({reason})")
        if not hand:
            if reply:
                await self.acts.say(reply)
            log("front", f"#{row.seq} {row.who}: agent {agent}; said {'agent' if reply else 'nothing'}")
            return
        said_lines = []
        if reply:  # no hold: the agent's line goes out the moment it answers, every time
            await self.acts.say(reply)
            said_lines.append("agent")
        route, offered = None, [x.id for x in self.loops.offered()]
        tasks_text = self.board(seen)
        try:
            got = await self.router.route(RouterEvent("LINE", row, said=reply, reason=reason, choice=choice),
                                          self.ctx, self.in_full(seen), tasks_text,
                                          self.loops.show(brief=False), loops=offered, tasks=self.tasks.names(),
                                          refs=self.refs, busy=self.under_way(row.who))
        except LlmError as e:  # the line still needs doing: a loop of its own
            log("router", f"#{row.seq} FAILED ({spent(e)}; {e}): a new loop")
            self.flush()
            router = f"FAILED {e.ms} ms, so"
        else:
            self.usage("router", got, f"#{row.seq}")
            route = got.value
            router = f"{got.ms} ms:"
        to, where, instead = await self.place(row, route, said=reply, new=new)
        if route is not None and route.to != to:  # placed elsewhere than the router said: both in the log line
            router += f" {route.to} {route.target() or '-'}, so"
        task = where.split(", ")[0] if to in (NEW_IMPLEMENTER, IMPLEMENTER) else ""  # the line's row names one
        own = route.reply.strip() if route is not None and to == NOWHERE else ""
        # The router's own line, to nowhere: said only when the agent said nothing, or when the agent's line
        # promised work the router is not placing (a status for a player with work under way is not a promise:
        # promises(), busy as the agent's own check reads it). Otherwise it only repeats the agent's, dropped.
        if own and reply and promises(reply, self.tasks.show(self.implementers.running()), row.who,
                                      busy=self.busy()) != "a promise":
            log("router", f"#{row.seq} nowhere: its line dropped, the agent's already said it: {reply!r}")
            own = ""
        if instead:          # a task that would not open: never promised
            line, which = instead, "code"
        else:                # the agent's line already went out; the router's own, if it has one, after it
            line, which = own, "router"
        if line:
            await self.acts.say(line, task)
            said_lines.append(which)
        log("front", f"#{row.seq} {row.who}: agent {agent}; router {router} {to} {where or '-'}; "
                     f"said {', '.join(said_lines) or 'nothing'}")

    async def place(self, row: Row, route, *, said: str, new: list[Row]) -> tuple[str, str, str]:
        """The router's choice carried out, in code, no model: (where the work went, its loop's id
        or its task's name, every task's for a line that followed several up, joined by ", ", and a
        line code wrote in place of the agent's when a task would not open). A loop that can no
        longer take a line, or follow-ups whose tasks are all gone, become a new loop, logged; so
        does no choice at all (the router failed). A follow-up whose task is gone beside one that
        is not is dropped, logged: the others carry the line."""
        to = route.to if route is not None else NEW_LOOP
        reason = route.reason.strip() if route is not None else ""
        if to == LOOP:
            loop = self.loops.get(route.loop)
            got = self.loops.add(loop, row, said=said, reason=reason) if loop is not None else "gone"
            if got != "gone":
                log("router", f"#{row.seq} -> {loop.id}, {got}: {reason}")
                if got == "taken up":
                    self.spawn(self.resume(loop), f"loop {loop.id}", loop.player)
                return LOOP, loop.id, ""
            log("router", f"#{row.seq} -> {route.loop}: it can no longer take a line, so a new loop ({reason})")
            to, reason = NEW_LOOP, ""
        if to == NEW_IMPLEMENTER:
            h = route.hand_offs[0]
            opened = self.acts.open_task(h, player=row.who, line=row.text)
            if opened is None:  # never promised: a line written here says so in place of the agent's
                words = h.words.strip().strip("“”\"'") or h.name
                log("task", f"#{row.seq} the router's hand-off {h.name} did not open")
                return NEW_IMPLEMENTER, "", f"sorry, I couldn't start “{short(words, 60)}” — say it again?"
            log("router", f"#{row.seq} -> a new implementer on {opened}: {reason}")
            try:
                await self.acts.start(opened, h.brief(), row.who)
            except asyncio.CancelledError:  # the brain stopped before its implementer had it
                self.acts.fail(opened, f"{opened} failed: cut off, the brain stopped before its implementer started")
                raise
            return NEW_IMPLEMENTER, opened, ""
        if to == IMPLEMENTER:  # one follow-up a task: a line about two tasks' things follows each up
            followed = []
            for f in route.follow_ups:
                name = f.task.strip()
                text = self.acts.reopen_task(f)
                if text is None:
                    log("router", f"#{row.seq} -> {name}: no such task now, its follow-up dropped")
                    continue
                log("router", f"#{row.seq} -> {name}'s implementer, {'a cancel' if f.cancel else 'a follow-up'}: "
                              f"{reason}")
                try:
                    await self.acts.send(name, text)
                except asyncio.CancelledError:
                    self.acts.fail(name, f"{name} failed: cut off, the brain stopped before its implementer got the "
                                         "follow-up")
                    raise
                followed.append(name)
            if followed:
                return IMPLEMENTER, ", ".join(followed), ""
            log("router", f"#{row.seq} -> {', '.join(f.task.strip() for f in route.follow_ups)}: no such task now, "
                          f"so a new loop ({reason})")
            to, reason = NEW_LOOP, ""
        if to == NOWHERE:
            log("router", f"#{row.seq} -> nowhere: {reason}")
            return NOWHERE, "", ""
        loop = self.loops.new(Event("LINE", row, said=said, reason=reason), row.who, row.text)
        log("router", f"#{row.seq} -> a new loop {loop.id}" + (f": {reason}" if reason else ""))
        self.spawn(self.run_new(loop, new), f"loop {loop.id}", row.who)
        return NEW_LOOP, loop.id, ""

    # ------------------------------------------------------------- a loop

    async def run_new(self, loop: Loop, new: list[Row]) -> None:
        """A new loop, a task of its own that holds no turn (WROTE unset: nobody waits for its rows
        to come back): the world read once, its prompt, then its steps. `new` is what the front
        turn read as NEW, the line itself left out."""
        WROTE.set(None)
        try:
            loop.seen = await self.world.seen(items=[loop.player] if loop.player else False, full=True)
            loop.prompt = self.call.prompt(loop.event, self.ctx, loop.seen, self.board(loop.seen), new, self.refs)
            loop.seen_seq = self.ctx.last_seq
        except BaseException:  # the error is said by the spawn's guard; the loop can take no line
            self.drop(loop)
            raise
        await self.run_loop(loop)

    async def resume(self, loop: Loop) -> None:
        """An ended loop taken up again by a line the router added: steps from where it stopped,
        on its prompt as it was (read from the cache), the line before the first of them."""
        WROTE.set(None)
        loop.begin(self.clock())
        await self.run_loop(loop)

    def drop(self, loop: Loop) -> None:
        """A loop that broke or was cut off: deleted, and the lines it never read logged."""
        for a in loop.pending:
            log("loop", f"{loop.id} never read #{a.row.seq} {a.row.who}: {a.row.text[:120]!r}")
        loop.pending.clear()
        self.loops.drop(loop)

    def read_added(self, loop: Loop) -> str:
        """The lines added since the loop's last step, as one block, with the rows it has not read
        yet; they are read now. Nothing is marked processed for it: the front turn did."""
        lines, loop.pending = loop.pending, []
        held = {a.row.seq for a in lines}
        since = [r for r in self.ctx.rows if r.seq > loop.seen_seq and r.seq not in held]
        loop.seen_seq = max(loop.seen_seq, self.ctx.last_seq)
        return added(lines, since)

    async def run_loop(self, loop: Loop) -> None:
        """Steps until one ends the loop, at most `loop.limit` of them, a limit each line added
        raises up to the cap; see the module's docstring. The world (`loop.seen`), TASKS and the
        conversation are the prompt's as they were at the loop's start, for every step; what
        changed comes in the results and with an added line. When something raises that nobody
        expected, the loop still leaves the record of what its steps did, is deleted, and raises on."""
        event, prompt, ref = loop.event, loop.prompt, loop.event.ref()
        # a reply that promises with nothing started is refused, but for a status: of the speaker's open task, or
        # of the follow-up a report's loop is told already runs
        promise = None if event.following else functools.partial(
            promises, tasks=self.tasks.show(self.implementers.running()), who=loop.player, offers=False)
        try:
            while True:
                if loop.pending:
                    prompt.steps.append(self.read_added(loop))
                loop.steps += 1
                n = loop.steps
                last = n >= loop.limit
                # with nothing of the loop landed yet, a step that runs nothing may not end silent or claim it done;
                # a report's loop has its implementer's work to speak of
                output = step_type(self.catalog, last=last, tasks=self.tasks.names(), promise=promise,
                                   landed=loop.did or event.kind == "REPORT")
                trailer = self.call.trailer(n, loop.limit, last)
                try:
                    answer = await self.call.step(prompt, trailer, output, f"{ref} s{n}")
                except LlmError as e:
                    loop.spend(e)
                    log("brain", f"{ref} s{n} FAILED ({spent(e)}): {e}")
                    self.flush()
                    said = await self.failed(loop, e)
                    prompt.steps.append(failed_step(n, e, said))
                    if self.loops.settle(loop):
                        self.went_on(loop)
                        continue
                    return
                loop.spend(answer)
                self.flush()
                step = answer.value
                run = await self.acts.run(step, n=n, player=loop.player, seen=loop.seen, refs=self.refs,
                                          ref=f"{ref} s{n}")
                loop.runs.append(run)
                loop.did = loop.did or any(r.state == LANDED for r in run.actions)
                log("brain", f"{ref} s{n} {spent(answer)} | {describe(step)} -> {run.summary()}")
                if run.landed and step.end != NEXT:
                    await self.end(loop, step, run)
                    prompt.steps.append(run.show(sent=True))  # what went out, for a step after it
                    if self.loops.settle(loop):  # a line came in while it ran: one more step reads it
                        self.went_on(loop)
                        continue
                    return
                if last:  # the last step's ending did not land: nothing comes after it
                    said = await self.failed(loop, None)
                    prompt.steps.append(run.show().rstrip("\n") + f"\nSENT INSTEAD: “{said}”\n\n")
                    if self.loops.settle(loop):
                        self.went_on(loop)
                        continue
                    return
                prompt.steps.append(run.show())
        except asyncio.CancelledError:
            loop.how = "cut off"
            self.drop(loop)
            raise
        except Exception as e:
            if loop.replied is None:  # the error line, after what landed, as failed() says it: once
                with contextlib.suppress(Exception):
                    await self.acts.say(errored(loop.player, e, loop.landed()))
            e.add_note(TOLD)          # said here, or the reply went out already: the guard says nothing
            if not loop.recorded and (done := record_of(loop.runs)):
                self.acts.post("action", "", done)
                loop.recorded = True
            self.drop(loop)
            raise
        finally:
            self.log_loop(loop)

    @staticmethod
    def went_on(loop: Loop) -> None:
        """A loop that goes on past an ending (or its error line), for a line added while it ran:
        what its steps ran is in the record that went out, so a later error line or record never
        claims it again; the steps after it start their own."""
        loop.runs, loop.recorded = [], False

    async def end(self, loop: Loop, step: StepBase, run: StepRun) -> None:
        """A run's ending, once its last step landed: each hand-off's task opened and each
        follow-up's opened again, with their `AI task` rows; the reply; the record; then each
        implementer started and each follow-up sent, the loop waiting for none of them to finish.
        A REPORT loop never closes its task: the conversation agent wrote its result. A task cut
        off before its implementer had it (the brain stopped) fails by name."""
        event = loop.event
        reported = str((event.task or {}).get("name") or "") if event.kind == "REPORT" else ""
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
            task = reported or (starts[0][0] if starts else sends[0][0] if sends else "")
            record = step.record.strip() or record_of(loop.runs)
            reply = step.reply.strip()
            if unopened:  # the reply promises what never started: a line written here says so instead
                said = ", ".join(f"“{short(w, 60)}”" for w in unopened)
                done = short("; ".join(loop.landed()), LANDED_MAX)
                head = (f"done: {done} — " if done else "") + ("the rest is on its way, but " if starts or sends
                                                               else "but " if done else "sorry, ")
                reply = short(f"{head}I couldn't start {said} — say it again?", LINE_MAX)
                record = short(f"{record + '; ' if record else ''}could not start {said}", 400)
                run.said = reply
                log("task", f"{event.ref()} {len(unopened)} hand-off(s) did not open: said {reply!r} instead of "
                            f"{step.reply.strip()!r}")
            if reply:
                await self.acts.say(reply, task)
                loop.replied = self.clock() - loop.started
                loop.last_reply = reply
            if record:
                self.acts.post("action", "", record)
                loop.recorded = True
                loop.record = record
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

    async def failed(self, loop: Loop, e: Exception | None) -> str:
        """A run with no ending: its step's call failed for good (`e`), or its last step's ending
        did not land. Its player hears the error line, after what landed, and the record of what
        its steps did goes in. The line said."""
        loop.how = "failed"
        why = e if e is not None else Exception("it reached its limit with nothing it could end on")
        said = errored(loop.player, why, loop.landed())
        reported = str((loop.event.task or {}).get("name") or "") if loop.event.kind == "REPORT" else ""
        await self.acts.say(said, reported)
        loop.replied = self.clock() - loop.started
        loop.last_reply = said
        if done := record_of(loop.runs):
            self.acts.post("action", "", done)
            loop.recorded = True
            loop.record = done
        return said

    def log_loop(self, loop: Loop) -> None:
        """A run's line: its steps, what it cost, how much of it the cache read, the time to its
        reply and how it ended; then the loop's id, its state after it (saved or deleted) and the
        lines it holds."""
        rate = f"{loop.cached_tokens / loop.prompt_tokens:.0%}" if loop.prompt_tokens else "no"
        reply = f"reply at {loop.replied:.1f} s" if loop.replied is not None else "no reply"
        who = loop.player or "nobody"
        state = {ENDED: "saved", DELETED: "deleted"}.get(loop.state, loop.state)
        lines = ", ".join(f"#{r.seq}" for r in loop.lines) or "none"
        log("loop", f"{loop.event.ref()} {who}: {loop.calls} step(s), ${loop.cost:.6f}"
                    f"{'' if loop.cost_known else '+'}, {rate} cached, {reply}, "
                    f"{self.clock() - loop.started:.1f} s, ended {loop.how}; {loop.id} {state}, "
                    f"{loop.steps} of {loop.limit} steps, lines {lines}")

    # ------------------------------------------------------------- reports

    def keep_session(self, name: str) -> None:
        """The task's implementer session into its row, in case on_session missed it."""
        session = getattr(self.implementers, "session", None)
        if callable(session):
            self.tasks.set_session(name, session(name))

    def result(self, name: str, status: str, record: str, following: bool) -> bool:
        """A report's record, an `AI result` row: the task closed with it, or kept open with it as
        the result so far while a follow-up to it runs (or a hand-off of what is left is on its
        way). True when kept open."""
        kept = following or name in self.implementers.running()
        if kept:
            self.tasks.note(name, record)
            log("task", f"{name} stays open: a follow-up to it runs, or what is left is handed off")
        else:
            self.tasks.close(name, status, record)
        self.acts.post("result", name, record)
        return kept

    async def on_report(self, name: str, status: str, text: str, description: str = "",
                        made: dict | None = None) -> None:
        """What the implementers call when one ends (their `on_report`), with its report, its
        DESCRIPTION line taken off it, and what it made: the conversation agent reads it at once,
        a task of its own beside whatever the player's turn is doing; it never queues, and the
        implementer waits for none of it. A task whose implementers still name it as running has
        a follow-up queued or started behind this run. While the brain stops, the task closes on
        the implementer's status, in code."""
        following = name in self.implementers.running()
        if self.stopping:
            return self.closed_in_code(name, status, text, description, made, following)
        self.spawn(self.report_front(name, status, text, description, made, following), f"report {name}")
        return None

    async def report_front(self, name: str, status: str, text: str, description: str = "",
                           made: dict | None = None, following: bool = False) -> None:
        """An implementer ended: its task takes what it made, its DESCRIPTION line (when it gave
        one; else the description it had stays) and the variables of its action; then the
        conversation agent reads the report whole, reading what no call has read yet, and writes
        the player's line, the record and the status. The task is closed with them, or kept open
        while a follow-up to it runs (`following`) or the agent hands off what is left, which the
        router then places (report_route()). The task never stays open for an error: the agent
        failing, or anything nobody expected, closes it in code with a line written here. Its rows
        are nobody's turn's (WROTE unset)."""
        WROTE.set(None)
        self.keep_session(name)
        self.tasks.merge_made(name, made)
        if description.strip():
            self.tasks.describe(name, description)
        recorded = False
        try:
            self.reload()
            self.refresh_variables(name)
            task = self.tasks.get(name) or {"name": name, "player": None, "line": "", "brief": "", "description": ""}
            new = self.take()
            seen = await self.world.glance()
            running = self.implementers.running()
            try:
                event = ReportEvent(task=task, status=status, report=text, following=following)
                answer = await self.respond.report(event, self.ctx, seen, self.tasks.show(running),
                                                   self.loops.show(brief=True), new)
            except LlmError as e:
                log("respond", f"{name} FAILED ({spent(e)}; {e}): the task closed in code")
                self.flush()
                kept = self.result(name, status, self.written(name, status), following)
                recorded = True
                await self.acts.say(self.line_for(name, status, kept), name)
                return
            self.usage("respond", answer, name)
            got = answer.value
            keep = following or got.hand_off or name in running
            reply = got.reply.strip() or self.line_for(name, got.status, keep)
            await self.acts.say(reply, name)
            record = got.record.strip() or self.written(name, got.status)
            self.result(name, got.status, record, following or got.hand_off)
            recorded = True
            log("respond", f"{name} {got.status}: {reply!r}; "
                           f"{'left: ' + short(got.left, 200) if got.hand_off else 'nothing left'} ({got.reason})")
            if got.hand_off:
                await self.report_route(task, got, reply, record, text, following, seen)
        except Exception:  # raised on, for the event's own log; the task never stays open for it
            if not recorded:
                kept = self.result(name, status, self.written(name, status), following)
                recorded = True
                await self.acts.say(self.line_for(name, status, kept), name)
            raise
        finally:
            if not recorded:  # cancelled: the brain stopped while it ran
                self.result(name, status, self.written(name, status), following)

    async def report_route(self, task: dict, got, said: str, record: str, report: str, following: bool,
                           seen: Seen) -> None:
        """What a report left, placed by the router: usually the task's implementer again, a
        follow-up whose brief names the done-when items left; a new loop when an action does the
        rest now; a new implementer; or nowhere, with the router's own line. No loop is offered:
        a report's work never joins a loop. The router failing sends what is left (`left`) to the
        task's implementer. Unless that task is followed up here (or a follow-up of it already
        runs), it is closed on the agent's status with its record once the work is placed: a
        loop's or an implementer's work on what is left has records of its own."""
        name = str(task.get("name") or "")
        player = str(task.get("player") or "")
        route = None
        try:
            routed = await self.router.route(RouterEvent("REPORT", task=task, said=said, status=got.status,
                                                         report=report, left=got.left),
                                             self.ctx, self.in_full(seen), self.board(seen),
                                             self.loops.show(brief=False), loops=[], tasks=self.tasks.names(),
                                             refs=self.refs)
        except LlmError as e:
            log("router", f"{name} FAILED ({spent(e)}; {e}): a follow-up to {name} with what is left")
            self.flush()
        else:
            self.usage("router", routed, name)
            route = routed.value
        followed = ""
        try:
            if route is None or route.to == IMPLEMENTER:
                f = route.follow_ups[0] if route is not None else FollowUp(task=name, brief=got.left or got.reason)
                text = self.acts.reopen_task(f)
                if text is None:
                    log("router", f"{name}: the follow-up's task {f.task.strip()} is gone: nothing more is done")
                    return
                followed = f.task.strip()
                log("router", f"{name} -> {followed}'s implementer: {route.reason if route is not None else 'left'}")
                await self.acts.send(followed, text)
            elif route.to == NEW_IMPLEMENTER:
                h = route.hand_offs[0]
                opened = self.acts.open_task(h, player=player, line=str(task.get("line") or ""))
                if opened is None:
                    log("task", f"{name}: the router's hand-off {h.name} of what was left did not open")
                    return
                log("router", f"{name} -> a new implementer on {opened}: {route.reason}")
                await self.acts.start(opened, h.brief(), player)
            elif route.to == NEW_LOOP:
                loop = self.loops.new(Event("REPORT", said=said, reason=route.reason, task=task, status=got.status,
                                            report=report, left=got.left, following=following),
                                      player, str(task.get("line") or ""))
                log("router", f"{name} -> a new loop {loop.id}: {route.reason}")
                self.spawn(self.run_new(loop, []), f"loop {loop.id}", player)
            else:
                log("router", f"{name} -> {route.to}: {route.reason}")
                if route.reply.strip():
                    await self.acts.say(route.reply.strip(), name)
        finally:
            if followed != name and not following and name not in self.implementers.running():
                self.tasks.close(name, got.status, record)

    def closed_in_code(self, name: str, status: str, text: str, description: str, made: dict | None,
                       following: bool) -> None:
        """A report no model reads (the brain stops): its task takes what the implementer made and
        closes on its status, with a record written in code and no model call."""
        log("task", f"{name} reported {status} while the brain stops: {short(text, 200)}")
        self.keep_session(name)
        self.tasks.merge_made(name, made)
        if description.strip():
            self.tasks.describe(name, description)
        self.result(name, status, self.written(name, status), following)

    def written(self, name: str, status: str) -> str:
        """A task's record written in code, never the implementer's words: `<name> done: <what it
        is>`, its description (a brief is in parts under headings now, and long)."""
        what = short((self.tasks.get(name) or {}).get("description") or "", 120)
        return short(f"{name} {status}" + (f": {what}" if what else ""), 400)

    def line_for(self, name: str, status: str, following: bool = False) -> str:
        """A report's chat line written in code, when the agent gave none: the player's own words,
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
        """Read nothing new, drop what waits for a turn and forget the saved loops; let the turns,
        loops and reports in flight finish for up to `grace_s` (a report that comes in meanwhile
        closes its task in code, on_report()); stop the implementers and fail their tasks as
        stopped."""
        self.stopping = True
        dropped = self.turns.close()  # first: a turn that ends in the grace starts nothing after it
        if dropped:
            log("stop", f"dropped {dropped} line(s) waiting for a turn")
        for row in self.waiting.values():  # one rule: a line dropped here is read, never answered, after a restart too
            if not row.processed:
                self.mark(row)
            log("stop", f"#{row.seq} {row.who} dropped at the stop, marked read: {row.text[:120]!r}")
        self.waiting.clear()
        forgot = self.loops.close()   # no line is added from now on; a loop in flight is deleted as it ends
        if forgot:
            log("stop", f"forgot {forgot} saved loop(s)")
        self.acts.echoes.release()  # the watch is done: no turn waits for its rows to come back
        if self.inflight:
            log("stop", f"waiting up to {grace_s:g} s for {len(self.inflight)} call(s) in flight")
            # A front turn finishing in the grace may still start a loop: what is spawned meanwhile is waited for
            # too, within the same grace, and cut off with the rest at its end.
            end = asyncio.get_running_loop().time() + grace_s
            while self.inflight:
                wait = end - asyncio.get_running_loop().time()
                if wait <= 0:
                    left = list(self.inflight)
                    for t in left:
                        t.cancel()
                    await asyncio.gather(*left, return_exceptions=True)
                    break
                await asyncio.wait(list(self.inflight), timeout=wait)
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
