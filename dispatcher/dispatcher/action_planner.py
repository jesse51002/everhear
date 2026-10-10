"""The action planner: one pick's instruction to the exact action, a hand-on or a drop.

It sees ONE action: its full docstring, each option's description and limits, the pick's
instruction, the task's context, the saved places, the reports of the task's earlier
actions and the last failure; for `run_script`, what each script does and every value it
takes; for `stop_script`, what each script does.
It never sees the other actions, the task's other parts or the server.

`command` is written in steps, with Minecraft's own reference in between:
1. a draft, from the list of every command (`refs.menu()`) but those that place or remove
   blocks and the owner's, which the drafting model never sees;
2. the reference for every command the draft uses (`refs.used()`, `refs.reference()`);
3. an approve call, which returns the draft as it is or changed;
4. a changed draft using a command not yet referenced gets that reference and is checked
   again, at most MAX_RECHECKS times; past that it is a failure (`Unchecked`).
A retry after a failure is written with the references already sent in view, and is
checked again only when it uses a new command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, create_model, model_validator

from .actions import action_name, doc
from .config import log as default_log

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "action_planner.md"
MAX_RECHECKS = 2  # approve calls after the first, for a draft that keeps adding commands
SHOWS_SCRIPTS = {"run_script", "stop_script"}

A = TypeVar("A", bound=BaseModel)


def _exactly_one(n: int, names: str) -> None:
    if n != 1:
        raise ValueError(f"set exactly one of {names}; this answer sets {n}")


class Planned(BaseModel, Generic[A]):
    action: A | None = Field(
        None, description="the exact action to run, its options chosen; null when you hand it on or drop it"
    )
    hand_on: str = Field(
        "", description="only when this action cannot do the instruction: what the orchestrator should do instead"
    )
    drop: str = Field(
        "", description="only when it is not needed or nothing can fix it: why, in words a player understands"
    )
    say: str = Field(description="one sentence for the conversation: what this action does, for whom")

    @model_validator(mode="after")
    def _one_of_three(self):
        _exactly_one((self.action is not None) + bool(self.hand_on.strip()) + bool(self.drop.strip()),
                     "action, hand_on and drop")
        return self


class Final(BaseModel):
    """After the last retry: only a hand-on or a drop is left."""
    hand_on: str = Field("", description="what the orchestrator should do instead")
    drop: str = Field("", description="why it is dropped, in words a player understands")
    say: str = Field(description="one sentence for the conversation: what happened to this action, for whom")

    @model_validator(mode="after")
    def _one_of_two(self):
        _exactly_one(bool(self.hand_on.strip()) + bool(self.drop.strip()), "hand_on and drop")
        return self


def all_required(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with every property of every object listed as required.

    The model leaves out what the schema lets it leave out, and for an option that is the
    wrong default as often as not: teleport to a place with no `target`, or a drop with
    no `drop` (then none of the three is set). Listing them all makes it write each one,
    its default included; pydantic still fills in any it leaves out."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    return schema


class _AllRequired:
    """For the classes the planner sends to structured(): the schema as all_required() makes it."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return all_required(super().model_json_schema(*args, **kwargs))  # type: ignore[misc]


@cache
def planned_type(cls: type[BaseModel]) -> type[Planned]:
    """`Planned[cls]` under a name OpenRouter accepts as a schema name (no brackets)."""
    return create_model(f"Planned_{action_name(cls) or cls.__name__}", __base__=(_AllRequired, Planned[cls]))


@cache
def final_type(cls: type[BaseModel]) -> type[Final]:
    return create_model(f"Final_{action_name(cls) or cls.__name__}", __base__=(_AllRequired, Final))


class Unchecked(Exception):
    """A command draft kept changing to commands that were never checked against a reference."""


@dataclass
class Spent:
    """The model calls one task made, for its log line."""
    calls: int = 0
    ms: int = 0
    cost: float = 0.0

    def add(self, answer) -> None:
        self.calls += 1
        self.ms += answer.ms
        self.cost += answer.cost


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


@dataclass
class Checked:
    """For one `command` pick, across its retries: what has been checked against a reference."""
    names: set[str] = field(default_factory=set)
    subs: set[str] = field(default_factory=set)


class Planner:
    def __init__(self, ask, model: str, refs, scripts: dict[str, str] | None = None, *,
                 hidden: frozenset[str] = frozenset(), log=default_log, prompt: Path = PROMPT):
        self.ask = ask
        self.model = model
        self.refs = refs  # the latest; each task takes them as they are when it starts (Pick.refs)
        self.scripts = scripts or {}
        self.hidden = hidden  # left out of the command list: block and owner commands
        self.log = log
        self.system = prompt.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ prompts

    def describe(self, cls: type[BaseModel]) -> str:
        name = action_name(cls) or cls.__name__
        text = f"ACTION: {name}\n{doc(cls)}\n\nOPTIONS:\n{options(cls)}\n"
        if name == "run_script" and self.scripts:
            rows = "\n".join(f"- {n}: {about}" for n, about in self.scripts.items())
            text += ("\nSCRIPTS (each: what it does, then every value it takes as key (type, default, "
                     "limits or options), set as key=value with the key exactly as listed; "
                     "for one written the old way, its usage and how it ends):\n"
                     f"{rows}\n")
        elif name in SHOWS_SCRIPTS and self.scripts:
            rows = "\n".join(f"- {n}: {(about.splitlines() or [''])[0]}" for n, about in self.scripts.items())
            text += f"\nSCRIPTS (each: what it does):\n{rows}\n"
        return text

    def base(self, cls: type[BaseModel], p: Pick) -> str:
        earlier = "".join(f"\n- {r}" for r in p.reports) or " none"
        return (f"{self.describe(cls)}\nINSTRUCTION: {p.instruction}\nCONTEXT: {p.context or '(none)'}\n"
                f"{p.places}\nEARLIER IN THIS TASK:{earlier}\n")

    def draft_prompt(self, cls, p: Pick) -> str:
        return (f"{self.base(cls, p)}\nMINECRAFT COMMANDS (name — what it does):\n{p.refs.menu(exclude=self.hidden)}\n\n"
                "Write the commands. Before they run you will see the exact reference for every command "
                "you use, and can correct them.\n")

    def approve_prompt(self, cls, p: Pick, commands: list[str], reference: str) -> str:
        return (f"{self.base(cls, p)}\nYOUR DRAFT:\n{numbered(commands)}\n\n"
                f"REFERENCES (each command's syntax on this server, then the wiki's Java Edition notes):\n"
                f"{reference}\n\n"
                "Check every command in your draft against its reference: the syntax, the ids, the selectors. "
                "Answer with the action as it is when it is right, or corrected when it is not; "
                "or hand it on, or drop it.\n")

    def retry_prompt(self, cls, p: Pick, last: BaseModel | None, failure: str, final: bool,
                     reference: str = "") -> str:
        text = f"{self.base(cls, p)}\nYOUR LAST ATTEMPT: {attempt(last)}\nIT FAILED: {failure}\n"
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
        answer = await self.ask(prompt, output, system=self.system, model=self.model)
        p.spent.add(answer)
        self.log("plan", f"{p.task_id} #{p.index + 1} {step} {answer.ms} ms ${answer.cost:.6f}: {outcome(answer.value)}")
        return answer.value

    async def plan(self, cls: type[BaseModel], p: Pick, *, failure: str | None = None,
                   last: BaseModel | None = None, final: bool = False,
                   checked: Checked | None = None) -> Planned | Final:
        """The action, a hand-on or a drop. `final`: no retry is left, so only the last two.
        Raises LlmError (a hand-on for the caller) and, for `command`, Unchecked (a failure)."""
        name = action_name(cls) or cls.__name__
        if final:
            return await self._call(f"{name} last", self.retry_prompt(cls, p, last, failure or "", True),
                                    final_type(cls), p)
        if name == "command":
            return await self._command(cls, p, failure, last, checked if checked is not None else Checked())
        if failure is None:
            return await self._call(name, self.base(cls, p), planned_type(cls), p)
        return await self._call(f"{name} retry", self.retry_prompt(cls, p, last, failure, False),
                                planned_type(cls), p)

    async def _command(self, cls, p: Pick, failure: str | None, last, checked: Checked) -> Planned:
        Out = planned_type(cls)
        if p.refs is None:
            return Out(hand_on=p.instruction, say=f"the orchestrator will do this: {p.instruction}")
        if failure is None:
            planned = await self._call("command draft", self.draft_prompt(cls, p), Out, p)
        else:
            reference = p.refs.reference(checked.names, checked.subs) if checked.names else ""
            planned = await self._call("command retry", self.retry_prompt(cls, p, last, failure, False, reference),
                                       Out, p)
        approvals = 0
        while planned.action is not None:
            used, subs = p.refs.used(list(planned.action.commands))
            new = (set(used) - checked.names) | {f"execute {s}" for s in set(subs) - checked.subs}
            if not new and (approvals or failure is not None):
                return planned  # every command in it was written with its reference in view
            if approvals > MAX_RECHECKS:
                raise Unchecked(f"the draft kept changing to commands never checked: {', '.join(sorted(new))}")
            checked.names |= set(used)
            checked.subs |= set(subs)
            step = "command approve" if not approvals else f"command recheck {approvals}"
            planned = await self._call(step, self.approve_prompt(cls, p, list(planned.action.commands),
                                                                 p.refs.reference(set(used), set(subs))), Out, p)
            approvals += 1
        return planned


# ---------------------------------------------------------------------- text

def numbered(commands: list[str]) -> str:
    return "\n".join(f"{i}. {c}" for i, c in enumerate(commands, 1))


def attempt(action: BaseModel | None) -> str:
    if action is None:
        return "(none)"
    fields = action.model_dump(mode="json", exclude={"action"})
    if set(fields) == {"commands"}:
        return "\n" + numbered(fields["commands"])
    return json.dumps(fields, ensure_ascii=False)


def outcome(value: BaseModel) -> str:
    """One log line on what an answer chose."""
    action = getattr(value, "action", None)
    if action is not None:
        return attempt(action).replace("\n", " ").strip()
    if value.hand_on.strip():
        return f"hand on: {value.hand_on.strip()}"
    return f"drop: {value.drop.strip()}"


def options(cls: type[BaseModel]) -> str:
    """Each option but `action`, in plain words: its description, then its limits and default."""
    schema = cls.model_json_schema()
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        if name == "action":
            continue
        prop = _resolve(prop, defs)
        bits = [b for b in (prop.get("description", "").strip().rstrip("."), _kind(prop, defs)) if b]
        if "default" in prop and name not in required:
            bits.append(f"default {json.dumps(prop['default'], ensure_ascii=False)}")
        rows.append(f"- {name}{' (required)' if name in required else ''}: {'; '.join(bits)}")
    return "\n".join(rows) or "(none)"


def _resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref")
    if not ref:
        return prop
    target = defs.get(ref.rsplit("/", 1)[-1], {})
    return {**target, **{k: v for k, v in prop.items() if k != "$ref"}}


def _range(prop: dict[str, Any], low: str, high: str, unit: str = "", word: str = "from") -> str:
    lo, hi = prop.get(low), prop.get(high)
    if lo is not None and hi is not None:
        return f" {word} {lo:g} to {hi:g}{unit}"
    if lo is not None:
        return f" of at least {lo:g}{unit}"
    if hi is not None:
        return f" of at most {hi:g}{unit}"
    return ""


def _kind(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    prop = _resolve(prop, defs)
    if "anyOf" in prop:
        kinds = [k for k in prop["anyOf"] if k.get("type") != "null"]
        text = " or ".join(filter(None, (_kind(k, defs) for k in kinds)))
        return text + (" or none" if len(kinds) < len(prop["anyOf"]) else "")
    if "enum" in prop:
        return "one of " + ", ".join(str(v) for v in prop["enum"])
    if "const" in prop:
        return f"always {prop['const']}"
    kind = prop.get("type")
    if kind == "string":
        text = "text" + _range(prop, "minLength", "maxLength", " characters", "of")
        return text + (f" matching {prop['pattern']}" if prop.get("pattern") else "")
    if kind == "integer":
        return "a whole number" + _range(prop, "minimum", "maximum")
    if kind == "number":
        return "a number" + _range(prop, "minimum", "maximum")
    if kind == "boolean":
        return "true or false"
    if kind == "array":
        items = _kind(prop.get("items", {}), defs) or "anything"
        lo, hi = prop.get("minItems"), prop.get("maxItems")
        count = (f" of {lo} to {hi} items" if lo is not None and hi is not None
                 else f" of at least {lo} items" if lo is not None
                 else f" of at most {hi} items" if hi is not None else "")
        return f"a list{count}, each {items}"
    return ""
