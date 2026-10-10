"""The brain's loop: every row of the feed into the shared context, and every event its own
asyncio task, so two players who speak 0.1 s apart each get a call at once and neither waits.

A row from `vfg watch` goes into the context; then, by what it is:
- a LINE Jev classed action_oriented: a brain call (call.py), its decision run (act.py);
- a LINE classed conversational: a Respond call (respond.py), which replies, or hands the line
  up to the brain, which then runs it as an action_oriented one, with what Respond read;
- anything else (context lines, game events, system notices, the brain's own AI rows): context
  only, for the next read.
A LINE already in the feed when the context was read (at or below its highest seq then, `start`)
and said more than HISTORY_S before that is history: context, marked processed, never acted on. A
LINE that arrives on the watch is live whatever its HH:MM:SS: a box clock a minute off the PC's
would otherwise make every line history.

Every live read takes in what no call has read yet, marks it processed (`vfg context mark`,
nobody waiting) and pushes the batch timer back (timer.py). An implementer's report is a brain
call of its own; it reads nothing new. Every model call writes a `usage` row, its cached tokens
included, nobody waiting.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

from vfg_actions import menu_line
from vfg_llm import LlmError

from . import actions
from .act import Acts, short
from .call import Call, Event, describe
from .config import log
from .context import LIVE, Context, Row, parse, show
from .respond import Respond
from .tasks import Tasks
from .timer import Timer
from .vfg import Later
from .world import World

HISTORY_S = 120  # a line older than this when it arrives is history
MARK_CHUNK = 500  # seqs in one `vfg context mark`
SORRY = "sorry, I lost that one — say it again?"
WATCH_AGAIN_S = 2.0  # after `vfg watch` exits, before it is followed again


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Brain:
    def __init__(self, *, ctx: Context, call: Call, respond: Respond, acts: Acts, world: World, tasks: Tasks,
                 implementers, later: Later, catalog, refs=None, batch_s: float = 60.0,
                 model: str = "", respond_model: str = "", now: Callable[[], datetime] = utc_now,
                 clock=None, menu: Callable[[str], object] | None = None, start: int = 0):
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
        self.model, self.respond_model = model, respond_model
        self.now = now
        self.menu = menu              # sets Jev's menu again when the actions change
        self.timer = Timer(batch_s, lambda: bool(self.ctx.unprocessed()), self.batch_now,
                           **({"clock": clock} if clock else {}))
        self.inflight: set[asyncio.Task] = set()
        self.stopping = False

    # ------------------------------------------------------------- rows in

    def spawn(self, coro, name: str) -> asyncio.Task:
        """An event as its own task, kept until it ends; what it raises is logged, never lost."""
        async def guarded() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one event's bug never stops the others
                log("error", f"{name} failed: {type(e).__name__}: {e} | {traceback.format_exc(limit=-4)}")

        t = asyncio.create_task(guarded(), name=name)
        self.inflight.add(t)
        t.add_done_callback(self.inflight.discard)
        return t

    def feed(self, text: str) -> asyncio.Task | None:
        """One line from `vfg watch`: into the context, then to its call; the event's task, if any."""
        row = parse(text, now=self.now())
        if row is None:
            return None
        if row.seq == 0:  # vfg watch's own notices: EC2 unreachable, reconnected, lines skipped
            log("watch", row.text)
            return None
        if row.seq <= self.position() or not self.ctx.add(row):  # not past where the watch follows from
            return None
        self.timer.arrived()
        return self.route(row)

    def route(self, row: Row) -> asyncio.Task | None:
        if self.stopping or row.type != "LINE" or row.cls not in LIVE or row.processed:
            return None
        if row.seq <= self.start and row.at < iso(self.now() - timedelta(seconds=HISTORY_S)):
            row.processed = True
            self.later("context", "mark", str(row.seq))
            log("line", f"#{row.seq} {row.who} from {row.hms} is history, not acted on: {row.text[:120]!r}")
            return None
        if row.cls == "action_oriented":
            return self.spawn(self.line(row), f"line #{row.seq}")
        return self.spawn(self.conversational(row), f"respond #{row.seq}")

    def position(self) -> int:
        """Where the watch follows from: the context's last row, or the feed's highest seq when the
        context was read, whichever is later; never 0 for a feed with rows in it."""
        return max(self.ctx.last_seq, self.start)

    def catch_up(self) -> list[Row]:
        """The context as read at the start: what is unread and older than HISTORY_S is history,
        marked read, never acted on; a line of the last HISTORY_S still unread is answered.
        Returns the history."""
        history = self.ctx.take_before(iso(self.now() - timedelta(seconds=HISTORY_S)))
        for i in range(0, len(history), MARK_CHUNK):
            self.later("context", "mark", *(str(r.seq) for r in history[i:i + MARK_CHUNK]), timeout=60)
        for row in self.ctx.unprocessed():
            if row.type == "LINE" and row.cls in LIVE:
                self.route(row)
        return history

    def take(self) -> list[Row]:
        """A read: what no call has read yet, marked processed, and the timer pushed back."""
        new = self.ctx.take_unprocessed()
        self.timer.read()
        if new:
            self.later("context", "mark", *(str(r.seq) for r in new))
        return new

    def usage(self, component: str, model: str, answer, ref: str) -> None:
        rate = f", {answer.cached_tokens / answer.prompt_tokens:.0%} cached" if answer.prompt_tokens else ""
        log(component, f"{ref} {answer.ms} ms ${answer.cost:.6f}, {answer.prompt_tokens} in{rate}, "
                       f"{answer.completion_tokens} out")
        self.later("usage", "add", "--component", component, "--model", model, "--in", str(answer.prompt_tokens),
                   "--out", str(answer.completion_tokens), "--cost", f"{answer.cost:.6f}",
                   "--cache-read", str(answer.cached_tokens), "--ref", ref[:64])

    def reload(self) -> None:
        """Before each brain call: every action file that changed is loaded again, so an action an
        implementer saved a moment ago is on the menu, and Jev's menu with it."""
        try:
            changed = actions.refresh(self.catalog)
        except Exception as e:  # noqa: BLE001 — the actions as they were are still good
            log("actions", f"reloading failed ({type(e).__name__}: {e}); the menu is as it was")
            return
        if changed and self.menu is not None:
            self.menu(jev_menu(self.catalog))

    # ------------------------------------------------------------- events

    async def line(self, row: Row, new: list[Row] | None = None, handed_up: str = "") -> None:
        """An action_oriented line, or one Respond handed up with what it read: one brain call."""
        if new is None:
            new = self.take()
        self.reload()
        seen = await self.world.seen()
        event = Event("LINE", row, handed_up=handed_up)
        try:
            answer = await self.call.decide(event, self.ctx, seen, self.open_tasks(),
                                            [r for r in new if r.seq != row.seq], self.refs)
        except LlmError as e:
            log("brain", f"#{row.seq} FAILED: {e}")
            await self.acts.say(f"{row.who}: {SORRY}")
            return
        self.usage("brain", self.model, answer, f"#{row.seq}")
        log("brain", f"#{row.seq} {row.who}: {row.text[:80]!r} -> {describe(answer.value)}")
        await self.acts.run(answer.value, player=row.who, line=row.text, seen=seen)

    async def conversational(self, row: Row) -> None:
        """A conversational line: Respond replies, or hands it up to the brain."""
        new = self.take()
        seen = await self.world.glance()
        try:
            answer = await self.respond.answer(row, self.ctx, seen, self.open_tasks(),
                                               [r for r in new if r.seq != row.seq])
        except LlmError as e:
            log("respond", f"#{row.seq} FAILED ({e}): handed up")
            return await self.line(row, new, handed_up="Respond could not answer")
        self.usage("respond", self.respond_model, answer, f"#{row.seq}")
        said = answer.value
        if said.hand_up:
            log("respond", f"#{row.seq} handed up: {said.reason}")
            return await self.line(row, new, handed_up=said.reason or "it may ask for something")
        log("respond", f"#{row.seq} {row.who}: {row.text[:80]!r} -> {said.reply!r} ({said.reason})")
        if said.reply.strip():
            await self.acts.say(said.reply)

    async def report(self, tid: str, status: str, text: str, following: bool = False) -> None:
        """An implementer ended: one brain call writes the player's line and the record. When a
        follow-up to the task already runs (`following`), the task stays open with the result so
        far, and the call is told, so its reply never says it is all done."""
        task = self.tasks.get(tid) or {"id": tid, "player": "", "line": "", "task": ""}
        self.keep_session(tid)
        recorded = False
        try:
            self.reload()
            seen = await self.world.seen()
            event = Event("REPORT", task=task, status=status, report=text, following=following)
            try:
                answer = await self.call.decide(event, self.ctx, seen, self.open_tasks(), [], self.refs)
            except LlmError as e:
                log("brain", f"{tid} report FAILED: {e}")
                self.result(tid, status, self.written(tid, status), following)
                recorded = True
                await self.acts.say(self.line_for(tid, status, following), tid)
                return
            self.usage("brain", self.model, answer, tid)
            d = answer.value
            log("brain", f"{tid} report ({status}) -> {describe(d)}")
            final = d.status or status
            self.result(tid, final, d.record.strip() or self.written(tid, final), following)
            recorded = True
            if d.parts:
                await self.acts.run(d, player=task.get("player") or "", line=task.get("line") or "", seen=seen)
            else:  # every report gets its chat line: the call's, else one written here
                await self.acts.say(d.reply.strip() or self.line_for(tid, final, following), tid)
        except Exception:  # raised on, for the event's own log; the task never stays open for it
            if not recorded:
                self.result(tid, status, self.written(tid, status), following)
                recorded = True
                await self.acts.say(self.line_for(tid, status, following), tid)
            raise
        finally:
            if not recorded:  # cancelled: the brain stopped while the call ran
                self.result(tid, status, self.written(tid, status), following)

    def keep_session(self, tid: str) -> None:
        """The task's implementer session into tasks.json, in case on_session missed it."""
        session = getattr(self.implementers, "session", None)
        if callable(session):
            self.tasks.set_session(tid, session(tid))

    def result(self, tid: str, status: str, record: str, following: bool) -> None:
        """A report's record, an `AI result` row: the task closed with it, or kept open with it as
        the result so far while a follow-up to it runs (one sent while the call ran included)."""
        if following or tid in self.implementers.running():
            self.tasks.note(tid, record)
            log("task", f"{tid} stays open: a follow-up to it runs")
        else:
            self.tasks.close(tid, status, record)
        self.acts.post("result", tid, record)

    async def on_report(self, tid: str, status: str, text: str) -> None:
        """What the implementers call when one ends (their `on_report`): the report is a brain
        call of its own, and the implementer waits for none of it. A task whose implementers still
        name it as running has a follow-up queued or started behind this run."""
        following = tid in self.implementers.running()
        if self.stopping:  # no model call now: the task closes on the implementer's status, in code
            log("task", f"{tid} reported {status} while the brain stops: {short(text, 200)}")
            self.keep_session(tid)
            self.result(tid, status, self.written(tid, status), following)
            return
        self.spawn(self.report(tid, status, text, following), f"report {tid}")

    def written(self, tid: str, status: str) -> str:
        """A task's record written in code, never the implementer's words: `T7 done: <its brief>`."""
        brief = short((self.tasks.get(tid) or {}).get("task") or "", 120)
        return f"{tid} {status}" + (f": {brief}" if brief else "")

    def line_for(self, tid: str, status: str, following: bool = False) -> str:
        """A report's chat line written in code, when the call gave none."""
        brief = short((self.tasks.get(tid) or {}).get("task") or "", 80) or tid
        if following:
            return f"still on it: {brief}"
        return f"done: {brief}" if status == "done" else f"sorry, that one failed: {brief}"

    def batch_now(self) -> asyncio.Task:
        return self.spawn(self.batch(), "batch")

    async def batch(self) -> None:
        """What no live read took in for a while: one brain call, which usually does nothing."""
        if self.stopping:
            return
        new = self.take()
        if not new:
            return
        self.reload()
        seen = await self.world.seen()
        try:
            answer = await self.call.decide(Event("BATCH"), self.ctx, seen, self.open_tasks(), new, self.refs)
        except LlmError as e:
            log("brain", f"batch of {len(new)} FAILED: {e}")
            return
        self.usage("brain", self.model, answer, f"batch #{new[0].seq}-#{new[-1].seq}")
        log("brain", f"batch of {len(new)} -> {describe(answer.value)}")
        if answer.value.parts or answer.value.reply.strip():
            await self.acts.run(answer.value, player="", line=short(show(new), 500), seen=seen)

    def open_tasks(self) -> str:
        return self.tasks.show(self.implementers.running())

    # ------------------------------------------------------------- running and stopping

    async def follow(self, watch: Callable[[int], AsyncIterator[str]]) -> None:
        """Feed every line of `vfg watch --after <position>`; follow again whenever it exits."""
        while not self.stopping:
            async for text in watch(self.position()):
                if self.stopping:
                    return
                try:
                    self.feed(text)
                except Exception as e:  # noqa: BLE001 — a line it cannot read never stops the watch
                    log("error", f"feeding {text.strip()[:200]!r}: {type(e).__name__}: {e}")
            if not self.stopping:
                log("watch", f"vfg watch ended; following again in {WATCH_AGAIN_S:g} s from #{self.position()}")
                await asyncio.sleep(WATCH_AGAIN_S)

    async def stop(self, grace_s: float, implementers_grace_s: float = 2.0) -> None:
        """Read nothing new; let the calls in flight finish for up to `grace_s` (a report that
        comes in meanwhile closes its task in code, on_report()); stop the implementers and fail
        their tasks as stopped."""
        self.stopping = True
        await self.timer.stop()
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
        for tid in stopped:
            try:
                self.keep_session(tid)  # a follow-up after the restart resumes it
                self.tasks.close(tid, "failed", f"{tid} failed: stopped, the brain stopped while it ran")
                self.acts.post("result", tid, f"{tid} failed: stopped, the brain stopped while it ran")
            except Exception as e:  # noqa: BLE001 — one task's trouble never keeps the rest open
                log("error", f"closing {tid} at the stop failed: {type(e).__name__}: {e}")
        if stopped:
            log("stop", f"stopped the implementers of {', '.join(stopped)}")


def jev_menu(catalog) -> str:
    """Jev's menu (`vfg jev menu set`): one `name — what it does` line per action."""
    return "\n".join(menu_line(n, catalog.get(n)) for n in catalog.names())
