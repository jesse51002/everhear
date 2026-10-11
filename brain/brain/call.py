"""The brain's one call: each event is one structured() call, on GPT-6.1 Sol with low reasoning,
which routes every part of a line and does the work in the same answer (Prototype_Brain).

Two events, one `Decision`, so the cached prefix is the same whatever the event:
- LINE: a line Jev classed action_oriented, or a conversational one Respond handed up;
- REPORT: an implementer's result.

A part goes to an action, written in full in `action` and ready to run; to `orchestrator`, with
an instruction, for anything new no action does, a change or fix to something that exists, or
stopping something running (code then makes the orchestrator's call, orchestrator.py, which
names the task: the brain never does, Prototype_Named_Tasks); or to `talk`, answered by `reply`.
When a part goes to the orchestrator, the orchestrator writes the line's reply, and the brain's
is held back, said only if the orchestrator's call fails (act.py). The prompt, in the order the
cache needs:
1. the opening, marked: the rules (prompts/brain.md, with the owner named), the menu, every
   library action in full and Minecraft's command list;
2. the conversation, a minute-block at a time, marked at checkpoints (context.py);
3. the tail, never cached: this server's own actions (an implementer may add one at any
   moment, and the opening must not change for it), the world, TASKS (every task on the server,
   open and closed: tasks.py), what is new since the last read (never a context line:
   context.py), and the event.

The schema never changes with the actions: `to` is a name and `action` the values, both
checked here against the actions as they are now, so a new action never changes the request
before the conversation. The old selector's code rules that still hold are the Decision's
validators, and an answer that breaks one goes back to the model once with why (structured()):
an action that is not on the menu, values its action refuses (a command that places blocks
among them: building is an action's that builds, else the orchestrator's), an instruction that
does nothing, a teleport that moves nobody, and a part with no reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from vfg_actions import check_values, menu_line

from .actions import Catalog, view
from .context import Context, Row, show
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "brain.md"
ORCHESTRATOR, TALK = "orchestrator", "talk"
REPLY_MAX = 300
HEAD_REPORT_MAX = 600  # characters of a report in the head the orchestrator is given

# An instruction that does nothing.
PLACEHOLDER = re.compile(r"^\W*(nothing( to do)?|none( needed)?|n/?a|no[ -]?op|skip|not needed|do nothing|"
                         r"no action( needed)?|empty|placeholder|tbd)?\W*$", re.IGNORECASE)
# A teleport to where the player already is: a part invented to fill the list.
NO_MOVE = re.compile(r"\bto (his|her|their|its|your) (own )?((current|present) )?(position|location)\b"
                     r"|\bto (his|her|their|its|your) (own )?(current|present) (spot|place)\b"
                     r"|\bto where (he|she|they|it) (is|are|stands?|stood)\b", re.IGNORECASE)


class Value(BaseModel):
    """One value of an action: a closed object, as OpenAI's schemas demand, so the schema stays
    the same whatever the actions."""
    key: str = Field(description="the field's name, as the action's FIELDS give it")
    value: str | int | float | bool | list[str] | list[int | float] = Field(
        description="its value: text, a number, true or false, or a list for a list field")


class PartBase(BaseModel):
    part: str = Field(description="the words of the line this part covers, short, as the player said them")
    to: str = Field(description="the action's name on the menu, or orchestrator, or talk")
    instruction: str = Field("", description="an action: one short sentence in plain words, the player names "
                                             "and amounts as said; orchestrator: what to make, change or stop, "
                                             "for whom; talk: empty")
    action: list[Value] | None = Field(None, description="an action part: the action written in full, ready "
                                                         "to run, one key and value per field it sets; null for "
                                                         "orchestrator, talk and a drop")
    drop: str = Field("", description="only when an action part cannot be done (an item the player does not "
                                      "have): why, in words a player understands; else empty")


    def values(self) -> dict[str, Any]:
        """The action as `vfg action run` takes it: {field: value}; for edit, the copy and the
        values to change, {"copy": …, "values": {…}}, whether each came as a key of its own or
        as `values.<name>`, or some each way. A Value cannot hold an object, so a field that
        holds another action's values (incantation_create's) comes as `values.<name>` keys,
        gathered here into {"values": {…}}."""
        given = {v.key.strip(): v.value for v in self.action or []}
        inner = {k.split(".", 1)[1].strip(): given.pop(k) for k in list(given) if k.startswith("values.")}
        if inner:
            given["values"] = {**(given["values"] if isinstance(given.get("values"), dict) else {}), **inner}
        if self.to != "edit":
            return given
        copy = given.pop("copy", "")
        nested = given.pop("values", None)
        return {"copy": copy, "values": {**given, **nested} if isinstance(nested, dict) else given}


class DecisionBase(BaseModel):
    parts: list[PartBase] = Field(default_factory=list, description="every part of the line, in the order they "
                                                                    "run; may be empty for a report or a batch")
    reply: str = Field("", description="the one chat line the players see, in the AI's own voice; required "
                                       "whenever there is any part")
    record: str = Field("", description="for a REPORT: one short line for the context, such as 'castle_steve "
                                        "done: castle built at spawn'; else empty")
    status: Literal["", "done", "failed"] = Field("", description="for a REPORT: the task's status after it; "
                                                                  "else empty")


def decision_type(catalog: Catalog) -> type[DecisionBase]:
    """`Decision`, checked against `catalog` as it is when an answer arrives. Its JSON schema is
    the same for every catalog."""

    class Part(PartBase):
        @model_validator(mode="after")
        def _checked(self):
            said = self.instruction.strip()
            if self.to in (ORCHESTRATOR, TALK):
                if self.action is not None:
                    raise ValueError(f"the {self.to} part {self.part!r} carries an action: leave it null")
                if self.to == ORCHESTRATOR and not said:
                    raise ValueError(f"the orchestrator part {self.part!r} has no instruction")
            else:
                cls = catalog.get(self.to)
                if cls is None:
                    raise ValueError(f"{self.to} is not on the menu: use an action that is, or orchestrator")
                if not said:
                    raise ValueError(f"the {self.to} part {self.part!r} has no instruction")
                if self.drop.strip():
                    if self.action is not None:
                        raise ValueError(f"the {self.to} part {self.part!r} has both an action and a drop: one")
                elif self.action is None:
                    raise ValueError(f"the {self.to} part {self.part!r} has no action: write it, or drop it "
                                     "and say why")
                else:
                    keys = [v.key.strip() for v in self.action]
                    if len(set(keys)) < len(keys):
                        raise ValueError(f"the {self.to} part {self.part!r} sets a field twice: once each")
                    try:
                        check_values(cls, self.values())
                    except ValueError as e:
                        raise ValueError(f"the {self.to} part {self.part!r}: "
                                         + str(e).replace("the orchestrator's: hand it on",
                                                          "an action's that builds it, else the orchestrator's: "
                                                          "send that part to orchestrator")) from None
            if said and PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {self.instruction!r} does nothing: leave the part out")
            if self.to == "teleport" and NO_MOVE.search(said):
                raise ValueError(f"{self.instruction!r} moves nobody: leave the part out")
            return self

    class Decision(DecisionBase):
        parts: list[Part] = Field(default_factory=list,
                                  description=DecisionBase.model_fields["parts"].description)

        @model_validator(mode="after")
        def _replied(self):
            if self.parts and not self.reply.strip():
                raise ValueError("there are parts and no reply: every line acted on gets one chat line")
            if len(self.reply) > REPLY_MAX:
                raise ValueError(f"the reply is {len(self.reply)} characters: one chat line, under 200")
            return self

    return Decision


@dataclass
class Event:
    kind: Literal["LINE", "REPORT"]
    row: Row | None = None          # LINE: the line
    handed_up: str = ""             # LINE: Respond's reason, when Respond handed it up
    task: dict | None = None        # REPORT: the task's row (tasks.py)
    status: str = ""                # REPORT: done or failed, as the implementer said
    report: str = ""                # REPORT: the implementer's report, its DESCRIPTION line cut off; it
                                    # never reaches the context
    following: bool = False         # REPORT: a follow-up to the task already runs, so it stays open

    def ref(self) -> str:
        """The call's id in its usage row: `#<seq>` for a line, the task's name for a report."""
        if self.kind == "LINE":
            return f"#{self.row.seq}"
        return str((self.task or {}).get("name") or "report")[:64]

    def show(self) -> str:
        if self.kind == "LINE":
            r = self.row
            how = "typed" if r.source == "chat" else "said"
            text = f"LINE #{r.seq}, {r.hms}: {r.who} {how}: {r.text}"
            if self.handed_up:
                text += f"\n(Respond, the cheap mode, handed this line up to you: {self.handed_up})"
            return text
        t = self.task or {}
        name = t.get("name") or "?"
        more = (f"\nA FOLLOW-UP to {name} is already under way, so the task stays open: say how this step "
                "went, never that it is all done." if self.following else "")
        return (f"REPORT: the implementer of {name} ended, {self.status}.\n"
                f"{name} was for {t.get('player') or 'nobody'}, who said: {t.get('line') or ''}\n"
                f"WHAT IT IS: {t.get('description') or ''}\nITS LATEST BRIEF: {t.get('brief') or ''}\n"
                f"ITS REPORT:\n{self.report.strip()}{more}")

    def head(self) -> str:
        """The event as the orchestrator is told it serves it: `THE LINE #812, …` or `THE REPORT: …`,
        the report cut short."""
        if self.kind == "LINE":
            return "THE " + self.show()
        t = self.task or {}
        name = t.get("name") or "?"
        report = " ".join(self.report.split())
        if len(report) > HEAD_REPORT_MAX:
            report = report[:HEAD_REPORT_MAX - 1] + "…"
        return (f"THE REPORT: the implementer of {name} ended, {self.status}. {name} was for "
                f"{t.get('player') or 'nobody'}, who said: {t.get('line') or ''}\nITS REPORT: {report}")


def library(catalog: Catalog) -> list[str]:
    return [n for n in catalog.names() if catalog.source(n) == "library"]


def made(catalog: Catalog) -> list[str]:
    return [n for n in catalog.names() if catalog.source(n) != "library"]


def menu_lines(catalog: Catalog, names: list[str]) -> str:
    return "\n".join(f"- {menu_line(n, catalog.get(n))}" for n in names)


class Call:
    def __init__(self, catalog: Catalog, ask, *, owner: str = "", hidden: frozenset[str] = frozenset(),
                 checkpoint: int = 5, prompt: Path = PROMPT):
        self.catalog = catalog
        self.ask = ask  # structured() with the brain's model, reasoning, providers and timeout
        self.hidden = hidden
        self.checkpoint = checkpoint
        rules = prompt.read_text(encoding="utf-8").rstrip("\n")
        who = owner or "not named (VFG_OWNER is not set)"
        self.rules = rules.replace("{owner}", who)
        self.Decision = decision_type(catalog)
        self._opening: tuple[tuple, str] | None = None

    def opening(self, refs) -> str:
        """The rules, the menu, every library action in full and the command list: the same for
        every call while the library and the references stay as they are."""
        names = library(self.catalog)
        key = (id(refs), tuple((n, id(self.catalog.get(n))) for n in names))
        if self._opening is None or self._opening[0] != key:
            commands = (refs.menu(exclude=self.hidden) if refs is not None
                        else "(not loaded yet: write commands from what you know of 26.3)")
            full = "\n".join(view(n, self.catalog.get(n)) for n in names)
            text = (f"{self.rules}\n\nTHE MENU (each action: its name and what it does; this server's own "
                    f"actions are at the end, after the conversation):\n{menu_lines(self.catalog, names)}\n"
                    f"- {ORCHESTRATOR} — anything new no action does, a change or fix to something that "
                    "exists, or stopping, cancelling or undoing something running\n"
                    f"- {TALK} — talk: answered by your reply alone\n\n"
                    f"THE ACTIONS IN FULL:\n{full}\n"
                    f"MINECRAFT COMMANDS (name — what it does):\n{commands}\n\n"
                    "THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                    "player's line Jev's class):\n")
            self._opening = (key, text)
        return self._opening[1]

    def own_actions(self) -> str:
        names = made(self.catalog)
        if not names:
            return "THIS SERVER'S OWN ACTIONS: none yet"
        full = "\n".join(view(n, self.catalog.get(n)) for n in names)
        return (f"THIS SERVER'S OWN ACTIONS (made by implementers; on the menu like the rest):\n"
                f"{menu_lines(self.catalog, names)}\n\n{full}")

    def tail(self, event: Event, seen: Seen, tasks: str, new: list[Row]) -> str:
        """Everything after the conversation: what changes from call to call."""
        unread = show(new) or "nothing"
        return (f"\n{self.own_actions()}\n\n{seen.show()}\n\n{tasks}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n{unread}\n\n"
                f"THE EVENT:\n{event.show()}\n")

    def cached(self, ctx: Context, refs) -> list[tuple[str, bool]]:
        return ctx.cached(self.opening(refs), self.checkpoint)

    async def decide(self, event: Event, ctx: Context, seen: Seen, tasks: str, new: list[Row], refs):
        """An Answer whose value is a Decision; raises LlmError. Its usage row is structured()'s:
        component brain, the event's ref."""
        return await self.ask(self.tail(event, seen, tasks, new), self.Decision, cached=self.cached(ctx, refs),
                              component="brain", call_id=event.ref())


def describe(decision) -> str:
    """One log line: `command: give Steve … | orchestrator: … || reply: …`."""
    def one(p) -> str:
        if p.to == ORCHESTRATOR:
            return f"orchestrator: {p.instruction[:120]}"
        if p.to == TALK:
            return f"talk: {p.part[:80]}"
        return f"{p.to}{' [dropped: ' + p.drop + ']' if p.drop else ''}: {p.instruction}"
    parts = " | ".join(one(p) for p in decision.parts) or "no parts"
    tail = "".join(f" || {k}: {v}" for k, v in (("reply", decision.reply), ("record", decision.record),
                                                 ("status", decision.status)) if v)
    return parts + tail
