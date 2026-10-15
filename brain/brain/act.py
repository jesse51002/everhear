"""Running a step: what one answer of the brain's loop does (Prototype_Brain_Loop). Code, no model.

- First the tools that need no model: each follow-up's task must exist. A refusal is a result,
  never an exception: the next step reads it and fixes it (the spec: "refused at the tool, and the
  refusal comes back as a result").
- The actions: `together` all at once, `in_turn` one after another, stopping at the first that does
  not land, the rest not run. Each is one of the game engine's ops (the game engine spec), run as
  `vfg action run …` with no task (only an implementer's work is one: Prototype_Named_Tasks), after
  its values are checked as vfg checks them; a scenario's or a mark's args go to the engine, which
  checks them against the class and refuses in words. An action lands when it ran without an
  error: vfg checked its values and the engine did it. Nothing is retried here: a refused command's
  result (the command scenario's) carries Minecraft's error and the reference of every command it
  used (refs.py), capped, for the next step to fix it. One vfg could not run at all did not happen:
  a value vfg refused is refused (fix it), a box out of reach or the engine not running "did not
  run" (safe to try again); one that timed out failed (it may have happened: never run blindly
  again). One that ran but whose facts say it changed nothing (`changed` empty: a Config already as
  written) "changed nothing", and is not landed either: a reply written up front never claims a fix
  on it.
- The reads: another player's items, by slot, from a state fetched for it (World.items); an engine
  class's Config, as written, with its file and lines (World.config, `vfg engine config get`), shown
  to the next step whole. An engine_config, the Config rewritten (the game engine spec), is refused at
  the tool unless an earlier step of the loop read that class's Config: the brain sends back the
  whole class as written, comments and all, so it must have seen it. An engine action that ran
  makes the world read the engine again for the next loop (World.engine_changed).
- The ending's pieces, which the loop puts together once a step lands and ends (loop.py), and the
  front turn when the router places a line with an implementer: a hand-off's task opened under a
  name no task, action or engine class has (names.unique), its brief
  rendered from its parts (call.HandOff.brief), and its `AI task <name> | new: …` row; a follow-up's
  task opened again, with its `AI task <name> | follow-up: …` row, the cancel words written here for
  a "never mind"; the reply; the record; then each implementer started and each follow-up sent.
No row is written per action: what a step ran stays inside its loop, which leaves the context one
record. Every `vfg say` and AI row a player's turn writes is noted (WROTE, Echoes), so the player's
next turn can wait for them to come back on the watch, and read what this one did; a loop holds no
turn, and writes with WROTE unset.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vfg_actions import check_values, engine_op

from . import names
from .actions import Catalog, commands_in, word
from .call import IN_TURN, NEXT, FollowUp, HandOff, StepBase, one_line
from .config import log
from .executor import Ran, execute
from .tasks import Tasks
from .vfg import Later, Vfg
from .world import Seen

SAY_TIMEOUT_S = 30
ROW_MAX = 400        # an AI row's text
BRIEF_SHORT = 160    # a brief in an `AI task` row
SAID_MAX = 600       # a result's words, as the next step reads them
REFERENCE_MAX = 6000  # a refused command's reference, as the next step reads it
CONFIG_MAX = 8000    # a Config read, as the next step reads it: whole, unless far longer than any is
# The cancel words of Prototype_Implementer_Harness, exactly: a follow-up with `cancel` sends these.
CANCEL = ("CANCELLED: the player said never mind. Stop building and put everything back the way it was before "
          "the run this cancels, then report Cancelled: and what you put back.")
CANCEL_ROW = "never mind — put back what its last run built"  # a cancel's `AI task` row
LANDED, REFUSED, FAILED, NOT_RUN, READ = "landed", "refused", "failed", "not run", "read"
DID_NOT_RUN = "did not run"  # the box out of reach, or the action not deployed: nothing happened, safe to try again
CHANGED_NOTHING = "changed nothing"  # it ran, and its facts say it changed nothing: not what was meant
VALUE_REFUSED = 2            # vfg's exit for a value or a key it refused

# The head of THE LOOP SO FAR, before a loop's first step block.
LOOP_SO_FAR = ("THE LOOP SO FAR (your earlier steps for this event: what each ran and read, what it meant to end with, "
               "and what each tool came to; nothing of them reached the players unless it says so):\n\n")

# The rows the running turn has written, as Echoes futures: set by the turn (loop.py), noted by say() and post();
# None outside a turn (a loop, a report), where nobody waits for them.
WROTE: contextvars.ContextVar[list[asyncio.Future] | None] = contextvars.ContextVar("wrote", default=None)


def spent(got) -> str:
    """What a call spent, from its Answer or its LlmError: `840 ms $0.001234, 9000 in, 96% cached, 40 out`,
    the cost `$0.001234+` when it timed out with an attempt in flight, whose cost is not in it."""
    rate = f", {got.cached_tokens / got.prompt_tokens:.0%} cached" if got.prompt_tokens else ""
    more = "" if getattr(got, "cost_known", True) else "+"
    return f"{got.ms} ms ${got.cost:.6f}{more}, {got.prompt_tokens} in{rate}, {got.completion_tokens} out"


def short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def told(ran: Ran, notes: list[str] = ()) -> str:
    """The executor's summary (a scenario that goes on says its instance, which engine_stop takes),
    and the numbers clamped to their limits."""
    text = ran.summary
    notes = [n for n in notes if n not in text]
    return text + (f" ({'; '.join(notes)})" if notes else "")


def offline_names(facts: dict[str, Any]) -> list[str]:
    got = facts.get("offline")
    return [str(n) for n in got if str(n).strip()] if isinstance(got, list) else []


def changed_nothing(facts: dict[str, Any]) -> bool:
    """An action that ran and says it changed nothing: its facts carry `changed`, what it changed,
    and it is empty: an engine_config to what the Config is already. Not landed: what was meant did
    not happen, so the step's ending (a reply written up front, "fixed it") never goes out on it,
    and the next step reads it."""
    return "changed" in facts and not facts["changed"]


def distinct(acts: list) -> list:
    """The step's actions, each (action, values) once, in order."""
    seen, out = set(), []
    for a in acts:
        key = (a.to, repr(sorted(a.values().items())))
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def cap(text: str, n: int) -> str:
    """Text kept whole up to `n` characters, its lines as they are, and a note where it was cut."""
    return text if len(text) <= n else text[:n].rstrip() + "\n(…cut here)"


@dataclass
class Result:
    """What one tool of a step came to, as the next step reads it."""
    what: str                       # "command: give Steve a diamond pickaxe", "read Alex's items", "follow up x"
    state: str                      # landed, changed nothing, refused, failed, did not run or not run; a read: read
    said: str                       # what happened, or why not
    values: dict | None = None      # an action's values as it ran, or as written when it never did
    reference: str = ""             # a refused command's reference
    tried: bool = False             # an action vfg ran, whatever came of it
    instruction: str = ""           # an action's instruction, for a record written in code
    config_of: str = ""             # a Config read: the class's name
    whole: bool = False             # `said` shown as it is, its lines kept (a Config as written)

    @property
    def ok(self) -> bool:
        return self.state in (LANDED, READ)

    def show(self) -> str:
        text = f"- {self.what}"
        if self.values is not None:
            text += " " + (" ".join(f"{k}={word(v)}" for k, v in self.values.items()) or "(its defaults)")
        if self.whole:
            text += f"\n  {self.state.upper()}:\n{cap(self.said, CONFIG_MAX)}"
        else:
            text += f"\n  {self.state.upper()}: {short(self.said, SAID_MAX)}"
        if self.reference:
            text += ("\n  THE REFERENCE OF THE COMMANDS IT USED (each command's syntax on this server, then the "
                     f"wiki's Java Edition notes):\n{self.reference}")
        return text


@dataclass
class StepRun:
    """One step and what each of its tools came to."""
    step: StepBase
    n: int
    actions: list[Result] = field(default_factory=list)
    reads: list[Result] = field(default_factory=list)
    refused: list[Result] = field(default_factory=list)  # tools refused before anything ran
    said: str | None = None  # the chat line its ending said, when code wrote another than the step's (loop.end)

    @property
    def landed(self) -> bool:
        """Every action landed and no tool was refused: the step's ending may go out."""
        return all(r.ok for r in self.actions) and not self.refused

    def show(self, sent: bool = False) -> str:
        """The step as THE LOOP SO FAR shows it to the steps after it: what it ran, read and
        meant to end with, and what each came to. The first step's block opens the section.
        `sent`: its ending went out (an ended step that a later step follows, because a line was
        added to the loop while it ran, or the loop was taken up again), so the block says what
        the players got rather than that nothing was sent."""
        s = self.step
        head = LOOP_SO_FAR if self.n == 1 else ""
        how = f", {s.order.replace('_', ' ')}" if len(self.actions) > 1 else ""
        lines = [f"STEP {self.n}{how}:"]
        lines += [r.show() for r in self.refused + self.actions + self.reads]
        if not (self.refused or self.actions or self.reads):
            lines.append("- ran nothing")
        reply = s.reply.strip() if self.said is None else self.said.strip()
        if s.end == NEXT:
            lines.append("YOU ENDED IT next: these results are what it was for; nothing was sent.")
        elif sent:
            went = [f"hand off {h.name}" for h in s.hand_offs]
            went += [f"{'cancel' if f.cancel else 'follow up'} {f.task}" for f in s.follow_ups]
            went += [f"reply “{one_line(reply)}”"] if reply else []
            went += [f"record “{one_line(s.record)}”"] if s.record.strip() else []
            lines.append(f"ENDED {s.end} AND SENT: {', '.join(went) or 'nothing in chat'}.")
        else:
            meant = [f"end {s.end}"]
            meant += [f"hand off {h.name}" for h in s.hand_offs]
            meant += [f"{'cancel' if f.cancel else 'follow up'} {f.task}" for f in s.follow_ups]
            if reply:
                meant.append(f"reply {reply!r}")
            if s.record.strip():
                meant.append(f"record {s.record.strip()!r}")
            lines.append(f"NOT SENT: you meant to {', '.join(meant)}, but not everything landed, so none of it went "
                         "out: no reply, no record, no task.")
        return head + "\n".join(lines) + "\n\n"

    def summary(self) -> str:
        """One log line on what came of it: `command landed, railgun refused | read Alex`."""
        bits = [", ".join(f"{r.what.split(':')[0]} {r.state}" for r in self.actions)] if self.actions else []
        bits += [r.what if r.state == READ else f"{r.what}: {r.state}" for r in self.reads + self.refused]
        return (" | ".join(bits) or "nothing ran") + ("" if self.landed else " (not landed)")


def record_of(runs: list[StepRun]) -> str:
    """The loop's record written in code, when the model left it empty but something ran: each
    action vfg ran, and how it went."""
    bits = []
    for run in runs:
        for r in run.actions:
            if r.tried:
                bits.append(f"{one_line(r.instruction)} — " + ("done" if r.ok else f"{r.state}: {short(r.said, 120)}"))
    return short("; ".join(bits), ROW_MAX)


def echo_key(kind: str, text: str) -> tuple[str, str]:
    """How an AI row is known when it comes back on the watch: its kind and its words, letters and
    digits only, the start of them (the toolkit cleans what it stores, and may cut it)."""
    return kind, re.sub(r"[^a-z0-9]", "", text.lower())[:80]


class Echoes:
    """The AI rows a turn wrote, waited for until they come back on the watch (loop.py, the settle):
    expect() when a row is written, seen() for each AI row the watch gives."""

    def __init__(self):
        self.waiting: list[tuple[tuple[str, str], asyncio.Future]] = []

    def expect(self, kind: str, text: str) -> asyncio.Future:
        got = asyncio.get_running_loop().create_future()
        self.waiting.append((echo_key(kind, text), got))
        return got

    def seen(self, kind: str, text: str) -> None:
        key = echo_key(kind, text)
        for i, (want, got) in enumerate(self.waiting):
            if want == key and not got.done():
                got.set_result(True)
                del self.waiting[i]
                return

    def forget(self, futures: list[asyncio.Future]) -> None:
        self.waiting = [(k, f) for k, f in self.waiting if f not in futures]

    def drop(self, got: asyncio.Future) -> None:
        """A row that will never come back (its write failed): no turn waits for it."""
        self.forget([got])
        if not got.done():
            got.set_result(False)

    def release(self) -> None:
        """At a stop: nothing more comes back on the watch, so no turn waits for it."""
        for _, got in self.waiting:
            if not got.done():
                got.set_result(False)
        self.waiting = []


class Acts:
    def __init__(self, *, vfg: Vfg, later: Later, catalog: Catalog, tasks: Tasks, implementers, world, repo: Path,
                 server: str, action_timeout_s: float = 20.0):
        self.vfg = vfg
        self.later = later
        self.catalog = catalog
        self.tasks = tasks
        self.implementers = implementers
        self.world = world
        self.repo, self.server = repo, server
        self.action_timeout_s = action_timeout_s
        self.echoes = Echoes()

    # ------------------------------------------------------------- the step

    async def run(self, step: StepBase, *, n: int, player: str, seen: Seen, refs, ref: str,
                  earlier: list[StepRun] = ()) -> StepRun:
        """Every tool of `step`: the checks, the actions (together or in turn), then the reads.
        `player` is the event's (the speaker; a report's: its task's player); `ref` (`#<seq> s<n>`)
        labels the log lines; `earlier` is the loop's steps before it, whose reads an engine_config
        needs."""
        out = StepRun(step, n)
        read = {r.config_of for run in earlier for r in run.reads if r.config_of and r.state == READ}
        for f in step.follow_ups:
            task = f.task.strip()
            if self.tasks.get(task) is None:
                out.refused.append(Result(f"follow up {task}", REFUSED, f"there is no task {task} under TASKS: "
                                          "follow up one that is, by its name, or hand it off as new"))
        acts = distinct(step.actions)
        if step.order == IN_TURN:
            for i, a in enumerate(acts):
                if out.actions and not out.actions[-1].ok:
                    out.actions.append(Result(f"{a.to}: {one_line(a.instruction)}", NOT_RUN,
                                              "an earlier one did not land", a.values(), instruction=a.instruction))
                    continue
                out.actions.append(await self.action(a, i, seen, refs, ref, read))
        elif acts:
            running = [asyncio.create_task(self.action(a, i, seen, refs, ref, read),
                                           name=f"action {ref} #{i + 1}")
                       for i, a in enumerate(acts)]
            try:
                out.actions = list(await asyncio.gather(*running))
            finally:
                for t in running:
                    t.cancel()
        if step.read_items or step.read_configs:
            out.reads = list(await asyncio.gather(*(self.read(p) for p in step.read_items),
                                                  *(self.read_config(c) for c in dict.fromkeys(step.read_configs))))
        return out

    @staticmethod
    def values(cls, given: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """What `vfg action run <name>` is given, checked as vfg checks it; raises ValueError."""
        checked = check_values(cls, given)
        return {k: checked.values[k] for k in given}, checked.notes

    async def action(self, a, i: int, seen: Seen, refs, ref: str, read: set[str] = frozenset()) -> Result:
        name, cls = a.to, self.catalog.get(a.to)
        what = f"{name}: {one_line(a.instruction)}"
        given = a.values(cls)
        if cls is None:
            return Result(what, REFUSED, f"{name} is no longer on the menu", given, instruction=a.instruction)
        op = engine_op(cls)
        if op == "config" and str(given.get("class_id") or "").strip() not in read:
            which = str(given.get("class_id") or "").strip() or "its class"
            return Result(what, REFUSED, f"refused before it ran: read {which}'s Config first (read_configs "
                          f"[\"{which}\"], end next), then send the whole class back as you read it, every line and "
                          "comment kept, only what was asked changed", given, instruction=a.instruction)
        try:
            values, notes = self.values(cls, given)
        except ValueError as e:
            return Result(what, REFUSED, f"refused before it ran: {e}", given, instruction=a.instruction)
        ran = await execute(name, cls, values, "", self.vfg, self.action_timeout_s, label=f" {ref} #{i + 1}")
        if ran.kind != "not_run":
            self.world.engine_changed()
        log("action", f"{ref} #{i + 1} {name} {ran.kind}: {ran.summary[:200]}")
        if ran.kind == "ok":
            if changed_nothing(ran.facts):
                return Result(what, CHANGED_NOTHING, told(ran, notes), values, tried=True, instruction=a.instruction)
            return Result(what, LANDED, told(ran, notes), values, tried=True, instruction=a.instruction)
        if ran.kind == "refused" and (offline := offline_names(ran.facts)):
            return Result(what, REFUSED, f"{' and '.join(offline)} not online", values, tried=True,
                          instruction=a.instruction)
        if ran.kind == "not_run":  # nothing happened: a value vfg refused is fixed; a box out of reach, tried again
            why = ran.summary.removeprefix("could not run it: ")
            if ran.code == VALUE_REFUSED:
                return Result(what, REFUSED, f"vfg refused it before it ran: {why}", values, tried=True,
                              instruction=a.instruction)
            return Result(what, DID_NOT_RUN, why, values, tried=True,
                          instruction=a.instruction)
        if ran.kind != "refused":  # it timed out or partly happened: it may have, so never claimed
            return Result(what, FAILED, ran.summary, values, tried=True, instruction=a.instruction)
        return Result(what, REFUSED, ran.summary, values, self.reference(values, seen, refs), tried=True,
                      instruction=a.instruction)

    @staticmethod
    def reference(values: dict[str, Any], seen: Seen, refs) -> str:
        """The reference of every command a refused action used, capped: the commands are the
        args of a scenario's whose type is a command (the command scenario's), as ENGINE lists
        them; "" with none, or before the references load."""
        commands = commands_in(values, seen.classes)
        if refs is None or not commands:
            return ""
        used, subs = refs.used(commands)
        return cap(refs.reference(set(used), set(subs)), REFERENCE_MAX) if used else ""

    async def read(self, player: str) -> Result:
        ok, said = await self.world.items(player)
        return Result(f"read {player.strip()}'s items", READ if ok else REFUSED, said)

    async def read_config(self, name: str) -> Result:
        """The read tool for a Config: the class's Config as written, whole, for the next step."""
        ok, said = await self.world.config(name)
        return Result(f"read {name.strip()}'s Config", READ if ok else REFUSED, said, config_of=name.strip(),
                      whole=ok)

    # ------------------------------------------------------------- the ending's pieces

    def open_task(self, h: HandOff, *, player: str, line: str) -> str | None:
        """A hand-off's task, open: its name made free of every task, action and engine class (as
        ENGINE lists them, and as files: names.content_names), and of `cast`, its row written through
        with the brief rendered from its parts, and its `AI task` row. The name, or None when it
        could not open."""
        taken = (self.tasks.names() | set(self.catalog.names()) | names.content_names(self.repo, self.server)
                 | {str(c.get("id")) for c in self.world.classes()} | names.RESERVED)
        name = names.unique(h.name.strip(), taken)
        try:
            self.tasks.open(name, player, line, h.brief(), h.description.strip())
        except ValueError as e:
            log("task", f"no task opened for {h.name!r}: {e}")
            return None
        self.post("task", name, f"new: {h.description.strip()}")
        return name

    def reopen_task(self, f: FollowUp) -> str | None:
        """A follow-up's task, open again with its new brief and description (a cancel: the cancel
        words, and the description as it was), and its `AI task` row. The text for its implementer,
        or None when there is no such task."""
        name = f.task.strip()
        if self.tasks.get(name) is None:
            log("task", f"{name} is not a task here: its follow-up starts nothing")
            return None
        text = CANCEL if f.cancel else f.brief.strip()
        self.tasks.follow_up(name, text, "" if f.cancel else f.description.strip())
        self.post("task", name, f"follow-up: {CANCEL_ROW if f.cancel else short(text, BRIEF_SHORT)}")
        return text

    async def start(self, name: str, brief: str, player: str) -> None:
        """A new task's implementer, started; one that cannot start fails its task."""
        try:
            await self.implementers.start(name, brief)
        except Exception as e:  # noqa: BLE001 — the task fails, said so; the loop's other tasks go on
            self.fail(name, f"{name} failed: the implementer could not start: {short(f'{type(e).__name__}: {e}', 160)}")
            return
        log("task", f"{name} started for {player or 'nobody'}: {short(brief, 300)}")

    async def send(self, name: str, text: str) -> None:
        """A follow-up, to its task's implementer: its session resumed when the row has one, else a
        new implementer on what the task is (its description) and the follow-up."""
        row = self.tasks.get(name) or {}
        try:
            await self.implementers.follow_up(name, text, session=row.get("session") or None,
                                              brief=str(row.get("description") or ""),
                                              made=row.get("made") if isinstance(row.get("made"), dict) else None)
        except Exception as e:  # noqa: BLE001
            self.fail(name, f"{name} failed: the implementer could not start: {short(f'{type(e).__name__}: {e}', 160)}")
            return
        log("task", f"{name} follow-up: {short(text, 300)}")

    # ------------------------------------------------------------- chat and rows

    async def say(self, text: str, task: str = "", *, to: str = "") -> None:
        """A chat line (`to`: one player's private one, nobody else's), through `vfg say`."""
        echo = None
        if not to and (wrote := WROTE.get()) is not None:
            echo = self.echoes.expect("say", text)
            wrote.append(echo)
        r = await self.vfg("say", text, *(["--task", task] if task else []), *(["--to", to] if to else []),
                           timeout=SAY_TIMEOUT_S)
        if r.code != 0:
            said = (r.stderr or r.stdout).strip()
            log("say", f"vfg say failed ({said[-200:] or r.code}): {text!r}")
            if echo is not None:  # no row will come back: the turn waits for none
                self.echoes.drop(echo)

    def post(self, kind: str, task: str, text: str) -> None:
        """An AI row: what the brain did, for the context. Nobody waits for it."""
        text = short(text, ROW_MAX)
        posting = self.later("feed", "post", "AI", f"{kind} {task or '-'}", text)
        if (wrote := WROTE.get()) is not None:
            echo = self.echoes.expect(kind, text)
            wrote.append(echo)
            # a post that failed brings no row back: the turn waits for none
            posting.add_done_callback(lambda t: None if t.cancelled() or t.result() else self.echoes.drop(echo))

    def fail(self, name: str, record: str) -> None:
        """A task that never got to its implementer, failed, with its `AI result` row."""
        if (self.tasks.get(name) or {}).get("status") == "open":
            self.tasks.close(name, "failed", record)
            self.post("result", name, record)
            log("task", record)
