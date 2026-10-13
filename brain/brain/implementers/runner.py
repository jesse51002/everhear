"""The implementers: one run per task, in the harness the settings name (VFG_IMPL_HARNESS: Claude
Code by default), which code starts from the brief the brain wrote. A task is known by its name
(Prototype_Named_Tasks), which the script, the action and the copy its implementer makes take too.
The implementer is the main agent, with nothing around it: its system prompt is
prompts/implementer.md and `vfg prefix`, its brief is the brain's with the task's name, the saved
places and the EXAMPLES block (brief.py), and the guard (guard.py) holds every tool call to its
rules and records what it makes. The runner knows the harness only through harness/base.py's
interface (Prototype_Implementer_Harness): it starts or resumes a session, reads its events, and
prices each model request itself (usage.py).

    implementers = Implementers(ImplementerConfig.load(), on_report)
    await implementers.start("galick_gun_styrk", brief)          # at once; later on_report(…)
    await implementers.follow_up("galick_gun_styrk", "bigger")   # resumes its session: another on_report
    # on_session("galick_gun_styrk", session), when given, as soon as the harness names the session
    stopped = await implementers.close(5)            # the names still running; the brain fails them

Its last message is its report, which goes to on_report with "failed" for an error, a crash or a
stop at its limit, else the report's first word (Done, Failed, or Cancelled, which is "done"). The
report's last line, DESCRIPTION: what the thing is now, is taken off and given on its own
(split_description), with what the task made (guard.made_now).

A follow-up to a task whose run is live goes into that run as a MESSAGE (Run.send), read at its next
safe point, and the run's one report covers it; one that comes after the run has ended resumes its
session, as does one queued while a run reports (parts of one line queued together make one run).
"Never mind" is a message too (CANCEL): the implementer stops and puts back, and code puts back the
rest from the run's snapshot (putback.py), which is kept until the task's next real run, so a cancel
after the run ended works against it. A run near its turn cap or its time limit is warned (LIMIT)
and reports what it tried; one that still reaches the limit is stopped, put back by code, and
reported from what the guard saw. Each run writes its usage with `vfg usage add`, its ref the task's
name, off the critical path, after its report has gone: one row, the sum of its requests, each
priced at its own size and time (config.PRICES), and each request's size logged as it happens."""
import asyncio
import hashlib
import os
import re
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from . import brief as briefing
from . import harness as harnesses
from . import prefix as implementer_prefix
from . import putback
from . import usage
from .config import ImplementerConfig, js_num, log
from .guard import MADE_KINDS, GuardContext, brief as short, made_now, running_copies
from .harness.base import (ERROR, STOPPED, TURNS, Compacted, Done, Harness, Request, Run, Session, Text, ToolCall,
                           ToolResult)
from .vfg import VfgRunner, vfg as vfg_runner, why

__all__ = ["Implementers", "ImplementerConfig", "Report", "split_description"]

# (task name, "done" or "failed", the report without its DESCRIPTION line, the description or "",
#  what it made {"scripts", "actions", "presets", "copies"})
Report = Callable[[str, str, str, str, dict[str, list[str]]], Awaitable[None]]
# (task name, the implementer's session id): the brain keeps it, so a follow-up after a restart resumes it
OnSession = Callable[[str, str], None]

# A close() waits this long at most for a cancelled run to end.
INTERRUPT_TIMEOUT_S = 5.0

# What code writes into a live run. A follow-up goes in as MESSAGE + its text; a cancel, the brain's
# CANCEL, as it is (known by its start); the warning near a run's limit, LIMIT.
MESSAGE = "MESSAGE from the brain: "
CANCEL = ("CANCELLED: the player said never mind. Stop building and put everything back the way it was before the "
          "run this cancels, then report Cancelled: and what you put back.")
CANCEL_START = "CANCELLED:"
LIMIT = ("LIMIT: you are about to run out of turns and time. Stop building now. Put everything back the way it was "
         "before this run, then report Failed: what you tried, why it did not work, and what might.")
# on_report's status for a report that starts Cancelled: (decided with Brain_Loop: the tasks table
# has open, done and failed only).
CANCELLED_STATUS = "done"
# The warning goes this many turns before the turn cap, or this long before the time limit (at most
# a quarter of it); a stopped run's report names its last commands, this many.
WARN_TURNS = 5
WARN_S = 60.0
LAST_COMMANDS = 5


def is_cancel(text: str) -> bool:
    return text.lstrip().startswith(CANCEL_START)


def system_file(cfg: ImplementerConfig, system: str) -> str:
    """The system prompt in a file of the brain's folder, named by its hash, written once: about
    180K characters with `vfg prefix`, too long for one argument of a command line (Linux takes at
    most 128 KiB in one), so it goes to the harness as a file. The same bytes always make the same
    file, so every implementer's prompt starts the same."""
    data = system.encode("utf-8")
    path = os.path.join(cfg.brain_dir, "implementer-system-%s.md" % hashlib.sha256(data).hexdigest()[:12])
    if not os.path.exists(path):
        os.makedirs(cfg.brain_dir, mode=0o700, exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def fill(rules_text: str, cfg: ImplementerConfig) -> str:
    return rules_text.replace("%REPO%", cfg.repo).replace("%SERVER%", cfg.server)


def follow_up_prompt(name: str, text: str) -> str:
    return ("FOLLOW-UP to %s, from the brain: %s\nDo it as before, check it once, and end with a new report, its "
            "last line DESCRIPTION: what %s is now." % (name, text.strip(), name))


def cancel_prompt(name: str, text: str) -> str:
    """A cancel after the run ended: its words as they are, and the report's last line."""
    return "%s\nEnd with your report, its last line DESCRIPTION: what %s is now." % (text.strip(), name)


# The report's DESCRIPTION line, however the model dresses it: "DESCRIPTION:", "**Description:**",
# "- _description_:".
_DESCRIPTION = re.compile(r"^[\s>*_`#-]*description[*_`]*\s*:", re.I)
# The description the brain keeps, at most.
DESCRIPTION_MAX = 600


def split_description(text: str) -> tuple[str, str]:
    """(the report, the description): the description is all after the colon of the report's last
    DESCRIPTION line, to the end, its whitespace collapsed and its markdown stripped, cut to 600
    characters; the report is what comes before that line. With no such line, (text, "")."""
    lines = text.split("\n")
    at = next((i for i in range(len(lines) - 1, -1, -1) if _DESCRIPTION.match(lines[i])), None)
    if at is None:
        return text, ""
    first = lines[at][_DESCRIPTION.match(lines[at]).end():]
    description = " ".join(" ".join([first] + lines[at + 1:]).split())
    while description != description.strip("*_`").strip():
        description = description.strip("*_`").strip()
    report = "\n".join(lines[:at]).rstrip()
    return report or "Done: the implementer ended without a report.", description[:DESCRIPTION_MAX].rstrip()


_FIRST_WORD = re.compile(r"\W*(done|failed|cancelled)\b", re.I)
_STATUS = {"done": "done", "failed": "failed", "cancelled": CANCELLED_STATUS}


def first_word(text: str) -> str | None:
    """The report's status word: its first word, else the first line's that starts with Done,
    Failed or Cancelled (a report may open with a line of what it put back); None for neither."""
    for line in [text] + text.split("\n")[1:]:
        m = _FIRST_WORD.match(line)
        if m:
            return _STATUS[m.group(1).lower()]
    return None


# What a run that ended on a summary of the conversation is resumed with, once, for its report.
RECOVER = ("Your last message summed up the conversation. Now write your report to the brain: the first word Done, "
           "Failed or Cancelled, the DONE WHEN items one per line, then DESCRIPTION:")

# A summary of the conversation in the shape of Claude Code's compaction prompt, which a model may
# write as its last message unasked (DeepSeek did, on T216 of six4, at 143K tokens, with no
# compaction): never a report.
_SUMMARY = re.compile(r"^\s*<(analysis|summary)>", re.I)


def is_summary(text: str) -> bool:
    return bool(_SUMMARY.match(text or "")) or ("<analysis>" in (text or "") and "<summary>" in (text or ""))


def outcome(done: Done | None, failed: str | None, max_turns: int) -> tuple[str, str]:
    """(status, report) for one run: failed on an error, its turns used up, a stop by the harness,
    a crash or the time limit; else the report's first word, Done, Failed or Cancelled (done), and
    done when it is none of them."""
    if failed:
        return "failed", "Failed: %s." % failed
    if done is None:
        return "failed", "Failed: the implementer ended without a report."
    text = (done.text or "").strip()
    if done.ended == TURNS:
        return "failed", "Failed: the implementer used its %d turns.%s" % (max_turns, " " + text if text else "")
    if done.ended == STOPPED:
        return "failed", "Failed: %s." % (text or "the harness stopped it")
    if done.ended == ERROR:
        return "failed", "Failed: %s" % (text or "; ".join(done.errors) or "it ended in an error")
    if not text:
        return "done", "Done: the implementer ended without a report."
    return first_word(text) or "done", text


@dataclass
class _Task:
    name: str
    guard: GuardContext
    brief: str = ""
    # The implementer's session, once the harness gave it: a follow-up resumes it.
    session: str | None = None
    # ("start", brief), ("follow_up", text) and ("cancel", text), run one after another by the
    # task's worker.
    jobs: deque = field(default_factory=deque)
    worker: asyncio.Task | None = None
    # A run is under way: set as a job starts, cleared as it reports.
    busy: bool = False
    # The job's run is being made (its brief, its start): a message waits in `early` meanwhile.
    starting: bool = False
    early: list = field(default_factory=list)  # (text, is a cancel)
    # The harness's run while it is live, and what the runner keeps of it.
    run: Run | None = None
    state: "_RunState | None" = None
    # The last real run's snapshot, kept until the task's next real run: a cancel puts back from it.
    snapshot: putback.Snapshot | None = None


@dataclass
class _RunState:
    """What the runner keeps of one run while it is live."""
    kind: str
    cancel: bool = False  # it is a cancel, or a cancel went into it
    warned: str | None = None  # "turns" or "time", once warned near its limit
    turns: int = 0  # its model requests
    messages: int = 0  # messages that went into it, LIMIT among them
    sent: list = field(default_factory=list)  # (as sent, the brain's text, is a cancel): its follow-ups
    calls: deque = field(default_factory=lambda: deque(maxlen=LAST_COMMANDS))  # (id, tool, input)
    results: dict = field(default_factory=dict)  # a call's id -> (error, text)
    made_before: dict = field(default_factory=dict)  # the task's guard.made as the run started
    last_report: str = ""  # its last text that reads as a report (Done, Failed or Cancelled), not a summary


class Implementers:
    def __init__(self, cfg: ImplementerConfig, on_report: Report, *, on_session: OnSession | None = None,
                 harness: Harness | None = None, vfg: VfgRunner | None = None):
        """`on_session` is told a task's session as soon as the harness gives it; `harness` is the
        adapter (tests pass a fake), else the one VFG_IMPL_HARNESS names, made at the first run;
        `vfg` runs vfg, as vfg.vfg does."""
        self.cfg = cfg
        self.on_report = on_report
        self.on_session = on_session
        self.harness = harness
        self.vfg = vfg or vfg_runner
        log("implementer", "harness %s, provider %s, model %s%s" % (
            harness.name if harness is not None else cfg.harness, cfg.provider, cfg.model,
            ", at " + cfg.base_url if cfg.base_url else ", on the owner's login"))
        with open(cfg.prompt, encoding="utf-8") as f:
            self._rules = fill(f.read(), cfg)
        # `vfg prefix`, read at the first start and kept, so every implementer's system prompt is
        # the same bytes; tried again at the next start when it failed.
        self._prefix = ""
        self._prefix_lock = asyncio.Lock()
        self._running_copies = running_copies(cfg.home, self.vfg)
        self._tasks: dict[str, _Task] = {}
        self._background: set[asyncio.Task] = set()
        self._closing = False

    # ------------------------------------------------------------------ the brain's calls

    async def start(self, name: str, brief: str) -> None:
        """Starts the implementer of the task `name` and returns at once; on_report follows when it
        ends. A task already started takes this as a follow-up."""
        t = self._tasks.get(name)
        if t is not None:
            log("implementer", "%s is already started: this brief goes to it as a follow-up" % name)
            await self.follow_up(name, brief)
            return
        t = self._tasks[name] = _Task(name, self._guard(name), brief=brief)
        t.jobs.append(("start", brief))
        self._kick(t)

    async def follow_up(self, name: str, text: str, session: str | None = None, brief: str = "") -> None:
        """To the task's implementer. While its run is live (or starting), the text goes into that
        run as a message, and the run's one report covers it; else its session is resumed with the
        text, ending in another on_report. A text that starts CANCELLED: is a cancel (CANCEL).
        `session` and `brief` are for a task this process did not start (the brain keeps both in the
        task's row): its session resumed; with no session at all, a new implementer starts on
        `brief`, what the task is, and the text."""
        t = self._tasks.get(name)
        if t is None:
            t = self._tasks[name] = _Task(name, self._guard(name), brief=brief, session=session)
        else:
            t.session = t.session or session
            t.brief = t.brief or brief
        cancel = is_cancel(text)
        if not self._closing:
            if t.starting or (not t.busy and t.jobs and t.jobs[-1][0] == "start"):
                t.early.append((text, cancel))  # into the run about to start
                log("implementer", "%s: a %s waits for its run to start" % (name, "cancel" if cancel else "message"))
                return
            if t.run is not None and await self._send(t, t.run, text, cancel):
                return
        self._queue(t, text, cancel)
        self._kick(t)

    def running(self) -> list[str]:
        """The tasks whose implementer runs, or has a follow-up waiting. While a run reports, its
        task is here only when a follow-up to it waits, which runs next."""
        return [t.name for t in self._tasks.values() if t.worker is not None and (t.busy or t.jobs)]

    def session(self, name: str) -> str | None:
        """The task's implementer session, for the task's row; None until the harness gave one."""
        t = self._tasks.get(name)
        return t.session if t else None

    async def close(self, grace_s: float) -> list[str]:
        """Stops every implementer: interrupted, given `grace_s` to end, then cancelled. Follow-ups
        still waiting are dropped, and nothing more is reported. Returns the names it stopped."""
        self._closing = True
        live = [t for t in self._tasks.values() if t.worker is not None]
        for t in live:
            t.jobs.clear()
            t.early.clear()
        stops = [t for t in live if t.run is not None]
        for t, r in zip(stops, await asyncio.gather(*(t.run.stop() for t in stops), return_exceptions=True)):
            if isinstance(r, BaseException):
                log("implementer", "%s: stop: %s: %s" % (t.name, type(r).__name__, r))
        workers = [t.worker for t in live if t.worker is not None]
        if workers:
            _, late = await asyncio.wait(workers, timeout=grace_s)
            for w in late:
                w.cancel()
            if late:
                await asyncio.wait(late, timeout=INTERRUPT_TIMEOUT_S)
        if self._background:  # usage rows still going out
            await asyncio.wait(set(self._background), timeout=grace_s)
        if self.harness is not None:
            try:
                await self.harness.close()
            except Exception as e:
                log("implementer", "the harness did not close: %s: %s" % (type(e).__name__, e))
        names = [t.name for t in live]
        log("implementer", "closed: %s" % (", ".join(names) or "nothing was running"))
        return names

    # ------------------------------------------------------------------ one task's jobs

    def _guard(self, name: str) -> GuardContext:
        """The task's guard, kept with it: what it made over every run, and its own script, the one
        named as the task, its own to change from the start (GuardContext.own)."""
        return GuardContext(repo=self.cfg.repo, server=self.cfg.server, task=name, running=self._running_copies,
                            test_server=bool(self.cfg.test_box))

    def _kick(self, t: _Task) -> None:
        if self._closing:
            log("implementer", "%s: closing, so its jobs are dropped" % t.name)
            t.jobs.clear()
            return
        if t.worker is None:
            t.worker = asyncio.get_running_loop().create_task(self._work(t))

    def _queue(self, t: _Task, text: str, cancel: bool) -> None:
        """A follow-up or a cancel for after the task's run: a job. Parts of one line queued behind a
        run go in one job, so they make one run; a cancel takes the queued follow-ups with it, and a
        task that never started is reported cancelled at once."""
        if cancel:
            if any(kind == "start" for kind, _ in t.jobs):
                t.jobs.clear()
                log("implementer", "%s: cancelled before it started" % t.name)
                self._spawn(self._report(t, CANCELLED_STATUS, "Cancelled: it had not started; nothing was made."))
                return
            t.jobs = deque(j for j in t.jobs if j[0] != "follow_up")
            t.jobs.append(("cancel", text.strip()))
        elif t.jobs and t.jobs[-1][0] == "follow_up":
            t.jobs[-1] = ("follow_up", "%s\n%s" % (t.jobs[-1][1].rstrip(), text.strip()))
        else:
            t.jobs.append(("follow_up", text))

    async def _send(self, t: _Task, run: Run, text: str, cancel: bool) -> bool:
        """A follow-up (as MESSAGE + its text) or a cancel (as it is) into the live run: False when the
        run has ended."""
        word = text.strip() if cancel else MESSAGE + text.strip()
        if not await run.send(word):
            return False
        if t.state is not None:
            t.state.messages += 1
            t.state.cancel = t.state.cancel or cancel
            t.state.sent.append((word, text, cancel))
        log("implementer", "%s: %s went into its run" % (t.name, "the cancel" if cancel else "a message"))
        return True

    async def _work(self, t: _Task) -> None:
        """The task's jobs in order: each run ends, and reports, before the next starts."""
        try:
            while t.jobs and not self._closing:
                kind, text = t.jobs.popleft()
                t.busy = t.starting = True
                try:
                    await self._run(t, kind, text)
                except Exception as e:
                    t.busy = False
                    log("implementer", "%s broke: %s" % (t.name, traceback.format_exc()))
                    if not self._closing:  # with what it made so far
                        await self._report(t, "failed", "Failed: the implementer could not run: %s: %s."
                                           % (type(e).__name__, e))
                finally:
                    t.busy = t.starting = False
                    t.guard.snapshot = None
                    early, t.early = t.early, []
                    for text_, cancel in early:  # its run never went live: after it, then
                        self._queue(t, text_, cancel)
        finally:
            t.worker = None

    # ------------------------------------------------------------------ one run

    async def _run(self, t: _Task, kind: str, text: str) -> None:
        """One job's run, from its prompt to its report; its usage is written whatever happens."""
        st = _RunState(kind, cancel=kind == "cancel")
        spent = usage.RunUsage(t.name)
        try:
            await self._run_one(t, st, text, spent)
        finally:  # at shutdown and after a crash too: what it spent was spent
            for row in spent.rows():
                self._spawn(self._usage_row(row))

    async def _run_one(self, t: _Task, st: _RunState, text: str, spent: usage.RunUsage) -> None:
        if st.kind == "cancel" and not t.session:  # no run to stop and no session to resume
            await self._cancel_without_session(t)
            return
        plan = await self._plan(t, st, text)
        if plan is None:
            return
        resume, prompt, snap = plan
        system = Path(system_file(self.cfg, await self._system()))
        harness = self._harness()
        log("implementer", "%s %s: %d characters of %s" % (
            t.name, "resumed " + resume if resume else "started", len(prompt), "cancel" if st.cancel else "brief"))
        st.made_before = {k: list(v) for k, v in t.guard.made.items()}
        t.guard.snapshot = snap
        t.guard.new_run()  # its own test runs, counted afresh
        done, failed, forced = await self._drive(t, harness, system, prompt, resume, spent, st)
        if done is not None and is_summary(done.text) and not (failed or forced or self._closing) and t.session:
            done, failed, forced = await self._recover(t, harness, system, spent, st, done)
        t.guard.snapshot = None
        self._keep_snapshot(t, snap)
        if self._closing:
            return
        status, report, description = await self._conclude(t, st, snap, done, failed, forced)
        t.busy = False  # this run is over: the brain sees whether a follow-up waits behind it
        await self._report(t, status, report, description)
        self._requeue(t, st, done, failed, forced)

    async def _recover(self, t: _Task, harness: Harness, system: Path, spent: usage.RunUsage, st: _RunState,
                       done: Done) -> tuple[Done | None, str | None, str | None]:
        """The run ended on a summary of the conversation in place of its report: its session is
        resumed once with RECOVER, as part of the same run (its limits, its snapshot, its one report).
        A report that comes back is the run's; another summary leaves `done` to _conclude."""
        log("implementer", "%s ended on a summary, not a report: its session is resumed once for the report" % t.name)
        again, failed, forced = await self._drive(t, harness, system, RECOVER, t.session, spent, st)
        if again is not None and again.text.strip() and not is_summary(again.text):
            again.turns += done.turns
            again.seconds += done.seconds
            return again, failed, forced
        return done, failed, forced

    async def _plan(self, t: _Task, st: _RunState, text: str) -> tuple[str | None, str, putback.Snapshot | None] | None:
        """(the session to resume or None, the prompt, the snapshot its calls go to), or None when it
        does not run: closing, or cancelled before it began."""
        if st.kind == "cancel":
            # the run it cancels is the last real one: its snapshot, which this run's calls add to
            # (a file it had not touched is the same as before it), or none after a restart
            plan = t.session, cancel_prompt(t.name, text), self._kept_snapshot(t)
        else:
            resume, prompt = await self._prompt(t, st.kind, text)
            plan = resume, prompt, None
        if self._closing:
            return None
        if st.kind != "cancel" and any(cancel for _, cancel in t.early):
            return await self._cancelled_early(t, st)
        if st.kind != "cancel":
            self._drop_kept_snapshot(t)
            plan = plan[0], plan[1], self._new_snapshot(t)
        return plan

    async def _prompt(self, t: _Task, kind: str, text: str) -> tuple[str | None, str]:
        """(the session to resume or None, the prompt) for a start or a follow-up."""
        resume = t.session if kind == "follow_up" else None
        if resume:
            return resume, follow_up_prompt(t.name, text)
        if kind == "follow_up" and t.brief:  # nothing to resume: a new implementer, on both
            text = "%s\n\nFOLLOW-UP: %s" % (t.brief.rstrip(), text.strip())
        return None, await briefing.compose(text, t.name, self.cfg, self.vfg)

    async def _cancelled_early(self, t: _Task, st: _RunState) -> tuple[str, str, putback.Snapshot | None] | None:
        """Never mind before the run began: a new task is not started (None); a follow-up becomes the
        cancel of the run before it."""
        t.early = []
        if st.kind == "start" or not t.session:
            t.busy = t.starting = False
            log("implementer", "%s: cancelled before its run began" % t.name)
            await self._report(t, CANCELLED_STATUS, "Cancelled: it had not started; nothing was made.")
            return None
        log("implementer", "%s: cancelled before its follow-up began: the cancel goes in its place" % t.name)
        st.cancel, st.kind = True, "cancel"
        return t.session, cancel_prompt(t.name, CANCEL), self._kept_snapshot(t)

    async def _conclude(self, t: _Task, st: _RunState, snap: putback.Snapshot | None, done: Done | None,
                        failed: str | None, forced: str | None) -> tuple[str, str, str]:
        """(status, report, description) of an ended run, after code's checks: a cancel, a stop at the
        limit, and a warned run that did not finish are put back; a warned run that reports done
        keeps its work."""
        summary_only = False
        if done is not None and is_summary(done.text):
            log("implementer", "%s ended on a summary of the conversation, not a report: %s" % (
                t.name, "its last report is taken" if st.last_report else "it made none"))
            done.text, summary_only = st.last_report, not st.last_report
        if forced:
            status, report = "failed", self._stopped_report(t, st, forced, snap)
        elif summary_only and not failed:
            status, report = "failed", ("Failed: the implementer ended without a report (its last message summed up "
                                        "the conversation instead).")
        else:
            status, report = outcome(done, failed, self.cfg.max_turns)  # the status from the whole text
            if st.warned and done is not None and first_word(done.text or "") is None:
                status = "failed"  # warned, and it said neither: it did not finish
        report, description = split_description(report)
        if st.warned and not (st.cancel or forced) and status == "done":
            log("implementer", "%s was warned near its limit and reported done: its work is kept" % t.name)
        elif st.cancel or forced or st.warned:  # code checks, and puts back what is not back
            if snap is None:
                report += "\n" + "Code put back: nothing; this process did not see the run this cancels."
            else:
                did, could_not = await putback.put_back(snap)
                report += "\n" + putback.line(did, could_not)
                if could_not:
                    status = "failed"
        return status, report, description

    def _requeue(self, t: _Task, st: _RunState, done: Done | None, failed: str | None, forced: str | None) -> None:
        """The follow-ups that went into the run and that it did not cover go to a resume: every one
        after a stop at its limit or a crash, and those the harness says it ended without reading. A
        cancel takes them with it."""
        if st.cancel or not st.sent:
            return
        if forced or failed:
            lost = [text for _, text, _ in st.sent]
        elif done is not None and done.unread:
            lost = [text for word, text, _ in st.sent if word in done.unread]
        else:
            return
        for text in lost:
            self._queue(t, text, False)
        if lost:
            log("implementer", "%s: %d message(s) the run did not cover go to a resume" % (t.name, len(lost)))

    async def _cancel_without_session(self, t: _Task) -> None:
        line = "Code put back: nothing; there was no run of it here to put back."
        status = CANCELLED_STATUS
        snap = self._kept_snapshot(t)
        if snap is not None:
            did, could_not = await putback.put_back(snap)
            line = putback.line(did, could_not)
            status = "failed" if could_not else status
        t.busy = False
        await self._report(t, status, "Cancelled: it had no session to resume.\n" + line)

    # ------------------------------------------------------------------ the snapshots

    def _new_snapshot(self, t: _Task) -> putback.Snapshot:
        t.snapshot = putback.Snapshot(repo=self.cfg.repo, server=self.cfg.server, home=self.cfg.home, task=t.name,
                                      vfg=self.vfg)
        return t.snapshot

    def _snapshot_path(self, name: str) -> str:
        """VFG_BRAIN_DIR/implementer-snapshots/<task>.json; a name that is not a plain word by its hash."""
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
            name = "task-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cfg.brain_dir, "implementer-snapshots", name + ".json")

    def _keep_snapshot(self, t: _Task, snap: putback.Snapshot | None) -> None:
        """The run's snapshot on disk too, so a cancel after a restart still puts back."""
        if snap is not None:
            try:
                putback.save(snap, self._snapshot_path(t.name))
            except (OSError, ValueError) as e:
                log("putback", "%s: its snapshot was not kept on disk: %s" % (t.name, e))

    def _kept_snapshot(self, t: _Task) -> putback.Snapshot | None:
        """The last real run's snapshot: in memory, else as kept on disk before a restart."""
        if t.snapshot is None:
            t.snapshot = putback.load(self._snapshot_path(t.name), self.vfg)
            if t.snapshot is not None:
                log("putback", "%s: its last run's snapshot read from disk" % t.name)
        return t.snapshot

    def _drop_kept_snapshot(self, t: _Task) -> None:
        """A new real run: the last one's snapshot goes."""
        t.snapshot = None
        try:
            os.remove(self._snapshot_path(t.name))
        except FileNotFoundError:
            pass
        except OSError as e:
            log("putback", "%s: its old snapshot was not removed: %s" % (t.name, e))

    def _harness(self) -> Harness:
        if self.harness is None:
            self.harness = harnesses.make(self.cfg)
        return self.harness

    # ------------------------------------------------------------------ driving a run

    async def _drive(self, t: _Task, harness: Harness, system: Path, prompt: str, resume: str | None,
                     spent: usage.RunUsage, st: _RunState) -> tuple[Done | None, str | None, str | None]:
        """One run to its end: (its Done, why it failed, "turns" or "time" when it was stopped at its
        limit). It is warned once near its limit; at the limit itself it is stopped."""
        began = asyncio.get_running_loop().time()
        done = failed = forced = None
        run = None
        try:
            try:
                async with asyncio.timeout(self.cfg.time_limit_s):
                    if resume:
                        run = await harness.resume(t.name, system, resume, prompt, t.guard)
                    else:
                        run = await harness.start(t.name, system, prompt, t.guard)
            except TimeoutError:
                forced = "time"
                return None, None, forced
            await self._go_live(t, run, st)
            done, forced = await self._events(t, run, spent, st, began)
        except Exception as e:
            failed = "it crashed: %s: %s" % (type(e).__name__, str(e)[:300])
        finally:
            t.run = t.state = None
            t.starting = False
            if run is not None:
                await self._close_run(t, run, stop=forced is not None and done is None)
            if forced:
                log("implementer", "%s stopped at its limit: %s" % (
                    t.name, "%d turns" % st.turns if forced == "turns" else "%s s" % js_num(self.cfg.time_limit_s)))
        return done, failed, forced

    async def _go_live(self, t: _Task, run: Run, st: _RunState) -> None:
        """The run is live: what came while it started goes in."""
        t.run, t.state, t.starting = run, st, False
        early, t.early = t.early, []
        for text, cancel in early:
            if not await self._send(t, run, text, cancel):
                self._queue(t, text, cancel)

    async def _events(self, t: _Task, run: Run, spent: usage.RunUsage, st: _RunState,
                      began: float) -> tuple[Done | None, str | None]:
        """The run's events to its Done: (its Done or None, "turns" or "time" when it reached its
        limit), with the warning near it."""
        loop = asyncio.get_running_loop()
        limit = self.cfg.time_limit_s
        end_at = began + limit
        warn_at = began + max(limit - WARN_S, limit * 0.75)
        events = run.events().__aiter__()
        nxt = None
        try:
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(events.__anext__())
                wake = end_at if st.warned else min(warn_at, end_at)
                finished, _ = await asyncio.wait({nxt}, timeout=max(0.0, wake - loop.time()))
                if not finished:
                    if loop.time() >= end_at:
                        return None, "time"
                    await self._warn(t, run, st, "time", "%s s of %s" % (
                        js_num(round(loop.time() - began)), js_num(limit)))
                    continue
                got, nxt = nxt, None
                try:
                    ev = got.result()
                except StopAsyncIteration:
                    return None, None
                self._seen(t, ev, spent, st)
                if isinstance(ev, Request):
                    if st.turns > self.cfg.max_turns:  # its turns over every turn of the run
                        return None, "turns"
                    if not st.warned and st.turns >= self.cfg.max_turns - WARN_TURNS:
                        await self._warn(t, run, st, "turns", "turn %d of %d" % (st.turns, self.cfg.max_turns))
                elif isinstance(ev, Done):
                    return ev, "turns" if ev.ended == TURNS else None
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()

    async def _close_run(self, t: _Task, run: Run, stop: bool) -> None:
        if stop:
            try:
                await run.stop()
            except Exception as e:
                log("implementer", "%s: stop: %s" % (t.name, e))
        try:
            await run.close()
        except Exception as e:
            log("implementer", "%s: close: %s" % (t.name, e))

    async def _warn(self, t: _Task, run: Run, st: _RunState, why_: str, where: str) -> None:
        """The one warning near the limit, into the live run."""
        st.warned = why_
        sent = await run.send(LIMIT)
        if sent:
            st.messages += 1
        log("implementer", "%s near its limit (%s): %s" % (t.name, where, "warned" if sent else "it had ended"))

    def _stopped_report(self, t: _Task, st: _RunState, forced: str, snap: putback.Snapshot | None) -> str:
        """The report of a run stopped at its limit, written from what the guard saw: what it made and
        changed, and its last commands with their errors."""
        what = ("its %d turns" % self.cfg.max_turns if forced == "turns"
                else "its time limit, %s s" % js_num(self.cfg.time_limit_s))
        out = ["Failed: stopped at its limit (%s); put back to the last working version." % what]
        made = {k: [n for n in t.guard.made.get(k, []) if n not in st.made_before.get(k, [])] for k in MADE_KINDS}
        if any(made.values()):
            out.append("It had made: %s." % "; ".join("%s %s" % (k, ", ".join(v)) for k, v in made.items() if v))
        files = putback.changed(snap) if snap is not None else []
        if files:
            out.append("It had changed: %s." % ", ".join(os.path.relpath(p, self.cfg.repo) for p in files))
        last = []
        for cid, tool, inp in st.calls:
            said = str(inp.get("command") if tool == "Bash" else "%s %s" % (tool, inp.get("file_path") or ""))
            said = re.sub(r"\s+", " ", said)[:160]
            err = st.results.get(cid)
            last.append("`%s`%s" % (said, " gave the error: %s" % re.sub(r"\s+", " ", err[1])[:160]
                                     if err and err[0] else ""))
        if last:
            out.append("Its last commands: %s." % "; ".join(last))
        return "\n".join(out)

    def _seen(self, t: _Task, ev, spent: usage.RunUsage, st: _RunState | None = None) -> None:
        """Notes one event: the session, what it does, each request's tokens, how it ended."""
        if isinstance(ev, Session):
            self._keep_session(t, ev.session)
        elif isinstance(ev, ToolCall):
            log("implementer", "%s %s %s" % (t.name, ev.name, short(ev.input)))
            if st is not None:
                st.calls.append((ev.id, ev.name, ev.input))
        elif isinstance(ev, ToolResult):
            if st is not None:
                st.results[ev.id] = (ev.error, ev.text)
        elif isinstance(ev, Text):
            log("implementer", "%s says: %s" % (t.name, re.sub(r"\s+", " ", ev.text.strip())[:300]))
            if st is not None and first_word(ev.text) is not None and not is_summary(ev.text):
                st.last_report = ev.text
        elif isinstance(ev, Request):
            cost = spent.add(ev)
            if st is not None:
                st.turns += 1
            log("request", "%s %s in=%s cached=%s written=%s out=%s size=%s cost=%.5f%s%s" % (
                t.name, ev.model, js_num(ev.input), js_num(ev.cache_read), js_num(ev.cache_write), js_num(ev.output),
                js_num(ev.size), cost, " provider=%s" % ev.provider if ev.provider else "",
                " billed=%.5f" % ev.billed if ev.billed is not None else ""))
        elif isinstance(ev, Compacted):
            log("implementer", "%s: its session was compacted (%s%s)" % (
                t.name, ev.trigger or "no trigger given",
                ", %s tokens before" % js_num(ev.tokens_before) if ev.tokens_before is not None else ""))
        elif isinstance(ev, Done):
            log("implementer", "%s ended %s (%s turns, %d s%s)" % (
                t.name, ev.ended, ev.turns, ev.seconds,
                ", %d message(s) read" % st.messages if st is not None and st.messages else ""))
            for d in ev.refused:
                log("denied", "%s %s" % (t.name, d))

    def _keep_session(self, t: _Task, session: str | None) -> None:
        """The session the harness named, kept here and given to on_session at once: a run the brain's
        stop or a crash cuts short is still resumed by its follow-up."""
        if not session or session == t.session:
            return
        t.session = session
        if self.on_session is not None:
            try:
                self.on_session(t.name, session)
            except Exception:
                log("implementer", "%s: on_session broke: %s" % (t.name, traceback.format_exc()))

    async def _system(self) -> str:
        async with self._prefix_lock:
            if not self._prefix:
                self._prefix = await implementer_prefix.load(self.vfg)
        return implementer_prefix.system_prompt(self._rules, self._prefix)

    async def _report(self, t: _Task, status: str, report: str, description: str = "") -> None:
        """The report to the brain, with the description and what the task made, cumulative over its
        runs in this process and checked against the disk."""
        made = made_now(t.guard)
        log("implementer", "%s %s: %s" % (t.name, status, re.sub(r"\s+", " ", report)[:300]))
        log("implementer", "%s made %s; description: %s" % (
            t.name, "; ".join("%s %s" % (k, ", ".join(made[k])) for k in MADE_KINDS if made[k]) or "nothing",
            description[:300] or "none"))
        try:
            await self.on_report(t.name, status, report, description, made)
        except Exception:
            log("implementer", "%s: on_report broke: %s" % (t.name, traceback.format_exc()))

    async def _usage_row(self, row: dict) -> None:
        r = await self.vfg(*usage.usage_args(row), timeout=30)
        if r.code != 0:
            log("usage", "vfg usage add failed (%s): %s in=%s out=%s" % (why(r), row["ref"], js_num(row["in"]),
                                                                       js_num(row["out"])))
            return
        log("usage", "implementer %s in=%s out=%s cache_read=%s cache_write=%s largest=%s cost=%.4f (priced by %s; "
            "list %.4f) ref=%s" % (
                row["model"], js_num(row["in"]), js_num(row["out"]), js_num(row["cacheRead"]), js_num(row["cacheWrite"]),
                js_num(row["largest"]), row["cost"], row["priced_by"], row["list_cost"], row["ref"]))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
