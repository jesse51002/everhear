"""The action planner: one pick's instruction to the exact action, a hand-on or a drop.

It sees ONE action: its whole docstring, how it runs, and each value it takes (its `takes`),
with its type, limits, default and description; the pick's instruction, the task's context,
the saved places, the reports of the task's earlier actions and the last failure. It answers
in planner_model(cls): the values the action takes, and nothing else. It never sees the
other actions, the task's other parts or the server.

An action with a Command field (`command`'s `commands`, or any other) is written in steps,
with Minecraft's own reference in between:
1. a draft, from the list of every command (`refs.menu()`) but those that place or remove
   blocks and the owner's, which the drafting model never sees;
2. the reference for every command the draft uses (`refs.used()`, `refs.reference()`);
3. an approve call, which returns the draft as it is or changed;
4. a changed draft using a command not yet referenced gets that reference and is checked
   again, at most MAX_RECHECKS times; past that it is a failure (`Unchecked`).
A retry after a failure is written with the references already sent in view, and is
checked again only when it uses a new command.

Steps 2 to 4 run only with `check` (VFG_PLAN_CHECK=on). Off, the default, the draft runs as
written, a planner call sooner. In the brain the planner only retries a command Minecraft
refused, and that retry is still written with the reference of every command the refused one
used.

`stop` and `edit` act on a running copy, and also see every running copy's line. The copy is
the one the instruction names, when it names exactly one; for `edit`, the only one running,
when one is; else the planner picks it from the lines. A stop whose copy is named needs no
model call. An edit is then planned again, nested as a command's reference is: the planner
sees that copy in full, describe(action, cls, its values now), and answers in edit_model(cls),
the values to change, every other null.
"""

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, PrivateAttr, create_model, model_validator
from vfg_actions import check_edit, check_values, describe, edit_model, planner_model
from vfg_llm import LlmError

from .actions import command_fields, commands_in, doc, named, view
from .config import log as default_log

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "action_planner.md"
MAX_RECHECKS = 2  # approve calls after the first, for a draft that keeps adding commands
COPY_ACTIONS = ("stop", "edit")  # the actions that act on a running copy, by its name

A = TypeVar("A", bound=BaseModel)


def _exactly_one(n: int, names: str) -> None:
    if n != 1:
        raise ValueError(f"set exactly one of {names}; this answer sets {n}")


class Planned(BaseModel, Generic[A]):
    needs: ClassVar[tuple[str, ...]] = ()  # Command fields that must hold a command
    changes: ClassVar[bool] = False         # an edit: at least one value is set
    _given: dict | None = PrivateAttr(None)  # the action as the model wrote it, before a number was clamped

    action: A | None = Field(
        None, description="the exact action to run, its values chosen; null when you hand it on or drop it"
    )
    hand_on: str = Field(
        "", description="only when this action cannot do the instruction: what an implementer should do instead"
    )
    drop: str = Field(
        "", description="only when it is not needed or nothing can fix it: why, in words a player understands"
    )
    say: str = Field(description="one sentence for the conversation: what this action does, for whom")

    @model_validator(mode="wrap")
    @classmethod
    def _keep_given(cls, data: Any, handler):
        out = handler(data)
        if isinstance(data, dict) and isinstance(data.get("action"), dict):
            out._given = data["action"]
        return out

    @model_validator(mode="after")
    def _one_of_three(self):
        _exactly_one((self.action is not None) + bool(self.hand_on.strip()) + bool(self.drop.strip()),
                     "action, hand_on and drop")
        if self.action is not None:
            for key in self.needs:
                if not getattr(self.action, key):
                    raise ValueError(f"{key} is empty: write the commands, or hand it on or drop it")
            if self.changes and not self.action.model_dump(exclude_none=True):
                raise ValueError("no value is set: set the values to change, or hand it on or drop it")
        return self


class Final(BaseModel):
    """After the last retry: only a hand-on or a drop is left."""
    hand_on: str = Field("", description="what an implementer should do instead")
    drop: str = Field("", description="why it is dropped, in words a player understands")
    say: str = Field(description="one sentence for the conversation: what happened to this action, for whom")

    @model_validator(mode="after")
    def _one_of_two(self):
        _exactly_one(bool(self.hand_on.strip()) + bool(self.drop.strip()), "hand_on and drop")
        return self


def all_required(schema: dict[str, Any], nested: bool = True) -> dict[str, Any]:
    """The schema with every property of every object listed as required (`nested` False: of
    the outer object alone).

    The model leaves out what the schema lets it leave out, and for a value that is the
    wrong default as often as not: teleport to a place with no `target`, or a drop with
    no `drop` (then none of the three is set). Listing them all makes it write each one,
    its default included; pydantic still fills in any it leaves out. An edit's values are
    the exception: one left out is one not changed."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    if nested:
        walk(schema)
    elif isinstance(schema.get("properties"), dict):
        schema["required"] = list(schema["properties"])
    return schema


class _AllRequired:
    """For the classes the planner sends to structured(): the schema as all_required() makes it."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return all_required(super().model_json_schema(*args, **kwargs))  # type: ignore[misc]


class _OuterRequired:
    """An edit's: the answer's own fields required, the values inside it not."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return all_required(super().model_json_schema(*args, **kwargs), nested=False)  # type: ignore[misc]


_planned: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def make_planned(name: str, model: type[BaseModel], *, needs: tuple[str, ...] = (),
                 changes: bool = False) -> type[Planned]:
    """`Planned[model]` under a name OpenRouter accepts as a schema name (no brackets):
    Planned_teleport. `needs`: the Command fields that may not be empty; `changes`: an edit's,
    at least one value set and the values inside not required."""
    out = create_model(f"Planned_{name}", __base__=(_OuterRequired if changes else _AllRequired, Planned[model]))
    out.needs, out.changes = tuple(needs), changes
    return out


def planned_type(name: str, model: type[BaseModel], *, needs: tuple[str, ...] = (),
                 changes: bool = False) -> type[Planned]:
    """make_planned(), kept for as long as the action's model is (a copy's choice, made for
    the copies running at the time, is made each time instead)."""
    per = _planned.setdefault(model, {})
    key = (name, needs, changes)
    if key not in per:
        per[key] = make_planned(name, model, needs=needs, changes=changes)
    return per[key]


@cache
def final_type(name: str) -> type[Final]:
    return create_model(f"Final_{name}", __base__=(_AllRequired, Final))


def copy_model(names: list[str]) -> type[BaseModel]:
    """stop's answer, and an edit's first: one of the running copies, by its name."""
    return create_model("Copy", copy=(Literal[tuple(names)], Field(description="the running copy's name")))  # type: ignore[valid-type]


class Unchecked(Exception):
    """A command draft kept changing to commands that were never checked against a reference."""


@dataclass
class Spent:
    """The model calls one task made, for its log line; a failed one counts too (an LlmError
    carries what it spent; `known` False once one timed out with an attempt in flight, whose cost
    is not in it). Each call's usage row is structured()'s own."""
    calls: int = 0
    ms: int = 0
    cost: float = 0.0
    known: bool = True

    def add(self, answer) -> None:
        self.calls += 1
        self.ms += answer.ms
        self.cost += answer.cost
        self.known = self.known and getattr(answer, "cost_known", True)


@dataclass
class Pick:
    """What the action planner is told about one pick."""
    task_id: str
    index: int                  # 0-based, in the task's picks
    instruction: str
    context: str
    places: str                 # the `SAVED PLACES: …` line
    reports: list[str]          # the task's earlier actions' reports
    spent: Spent = field(default_factory=Spent)
    refs: Any = None            # the references there were when the task started; None: none yet
    already: list[str] = field(default_factory=list)  # commands of this pick that ran and took effect
    known: set[str] | None = None  # the saved places' names, for the values' check; None: not known
    copies: dict[str, dict] = field(default_factory=dict)  # running.json's copies when the task started
    running: str = "RUNNING NOW: none"  # their lines
    catalog: Any = None         # for a copy's own action
    copy: str = ""              # the copy an edit changes, once chosen


@dataclass
class Checked:
    """For one pick with Command fields, across its retries: what has been checked against a reference."""
    names: set[str] = field(default_factory=set)
    subs: set[str] = field(default_factory=set)


class Planner:
    def __init__(self, ask, model: str, refs, *, hidden: frozenset[str] = frozenset(), log=default_log,
                 prompt: Path = PROMPT, check: bool = False):
        self.ask = ask
        self.model = model
        self.check = check  # VFG_PLAN_CHECK: the approve call before a drafted command runs
        self.refs = refs  # the latest; each task takes them as they are when it starts (Pick.refs)
        self.hidden = hidden  # left out of the command list: block and owner commands
        self.log = log
        self.system = prompt.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ prompts

    @staticmethod
    def base(head: str, p: Pick) -> str:
        earlier = "".join(f"\n- {r}" for r in p.reports) or " none"
        return (f"{head}\nINSTRUCTION: {p.instruction}\nCONTEXT: {p.context or '(none)'}\n"
                f"{p.places}\nEARLIER IN THIS TASK:{earlier}\n")

    @staticmethod
    def copies_head(name: str, cls, p: Pick) -> str:
        """stop's view, and edit's while its copy is not chosen: the action, then every copy."""
        return f"{view(name, cls)}\n{p.running}\n"

    @staticmethod
    def edit_head(cls, p: Pick, copy_cls) -> str:
        """edit's view of the copy it changes: every value it has, with its setting now."""
        info = p.copies[p.copy]
        task = str(info.get("task") or "").strip()
        current = info.get("values") if isinstance(info.get("values"), dict) else {}
        return (f"ACTION: edit\n{doc(cls)}\n\n{p.running}\n\n"
                f"THE COPY TO CHANGE: {p.copy}, a copy of {info.get('action')}"
                f"{f', started by {task}' if task else ''}. Its values (now: its setting):\n"
                f"{describe(str(info.get('action')), copy_cls, current)}\n\n"
                "Answer with the values to change, each by its name above with its new setting, "
                "and every other null.\n")

    def draft_prompt(self, head: str, p: Pick) -> str:
        return (f"{self.base(head, p)}\nMINECRAFT COMMANDS (name — what it does):\n{p.refs.menu(exclude=self.hidden)}\n\n"
                "Write the commands. Before they run you will see the exact reference for every command "
                "you use, and can correct them.\n")

    def approve_prompt(self, head: str, p: Pick, commands: list[str], reference: str) -> str:
        return (f"{self.base(head, p)}\nYOUR DRAFT:\n{numbered(commands)}\n\n"
                f"REFERENCES (each command's syntax on this server, then the wiki's Java Edition notes):\n"
                f"{reference}\n\n"
                "Check every command in your draft against its reference: the syntax, the ids, the selectors. "
                "Answer with the action as it is when it is right, or corrected when it is not; "
                "or hand it on, or drop it.\n")

    def retry_prompt(self, head: str, p: Pick, last: dict | None, failure: str, final: bool,
                     reference: str = "") -> str:
        text = f"{self.base(head, p)}\nYOUR LAST ATTEMPT: {attempt(last)}\nIT FAILED: {failure}\n"
        if p.already:
            text += (f"\nTHESE ALREADY RAN AND TOOK EFFECT:\n{numbered(p.already)}\n"
                     "Do NOT repeat them; write only what is still to do.\n")
        if reference:
            text += f"\nREFERENCES (each command's syntax on this server, then the wiki's Java Edition notes):\n{reference}\n"
        if final:
            return text + "\nIt cannot be tried again. Hand it on or drop it.\n"
        return text + "\nChange the action, hand it on, or drop it.\n"

    # ------------------------------------------------------------------ calls

    async def _call(self, step: str, prompt: str, output: type[BaseModel], p: Pick):
        """One planner call: its usage row is structured()'s, component planner, `<task> #<part>`."""
        try:
            answer = await self.ask(prompt, output, system=self.system, model=self.model, component="planner",
                                    call_id=f"{p.task_id} #{p.index + 1}"[:64])
        except LlmError as e:
            p.spent.add(e)
            raise
        p.spent.add(answer)
        self.log("plan", f"{p.task_id} #{p.index + 1} {step} {answer.ms} ms ${answer.cost:.6f}: {outcome(answer.value)}")
        return answer.value

    async def plan(self, name: str, cls, p: Pick, *, failure: str | None = None, last: dict | None = None,
                   final: bool = False, checked: Checked | None = None) -> Planned | Final:
        """The action, a hand-on or a drop. `final`: no retry is left, so only the last two.
        `last` is the values the last attempt ran with. Raises LlmError (a hand-on for the
        caller) and, for an action with Command fields, Unchecked (a failure)."""
        if name in COPY_ACTIONS:
            return await self._copy_action(name, cls, p, failure, last, final)
        if final:
            return await self._call(f"{name} last", self.retry_prompt(view(name, cls), p, last, failure or "", True),
                                    final_type(name), p)
        fields = tuple(k for k in command_fields(cls) if k in cls.takes)
        if fields:
            return await self._commands(name, cls, p, failure, last, checked if checked is not None else Checked(),
                                        fields)
        Out = planned_type(name, planner_model(cls))
        if failure is None:
            return await self._call(name, self.base(view(name, cls), p), Out, p)
        return await self._call(f"{name} retry", self.retry_prompt(view(name, cls), p, last, failure, False), Out, p)

    async def _commands(self, name: str, cls, p: Pick, failure: str | None, last, checked: Checked,
                        fields: tuple[str, ...]) -> Planned | Final:
        Out = planned_type(name, planner_model(cls), needs=fields)
        head = view(name, cls)
        if p.refs is None:
            return Final(hand_on=p.instruction, say=f"an implementer would do this: {p.instruction}")
        if failure is None:
            planned = await self._call(f"{name} draft", self.draft_prompt(head, p), Out, p)
        else:
            reference = p.refs.reference(checked.names, checked.subs) if checked.names else ""
            planned = await self._call(f"{name} retry", self.retry_prompt(head, p, last, failure, False, reference),
                                       Out, p)
        if not self.check:
            return planned
        approvals = 0
        while planned.action is not None:
            commands = commands_in(planned.action.model_dump(mode="json"), fields)
            used, subs = p.refs.used(commands)
            new = (set(used) - checked.names) | {f"execute {s}" for s in set(subs) - checked.subs}
            if not new and (approvals or failure is not None):
                return planned  # every command in it was written with its reference in view
            if approvals > MAX_RECHECKS:
                raise Unchecked(f"the draft kept changing to commands never checked: {', '.join(sorted(new))}")
            checked.names |= set(used)
            checked.subs |= set(subs)
            step = f"{name} approve" if not approvals else f"{name} recheck {approvals}"
            planned = await self._call(step, self.approve_prompt(head, p, commands,
                                                                 p.refs.reference(set(used), set(subs))), Out, p)
            approvals += 1
        return planned

    async def _copy_action(self, name: str, cls, p: Pick, failure: str | None, last, final: bool) -> Planned | Final:
        """stop and edit: the copy, then for edit the values to change."""
        names = sorted(p.copies)
        verb = "stop" if name == "stop" else "change"
        if not names:
            return Final(drop="nothing is running", say=f"nothing is running to {verb}")
        if name == "edit" and p.copy:
            return await self._edit_values(cls, p, failure, last, final)
        if final:
            return await self._call(f"{name} last", self.retry_prompt(self.copies_head(name, cls, p), p, last,
                                                                      failure or "", True), final_type(name), p)
        Model = copy_model(names)
        Out = make_planned(name if name == "stop" else "edit_copy", Model)
        said = named(p.instruction, names)
        if failure is None and len(said) == 1:
            chosen = Out(action=Model(copy=said[0]), say=f"{'stopping' if name == 'stop' else 'changing'} {said[0]}")
            self.log("plan", f"{p.task_id} #{p.index + 1} {name}: {said[0]}, named in the instruction")
        elif failure is None and name == "edit" and len(names) == 1:
            chosen = Out(action=Model(copy=names[0]), say=f"changing {names[0]}")
            self.log("plan", f"{p.task_id} #{p.index + 1} edit: {names[0]}, the only copy running")
        elif failure is None:
            chosen = await self._call(f"{name} copy", self.base(self.copies_head(name, cls, p), p), Out, p)
        else:
            chosen = await self._call(f"{name} retry", self.retry_prompt(self.copies_head(name, cls, p), p, last,
                                                                         failure, False), Out, p)
        if name == "stop" or chosen.action is None:
            return chosen
        p.copy = chosen.action.copy
        return await self._edit_values(cls, p, None, None, False)

    async def _edit_values(self, cls, p: Pick, failure: str | None, last, final: bool) -> Planned | Final:
        info = p.copies[p.copy]
        action = str(info.get("action") or "")
        copy_cls = p.catalog.get(action) if p.catalog is not None else None
        if copy_cls is None:
            return Final(hand_on=f"{p.instruction} (the copy {p.copy}'s action {action or '?'} is not loaded)",
                         say=f"an implementer would change {p.copy}")
        head = self.edit_head(cls, p, copy_cls)
        if final:
            return await self._call("edit last", self.retry_prompt(head, p, last, failure or "", True),
                                    final_type("edit"), p)
        Out = planned_type("edit", edit_model(copy_cls), changes=True)
        if failure is None:
            return await self._call(f"edit {p.copy}", self.base(head, p), Out, p)
        return await self._call("edit retry", self.retry_prompt(head, p, last, failure, False), Out, p)

    # ------------------------------------------------------------------ the values to run

    @staticmethod
    def values(name: str, cls, planned: Planned, p: Pick) -> tuple[dict[str, Any], list[str]]:
        """What `vfg action run <name>` is given for a planned action, checked as vfg checks it:
        the values the planner set (for an edit, the copy and the values it changes), and the
        notes on any number clamped to its limits, read from what the model wrote. Raises
        ValueError, saying what is wrong."""
        places = None if p.known is None else {n: {} for n in p.known}
        action = planned.action
        if name == "stop":
            return {"copy": action.copy}, []
        wrote = planned._given or {}
        if name == "edit":
            info = p.copies[p.copy]
            given = action.model_dump(mode="json", exclude_none=True)
            edit = check_edit(p.catalog.get(info["action"]), info.get("values") or {},
                              {k: wrote.get(k, v) for k, v in given.items()}, places=places)
            return {"copy": p.copy, **{k: edit.values[k] for k in given}}, edit.notes
        given = action.model_dump(mode="json", exclude_unset=True)
        checked = check_values(cls, {k: wrote.get(k, v) for k, v in given.items()}, places=places)
        return {k: checked.values[k] for k in given}, checked.notes


# ---------------------------------------------------------------------- text

def numbered(commands: list[str]) -> str:
    return "\n".join(f"{i}. {c}" for i, c in enumerate(commands, 1))


def attempt(values: dict | None) -> str:
    """The values an attempt ran with: a command list numbered, anything else as JSON."""
    if values is None:
        return "(none)"
    if set(values) == {"commands"} and isinstance(values["commands"], list):
        return "\n" + numbered(values["commands"])
    return json.dumps(values, ensure_ascii=False)


def outcome(value: BaseModel) -> str:
    """One log line on what an answer chose."""
    action = getattr(value, "action", None)
    if action is not None:
        return attempt(action.model_dump(mode="json", exclude_none=True)).replace("\n", " ").strip()
    if value.hand_on.strip():
        return f"hand on: {value.hand_on.strip()}"
    return f"drop: {value.drop.strip()}"
