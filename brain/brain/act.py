"""Running a decision: what one answer of the brain becomes. Code, no model, but for the one
retry of a refused command.

- Action parts run in order, each after the one before has finished, under the line's own task
  id (`vfg action run … --task T<n>`, executor.py), after their values are checked as vfg
  checks them (the saved places and the running copies included). A command Minecraft refuses
  gets ONE retry from the action planner, on VFG_PLAN_MODEL, written with the reference of
  every command the refused one used; nothing else is retried. Each part, run or dropped,
  becomes an `AI action` row.
- Implementer parts start at once, beside the actions: a new one gets its own T<n>, a
  tasks.json entry, an `AI task` row and Implementers.start; one naming a task is a follow-up,
  Implementers.follow_up and an `AI task` row, and that task is open again.
- The reply goes out through `vfg say … --task T<n>`: at once when the parts are only
  implementer and talk, after the actions otherwise. It never claims a success that failed:
  when an action failed, or was dropped here (a copy not running, a player offline), a short
  line written here goes out instead. A part the brain dropped itself keeps its reply, which
  was written knowing it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from vfg_actions import check_edit, check_values
from vfg_llm import LlmError

from .action_planner import Checked, Final, Pick, Planner, Spent, Unchecked
from .actions import Catalog, command_fields
from .call import CLARIFY, IMPLEMENTER, TALK
from .config import log
from .executor import Ran, execute
from .tasks import Tasks
from .vfg import Later, Vfg
from .world import Seen

SAY_TIMEOUT_S = 30
BROKE = "sorry, something broke on my side and that one didn't finish"
LINE_MAX = 200    # a chat line written here
ROW_MAX = 400     # an AI row's text
BRIEF_SHORT = 160  # a brief in an `AI task` row


@dataclass
class Outcome:
    """What one action part came to."""
    instruction: str
    ok: bool
    summary: str           # what ran (the executor's summary, with vfg's notes), or why not
    dropped: bool = False  # never run: something it needs is missing
    chosen: bool = False   # dropped by the brain itself, which knew it as it wrote its reply


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
    """The line said instead of the brain's reply when an action failed or was dropped: what was
    done, what was not and why, and what an implementer is on."""
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


class Acts:
    def __init__(self, *, vfg: Vfg, later: Later, catalog: Catalog, planner: Planner, tasks: Tasks,
                 implementers, world, action_timeout_s: float = 20.0):
        self.vfg = vfg
        self.later = later
        self.catalog = catalog
        self.planner = planner
        self.tasks = tasks
        self.implementers = implementers
        self.world = world
        self.action_timeout_s = action_timeout_s

    # ------------------------------------------------------------- the decision

    async def run(self, decision, *, player: str, line: str, seen: Seen) -> None:
        """Every part of `decision`, then its reply. `player` and `line` are what the tasks it
        opens are for."""
        actions = distinct([p for p in decision.parts if p.to not in (IMPLEMENTER, TALK, CLARIFY)])
        tid = self.tasks.open(player, line, "; ".join(p.instruction for p in actions)) if actions else ""
        handed = [p for p in decision.parts if p.to == IMPLEMENTER]
        task = tid
        try:
            ids = [await self.implementer(p, player, line) for p in handed]
            task = tid or next((i for i in ids if i), "")
            if not actions:
                if decision.reply.strip():
                    await self.say(decision.reply, task)
                return
            outcomes = await self.actions(actions, tid, player, line, seen)
            ok = all(o.ok or o.chosen for o in outcomes)  # the brain's reply already says what it dropped
            self.tasks.close(tid, "done" if any(o.ok for o in outcomes) else "failed",
                             short("; ".join(f"{'ok' if o.ok else 'failed'}: {o.summary}" for o in outcomes),
                                   ROW_MAX))
            reply = decision.reply if ok else fallback(outcomes, [p.part for p in handed])
            if not ok:
                log("reply", f"{tid} an action failed or was dropped: said {reply!r} instead of {decision.reply!r}")
            await self.say(reply, task)
        except Exception as e:  # raised on, for the event's own log: the task closes, the player hears so
            self.cut(tid, f"{tid} failed: it broke ({type(e).__name__}: {short(str(e), 120)})")
            await self.say(BROKE, task)
            raise
        finally:  # cancelled: the brain stopped while it ran
            self.cut(tid, f"{tid} failed: cut off, the brain stopped while it ran")

    def cut(self, tid: str, result: str) -> None:
        """A line's task that did not get to its end, failed, unless it was closed already."""
        if tid and (self.tasks.get(tid) or {}).get("status") == "open":
            self.tasks.close(tid, "failed", result)
            log("task", result)

    async def implementer(self, p, player: str, line: str) -> str:
        """Start a new implementer task, or follow one up; its id."""
        brief = p.instruction.strip()
        follow = p.task.strip()
        if follow and self.tasks.get(follow) is not None:
            known = self.tasks.get(follow)
            self.tasks.reopen(follow)
            self.post("task", follow, f"{follow} follow-up: {short(brief, BRIEF_SHORT)}")
            # its session, when the implementer gave one, and its brief: a follow-up after a restart
            # resumes the session, or starts a new implementer on the brief and the follow-up
            await self.implementers.follow_up(follow, brief, session=known.get("session") or None,
                                              brief=str(known.get("task") or ""))
            log("task", f"{follow} follow-up: {short(brief, 300)}")
            return follow
        if follow:
            log("task", f"{follow} is not a task here: {brief[:80]!r} starts a new one")
        tid = self.tasks.open(player, line, brief)
        self.post("task", tid, f"{tid}: {short(brief, BRIEF_SHORT)}")
        await self.implementers.start(tid, brief)
        log("task", f"{tid} started for {player or 'nobody'}: {short(brief, 300)}")
        return tid

    async def say(self, text: str, task: str = "") -> None:
        r = await self.vfg("say", text, *(["--task", task] if task else []), timeout=SAY_TIMEOUT_S)
        if r.code != 0:
            said = (r.stderr or r.stdout).strip()
            log("say", f"vfg say failed ({said[-200:] or r.code}): {text!r}")

    def post(self, kind: str, task: str, text: str) -> None:
        """An AI row: what the brain did, for the context. Nobody waits for it."""
        self.later("feed", "post", "AI", f"{kind} {task or '-'}", short(text, ROW_MAX))

    # ------------------------------------------------------------- the actions

    async def actions(self, parts: list, tid: str, player: str, line: str, seen: Seen) -> list[Outcome]:
        outcomes = []
        spent = Spent()
        for i, p in enumerate(parts):
            t0 = time.perf_counter()
            o = await self.action(p, tid, i, player, line, seen, spent)
            outcomes.append(o)
            state = "ok" if o.ok else "dropped" if o.dropped else "failed"
            self.post("action", tid, f"{p.to}: {strip_end(p.instruction)} — {state}: {o.summary}")
            log("action", f"{tid} #{i + 1} {p.to} {state} in {time.perf_counter() - t0:.1f} s: {o.summary[:200]}")
            if p.to in ("stop", "edit") or (o.ok and getattr(self.catalog.get(p.to), "runs", "") == "until_stopped"):
                self.world.refetch_running()
        if spent.calls:
            log("plan", f"{tid} retries: {spent.calls} planner call(s), {spent.ms} ms, ${spent.cost:.6f}")
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

    async def action(self, p, tid: str, i: int, player: str, line: str, seen: Seen, spent: Spent) -> Outcome:
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
        ran = await execute(name, cls, values, tid, self.vfg, self.action_timeout_s, label=f" #{i + 1}")
        if ran.kind == "ok":
            return Outcome(p.instruction, True, told(ran, notes))
        if ran.kind == "refused" and (offline := offline_names(ran.facts)):
            return Outcome(p.instruction, False, f"{' and '.join(offline)} not online", dropped=True)
        fields = [k for k in command_fields(cls) if k in cls.takes]
        if ran.kind != "refused" or not fields:
            return Outcome(p.instruction, False, ran.summary)
        return await self.retry(p, name, cls, values, ran, fields, tid, i, player, line, seen, spent)

    async def retry(self, p, name: str, cls, values: dict[str, Any], ran: Ran, fields: list[str], tid: str, i: int,
                    player: str, line: str, seen: Seen, spent: Spent) -> Outcome:
        """The one retry of a refused command, from the action planner, with the reference of
        every command the refused one used in view."""
        refs = self.planner.refs
        if refs is None:
            return Outcome(p.instruction, False, ran.summary)
        already = [str(c) for c in ran.facts.get("ran") or []]
        took = [str(x) for x in (ran.facts.get("replies") or [])[:len(already)]]
        pick = Pick(tid, i, p.instruction, f"{player or 'someone'} said: {line}", seen.places, [], spent, refs,
                    already=list(already), known=seen.known, copies=seen.copies, running=seen.running,
                    catalog=self.catalog)
        checked = Checked()
        used, subs = refs.used([c for k in fields for c in (values.get(k) or [])])
        checked.names |= set(used)
        checked.subs |= set(subs)
        try:
            planned = await self.planner.plan(name, cls, pick, failure=ran.summary, last=values, checked=checked)
        except (LlmError, Unchecked) as e:
            log("plan", f"{tid} #{i + 1} the retry failed: {e}")
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
        ran2 = await execute(name, cls, again, tid, self.vfg, self.action_timeout_s, label=f" #{i + 1} retry")
        if ran2.kind == "ok":
            return Outcome(p.instruction, True, "; ".join([*took, told(ran2, notes)]))
        return Outcome(p.instruction, False, ran2.summary)
