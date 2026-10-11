"""Running a decision: what one answer of the brain becomes. Code, no model, but for the
orchestrator's calls and the one retry of a refused command.

- Action parts run in order, each after the one before has finished, as their own asyncio task,
  with no task (Prototype_Named_Tasks: only an implementer's work is one). Each runs as `vfg
  action run … --for <player>`, so its copy is named `<action>_<player>` on the box, after its
  values are checked as vfg checks them (the saved places and the running copies included). A
  command Minecraft refuses gets ONE retry from the action planner, on VFG_PLAN_MODEL, written
  with the reference of every command the refused one used; nothing else is retried. Each part,
  run or dropped, becomes an `AI action -` row.
- Orchestrator parts run beside the actions, one orchestrator call each (orchestrator.py), one
  after another in the order they come, each told what the ones before it answered. Its answer
  is carried out here: `new` opens a task under a name no task, action, script or running copy
  has (names.unique), posts `AI task <name> | new: …` and starts its implementer at once;
  `follow_up` opens the task again and sends the brief to its implementer; `edit` runs the edit
  action on the copy and `stop` the stop action on each copy, each an `AI note` row, the task's
  status untouched; a reply only starts nothing.
- The reply. When no part goes to the orchestrator, it is the brain's, said once the actions
  are done (at once when there are none). When one does, it is the first orchestrator call's,
  said once the actions, that call and its edit or stop are done, never waiting for an
  implementer to start or for a later call; the brain's own reply is held back, and said only
  when that call fails. A later call's reply only, a failed edit or stop of its own, or its
  failure, is a line of its own after it. Through `vfg say … --task <name>` when a new or
  follow_up opened a task on the line. It never claims a success that failed: when an action,
  an edit or a stop failed, or was dropped here (a copy not running, a player offline), a short
  line written here goes out instead. A part the brain dropped itself keeps its reply, which
  was written knowing it.
- A report's decision runs the same, with the task's player, but its orchestrator calls write
  no reply: the brain's report reply is said once the actions are done.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vfg_actions import check_edit, check_values
from vfg_llm import LlmError

from . import names
from .action_planner import Checked, Final, Pick, Planner, Spent, Unchecked
from .actions import Catalog, command_fields, word
from .call import ORCHESTRATOR, TALK
from .config import log
from .executor import Ran, execute
from .tasks import Tasks
from .vfg import Later, Vfg
from .world import Seen

SAY_TIMEOUT_S = 30
BROKE = "sorry, something broke on my side and that one didn't finish"
LOST = 'sorry, I lost part of that — say "{part}" again?'
LINE_MAX = 200    # a chat line written here
ROW_MAX = 400     # an AI row's text
BRIEF_SHORT = 160  # a brief in an `AI task` row
PART_SHORT = 60   # the player's words in LOST
PLAYER = re.compile(r"^[A-Za-z0-9_]{1,16}$")  # a player's name, as --for takes it
NEW, FOLLOW_UP, EDIT, STOP = "new", "follow_up", "edit", "stop"  # the orchestrator's answers; and "reply"


@dataclass
class Outcome:
    """What one action part, edit or stop came to."""
    instruction: str
    ok: bool
    summary: str           # what ran (the executor's summary, with vfg's notes), or why not
    dropped: bool = False  # never run: something it needs is missing
    chosen: bool = False   # dropped by the brain itself, which knew it as it wrote its reply


@dataclass
class Turn:
    """What a decision's orchestrator calls have come to so far."""
    decided: asyncio.Event = field(default_factory=asyncio.Event)  # the first call and its edit or stop are done
    answered: bool = False   # the first call answered (False: it failed, and the brain's reply is said)
    reply: str = ""          # the first call's reply
    outcomes: list[Outcome] = field(default_factory=list)  # the first call's edit or stop
    started: list[str] = field(default_factory=list)  # the player's words of each new or follow_up
    task: str = ""           # the first task a new or follow_up opened: the reply's --task
    extras: list[str] = field(default_factory=list)   # lines said after the reply, each on its own


class NotRunning(Exception):
    """An edit or a stop of a copy that is not running: the part is dropped."""


def strip_end(text: str) -> str:
    return " ".join(str(text).split()).rstrip(".").strip()


def short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def told(ran: Ran, notes: list[str] = ()) -> str:
    """The executor's summary; the copy's name when it keeps running and the summary does not say
    it, so a later edit or stop can name it; and the numbers clamped to their limits."""
    text = ran.summary
    if ran.running and ran.copy and ran.copy not in text:
        text += f" (running as {ran.copy})"
    notes = [n for n in notes if n not in text]
    return text + (f" ({'; '.join(notes)})" if notes else "")


def offline_names(facts: dict[str, Any]) -> list[str]:
    names = facts.get("offline")
    return [str(n) for n in names if str(n).strip()] if isinstance(names, list) else []


def without_repeats(commands: list[str], already: list[str]) -> list[str]:
    """A retry's commands without the leading ones that already ran and took effect."""
    left = list(commands)
    while left and left[0] in already:
        left.pop(0)
    return left


def distinct(parts: list) -> list:
    """The action parts, each (action, values) once, in order."""
    seen, out = set(), []
    for p in parts:
        key = (p.to, repr(sorted(p.values().items())), p.drop.strip())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def fallback(outcomes: list[Outcome], started: list[str]) -> str:
    """The line said instead of the reply when an action, an edit or a stop failed or was dropped:
    what was done, what was not and why, and what an implementer is on."""
    bits = []
    done = [strip_end(o.instruction) for o in outcomes if o.ok]
    if done:
        bits.append("done: " + "; ".join(done))
    for o in outcomes:
        if not o.ok:
            what = strip_end(o.instruction)
            bits.append(f"couldn't {what[:1].lower() + what[1:]}: {short(strip_end(o.summary), 90)}")
    if started:
        bits.append("on it: " + "; ".join(started))
    return short(" — ".join(bits), LINE_MAX)


def for_whom(name: str, values: dict[str, Any], player: str) -> str | None:
    """Whose copy an action's run is, for `--for`: its `player` value when that is one player's
    name, else the speaker's; none for edit and stop, which name the copy they change."""
    if name in ("edit", "stop"):
        return None
    for who in (values.get("player"), player):
        if isinstance(who, str) and PLAYER.match(who):
            return who
    return None


def task_of(info: dict[str, Any]) -> str:
    """A running copy's task, when it is a task's name (not the bridge's `cast`); else ""."""
    task = str(info.get("task") or "").strip()
    return task if names.NAME.match(task) and task not in names.RESERVED else ""


def answered(v) -> str:
    """One orchestrator answer, for the log."""
    what = {NEW: v.name, FOLLOW_UP: v.task, EDIT: v.copy, STOP: ", ".join(v.copies)}.get(v.kind, "")
    return f"{v.kind}{' ' + what if what else ''}{': ' + short(v.brief, 160) if v.brief else ''}" \
           f"{' || reply: ' + v.reply if v.reply else ''}"


class Acts:
    def __init__(self, *, vfg: Vfg, later: Later, catalog: Catalog, planner: Planner, tasks: Tasks,
                 implementers, world, orchestrator, usage: Callable[[str, str, Any, str], None],
                 orchestrator_model: str, repo: Path, server: str, action_timeout_s: float = 20.0):
        self.vfg = vfg
        self.later = later
        self.catalog = catalog
        self.planner = planner
        self.tasks = tasks
        self.implementers = implementers
        self.world = world
        self.orchestrator = orchestrator
        self.usage = usage    # Brain.usage: (component, model, answer, ref)
        self.orchestrator_model = orchestrator_model
        self.repo, self.server = repo, server
        self.action_timeout_s = action_timeout_s

    # ------------------------------------------------------------- the decision

    async def run(self, decision, *, player: str, line: str, seen: Seen, ctx, head: str, ref: str,
                  report: bool = False) -> None:
        """Every part of `decision`, then its reply. `player` and `line` are whom a new task is for
        and the words that started it; `head` is the event as the orchestrator is told it; `ref`
        is `#<seq>` for a line, the task's name for a report (`report`), and labels the log, the
        usage rows and a report's reply."""
        actions = distinct([p for p in decision.parts if p.to not in (ORCHESTRATOR, TALK)])
        handed = [p for p in decision.parts if p.to == ORCHESTRATOR]
        others = [p for p in decision.parts if p.to != ORCHESTRATOR]
        turn = Turn()
        acting = (asyncio.create_task(self.actions(actions, player, seen, ref, line=line), name=f"actions {ref}")
                  if actions else None)
        orchestrating = (asyncio.create_task(self.orchestrate(turn, handed, others, player=player, line=line,
                                                              seen=seen, ctx=ctx, head=head, ref=ref, report=report),
                                             name=f"orchestrator {ref}") if handed else None)
        task = ref if report else ""
        try:
            outcomes = await acting if acting is not None else []
            if orchestrating is not None and not report:
                await turn.decided.wait()
                if orchestrating.done():
                    orchestrating.result()  # what it raised, raised here
                task = turn.task
            reply = self.reply_for(decision, outcomes, turn, handed, ref, replying=orchestrating is not None
                                   and not report)
            if reply.strip():
                await self.say(reply, task)
            if orchestrating is not None:
                await orchestrating
            for extra in turn.extras:
                await self.say(extra)
        except Exception:  # raised on, for the event's own log: the player hears so
            await self.say(BROKE, task)
            raise
        finally:  # cancelled: the brain stopped while it ran
            left = [t for t in (acting, orchestrating) if t is not None and not t.done()]
            for t in left:
                t.cancel()
            await asyncio.gather(*left, return_exceptions=True)

    def reply_for(self, decision, outcomes: list[Outcome], turn: Turn, handed: list, ref: str, *,
                  replying: bool) -> str:
        """The one reply: the first orchestrator call's when it wrote it (`replying`), else the
        brain's; a line written here when anything failed."""
        acted = all(o.ok or o.chosen for o in outcomes)  # the brain's reply already says what it dropped
        if not replying:
            if acted:
                return decision.reply
            reply = fallback(outcomes, [p.part for p in handed])
            log("reply", f"{ref} an action failed or was dropped: said {reply!r} instead of {decision.reply!r}")
            return reply
        if not turn.answered:
            reply = decision.reply if acted else fallback(outcomes, [])
            log("reply", f"{ref} the orchestrator gave no answer: said {reply!r}, not its own")
            return reply
        if acted and all(o.ok for o in turn.outcomes):
            return turn.reply or decision.reply
        reply = fallback(outcomes + turn.outcomes, turn.started)
        log("reply", f"{ref} an action, an edit or a stop failed or was dropped: said {reply!r} instead of "
                     f"{turn.reply!r}")
        return reply

    async def say(self, text: str, task: str = "") -> None:
        r = await self.vfg("say", text, *(["--task", task] if task else []), timeout=SAY_TIMEOUT_S)
        if r.code != 0:
            said = (r.stderr or r.stdout).strip()
            log("say", f"vfg say failed ({said[-200:] or r.code}): {text!r}")

    def post(self, kind: str, task: str, text: str) -> None:
        """An AI row: what the brain did, for the context. Nobody waits for it."""
        self.later("feed", "post", "AI", f"{kind} {task or '-'}", short(text, ROW_MAX))

    def fail(self, name: str, record: str) -> None:
        """A task that never got to its implementer, failed, with its `AI result` row."""
        if (self.tasks.get(name) or {}).get("status") == "open":
            self.tasks.close(name, "failed", record)
            self.post("result", name, record)
            log("task", record)

    # ------------------------------------------------------------- the orchestrator

    async def orchestrate(self, turn: Turn, handed: list, others: list, *, player: str, line: str, seen: Seen,
                          ctx, head: str, ref: str, report: bool) -> None:
        """One orchestrator call per orchestrator part, one after another, each answer carried out
        before the next call. `turn.decided` is set once the first is answered and its edit or
        stop done, or it failed, so the reply can go."""
        given: list[str] = []
        waiting: list[str] = []  # tasks opened whose implementer has not started yet
        try:
            for k, p in enumerate(handed):
                first = k == 0 and not report
                try:
                    answer = await self.orchestrator.answer(
                        ctx, head=head, parts=handed, k=k, given=list(given), others=others, replying=first,
                        tasks=self.tasks.all(), copies=self.world.copies())
                except LlmError as e:
                    log("orchestrator", f"{ref} part {k + 1} FAILED: {e}")
                    given.append("failed")
                    if first:
                        turn.decided.set()
                    elif not report:
                        turn.extras.append(LOST.format(part=short(p.part, PART_SHORT)))
                    continue
                self.usage("orchestrator", self.orchestrator_model, answer, ref)
                v = answer.value
                log("orchestrator", f"{ref} part {k + 1} {p.part[:80]!r} -> {answered(v)}")
                if v.kind == NEW:
                    await self.new(v, p, turn, given, waiting, player=player, line=line, first=first)
                elif v.kind == FOLLOW_UP:
                    await self.follow_up(v, p, turn, given, first=first)
                elif v.kind in (EDIT, STOP):
                    done = await (self.edit(v, seen, ref) if v.kind == EDIT else self.stop(v, seen, ref))
                    given.append(f"edit {v.copy}" if v.kind == EDIT else f"stop {', '.join(v.copies)}")
                    if first:
                        turn.outcomes = done
                        self.free(turn, v.reply)
                    elif not all(o.ok for o in done):
                        turn.extras.append(fallback([o for o in done if not o.ok], []))
                else:
                    given.append("reply only")
                    if first:
                        self.free(turn, v.reply)
                    elif v.reply.strip():
                        turn.extras.append(v.reply)
        finally:
            for name in waiting:  # cancelled before its implementer started
                self.fail(name, f"{name} failed: cut off, the brain stopped before its implementer started")
            turn.decided.set()

    @staticmethod
    def free(turn: Turn, reply: str) -> None:
        """The first call's answer is carried out as far as the reply waits for: it may go."""
        turn.answered, turn.reply = True, reply
        turn.decided.set()

    async def new(self, v, p, turn: Turn, given: list[str], waiting: list[str], *, player: str, line: str,
                  first: bool) -> None:
        """A new task: its name made free, its row opened and written through, its `AI task` row,
        the reply let go, then its implementer started, at once."""
        copies = self.world.copies()
        taken = (self.tasks.names() | set(self.catalog.names()) | names.script_names(self.repo, self.server)
                 | set(copies) | names.RESERVED)
        name = names.unique(v.name, taken)
        try:
            self.tasks.open(name, player, line, v.brief, v.description)
        except ValueError as e:
            log("task", f"no task opened for {p.part[:80]!r}: {e}")
            given.append("failed")
            if first:
                turn.decided.set()
            return
        waiting.append(name)
        self.post("task", name, f"new: {v.description}")
        turn.started.append(p.part)
        turn.task = turn.task or name
        given.append(f"new {name}")
        if first:
            self.free(turn, v.reply)
        await asyncio.sleep(0)  # the reply and the row's write go out first; nothing waits for them
        try:
            await self.implementers.start(name, v.brief)
        except Exception as e:  # noqa: BLE001 — the task fails, said so; the line's other parts go on
            waiting.remove(name)
            self.fail(name, f"{name} failed: the implementer could not start: {short(f'{type(e).__name__}: {e}', 160)}")
            return
        waiting.remove(name)
        log("task", f"{name} started for {player or 'nobody'}: {short(v.brief, 300)}")

    async def follow_up(self, v, p, turn: Turn, given: list[str], *, first: bool) -> None:
        """A follow-up: the task open again with its brief and description, its `AI task` row, the
        reply let go, then the brief sent to its implementer."""
        name = v.task
        if self.tasks.get(name) is None:
            log("task", f"{name} is not a task here: {p.part[:80]!r} starts nothing")
            given.append("failed")
            if first:
                turn.decided.set()
            return
        self.tasks.follow_up(name, v.brief, v.description)
        self.post("task", name, f"follow-up: {short(v.brief, BRIEF_SHORT)}")
        turn.started.append(p.part)
        turn.task = turn.task or name
        given.append(f"follow_up {name}")
        if first:
            self.free(turn, v.reply)
        await asyncio.sleep(0)
        row = self.tasks.get(name) or {}
        # its session, when the implementer gave one, and what it is now: a follow-up after a restart
        # resumes the session, or starts a new implementer on the description and the follow-up
        try:
            await self.implementers.follow_up(name, v.brief, session=row.get("session") or None,
                                              brief=str(row.get("description") or ""))
        except Exception as e:  # noqa: BLE001
            self.fail(name, f"{name} failed: the implementer could not start: {short(f'{type(e).__name__}: {e}', 160)}")
            return
        log("task", f"{name} follow-up: {short(v.brief, 300)}")

    async def edit(self, v, seen: Seen, ref: str) -> list[Outcome]:
        """The edit action on one copy, its values as the orchestrator's validator checked them;
        an `AI note` row either way."""
        copies = self.world.copies()
        copy, cls = v.copy, self.catalog.get("edit")
        task = task_of(copies.get(copy) or {})
        doing = f"change {copy}"
        if cls is None:
            o = Outcome(doing, False, "edit is no longer on the menu")
        else:
            try:
                values, notes = self.values("edit", cls, {"copy": copy, "values": v.edits()},
                                            dataclasses.replace(seen, copies=copies))
            except NotRunning as e:
                o = Outcome(doing, False, str(e), dropped=True)
            except ValueError as e:
                o = Outcome(doing, False, f"refused before it ran: {e}")
            else:
                ran = await execute("edit", cls, values, "", self.vfg, self.action_timeout_s, label=f" {ref} edit")
                o = Outcome(doing, ran.kind == "ok", told(ran, notes) if ran.kind == "ok" else ran.summary)
                if o.ok:
                    changed = " ".join(f"{k}={word(x)}" for k, x in values.items() if k != "copy")
                    self.post("note", task, f"edited {copy}: {changed}")
        if not o.ok:
            self.post("note", task, f"couldn't edit {copy}: {o.summary}")
        log("action", f"{ref} edit {copy} {'ok' if o.ok else 'failed'}: {o.summary[:200]}")
        self.world.refetch_running()
        return [o]

    async def stop(self, v, seen: Seen, ref: str) -> list[Outcome]:
        """The stop action on each copy, one after another; an `AI note` row each. The task's
        status is never changed by it: a done task whose copy is stopped stays done."""
        copies = self.world.copies()
        cls = self.catalog.get("stop")
        out = []
        for copy in v.copies:
            task = task_of(copies.get(copy) or {})
            doing = f"stop {copy}"
            if cls is None:
                o = Outcome(doing, False, "stop is no longer on the menu")
            elif copy not in copies:
                o = Outcome(doing, False, f"nothing called {copy} is running", dropped=True)
            else:
                ran = await execute("stop", cls, {"copy": copy}, "", self.vfg, self.action_timeout_s,
                                    label=f" {ref} stop")
                o = Outcome(doing, ran.kind == "ok", ran.summary)
            self.post("note", task, f"stopped {copy}" if o.ok else f"couldn't stop {copy}: {o.summary}")
            log("action", f"{ref} stop {copy} {'ok' if o.ok else 'failed'}: {o.summary[:200]}")
            out.append(o)
        self.world.refetch_running()
        return out

    # ------------------------------------------------------------- the actions

    async def actions(self, parts: list, player: str, seen: Seen, ref: str = "", *, line: str = "") -> list[Outcome]:
        """Each action part in order, with no task. `ref` (#<seq>, or the task's name for a
        report's parts) labels the log lines and the planner's Pick; `line` is what the player
        said, for a retry."""
        outcomes = []
        spent = Spent()
        for i, p in enumerate(parts):
            t0 = time.perf_counter()
            o = await self.action(p, ref, i, player, line, seen, spent)
            outcomes.append(o)
            state = "ok" if o.ok else "dropped" if o.dropped else "failed"
            self.post("action", "", f"{p.to}: {strip_end(p.instruction)} — {state}: {o.summary}")
            log("action", f"{ref} #{i + 1} {p.to} {state} in {time.perf_counter() - t0:.1f} s: {o.summary[:200]}")
            if p.to in ("stop", "edit") or (o.ok and getattr(self.catalog.get(p.to), "runs", "") == "until_stopped"):
                self.world.refetch_running()
        if spent.calls:
            log("plan", f"{ref} retries: {spent.calls} planner call(s), {spent.ms} ms, ${spent.cost:.6f}")
        return outcomes

    def values(self, name: str, cls, given: dict[str, Any], seen: Seen) -> tuple[dict[str, Any], list[str]]:
        """What `vfg action run <name>` is given, checked as vfg checks it; raises ValueError, and
        NotRunning for a copy that is not running."""
        places = None if seen.known is None else {n: {} for n in seen.known}
        if name in ("stop", "edit"):
            copy = str(given.get("copy") or "")
            if copy not in seen.copies:
                raise NotRunning(f"nothing called {copy or '(no name)'} is running")
            if name == "stop":
                return {"copy": copy}, []
            info = seen.copies[copy]
            copy_cls = self.catalog.get(str(info.get("action") or ""))
            if copy_cls is None:
                raise ValueError(f"{copy}'s action {info.get('action')} is not loaded")
            asked = given.get("values") if isinstance(given.get("values"), dict) else {}
            edit = check_edit(copy_cls, info.get("values") or {}, asked, places=places)
            return {"copy": copy, **{k: edit.values[k] for k in asked}}, edit.notes
        checked = check_values(cls, given, places=places)
        return {k: checked.values[k] for k in given}, checked.notes

    async def action(self, p, ref: str, i: int, player: str, line: str, seen: Seen, spent: Spent) -> Outcome:
        name, cls = p.to, self.catalog.get(p.to)
        if p.drop.strip():
            return Outcome(p.instruction, False, strip_end(p.drop), dropped=True, chosen=True)
        if cls is None:
            return Outcome(p.instruction, False, f"{name} is no longer on the menu")
        try:
            values, notes = self.values(name, cls, p.values(), seen)
        except NotRunning as e:
            return Outcome(p.instruction, False, str(e), dropped=True)
        except ValueError as e:
            return Outcome(p.instruction, False, f"refused before it ran: {e}")
        whom = for_whom(name, values, player)
        ran = await execute(name, cls, values, "", self.vfg, self.action_timeout_s, for_player=whom,
                            label=f" {ref} #{i + 1}")
        if ran.kind == "ok":
            return Outcome(p.instruction, True, told(ran, notes))
        if ran.kind == "refused" and (offline := offline_names(ran.facts)):
            return Outcome(p.instruction, False, f"{' and '.join(offline)} not online", dropped=True)
        fields = [k for k in command_fields(cls) if k in cls.takes]
        if ran.kind != "refused" or not fields:
            return Outcome(p.instruction, False, ran.summary)
        return await self.retry(p, name, cls, values, ran, fields, ref, i, player, line, seen, spent, whom)

    async def retry(self, p, name: str, cls, values: dict[str, Any], ran: Ran, fields: list[str], ref: str, i: int,
                    player: str, line: str, seen: Seen, spent: Spent, whom: str | None) -> Outcome:
        """The one retry of a refused command, from the action planner, with the reference of
        every command the refused one used in view."""
        refs = self.planner.refs
        if refs is None:
            return Outcome(p.instruction, False, ran.summary)
        already = [str(c) for c in ran.facts.get("ran") or []]
        took = [str(x) for x in (ran.facts.get("replies") or [])[:len(already)]]
        pick = Pick(ref, i, p.instruction, f"{player or 'someone'} said: {line}", seen.places, [], spent, refs,
                    already=list(already), known=seen.known, copies=seen.copies, running=seen.running,
                    catalog=self.catalog)
        checked = Checked()
        used, subs = refs.used([c for k in fields for c in (values.get(k) or [])])
        checked.names |= set(used)
        checked.subs |= set(subs)
        try:
            planned = await self.planner.plan(name, cls, pick, failure=ran.summary, last=values, checked=checked)
        except (LlmError, Unchecked) as e:
            log("plan", f"{ref} #{i + 1} the retry failed: {e}")
            return Outcome(p.instruction, False, ran.summary)
        if isinstance(planned, Final) or planned.action is None:
            why = (planned.drop or planned.hand_on).strip() or ran.summary
            return Outcome(p.instruction, False, strip_end(why), dropped=bool(planned.drop.strip()))
        try:
            again, notes = self.planner.values(name, cls, planned, pick)
        except ValueError as e:
            return Outcome(p.instruction, False, f"{ran.summary}; the retry was refused too: {e}")
        for k in fields:
            if isinstance(again.get(k), list):
                again[k] = without_repeats(again[k], already)
        if not any(again.get(k) for k in fields):  # the retry only repeated what took effect
            return Outcome(p.instruction, True, "; ".join(took) or "done")
        ran2 = await execute(name, cls, again, "", self.vfg, self.action_timeout_s, for_player=whom,
                             label=f" {ref} #{i + 1} retry")
        if ran2.kind == "ok":
            return Outcome(p.instruction, True, "; ".join([*took, told(ran2, notes)]))
        return Outcome(p.instruction, False, ran2.summary)
