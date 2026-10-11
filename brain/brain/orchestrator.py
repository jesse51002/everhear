"""The orchestrator: one structured() call per part the brain sends to `orchestrator`
(Prototype_Named_Tasks). It is not an agent and not a session: the LLM version of the old
orchestrator, on a small model (VFG_ORCHESTRATOR_MODEL, Gemini 3.8 Flash at low).

It answers one of five, and code carries the answer out (act.py):
- new: a task's name, its description and the brief a fresh implementer starts on;
- follow_up: a task's name from TASKS, its new description and the brief its implementer gets;
- edit: a copy under RUNNING NOW and each value to change;
- stop: the copies to stop;
- reply: nothing starts, and the reply is all (an ask-back, an answer, "nothing by that name is
  running").
On the line's first orchestrator part it also writes the line's one chat reply.

It sees what the brain sees of the conversation, cached the same way, and nothing of the actions,
the commands or the world but RUNNING NOW. The prompt, in the order the cache needs:
1. the opening, marked: its rules (prompts/orchestrator.md, with the owner named) and the
   brain's own conversation header, so both calls render the conversation alike;
2. the conversation, a minute-block at a time, marked at checkpoints (context.py);
3. the tail, never cached: TASKS (every task, what it made and its copies running now), RUNNING
   NOW (every copy with every value of its action, its value now, its limits, live or restart),
   the head the brain core gives it (THE LINE … or THE REPORT …), the brain's instruction for
   this part, the line's other parts, and whether the reply is this call's.

The schema never changes with the tasks or the copies: an answer is checked against them by its
validator, and one that breaks a rule goes back to the model once with why (structured()). Code,
not this, makes a new name unique, runs the edit and stop actions, and logs the usage.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import vfg_actions
from pydantic import BaseModel, Field, model_validator
from vfg_actions import check_edit

from . import call
from .names import NAME, RESERVED
from .actions import word
from .context import Context

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "orchestrator.md"
NEW, FOLLOW_UP, EDIT, STOP, REPLY = "new", "follow_up", "edit", "stop", "reply"
REPLY_MAX = 300
ORCHESTRATOR = "orchestrator"  # the brain's route for a part that comes here (call.ORCHESTRATOR)
CAST = "cast"           # the bridge's task for an incantation's copies
VALUE_MAX = 40          # characters of a value on RUNNING NOW
CHOICES_MAX = 8         # choices shown for one value
# The brain's own conversation header, character for character (call.py's opening ends with it).
CONVERSATION = ("THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                "player's line Jev's class):\n")


class EditValue(BaseModel):
    key: str = Field(description="the value's name, as RUNNING NOW shows it")
    value: str = Field(description="its new value, as text: 0, true, 30, minecraft:ice")


class AnswerBase(BaseModel):
    """The orchestrator's answer. The field order is fixed: reply last, written knowing the rest."""
    kind: Literal["new", "follow_up", "edit", "stop", "reply"] = Field(
        description="new: work no task made; follow_up: a change to a thing a task made; edit: values of a copy "
                    "under RUNNING NOW; stop: copies under RUNNING NOW to end; reply: start nothing, only answer")
    task: str = Field("", description="follow_up: the task's name, exactly as TASKS shows it; else empty")
    name: str = Field("", description="new: the new task's name, lowercase letters, digits and underscores, the "
                                      "thing then whose (frost_bow_alex); else empty")
    description: str = Field("", description="new and follow_up: one or two sentences, what the thing is now, "
                                             "after this ask, for whom, and how a player uses it; else empty")
    brief: str = Field("", description="new and follow_up: one to three sentences the implementer acts on "
                                       "alone; a follow-up's says what to change; else empty")
    copy: str = Field("", description="edit: the copy's name, as RUNNING NOW shows it; else empty")
    values: list[EditValue] = Field(default_factory=list, description="edit: each value to change; else empty")
    copies: list[str] = Field(default_factory=list, description="stop: the name of each copy to stop, as "
                                                                "RUNNING NOW shows it; else empty")
    reply: str = Field("", description="the line's one chat line when THE REPLY says it is yours, and the "
                                       "answer of a reply; else empty")

    def edits(self) -> dict[str, Any]:
        """An edit's values as check_edit takes them: {key: value}, a value that reads as a JSON
        list or object being that list or object, any other kept as text for the field's type to
        read (0, true, 30). Text in JSON quotes, as RUNNING NOW shows a value with a space in it
        (name="Frost Bow"), is that text without them. The validator and Acts both use it, so what
        was checked is what runs."""
        out: dict[str, Any] = {}
        for v in self.values:
            text = v.value.strip()
            try:
                got = json.loads(text)
            except ValueError:
                got = None
            out[v.key.strip()] = got if isinstance(got, (list, dict, str)) else text
        return out


def answer_type(names: set[str], banned: set[str], copies: dict[str, dict], catalog,
                replying: bool) -> type[AnswerBase]:
    """A subclass named "TaskAnswer" (its __name__, the schema's name), with one model_validator
    checking an answer against the tasks there are (`names`), the names a new task may not take
    (`banned`: the library's actions and RESERVED), the copies running now and their actions,
    and whether the line's reply is this call's. Its JSON schema is the same for every input."""

    class TaskAnswer(AnswerBase):
        @model_validator(mode="after")
        def _checked(self):
            kind = self.kind
            task, name, copy = self.task.strip(), self.name.strip(), self.copy.strip()
            listed = [c.strip() for c in self.copies if c.strip()]
            said = self.reply.strip()
            if (replying or kind == REPLY) and not said:
                raise ValueError("the reply is empty: " + ("a reply only is its reply" if kind == REPLY else
                                                           "THE REPLY is yours, the line's one chat line"))
            if len(self.reply) > REPLY_MAX:
                raise ValueError(f"the reply is {len(self.reply)} characters: one chat line, under 200")
            if kind != EDIT and (copy or self.values):
                raise ValueError("`copy` and `values` are for an edit alone: leave them empty")
            if kind != STOP and listed:
                raise ValueError("`copies` is for a stop alone: leave it empty")
            if kind == REPLY:
                if task or name or self.description.strip() or self.brief.strip():
                    raise ValueError("a reply only starts nothing: leave task, name, description and brief empty")
                return self
            if kind == STOP:
                if not listed:
                    raise ValueError("a stop names no copy: list each copy to stop, as RUNNING NOW shows it")
                gone = [c for c in listed if c not in copies]
                if gone:
                    raise ValueError(f"{', '.join(gone)}: not running, not a copy under RUNNING NOW; when nothing "
                                     "by that name runs, answer reply and say so")
                return self
            if kind == EDIT:
                if task or name:
                    raise ValueError("an edit changes no task: leave task and name empty")
                if copy not in copies:
                    raise ValueError(f"{copy!r} is not a copy under RUNNING NOW")
                given = self.edits()
                if not given or "" in given:
                    raise ValueError("an edit changes no value: give each value to change, by its name")
                cls = catalog.get(str(copies[copy].get("action") or "")) if catalog is not None else None
                if cls is not None:
                    current = copies[copy].get("values")
                    try:
                        check_edit(cls, current if isinstance(current, dict) else {}, given)
                    except ValueError as e:
                        raise ValueError(f"the edit of {copy}: {e}") from None
                return self
            if not self.description.strip():
                raise ValueError("the description is empty: say what the thing is now, for whom")
            if not self.brief.strip():
                raise ValueError("the brief is empty: say what the implementer makes or changes")
            if kind == NEW:
                if task:
                    raise ValueError("a new task follows up no task: leave task empty, or answer follow_up")
                if not NAME.match(name):
                    raise ValueError(f"name {name!r}: lowercase letters, digits and underscores, starting with a "
                                     "letter, 2 to 32 characters")
                if name in RESERVED:
                    raise ValueError(f"{name} is the bridge's task for an incantation's copies, never a task's "
                                     "name: name the thing, then whose")
                if name in banned:
                    raise ValueError(f"{name} is a library action's name: add whose it is, as frost_bow_alex")
                return self
            if task not in names:
                raise ValueError(f"task {task!r} is not on TASKS: a follow-up names one of its tasks; work no "
                                 "task made is new")
            if name and name != task:
                raise ValueError("a follow-up keeps its task's name: leave name empty")
            return self

    TaskAnswer.__name__ = TaskAnswer.__qualname__ = "TaskAnswer"
    return TaskAnswer


# ------------------------------------------------------------------ what the call sees

def _when(at: str, today: str) -> str:
    """An ISO time as HH:MM:SS, with its date when that is not `today`."""
    at = str(at or "")
    if len(at) < 19:
        return at or "?"
    return at[11:19] if at[:10] == today else f"{at[:10]} {at[11:19]}"


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _task_of(info: dict) -> str:
    return str(info.get("task") or "").strip()


def task_lines(tasks: list[dict], copies: dict[str, dict], *, today: str | None = None) -> str:
    """TASKS, the board: every task, oldest created first, with what it made, its copies running
    now (running.json's task is its name) and its last result."""
    if not tasks:
        return "TASKS: none yet"
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    rows = []
    for t in sorted(tasks, key=lambda t: str(t.get("created_at") or "")):
        name = str(t.get("name") or "?")
        made = t.get("made")
        if isinstance(made, dict):
            kinds = [f"{k} {', '.join(map(str, made[k]))}" for k in ("scripts", "actions", "presets", "copies")
                     if isinstance(made.get(k), list) and made[k]]
            what = "; ".join(kinds) or "nothing yet"
        else:
            what = "not known"  # null: never recorded
        running = sorted(c for c, info in copies.items() if _task_of(info) == name)
        rows.append(f"- {name}, for {t.get('player') or 'nobody'}, {t.get('status') or '?'}, "
                    f"{_when(t.get('updated_at') or t.get('created_at'), today)}: {_one_line(t.get('description'))}\n"
                    f"  made: {what}; running now: {', '.join(running) or 'none'}")
        result = _one_line(t.get("result"))
        if result:
            rows[-1] += f"\n  last result: {result}"
    return ("TASKS (oldest first: name, for whom, status, when last written, what it is now; what it made, and "
            "its copies running now; its last result):\n" + "\n".join(rows))


def _shown(v: Any) -> str:
    """A value on RUNNING NOW: as `key=<this>` carries it, text with a space or nothing in it quoted,
    cut at VALUE_MAX characters."""
    text = word(v)
    if isinstance(v, str) and (not v or re.search(r"\s", v)):
        text = json.dumps(v, ensure_ascii=False)
    return text if len(text) <= VALUE_MAX else text[:VALUE_MAX - 1] + "…"


def _bounds(var: dict) -> str:
    lo, hi = var.get("ge"), var.get("le")
    if lo is not None or hi is not None:
        return f" ({'' if lo is None else word(lo)}..{'' if hi is None else word(hi)})"
    options = var.get("choices")
    if options:
        shown = "|".join(word(o) for o in options[:CHOICES_MAX])
        more = len(options) - CHOICES_MAX
        return f" ({shown}{f'|…+{more}' if more > 0 else ''})"
    return ""


def _started_by(info: dict) -> str:
    task = _task_of(info)
    if task == CAST:
        return "an incantation started it"
    return f"started by task {task}" if task else "the brain started it"


def running_lines(copies: dict[str, dict], catalog) -> str:
    """RUNNING NOW, two or three lines a copy, sorted by name: its action and who started it, then
    every value of its action but the fixed ones, with its value now, its limits or choices, live
    or restart. A copy whose action is not loaded shows running.json's values and live list."""
    if not copies:
        return "RUNNING NOW: none"
    rows = []
    for c in sorted(copies):
        info = copies[c]
        action = str(info.get("action") or "?")
        now = info.get("values") if isinstance(info.get("values"), dict) else {}
        cls = catalog.get(action) if catalog is not None else None
        live, restart = [], []
        if cls is not None:
            for var in vfg_actions.variables(cls):
                if var["fixed"]:
                    continue
                v = now.get(var["name"], var["default"])
                (live if var["live"] else restart).append(f"{var['name']}={_shown(v)}{_bounds(var)}")
        else:
            marked = set(info.get("live") or []) if isinstance(info.get("live"), list) else set()
            for k, v in now.items():
                (live if k in marked else restart).append(f"{k}={_shown(v)}")
        text = f"- {c}: {action}, {_started_by(info)}"
        if live:
            text += "\n  live: " + " ".join(live)
        if restart:
            text += "\n  restart: " + " ".join(restart)
        rows.append(text)
    return ("RUNNING NOW (each copy: its action, who started it; then every value of its action with its value "
            "now, its limits or choices, live (changes at once) or restart):\n" + "\n".join(rows))


def describe(value: AnswerBase) -> str:
    """One log line: `new frost_bow_alex: … || reply: …`."""
    k = value.kind
    if k == NEW:
        text = f"new {value.name}: {_one_line(value.brief)[:120]}"
    elif k == FOLLOW_UP:
        text = f"follow_up {value.task}: {_one_line(value.brief)[:120]}"
    elif k == EDIT:
        text = f"edit {value.copy}: " + " ".join(f"{key}={v}" for key, v in value.edits().items())
    elif k == STOP:
        text = "stop " + ", ".join(value.copies)
    else:
        text = "reply only"
    return text + (f" || reply: {value.reply}" if value.reply else "")


class Orchestrator:
    def __init__(self, catalog, ask, *, owner: str = "", checkpoint: int = 5, prompt: Path = PROMPT,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.catalog = catalog
        self.now = now  # today, for TASKS' times: a time not today shows its date (the replay sets it)
        self.ask = ask  # structured() with the orchestrator's model, reasoning, providers and timeout
        self.checkpoint = checkpoint
        rules = prompt.read_text(encoding="utf-8").rstrip("\n")
        who = owner or "not named (VFG_OWNER is not set)"
        self.rules = rules.replace("{owner}", who)
        self._opening: str | None = None

    def opening(self) -> str:
        """The rules, then the brain's conversation header: the same for every call."""
        if self._opening is None:
            self._opening = f"{self.rules}\n\n{CONVERSATION}"
        return self._opening

    def tail(self, *, head: str, parts: list, k: int, given: list[str], others: list, replying: bool,
             tasks: list[dict], copies: dict[str, dict]) -> str:
        """Everything after the conversation, for the k-th of the event's orchestrator parts."""
        p = parts[k]
        mine = []
        for j, q in enumerate(parts):
            if j < k:
                went = given[j] if j < len(given) else "failed"
                mine.append(f"- \"{q.part}\": {q.instruction} -> {went}")
            elif j > k:
                mine.append(f"- \"{q.part}\": {q.instruction} (asked after this one)")
        rest = [q for q in others if q.to != ORCHESTRATOR]
        acts = [q for q in rest if q.to != call.TALK]
        talk = [q for q in rest if q.to == call.TALK]

        def block(title: str, lines: list[str]) -> str:
            return f"{title}:\n" + "\n".join(lines) if lines else f"{title}: none"

        reply = ("THE REPLY: yours — the line's one chat line, said once the action parts are done." if replying
                 else "THE REPLY: not yours — leave reply empty, unless you answer reply only.")
        today = self.now().astimezone(UTC).strftime("%Y-%m-%d")
        return (f"\n{task_lines(tasks, copies, today=today)}\n\n{running_lines(copies, self.catalog)}\n\n"
                f"{head}\n\n"
                f"THE BRAIN'S INSTRUCTION FOR THIS PART: {p.instruction}\n"
                f"(the part of the line it covers: \"{p.part}\")\n\n"
                + block("THE LINE'S OTHER PARTS FOR YOU", mine) + "\n\n"
                + block("THE LINE'S ACTION PARTS (the brain runs them; your reply is said once they are done)",
                        [f"- {q.to}: {q.instruction}"
                         + (f" — dropped, it cannot be done: {q.drop}" if getattr(q, "drop", "") else "")
                         for q in acts]) + "\n\n"
                + block("THE LINE'S TALK PARTS (only your reply answers them)", [f"- \"{q.part}\"" for q in talk])
                + f"\n\n{reply}\n")

    async def answer(self, ctx: Context, *, head: str, parts: list, k: int, given: list[str], others: list,
                     replying: bool, tasks: list[dict], copies: dict[str, dict]):
        """One call: an Answer whose value is a TaskAnswer, every string stripped and the reply
        emptied when it is not this call's and the answer is not a reply only. Raises LlmError."""
        output = answer_type({str(t.get("name")) for t in tasks}, set(call.library(self.catalog)) | RESERVED,
                             copies, self.catalog, replying)
        got = await self.ask(self.tail(head=head, parts=parts, k=k, given=given, others=others, replying=replying,
                                       tasks=tasks, copies=copies),
                             output, cached=ctx.cached(self.opening(), self.checkpoint))
        v = got.value
        v.task, v.name, v.copy = v.task.strip(), v.name.strip(), v.copy.strip()
        v.description, v.brief, v.reply = v.description.strip(), v.brief.strip(), v.reply.strip()
        v.values = [EditValue(key=x.key.strip(), value=x.value.strip()) for x in v.values]
        v.copies = [c.strip() for c in v.copies if c.strip()]
        if v.kind == FOLLOW_UP:
            v.name = ""
        if not replying and v.kind != REPLY:
            v.reply = ""
        return got
