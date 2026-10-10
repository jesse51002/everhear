"""One task, end to end: the actions reloaded where a file changed, the saved places and the
running copies, the selector, then each pick in order through the action planner and the
executor, every report out as it finishes, and the rest to the batch.

Every task ends one of three ways, and the conversation always hears which:
- done: the last action's report closes it (`done`), naming anything dropped;
- handed on: the pick that could not be done, every pick after it and the selector's
  orchestrator part go to the batch as ONE task line, and the orchestrator closes it;
- failed: every pick was dropped, with the reasons.

A task's actions run strictly in order, each after the one before has finished. Each task
is its own asyncio task, so tasks run side by side. What was done is in state.json at every
step, so a crash or a stop hands the rest on with it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from vfg_llm import LlmError

from . import running
from .action_planner import Checked, Pick, Planner, Spent, Unchecked
from .actions import Catalog, command_fields, refresh
from .batch import Batch
from .config import Config, log
from .executor import Ran, execute
from .inbox import State
from .reply import Reply
from .selector import Selector, describe
from .vfg import Vfg, why


@dataclass
class Done:
    report: str


@dataclass
class Dropped:
    why: str


@dataclass
class HandOn:
    note: str  # for the orchestrator's context: "failed <instruction> because <why>"
    stopped: bool = False  # the dispatcher stopped before it ran: the note names every pick not done


def report(say: str, summary: str) -> str:
    """The planner's sentence plus the executor's facts."""
    say, summary = say.strip().rstrip("."), summary.strip()
    if say and summary:
        return f"{say} — {summary}"
    return say or summary or "done"


def told(ran: Ran, notes: list[str] = ()) -> str:
    """What the executor says of a run: its summary; the copy's name when it keeps running and
    the summary does not say it, so the conversation can ask to stop or change it; and the
    numbers clamped to their limits."""
    text = ran.summary
    if ran.running and ran.copy and ran.copy not in text:
        text += f" (running as {ran.copy})"
    notes = [n for n in notes if n not in text]
    return text + (f" ({'; '.join(notes)})" if notes else "")


def since(at: str) -> str:
    """`, 4.2 s since it was sent`, from the task line's time."""
    try:
        sent = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f", {(datetime.now(UTC) - sent).total_seconds():.1f} s since it was sent"


def offline_names(facts: dict[str, Any]) -> list[str]:
    """The players a handler found offline (its result's `offline`), else []."""
    names = facts.get("offline")
    return [str(n) for n in names if str(n).strip()] if isinstance(names, list) else []


def without_repeats(commands: list[str], already: list[str]) -> list[str]:
    """A retry's commands without the leading ones that already ran and took effect."""
    left = list(commands)
    while left and left[0] in already:
        left.pop(0)
    return left


def places_named(lines: list[str]) -> set[str]:
    """The saved places' names, from `vfg place list`'s lines (`castle 197 68 251 overworld (T23)`)."""
    return {x.split()[0] for x in lines if x.split()}


def strip_end(text: str) -> str:
    return text.strip().rstrip(".").strip()


class Dispatcher:
    def __init__(self, cfg: Config, *, catalog: Catalog, refs, vfg: Vfg, ask, state: State,
                 reply: Reply, batch: Batch, hidden: frozenset[str] = frozenset()):
        self.cfg = cfg
        self.catalog = catalog  # kept in memory; refreshed before each ask
        self.ask = ask
        self.vfg = vfg
        self.state = state
        self.reply = reply
        self.batch = batch
        self.selector = Selector(catalog, ask, cfg.model) if catalog.names() else None
        self.planner = Planner(ask, cfg.model, refs, hidden=hidden)
        self.stopping = False
        self.running: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------- lifecycle

    def start(self, task_id: str) -> asyncio.Task:
        t = asyncio.create_task(self.run(task_id), name=f"task {task_id}")
        self.running[task_id] = t
        t.add_done_callback(lambda _: self.running.pop(task_id, None))
        return t

    def recover(self, why_: str = "the dispatcher restarted while it ran") -> None:
        """Hand on every in-flight task in state.json, with what was done."""
        for rec in list(self.state.tasks.values()):
            self.hand(rec, rec["next"], self._interrupted(rec, why_))
        self.state.save()

    async def stop(self, grace_s: float) -> None:
        """Start no new step; let what runs finish for up to `grace_s`; hand the rest on."""
        self.stopping = True
        tasks = list(self.running.values())
        if tasks:
            log("stop", f"waiting up to {grace_s:g} s for {len(tasks)} running task(s)")
            _, pending = await asyncio.wait(tasks, timeout=grace_s)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self.recover("the dispatcher stopped while it ran")

    def _interrupted(self, rec: dict[str, Any], why_: str) -> str:
        picks = rec.get("picks")
        if picks and rec["next"] < len(picks):
            note = f"interrupted {strip_end(picks[rec['next']]['instruction'])}: {why_}; it may or may not have happened"
            later = [strip_end(p["instruction"]) for p in picks[rec["next"] + 1:]]
            return note + (f"; not started: {'; '.join(later)}" if later else "")
        return f"interrupted: {why_}"

    @staticmethod
    def _not_started(rec: dict[str, Any], at: int) -> str:
        """The note for a stop before pick `at` ran: every pick not done, by its instruction."""
        left = [strip_end(p["instruction"]) for p in (rec.get("picks") or [])[at:]]
        return f"not started: {'; '.join(left)} (the dispatcher stopped)"

    # ------------------------------------------------------------- a task

    def reload(self) -> None:
        """Before each ask: load again every action file that changed, and rebuild the
        selector's menu when anything did."""
        try:
            changed = refresh(self.catalog)
        except Exception as e:  # noqa: BLE001 — the actions as they were are still good
            log("actions", f"reloading failed ({type(e).__name__}: {e}); the menu is as it was")
            return
        if changed or self.selector is None:
            if not self.catalog.names():
                self.selector = None
            elif self.selector is None:
                self.selector = Selector(self.catalog, self.ask, self.cfg.model)
            else:
                self.selector.update(self.catalog)

    async def places(self, task_id: str) -> tuple[str, set[str] | None]:
        """The `SAVED PLACES: …` line, from one `vfg place list`, and the places' names (None
        when the list could not be read)."""
        t0 = time.perf_counter()
        r = await self.vfg("place", "list", timeout=10)
        ms = int((time.perf_counter() - t0) * 1000)
        if r.code != 0:
            log("places", f"{task_id} vfg place list failed ({why(r)}), {ms} ms")
            return "SAVED PLACES: unknown (the list could not be read)", None
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        if not lines or lines == ["none"]:
            lines = []
        log("places", f"{task_id} {len(lines)} saved, {ms} ms")
        return "SAVED PLACES: " + ("; ".join(lines) or "none"), places_named(lines)

    async def run(self, task_id: str) -> None:
        rec = self.state.tasks[task_id]
        t0 = time.perf_counter()
        spent = Spent()
        try:
            await self._run(rec, spent)
        finally:
            if task_id not in self.state.tasks:
                log("task", f"{task_id} finished in {time.perf_counter() - t0:.1f} s"
                            f"{since(rec['at'])}; {spent.calls} model call(s), {spent.ms} ms, ${spent.cost:.6f}")

    async def _run(self, rec: dict[str, Any], spent: Spent) -> None:
        tid = rec["id"]
        if self.stopping:
            return self.hand(rec, 0, "not started: the dispatcher stopped")
        if Selector.whole(rec["task"]):  # Fix …: new code, nothing to fetch for it
            log("select", f"{tid} is a fix: the whole task is the orchestrator's")
            return self.hand(rec, 0, "")
        self.reload()
        if self.selector is None:
            return self.hand(rec, 0, "no actions are loaded")
        places, known = await self.places(tid)
        copies = running.read(self.cfg.running_file)
        lines = running.block(copies, self.catalog)
        if self.stopping:
            return self.hand(rec, 0, "not started: the dispatcher stopped")
        # The actions as the menu has them, for this task: another task may reload them while it runs.
        classes = {n: self.catalog.get(n) for n in self.catalog.names()}
        try:
            answer = await self.selector.select(rec["task"], rec["context"], places, lines)
        except LlmError as e:
            log("select", f"{tid} FAILED: {e}")
            return self.hand(rec, 0, f"the complexity selector failed: {e}")
        spent.add(answer)
        sel = answer.value
        log("select", f"{tid} {answer.ms} ms ${answer.cost:.6f}: {describe(sel)}")
        rec["picks"] = [{"action": p.action, "instruction": p.instruction.strip()} for p in sel.picks]
        rec["orchestrator"] = sel.orchestrator.strip()
        self.state.save()
        if not rec["picks"]:
            return self.hand(rec, 0, "")

        reports: list[str] = []
        refs = self.planner.refs  # what this task has, though they may arrive while it runs
        for i, pick in enumerate(rec["picks"]):
            if self.stopping:
                return self.hand(rec, i, self._not_started(rec, i))
            p = Pick(tid, i, pick["instruction"], rec["context"], places, list(reports), spent, refs,
                     known=known, copies=copies, running=lines, catalog=self.catalog)
            result = await self.run_pick(pick["action"], classes[pick["action"]], p)
            if isinstance(result, HandOn):
                return self.hand(rec, i, self._not_started(rec, i) if result.stopped else result.note)
            last = i == len(rec["picks"]) - 1 and not rec["orchestrator"]
            rec["next"] = i + 1
            if isinstance(result, Done):
                rec["done"].append({"instruction": pick["instruction"], "report": result.report})
                reports.append(result.report)
                if last:
                    return self.close(rec, result)
                self.reply.send(tid, "progress", result.report)
            else:
                rec["dropped"].append({"instruction": pick["instruction"], "why": result.why})
                if last:
                    return self.close(rec, result)
                self.reply.send(tid, "progress", f"dropped: {strip_end(pick['instruction'])} — {result.why}")
            self.state.save()
        # every pick is done or dropped; what is left is the orchestrator's part
        return self.hand(rec, len(rec["picks"]), "")

    async def run_pick(self, name: str, cls, p: Pick) -> Done | Dropped | HandOn:
        """The failure loop: plan, check, run; when Minecraft, the script or the values' check
        refuses, the planner retries (at most `retries` times), hands on or drops. A timeout
        (it may have happened) and an action that could not be run at all are handed on at
        once, with no planner call. An answer that will not validate, or a failed call, is a
        hand-on."""
        fields = [k for k in command_fields(cls) if k in cls.takes]
        if p.refs is None and fields:
            return HandOn(f"not done {strip_end(p.instruction)}: the command references are not loaded, "
                          "so no command can be checked")
        failure: str | None = None
        last: dict[str, Any] | None = None
        tries = 0
        checked = Checked()
        took: list[str] = []  # the replies of the commands in p.already, for the report
        while True:
            final = failure is not None and tries > self.cfg.retries
            try:
                planned = await self.planner.plan(name, cls, p, failure=failure, last=last, final=final,
                                                  checked=checked)
            except LlmError as e:
                log("plan", f"{p.task_id} #{p.index + 1} FAILED: {e}")
                return HandOn(self._note(p, failure, f"the action planner failed: {e}"))
            except Unchecked as e:
                failure, tries = str(e), tries + 1
                log("plan", f"{p.task_id} #{p.index + 1} {e}")
                continue
            if planned.drop.strip():
                return Dropped(strip_end(planned.drop))
            if planned.hand_on.strip():
                return HandOn(self._note(p, failure, f"the action planner handed it on: {strip_end(planned.hand_on)}"))
            if self.stopping:
                return HandOn("", stopped=True)
            try:
                values, notes = self.planner.values(name, cls, planned, p)
            except ValueError as e:  # what vfg would refuse: back to the planner, as a refusal is
                failure, last, tries = str(e), planned.action.model_dump(mode="json", exclude_none=True), tries + 1
                log("plan", f"{p.task_id} #{p.index + 1} {name} refused before it ran: {failure[:300]}")
                continue
            if p.already:
                for k in fields:
                    if isinstance(values.get(k), list):
                        left = without_repeats(values[k], p.already)
                        if len(left) < len(values[k]):
                            log("plan", f"{p.task_id} #{p.index + 1} dropped {len(values[k]) - len(left)} "
                                        "command(s) that already ran")
                        values[k] = left
                if fields and not any(values.get(k) for k in fields):  # the retry only repeated what took effect
                    return Done(report(planned.say or p.instruction, "; ".join(took) or "already done"))
            ran = await execute(name, cls, values, p.task_id, self.vfg, self.cfg.action_timeout_s,
                                label=f" #{p.index + 1}")
            tries += 1
            if ran.kind == "ok":
                return Done(report(planned.say or p.instruction, "; ".join([*took, told(ran, notes)])))
            if ran.kind == "refused" and (offline := offline_names(ran.facts)):
                return Dropped(f"{' and '.join(offline)} not online")  # nothing can fix it: no planner call
            if ran.kind != "refused":  # timeout and partial may have happened; not_run no retry can fix
                note = (f"not done {strip_end(p.instruction)}: {ran.summary}" if ran.kind == "not_run"
                        else f"{strip_end(p.instruction)}: {ran.summary}")
                return HandOn(note + self._already(p))
            ran_now = [str(c) for c in ran.facts.get("ran") or []]
            p.already += ran_now
            took += [str(x) for x in (ran.facts.get("replies") or [])[:len(ran_now)]]
            failure, last = ran.summary, values

    @classmethod
    def _note(cls, p: Pick, failure: str | None, why_: str) -> str:
        what = strip_end(p.instruction)
        if failure:
            return f"failed {what} because {strip_end(failure)} ({why_})" + cls._already(p)
        return f"not done {what}: {why_}" + cls._already(p)

    @staticmethod
    def _already(p: Pick) -> str:
        """For a hand-on note: the commands of this pick that already took effect, not to redo."""
        return f"; these already ran and took effect, do not redo them: {'; '.join(p.already)}" if p.already else ""

    # ------------------------------------------------------------- endings

    def close(self, rec: dict[str, Any], last: Done | Dropped) -> None:
        """Every pick done or dropped and nothing for the orchestrator: the closing answer."""
        dropped = "; ".join(f"{strip_end(d['instruction'])} — {d['why']}" for d in rec["dropped"])
        if not rec["done"]:
            status, text = "failed", f"dropped: {dropped}"
        elif isinstance(last, Done):
            status, text = "done", last.report + (f"; dropped: {dropped}" if dropped else "")
        else:
            done = "; ".join(strip_end(d["instruction"]) for d in rec["done"])
            status, text = "done", f"done: {done}; dropped: {dropped}"
        del self.state.tasks[rec["id"]]
        self.reply.send(rec["id"], status, text)
        self.state.save()
        log("task", f"{rec['id']} {status}: {text[:200]}")

    def hand(self, rec: dict[str, Any], at: int, note: str) -> None:
        """Hand on pick `at`, every pick after it and the orchestrator part, as ONE task line."""
        picks = rec.get("picks") or []
        left = [strip_end(p["instruction"]) for p in picks[at:]]
        if rec.get("orchestrator"):
            left.append(strip_end(rec["orchestrator"]))
        happened = bool(rec["done"] or rec["dropped"])
        task = "; then ".join(left) if happened and left else rec["task"]
        notes = [f"done {d['report']}" for d in rec["done"]]
        notes += [f"dropped {strip_end(d['instruction'])} because {d['why']}" for d in rec["dropped"]]
        if note:
            notes.append(note)
        context = rec["context"]
        if notes:
            context = (context + "\n\n" if context else "") + "Dispatcher: " + "; ".join(notes)
        line = {"id": rec["id"], "at": rec["at"], "task": task, "context": context}
        self.state.tasks.pop(rec["id"], None)
        self.batch.add(line)
        self.reply.send(rec["id"], "progress", f"handed on to the orchestrator: {task}")
        self.state.save()
        log("task", f"{rec['id']} handed on: {task[:200]}" + (f" ({note[:200]})" if note else ""))

    def expired(self, line: dict[str, Any], minutes: int) -> None:
        """A task that waited too long is never run. The caller saves."""
        self.reply.send(line["id"], "failed", f"Expired: it waited {minutes} minutes for the dispatcher.")

    def refused(self, line: dict[str, Any], reason: str) -> None:
        """`vfg orch hand` will never take this line: the conversation is told."""
        self.reply.send(line["id"], "failed", f"could not hand this to the orchestrator: {reason}")
