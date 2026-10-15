"""The brain's step: one structured() call on DeepSeek V4.1 Flash (reasoning off or low: config.py),
answering one `Step` (Prototype_Brain_Loop, then Prototype_Conversation_And_Router). A loop is steps
until one ends it (loop.py runs it, loops.py keeps it, act.py runs each step's tools); this module is
the answer and the prompt.

Two events, one `Step`, so the cached prefix is the same whatever the event:
- LINE: a line to the AI the router sent to a new loop, after the conversation agent answered it
  (its line is in the event, so the brain's reply follows it and never repeats it);
- REPORT: what an implementer's report left: the conversation agent told the player how it went
  and wrote the task's result, and the router sent the rest here, to be done with actions or
  followed up. The loop never closes the task.
A line the router adds to a loop already on that thing comes before the loop's next step, as a
LINE ADDED block (added()).

A step may run actions from the menu (the game engine's ops), written in full, together or in turn
(`order`); read another player's items, or an engine class's Config as written (before
engine_config rewrites it: the game engine spec's "rewritten by the brain"); hand new work off to
an implementer with a brief in parts; follow a task up, or cancel its last run; and end the loop
(`end`): with a reply, silent, or with the hand-off. `end` is also the send-if-all-land flag:
`reply`, `silent` and `handoff` say "if every action of this step lands, send the reply, write the
record, open the tasks, and the loop is done"; `next` says "run these and show me the results".
The reply and the record are written up front, in the same answer, so most asks end in one step.
Picking who does the work is the router's now; the brain still hands off or follows a task up when
an ask turns out bigger than its actions.

The schema never changes, not with the actions, the step's number or the last step, so the cached
prefix holds: `to` is a name and `fields` the values, both checked here against the actions as
they are now, and every rule that depends on the moment is a validator (step_type). Every property
is required in the schema (all_required), so the model writes each field, its default included,
rather than leave one out: on the first pilot Gemini answered a report with `end` and `status` alone,
and no reply. A validator sends back only what code cannot carry out, or would carry out wrongly
(structured() asks once more, at the price of the whole prompt again): an action not on the menu,
values its action refuses, an instruction that does nothing, a teleport that moves nobody, an
ending with nothing to end on, a hand-off with a brief missing (checks.py, the router's bar too),
and a Config to read that is no class's name. A field that would have no effect is dropped and
logged, never sent back: a reply or a record on a `next` step, and on the last step any action or
read. There is no status: the conversation agent writes a report's.

The prompt, in the order the cache needs, its text fixed for the loop's life:
1. the opening, marked: the rules (prompts/brain.md, the owner named, its `# TODO` lines left out),
   the menu, every action in full (the game engine's ops) and Minecraft's command list;
2. the conversation, a minute-block at a time, marked at checkpoints (context.py), fewer of them
   than the conversation agent's (LOOP_MARKS);
3. the tail, the event's own, read once at the loop's start: the world (the speaker's items by slot;
   other players' come from the read tool; RUNNING NOW, SAVED PLACES, INCANTATIONS and ENGINE, the
   game engine's: world.py), TASKS (tasks.py), what is new since the last read, and the event;
4. THE LOOP SO FAR: each earlier step, what it ran, read and would have ended with, and what each
   came to (act.py), one block a step, a LINE ADDED block where a line came in, the last marked;
then a trailer, never cached: the step's number, the loop's limit now, and on the last step the
notice. Each step's prompt but its trailer is so the next step's cached prefix, and an ended loop
taken up again sends the same prefix, read from the cache.
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

from . import checks
from .actions import Catalog, view
from .config import log
from .context import Context, Row, show
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
    key: str = Field(description="one field's name, exactly as the action's FIELDS list it, never the action's "
                                 "own name: `scenario` for engine_start, `player` for engine_grant, `args.commands` "
                                 "for one of a scenario's args")
    value: str | int | float | bool | list[str] | list[int | float] = Field(
        description="its value: text, a number, true or false, or a list for a list field")


class ActBase(BaseModel):
    to: str = Field(description="the name of one action on the menu, exactly as the menu writes it: "
                                "`engine_start`, `engine_give`, `engine_bind`; never a player, a field, an ending or "
                                "an order")
    instruction: str = Field(description="one short sentence in plain words: what it does, for whom, the player "
                                         "names and amounts as said")
    # `fields`, never `action`: named `action`, DeepSeek read it as "which action" and echoed the action's name
    # into it ({"key": "command", "value": "command"}: the low loop replay's #213, #510, #66, #512).
    fields: list[Value] = Field(default_factory=list, description="every field the action sets, one key and value "
                                                                  "each, as the action's FIELDS name them")

    def values(self, cls=None) -> dict[str, Any]:
        """The action as `vfg action run` takes it: {field: value}. A Value cannot hold an object, so
        the field that holds a scenario's or a mark's args (Args) comes as `args.<name>` keys,
        gathered here into {"args": {…}}; an args field given as text that reads as a JSON object
        (`"{\"at\": \"home\"}"`) is read as that object, never sent back to the model; a text field
        is never read so."""
        given = {v.key.strip(): v.value for v in self.fields}
        for k, v in given.items():
            if holds_object(cls, k):
                given[k] = as_object(v)
        inner = {k.split(".", 1)[1].strip(): given.pop(k) for k in list(given) if k.startswith("args.")}
        if inner:
            given["args"] = {**(given["args"] if isinstance(given.get("args"), dict) else {}), **inner}
        return given


def holds_object(cls, key: str) -> bool:
    """Whether the action `cls`'s field `key` holds an object (a dict), as `values` does."""
    f = getattr(cls, "model_fields", {}).get(key) if cls is not None else None
    if f is None:
        return False
    ann = f.annotation
    if typing.get_origin(ann) is typing.Annotated:
        ann = typing.get_args(ann)[0]
    return ann is dict or typing.get_origin(ann) is dict


def echoes(to: str, fields: list) -> list:
    """The action's fields without an echo of its name: a key and a value that are both the
    action's own name ({"key": "command", "value": "command"}, beside the real `commands`: the low
    loop replay's #512) set nothing, and are dropped, logged. With nothing else written, the step
    is sent back (the Act validator): the action has no field set at all."""
    name = to.strip()
    kept = [v for v in fields if not (v.key.strip() == name and isinstance(v.value, str) and v.value.strip() == name)]
    if len(kept) < len(fields):
        log("step", f"the {name} action's echo of its own name dropped from its fields")
    return kept


def renamed(to: str, cls, action: list) -> list:
    """The action's values with a key under the action's own name read as the one field it plainly
    means, as as_object() reads JSON text: DeepSeek wrote `command` for `commands`, and the old
    incantation actions' values under their own names (10 of the low loop replay's 19 answers sent
    back, and the send-back's hint did not change the second answer). Only three readings, never a
    guess beyond them: the field named the action's name and an "s"; the one field that holds an
    object (a scenario's `args`), for a value that is an object or reads as one; or the action's one
    required field. Each is one not given already. Logged; any other
    key stays as written, for check_values to refuse."""
    name = to.strip()
    fields = getattr(cls, "model_fields", {})
    keys = [v.key.strip() for v in action]
    if not name or name in fields or name not in keys:
        return action
    given = set(keys) - {name}
    value = next(v.value for v in action if v.key.strip() == name)
    objects = [k for k in fields if holds_object(cls, k) and k not in given]
    required = [k for k, f in fields.items() if f.is_required() and k not in given]
    if f"{name}s" in fields and f"{name}s" not in given:
        meant = f"{name}s"
    elif len(objects) == 1 and isinstance(as_object(value), dict):
        meant = objects[0]
    elif len(required) == 1:
        meant = required[0]
    else:
        return action
    log("step", f"the {name} action's value under its own name read as {meant!r}")
    return [v.model_copy(update={"key": meant}) if v.key.strip() == name else v for v in action]


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
                                  "then whose (grappling_hook_alex)")
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


# A reply that tells the player to use a thing now: an imperative at a clause's start, then "and" what it does
# ("Say Ice beam and a frost beam freezes whatever it touches", "Hold right-click and it charges"); never one
# that waits for it ("once it's ready, …", "you'll …").
USE_NOW = re.compile(r"(?:^|[.!?;]\s+)(?:just\s+|now\s+)?(?:say|shout|type|hold|right[- ]click|left[- ]click|click|sneak|"
                     r"crouch|jump|press|swing|throw|aim)\b(?![^.!?]*\b(?:once|when|soon|will|after|ready)\b|[^.!?]*'ll\b)"
                     r"[^.!?]*\band\b", re.IGNORECASE)

# What a model writes in `to` when it is not an action: the step's own words, which tell the fix.
ENDINGS, ORDERS = ("next", "reply", "silent", "handoff"), ("together", "in_turn")
# Real fields of the step or its action (never "action": there is no such field any more, renamed to
# `fields` so DeepSeek stopped echoing the action's name into it (ActBase.fields's note); "action" and
# "actions" get their own hint below, never this one, since "write it there" names no field a model
# could find for either (the live run's "stop my effects" named it twice, #39).
STEP_FIELDS = ("fields", "hand_off", "hand_offs", "follow_up", "follow_ups", "read_items", "end")
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def unknown_action(to: str, catalog: Catalog, given: list) -> str:
    """The send-back for a `to` not on the menu, naming the likely fix: `to` holds an ending, an
    order, one of the step's own fields (DeepSeek on the live tests: "together", "follow_up",
    "hand_offs"), a field of an action ("commands"), a player, or the word "action"/"actions" itself,
    which names no field (`fields` holds an action's own values); else the plain refusal."""
    name = to.strip()
    low = name.lower().replace("-", "_").replace(" ", "_")
    fix = f"{name} is not on the menu: `to` is the name of one action on the menu, as `engine_start`"
    if low in ENDINGS:
        return f"{fix}; {name} is an ending: it goes in `end`"
    if low in ORDERS:
        return f"{fix}; {name} is an order: it goes in `order`"
    if low in ("action", "actions"):
        return (f'{fix}; `to` holds the action\'s own name as THE MENU lists it (engine_start, '
                f'engine_bind, engine_stop…), never the word "{name}"')
    if low in STEP_FIELDS:
        return f"{fix}; {name} is a field of the step, not an action: write it there, or name the action"
    owners = [n for n in catalog.names() if low in getattr(catalog.get(n), "model_fields", {})]
    if owners:
        return (f"{fix}; {name} is a field of {', '.join(owners[:3])}: name the action in `to`, and the field as a "
                f"`key` in its values")
    if PLAYER_NAME.match(name) and any(c.isupper() for c in name):
        return f"{fix}; {name} looks like a player: players go in the action's values, as `player`"
    return f"{name} is not on the menu: use an action that is, or hand it off"


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


def step_type(catalog: Catalog, *, last: bool = False, tasks: frozenset[str] | set[str] = frozenset(),
              promise: Callable[[str], str] | None = None, landed: bool = True) -> type[StepBase]:
    """`Step`, checked against `catalog` as it is when an answer arrives, and against the moment:
    the last step (`last`: an ending, a reply or a hand-off; a follow-up only of a task in `tasks`,
    since no next step could read its refusal; any action or read it still writes is dropped,
    logged, and its ending kept: DeepSeek wrote the refused actions again on the last step, and a
    second refusal failed the loop, live #115) and whether anything of the loop has landed in the
    game yet (`landed`: a report's loop counts, its implementer built something). `promise`
    (respond.promises, offers left out) says "a promise" of a reply that promises something to
    happen: a step that ends `reply` and starts nothing may not say one, since nothing would ever
    come of it. With nothing landed, a step that runs nothing may neither end `reply` claiming it
    is done ("Hot Fireball's away", the low replay's #461) nor end `silent`, which would leave the
    player with the conversation agent's "On it!" alone (live run 4's helmet). A hand-off and a
    follow-up are held to checks.py, the router's bar too, in the same words. Its JSON schema is
    the same for every catalog and every moment. What would have no effect is dropped here, and
    logged; what code cannot carry out, or would carry out wrongly, is sent back."""
    from .respond import claims_done  # respond.py imports this module: read here, once it is loaded

    class Act(ActBase):
        @model_validator(mode="after")
        def _checked(self):
            said = self.instruction.strip()
            cls = catalog.get(self.to)
            if last:  # the last step's actions are dropped, whatever they are (Step below)
                return self
            if cls is None:
                raise ValueError(unknown_action(self.to, catalog, self.fields))
            if not said:
                raise ValueError(f"the {self.to} action has no instruction")
            if PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {self.instruction!r} does nothing: leave the action out")
            if self.to == "engine_start" and str(self.values(cls).get("scenario")) == "teleport" \
                    and NO_MOVE.search(said):
                raise ValueError(f"{self.instruction!r} moves nobody: leave it out")
            written = len(self.fields)
            self.fields = echoes(self.to, self.fields)
            if written and not self.fields:
                raise ValueError(f"the {self.to} action {said!r} sets no field: `fields` holds each field it sets, "
                                 "one key and value each, as its FIELDS name them, never the action's own name")
            self.fields = renamed(self.to, cls, self.fields)
            keys = [v.key.strip() for v in self.fields]
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
            if last and self.end not in (REPLY, HANDOFF):
                raise ValueError("this is the last step: end reply or handoff")
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
            if not landed and not self.actions and self.end == REPLY and claims_done(reply):
                raise ValueError("nothing ran: the reply says it is done, and nothing of this ask has happened in the "
                                 "game; say what happened, or run it")
            if not landed and not self.actions and self.end == SILENT:
                raise ValueError("nothing ran: silent leaves the player with nothing, since nothing of this ask has "
                                 "happened in the game; say what happened in a reply, or run it")
            if self.end == HANDOFF and not self.hand_offs and not self.follow_ups:
                raise ValueError("end handoff needs a hand-off or a follow-up")
            checks.hand_offs(self.hand_offs, catalog)
            checks.follow_ups(self.follow_ups, tasks if last else None)
            return self

        def _drop_what_does_nothing(self) -> None:
            """The fields this step's ending would ignore, dropped and logged rather than sent back;
            a hand-off or a follow-up under a reply or silent ending makes it a hand-off's."""
            dropped = []
            if last and (self.actions or self.read_items or self.read_configs):  # nothing comes after it
                dropped += [f"{a.to} ({one_line(a.instruction)[:80]})" for a in self.actions]
                dropped += [f"read_items {', '.join(self.read_items)}"] if self.read_items else []
                dropped += [f"read_configs {', '.join(self.read_configs)}"] if self.read_configs else []
                dropped[-1] += " (the last step runs and reads nothing)"
                self.actions, self.read_items, self.read_configs = [], [], []
                if self.end == NEXT and self.reply.strip():
                    dropped.append("end next (the last step ends: end reply)")
                    self.end = REPLY
            if (self.hand_offs or self.follow_ups) and self.end in (REPLY, SILENT):
                dropped.append(f"end {self.end} (it hands off: end handoff)")
                self.end = HANDOFF
            if self.end == HANDOFF and not self.actions and (claims_done(self.reply) or USE_NOW.search(self.reply)):
                # a hand-off's reply is optional: one that says the thing is there, or how to use it now, is
                # dropped (the low replay's #422: "Say Ice beam and a frost beam freezes whatever it touches.")
                dropped.append(f"reply {self.reply.strip()!r} (a hand-off: nothing of it is built yet)")
                self.reply = ""
            if self.end in (NEXT, SILENT) and self.reply.strip():
                dropped.append(f"reply {self.reply.strip()!r}")
                self.reply = ""
            if self.end == NEXT and self.record.strip():
                dropped.append(f"record {self.record.strip()!r}")
                self.record = ""
            if self.read_items and self.end != NEXT:
                dropped.append(f"read_items {', '.join(self.read_items)} (a read comes back only to a next step)")
                self.read_items = []
            if self.read_configs and self.end != NEXT:
                dropped.append(f"read_configs {', '.join(self.read_configs)} (a read comes back only to a next step)")
                self.read_configs = []
            if dropped:
                log("step", f"end {self.end}: dropped, it would do nothing: {'; '.join(dropped)}")

    return Step


def one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def quoted(text: str) -> str:
    """Words as a prompt quotes them, on one line: “On it!”."""
    return f"“{one_line(text)}”"


@dataclass
class Event:
    """What a loop is for: a LINE the router sent it, or the work a REPORT left (the module's
    docstring)."""
    kind: Literal["LINE", "REPORT"]
    row: Row | None = None          # LINE: the line
    said: str = ""                  # the conversation agent's line to the player ("" = it said nothing): a
                                    # LINE's answer, a REPORT's chat line
    reason: str = ""                # the router's reason for sending it here ("" = none: it failed)
    task: dict | None = None        # REPORT: the task's row (tasks.py)
    status: str = ""                # REPORT: done or failed, as the conversation agent read the report
    report: str = ""                # REPORT: the implementer's report, its DESCRIPTION line cut off; it
                                    # never reaches the context
    left: str = ""                  # REPORT: what the conversation agent says is left, in plain words
    following: bool = False         # REPORT: a follow-up to the task already runs, so it stays open

    def ref(self) -> str:
        """The loop's id in its steps' usage rows: `#<seq>` for a line, the task's name for a report."""
        if self.kind == "LINE":
            return f"#{self.row.seq}"
        return str((self.task or {}).get("name") or "report")[:56]

    def show(self) -> str:
        router = f"\n(the router: {one_line(self.reason)})" if self.reason.strip() else ""
        if self.kind == "LINE":
            r = self.row
            how = "typed" if r.source == "chat" else "said"
            answered = (f"(the conversation agent already answered the player: {quoted(self.said)}; your reply "
                        "follows it — never repeat it)" if self.said.strip()
                        else "(the conversation agent said nothing)")
            return f"LINE #{r.seq}, {r.hms}: {r.who} {how}: {r.text}\n{answered}{router}"
        t = self.task or {}
        name = t.get("name") or "?"
        player = t.get("player") or "nobody"
        told = (f"The conversation agent already told {player} {quoted(self.said)}" if self.said.strip()
                else "The conversation agent already told the player how it went")
        more = (f"\nA FOLLOW-UP to {name} is already under way, so the task stays open: never say it is all done."
                if self.following else "")
        return (f"REPORT: the implementer of {name} ended, {self.status or '?'}, and left something for you.\n"
                f"{told}, and wrote the task's result: never close the task or write its result again.\n"
                f"{name} was for {player}, who said: {t.get('line') or ''}\n"
                f"WHAT IT IS: {t.get('description') or ''}\nITS LATEST BRIEF:\n{t.get('brief') or ''}\n"
                f"ITS REPORT:\n{self.report.strip()}\n"
                f"WHAT IS LEFT: {one_line(self.left) or 'not said: read the report'}{more}{router}")


@dataclass
class Added:
    """A line the router added to a loop already on that thing: the line, the conversation agent's
    answer to it ("" = it said nothing) and the router's reason. It waits in the loop's `pending`
    until the loop's next step reads it (loops.py)."""
    row: Row
    said: str = ""
    reason: str = ""


def added(lines: list[Added], since: list[Row]) -> str:
    """The block a loop's next step reads its added lines in, after its earlier steps: each line,
    what the conversation agent answered and why the router sent it here, then every row the loop
    has not read yet (`since`: the conversation past what its prompt showed, the added lines left
    out), so a loop that ran for a while, or was taken up again, knows what happened meanwhile."""
    out = []
    for a in lines:
        r = a.row
        how = "typed" if r.source == "chat" else "said"
        text = f"LINE ADDED TO THIS LOOP: LINE #{r.seq}, {r.hms}: {r.who} {how}: {r.text}"
        text += (f"\n(the conversation agent answered: {quoted(a.said)})" if a.said.strip()
                 else "\n(the conversation agent said nothing)")
        if a.reason.strip():
            text += f"\n(the router: {one_line(a.reason)})"
        out.append(text)
    return "\n".join(out) + f"\nROWS SINCE THIS LOOP LAST READ:\n{show(since) or 'nothing'}\n\n"


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


def rules_text(prompt: Path) -> str:
    """A prompt file as the model reads it: its `# TODO` lines, notes for whoever edits it, left out."""
    lines = prompt.read_text(encoding="utf-8").rstrip("\n").split("\n")
    return "\n".join(line for line in lines if not line.startswith("# TODO"))


def menu_lines(catalog: Catalog, names: list[str]) -> str:
    return "\n".join(f"- {menu_line(n, catalog.get(n))}" for n in names)


class Opening:
    """A prompt's opening, the brain's and the router's: the rules, the menu (every action, the game
    engine's ops) in full and Minecraft's command list, then the conversation's header. Built again
    only when the actions or the references change, so it stays the cached prefix of every call
    meanwhile; what the engine has (its classes, which a reload changes) is the tail's, ENGINE in
    THE WORLD NOW. The words that differ between the two prompts are given: the menu's and the
    commands' headings, and what the command list says before the references load."""

    def __init__(self, rules: str, catalog: Catalog, hidden: frozenset[str], *, menu: str, commands: str,
                 not_loaded: str):
        self.rules, self.catalog, self.hidden = rules, catalog, hidden
        self.menu, self.commands, self.not_loaded = menu, commands, not_loaded
        self._built: tuple[tuple, str] | None = None

    def __call__(self, refs) -> str:
        names = self.catalog.names()
        key = (id(refs), tuple((n, id(self.catalog.get(n))) for n in names))
        if self._built is None or self._built[0] != key:
            listed = refs.menu(exclude=self.hidden) if refs is not None else self.not_loaded
            full = "\n".join(view(n, self.catalog.get(n)) for n in names)
            text = (f"{self.rules}\n\n{self.menu}:\n{menu_lines(self.catalog, names)}\n\n"
                    f"THE ACTIONS IN FULL:\n{full}\n{self.commands}:\n{listed}\n\n"
                    "THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                    "player's line Jev's class):\n")
            self._built = (key, text)
        return self._built[1]


class Call:
    def __init__(self, catalog: Catalog, ask, *, owner: str = "", hidden: frozenset[str] = frozenset(),
                 checkpoint: int = 5, prompt: Path = PROMPT, grant: int = 10, cap: int = 20):
        self.catalog = catalog
        self.ask = ask  # structured() with the brain's model, reasoning, providers and timeout
        self.hidden = hidden
        self.checkpoint = checkpoint
        rules = rules_text(prompt)
        who = owner or "not named (VFG_OWNER is not set)"
        # The limits as the loops keep them (VFG_LOOP_STEPS, VFG_LOOP_CAP), so the prompt never says others.
        self.rules = rules.replace("{owner}", who).replace("{grant}", str(grant)).replace("{cap}", str(cap))
        self._opening = Opening(self.rules, catalog, hidden,
                                menu="THE MENU (each action: its name and what it does; what each can start, grant, "
                                     "give or apply is under ENGINE, in THE WORLD NOW)",
                                commands="MINECRAFT COMMANDS (name — what it does)",
                                not_loaded="(not loaded yet: write commands from what you know of 26.3)")

    def opening(self, refs) -> str:
        """The rules, the menu, every action in full and the command list: the same for every step
        while the actions and the references stay as they are."""
        return self._opening(refs)

    def tail(self, event: Event, seen: Seen, tasks: str, new: list[Row]) -> str:
        """Everything after the conversation that is the event's own, read once at the loop's
        start: the world as it was then (seen: RUNNING NOW, SAVED PLACES, INCANTATIONS and ENGINE,
        the engine's), TASKS, NEW and the event."""
        unread = show(new) or "nothing"
        return (f"\n{seen.show()}\n\n{tasks}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n{unread}\n\n"
                f"THE EVENT:\n{event.show()}\n")

    def prompt(self, event: Event, ctx: Context, seen: Seen, tasks: str, new: list[Row], refs) -> Prompt:
        """The loop's prompt as its first step sends it: the conversation as it is now, kept for
        every step, so a row that arrives meanwhile never moves the prefix."""
        return Prompt(ctx.cached(self.opening(refs), self.checkpoint, marks=LOOP_MARKS),
                      self.tail(event, seen, tasks, new))

    @staticmethod
    def trailer(n: int, limit: int, last: bool) -> str:
        """The step's own words, never cached: its number, the loop's limit now (it grows with each
        line added), and the last step's notice. A loop has no time limit: its steps and the step
        timeout bound it."""
        head = f"\nSTEP {n} of at most {limit}."
        if not last:
            return head + "\n"
        return (f"{head}\nTHIS IS THE LAST STEP (this is step {limit}): run no action and read nothing. End with "
                "handoff (what is left, to an implementer, with its brief), or with reply: what was done and what was "
                "not, in the player's terms, offering to build the rest or asking them to try again. The reply never "
                "mentions steps, time, limits or how you work.\n")

    async def step(self, prompt: Prompt, trailer: str, output: type[StepBase], ref: str):
        """One step: an Answer whose value is a Step; raises LlmError. Its usage row is
        structured()'s: component brain, `ref` (`#<seq> s<n>`, `<task> s<n>`)."""
        return await self.ask(trailer, output, cached=prompt.cached(), component="brain", call_id=ref[:64])


def describe(step: StepBase) -> str:
    """One log line: `together: engine_start, engine_give | read Alex | hand off frost_bow_alex || end reply: …`."""
    bits = []
    if step.actions:
        bits.append(f"{step.order}: " + ", ".join(f"{a.to} ({one_line(a.instruction)[:80]})" for a in step.actions))
    if step.read_items:
        bits.append("read " + ", ".join(step.read_items))
    if step.read_configs:
        bits.append("read the Config of " + ", ".join(step.read_configs))
    bits += [f"hand off {h.name}" for h in step.hand_offs]
    bits += [f"{'cancel' if f.cancel else 'follow up'} {f.task}" for f in step.follow_ups]
    tail = "".join(f" || {k}: {v}" for k, v in (("reply", step.reply), ("record", step.record)) if v)
    return f"{' | '.join(bits) or 'nothing run'} || end {step.end}{tail}"
