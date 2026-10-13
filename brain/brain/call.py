"""The brain's step: one structured() call on Gemini 3.8 Flash with low reasoning, answering one
`Step` (Prototype_Brain_Loop). A loop is steps until one ends it (loop.py runs it, act.py runs each
step's tools); this module is the answer and the prompt.

Two events, one `Step`, so the cached prefix is the same whatever the event:
- LINE: a line Jev classed action_oriented, or a conversational one Respond handed up;
- REPORT: an implementer's result.

A step may run actions from the menu, written in full, together or in turn (`order`); read another
player's items, or an engine class's Config as written (before engine_config rewrites it: the game
engine spec's "rewritten by the brain"); hand new work off to an implementer with a brief in parts;
follow a task up, or cancel its last run; and end the loop (`end`): with a reply, silent, or with
the hand-off. `end` is also the send-if-all-land flag: `reply`, `silent` and `handoff` say "if
every action of this step lands, send the reply, write the record, open the tasks, and the loop is
done"; `next` says "run these and show me the results". The reply and the record are written up
front, in the same answer, so most asks end in one step.

The schema never changes, not with the actions, the step's number or the last step, so the cached
prefix holds: `to` is a name and `action` the values, both checked here against the actions as
they are now, and every rule that depends on the moment is a validator (step_type). Every property
is required in the schema (all_required), so the model writes each field, its default included,
rather than leave one out: on the first pilot Gemini answered a report with `end` and `status` alone,
and no reply. A validator sends back only what code cannot carry out, or would carry out wrongly
(structured() asks once more, at the price of the whole prompt again): an action not on the menu,
values its action refuses (a command that places blocks among them), an instruction that does
nothing, a teleport that moves nobody, an ending with nothing to end on, a hand-off with a brief
missing, a Config to read that is no class's name, and on the last step any action or read. A field
that would have no effect is dropped and logged, never sent back: a status outside a report's loop,
a reply or a record on a `next` step.

The prompt, in the order the cache needs, its text fixed for the whole loop:
1. the opening, marked: the rules (prompts/brain.md, the owner named), the menu, every library
   action in full and Minecraft's command list;
2. the conversation, a minute-block at a time, marked at checkpoints (context.py), fewer of them
   than Respond's (LOOP_MARKS);
3. the tail, the event's own, read once at the loop's start: this server's own actions, the world
   (the speaker's items by slot; other players' come from the read tool), RUNNING NOW and TASKS in
   full (running.py, tasks.py), what is new since the last read, and the event;
4. THE LOOP SO FAR: each earlier step, what it ran, read and would have ended with, and what each
   came to (act.py), one block a step, the last one marked;
then a trailer, never cached: the step's number, the time used, and on the last step the notice.
Each step's prompt but its trailer is so the next step's cached prefix.
"""

from __future__ import annotations

import json
import re
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from vfg_actions import check_values, menu_line

from .actions import Catalog, view
from .config import log
from .context import Context, Row, show
from .names import NAME, RESERVED
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "brain.md"
TOGETHER, IN_TURN = "together", "in_turn"
NEXT, REPLY, SILENT, HANDOFF = "next", "reply", "silent", "handoff"
# The reply's hard cap. The prompt asks for under 80 characters and never over 200, the soft line; one a
# little over 200 goes out rather than cost a second call, and only one past 300, not one chat line, goes back.
REPLY_MAX = 300
LOOP_MARKS = 2  # conversation marks in a step's prompt: the opening and the loop's end take the other two

# An instruction that does nothing.
PLACEHOLDER = re.compile(r"^\W*(nothing( to do)?|none( needed)?|n/?a|no[ -]?op|skip|not needed|do nothing|"
                         r"no action( needed)?|empty|placeholder|tbd)?\W*$", re.IGNORECASE)
# An engine class's name, as ENGINE shows it and `vfg engine config get` takes it.
CLASS_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
# A teleport to where the player already is: an action invented to fill the list.
NO_MOVE = re.compile(r"\bto (his|her|their|its|your) (own )?((current|present) )?(position|location)\b"
                     r"|\bto (his|her|their|its|your) (own )?(current|present) (spot|place)\b"
                     r"|\bto where (he|she|they|it) (is|are|stands?|stood)\b", re.IGNORECASE)


class Value(BaseModel):
    """One value of an action: a closed object, as OpenAI's schemas demand, so the schema stays
    the same whatever the actions."""
    key: str = Field(description="the field's name, as the action's FIELDS give it")
    value: str | int | float | bool | list[str] | list[int | float] = Field(
        description="its value: text, a number, true or false, or a list for a list field")


class ActBase(BaseModel):
    to: str = Field(description="the action's name on the menu")
    instruction: str = Field(description="one short sentence in plain words: what it does, for whom, the player "
                                         "names and amounts as said")
    action: list[Value] = Field(default_factory=list, description="the action written in full, ready to run: one "
                                                                  "key and value per field it sets")

    def values(self, cls=None) -> dict[str, Any]:
        """The action as `vfg action run` takes it: {field: value}; for edit, the copy and the
        values to change, {"copy": …, "values": {…}}, whether each came as a key of its own or
        as `values.<name>`, or some each way. A Value cannot hold an object, so a field that
        holds another action's values (incantation_create's) comes as `values.<name>` keys,
        gathered here into {"values": {…}}. A field of `cls` (the action) that holds an object,
        given as text that reads as a JSON object (`"{\"power\": 6}"`, as Gemini wrote
        incantation_edit's `values` twice on the 10-11 replay), is read as that object here,
        never sent back to the model; a text field is never read so."""
        given = {v.key.strip(): v.value for v in self.action}
        for k, v in given.items():
            if k == "values" or holds_object(cls, k):
                given[k] = as_object(v)
        inner = {k.split(".", 1)[1].strip(): given.pop(k) for k in list(given) if k.startswith("values.")}
        if inner:
            given["values"] = {**(given["values"] if isinstance(given.get("values"), dict) else {}), **inner}
        if self.to != "edit":
            return given
        copy = given.pop("copy", "")
        nested = given.pop("values", None)
        return {"copy": copy, "values": {**given, **nested} if isinstance(nested, dict) else given}


def holds_object(cls, key: str) -> bool:
    """Whether the action `cls`'s field `key` holds an object (a dict), as `values` does."""
    f = getattr(cls, "model_fields", {}).get(key) if cls is not None else None
    if f is None:
        return False
    ann = f.annotation
    if typing.get_origin(ann) is typing.Annotated:
        ann = typing.get_args(ann)[0]
    return ann is dict or typing.get_origin(ann) is dict


def as_object(v: Any) -> Any:
    """Text that reads as a JSON object, that object; anything else as it is."""
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            got = json.loads(v)
        except ValueError:
            return v
        return got if isinstance(got, dict) else v
    return v


class HandOff(BaseModel):
    """New work for an implementer: the task's name and description, and the brief in parts, which
    code renders into the text the implementer starts from (brief())."""
    name: str = Field(description="the new task's name: lowercase letters, digits and underscores, the thing "
                                  "then whose (hot_fireball_krown)")
    description: str = Field(description="one or two sentences: what the thing will be, for whom, and how a "
                                         "player uses it")
    words: str = Field(description="the player's own words for it, quoted as they said them")
    picture: str = Field(description="the whole picture: how it behaves, looks, sounds and feels, in full")
    assumed: str = Field(description="what the player takes for granted and never said: what anyone would "
                                     "expect of such a thing")
    cast: str = Field(description="how it is cast or used, and for whom")
    done_when: list[str] = Field(description="the checklist the implementer checks item by item before it "
                                             "reports: each a short thing that can be seen in the game")

    def brief(self) -> str:
        """The text the implementer gets and the task's row keeps: each part under its heading."""
        items = "\n".join(f"- {one_line(x)}" for x in self.done_when if x.strip())
        return (f"THE PLAYER'S WORDS\n{self.words.strip()}\n\n"
                f"THE WHOLE PICTURE\n{self.picture.strip()}\n\n"
                f"WHAT THE PLAYER TAKES FOR GRANTED\n{self.assumed.strip()}\n\n"
                f"HOW IT IS CAST, AND FOR WHOM\n{self.cast.strip()}\n\n"
                f"DONE WHEN\n{items}")


class FollowUp(BaseModel):
    task: str = Field(description="the task's name, exactly as TASKS shows it")
    description: str = Field("", description="what the thing is after this change, for whom, and how a player "
                                             "uses it; empty with cancel (empty: it stays as it was)")
    brief: str = Field("", description="what to change: one to three sentences its implementer acts on alone; "
                                       "empty with cancel")
    cancel: bool = Field(False, description="true when the player says never mind: code sends the cancel words, "
                                            "and the implementer puts back what its last run built")


def all_required(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with every property of every object listed as required, its default kept: a
    model leaves out what the schema lets it, and a field left out is its default, rarely what it
    meant."""
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


class StepBase(BaseModel):
    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """What structured() sends: every property of the step and of what it holds, required."""
        return all_required(super().model_json_schema(*args, **kwargs))

    order: Literal["together", "in_turn"] = Field(
        "together", description="together: the actions run at once; in_turn: one after another, stopping at the "
                                "first that does not land")
    actions: list[ActBase] = Field(default_factory=list, description="the actions this step runs, in order")
    read_items: list[str] = Field(default_factory=list, description="players whose items to read, by name, for "
                                                                    "the next step; only with end next; else []")
    read_configs: list[str] = Field(default_factory=list, description="engine classes whose Config to read as "
                                                                      "written, by their names under ENGINE, for the "
                                                                      "next step, before an engine_config; only with "
                                                                      "end next; else []")
    hand_offs: list[HandOff] = Field(default_factory=list, description="new work for an implementer; only with "
                                                                       "end handoff; else []")
    follow_ups: list[FollowUp] = Field(default_factory=list, description="changes, fixes or a cancel for a task "
                                                                         "under TASKS; only with end handoff; else []")
    end: Literal["next", "reply", "silent", "handoff"] = Field(
        description="next: run this and show me the results; reply, silent, handoff: if every action lands, "
                    "send the reply, write the record, start the hand-offs and follow-ups, and the loop ends")
    reply: str = Field("", description="the chat line the players see, in the AI's own voice: required for "
                                       "reply, optional with handoff, empty for silent and next")
    record: str = Field("", description="the loop's one short record for the context: what was done, what "
                                        "failed and why; empty for next")
    status: Literal["", "done", "failed"] = Field("", description="a report's loop: the task's status after it; "
                                                                  "else empty")


def step_type(catalog: Catalog, *, last: bool = False, report: bool = False,
              tasks: frozenset[str] | set[str] = frozenset(),
              promise: Callable[[str], str] | None = None) -> type[StepBase]:
    """`Step`, checked against `catalog` as it is when an answer arrives, and against the moment:
    the last step (`last`: no action, no read, and an ending; a follow-up only of a task in `tasks`,
    since no next step could read its refusal) and a report's loop (`report`: only it has a status).
    `promise` (respond.promises, offers left out) says "a promise" of a reply that promises
    something to happen: a step that ends `reply` and starts nothing may not say one, since
    nothing would ever come of it. Its JSON schema is the same for every catalog and every moment.
    What would have no effect is dropped here, and logged; what code cannot carry out, or would
    carry out wrongly, is sent back."""

    class Act(ActBase):
        @model_validator(mode="after")
        def _checked(self):
            said = self.instruction.strip()
            cls = catalog.get(self.to)
            if cls is None:
                raise ValueError(f"{self.to} is not on the menu: use an action that is, or hand it off")
            if not said:
                raise ValueError(f"the {self.to} action has no instruction")
            if PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {self.instruction!r} does nothing: leave the action out")
            if self.to == "teleport" and NO_MOVE.search(said):
                raise ValueError(f"{self.instruction!r} moves nobody: leave it out")
            keys = [v.key.strip() for v in self.action]
            if len(set(keys)) < len(keys):
                raise ValueError(f"the {self.to} action {said!r} sets a field twice: once each")
            try:
                check_values(cls, self.values(cls))
            except ValueError as e:
                raise ValueError(f"the {self.to} action {said!r}: {e}") from None
            return self

    class Step(StepBase):
        actions: list[Act] = Field(default_factory=list, description=StepBase.model_fields["actions"].description)

        @model_validator(mode="after")
        def _ends(self):
            self._drop_what_does_nothing()
            reply = self.reply.strip()
            if len(self.reply) > REPLY_MAX:
                raise ValueError(f"the reply is {len(self.reply)} characters, past the {REPLY_MAX} a chat line may "
                                 "hold: one line, never over 200")
            if (self.hand_offs or self.follow_ups) and self.end == NEXT:
                raise ValueError("hand_offs and follow_ups end the loop: end handoff, or leave them for the step "
                                 "that ends it")
            if self.end == NEXT and not self.actions and not self.read_items and not self.read_configs:
                raise ValueError("end next runs or reads something: with nothing to run, end reply, silent or "
                                 "handoff")
            for name in self.read_configs:
                if not CLASS_NAME.match(name.strip()):
                    raise ValueError(f"read_configs {name!r}: a class's name as ENGINE shows it, such as fireball")
            if self.end == REPLY and not reply:
                raise ValueError("end reply needs the reply")
            promised = promise is not None and self.end == REPLY and not self.actions and promise(reply)
            if promised == "a promise":
                raise ValueError("the reply promises work and this step starts nothing for it: hand it off or follow "
                                 "it up in this step, run the action, or else ask, answer, or say plainly what you "
                                 "can't do")
            if self.end == HANDOFF and not self.hand_offs and not self.follow_ups:
                raise ValueError("end handoff needs a hand-off or a follow-up")
            names = [h.name.strip() for h in self.hand_offs]
            for h in self.hand_offs:
                name = h.name.strip()
                if not NAME.match(name):
                    raise ValueError(f"hand-off name {name!r}: lowercase letters, digits and underscores, starting "
                                     "with a letter, 2 to 32 characters")
                if name in RESERVED:
                    raise ValueError(f"{name} is the bridge's task for an incantation's copies, never a task's "
                                     "name: name the thing, then whose")
                if catalog.get(name) is not None and catalog.source(name) == "library":
                    raise ValueError(f"{name} is a library action's name: add whose it is, as frost_bow_alex")
                missing = [k for k in ("description", "words", "picture", "assumed", "cast")
                           if not getattr(h, k).strip()]
                if missing:
                    raise ValueError(f"the hand-off {name}'s brief has no {', '.join(missing)}: write every part")
                if not any(x.strip() for x in h.done_when):
                    raise ValueError(f"the hand-off {name} has no done_when: the checklist the implementer checks")
            if len(set(names)) < len(names):
                raise ValueError("two hand-offs share a name: one name a thing")
            followed = [f.task.strip() for f in self.follow_ups]
            if len(set(followed)) < len(followed):
                raise ValueError("a task is followed up twice: one follow-up a task, saying everything")
            for f in self.follow_ups:
                if not f.task.strip():
                    raise ValueError("a follow-up names no task: its name as TASKS shows it")
                if not f.cancel and not f.brief.strip():
                    raise ValueError(f"the follow-up of {f.task} has no brief: what its implementer changes")
                if last and f.task.strip() not in tasks:
                    raise ValueError(f"task {f.task.strip()!r} is not under TASKS: follow up one that is, or hand "
                                     "it off as new")
            if last:
                if self.actions or self.read_items or self.read_configs:
                    raise ValueError("this is the last step: run no action and read nothing; end reply or handoff")
                if self.end not in (REPLY, HANDOFF):
                    raise ValueError("this is the last step: end reply or handoff")
            return self

        def _drop_what_does_nothing(self) -> None:
            """The fields this step's ending would ignore, dropped and logged rather than sent back;
            a hand-off or a follow-up under a reply or silent ending makes it a hand-off's."""
            dropped = []
            if self.status and (not report or self.end == NEXT):
                dropped.append(f"status {self.status!r}")
                self.status = ""
            if (self.hand_offs or self.follow_ups) and self.end in (REPLY, SILENT):
                dropped.append(f"end {self.end} (it hands off: end handoff)")
                self.end = HANDOFF
            if self.end in (NEXT, SILENT) and self.reply.strip():
                dropped.append(f"reply {self.reply.strip()!r}")
                self.reply = ""
            if self.end == NEXT and self.record.strip():
                dropped.append(f"record {self.record.strip()!r}")
                self.record = ""
            if self.read_items and self.end != NEXT and not last:
                dropped.append(f"read_items {', '.join(self.read_items)} (a read comes back only to a next step)")
                self.read_items = []
            if self.read_configs and self.end != NEXT and not last:
                dropped.append(f"read_configs {', '.join(self.read_configs)} (a read comes back only to a next step)")
                self.read_configs = []
            if dropped:
                log("step", f"end {self.end}: dropped, it would do nothing: {'; '.join(dropped)}")

    return Step


def one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


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
        """The loop's id in its steps' usage rows: `#<seq>` for a line, the task's name for a report."""
        if self.kind == "LINE":
            return f"#{self.row.seq}"
        return str((self.task or {}).get("name") or "report")[:56]

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
        more = (f"\nA FOLLOW-UP to {name} is already under way, so the task stays open: say how this run "
                "went, never that it is all done." if self.following else "")
        return (f"REPORT: the implementer of {name} ended, {self.status}.\n"
                f"{name} was for {t.get('player') or 'nobody'}, who said: {t.get('line') or ''}\n"
                f"WHAT IT IS: {t.get('description') or ''}\nITS LATEST BRIEF:\n{t.get('brief') or ''}\n"
                f"ITS REPORT:\n{self.report.strip()}{more}")


@dataclass
class Prompt:
    """One loop's prompt: the blocks read once at its start (the opening and the conversation, as
    context.py marks them, and the tail), then one block per step that ran. Each step's cached
    blocks are the next step's prefix: the last block is marked, the rest keep their text."""
    head: list[tuple[str, bool]]
    tail: str
    steps: list[str] = field(default_factory=list)

    def cached(self) -> list[tuple[str, bool]]:
        blocks = [*self.head, (self.tail, not self.steps)]
        return blocks + [(text, i == len(self.steps) - 1) for i, text in enumerate(self.steps)]


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
        self._opening: tuple[tuple, str] | None = None

    def opening(self, refs) -> str:
        """The rules, the menu, every library action in full and the command list: the same for
        every step while the library and the references stay as they are."""
        names = library(self.catalog)
        key = (id(refs), tuple((n, id(self.catalog.get(n))) for n in names))
        if self._opening is None or self._opening[0] != key:
            commands = (refs.menu(exclude=self.hidden) if refs is not None
                        else "(not loaded yet: write commands from what you know of 26.3)")
            full = "\n".join(view(n, self.catalog.get(n)) for n in names)
            text = (f"{self.rules}\n\nTHE MENU (each action: its name and what it does; this server's own "
                    f"actions are at the end, after the conversation):\n{menu_lines(self.catalog, names)}\n\n"
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
        """Everything after the conversation that is the event's own, read once at the loop's
        start: the world as it was then (seen, its RUNNING NOW in full), TASKS, NEW and the event."""
        unread = show(new) or "nothing"
        return (f"\n{self.own_actions()}\n\n{seen.show()}\n\n{tasks}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n{unread}\n\n"
                f"THE EVENT:\n{event.show()}\n")

    def prompt(self, event: Event, ctx: Context, seen: Seen, tasks: str, new: list[Row], refs) -> Prompt:
        """The loop's prompt as its first step sends it: the conversation as it is now, kept for
        every step, so a row that arrives meanwhile never moves the prefix."""
        return Prompt(ctx.cached(self.opening(refs), self.checkpoint, marks=LOOP_MARKS),
                      self.tail(event, seen, tasks, new))

    @staticmethod
    def trailer(n: int, most: int, used_s: float, seconds: float, last: bool) -> str:
        """The step's own words, never cached: its number, the time used, and the last step's notice."""
        head = f"\nSTEP {n} of at most {most}; {used_s:.0f} s of {seconds:g} used."
        if not last:
            return head + "\n"
        why = f"this is step {most}" if n >= most else f"{seconds:g} s have gone"
        return (f"{head}\nTHIS IS THE LAST STEP ({why}): run no action and read nothing. End with handoff (what is "
                "left, to an implementer, with its brief), or with reply: what was done and what was not, in the "
                "player's terms, offering to build the rest or asking them to try again. The reply never mentions "
                "steps, time, limits or how you work.\n")

    async def step(self, prompt: Prompt, trailer: str, output: type[StepBase], ref: str):
        """One step: an Answer whose value is a Step; raises LlmError. Its usage row is
        structured()'s: component brain, `ref` (`#<seq> s<n>`, `<task> s<n>`)."""
        return await self.ask(trailer, output, cached=prompt.cached(), component="brain", call_id=ref[:64])


def describe(step: StepBase) -> str:
    """One log line: `together: command, railgun | read Alex | hand off frost_bow_alex || end reply: …`."""
    bits = []
    if step.actions:
        bits.append(f"{step.order}: " + ", ".join(f"{a.to} ({one_line(a.instruction)[:80]})" for a in step.actions))
    if step.read_items:
        bits.append("read " + ", ".join(step.read_items))
    if step.read_configs:
        bits.append("read the Config of " + ", ".join(step.read_configs))
    bits += [f"hand off {h.name}" for h in step.hand_offs]
    bits += [f"{'cancel' if f.cancel else 'follow up'} {f.task}" for f in step.follow_ups]
    tail = "".join(f" || {k}: {v}" for k, v in (("reply", step.reply), ("record", step.record),
                                                 ("status", step.status)) if v)
    return f"{' | '.join(bits) or 'nothing run'} || end {step.end}{tail}"
