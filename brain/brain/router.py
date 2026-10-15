"""The router (Prototype_Conversation_And_Router): one structured() call per hand-off, on DeepSeek
V4.1 Flash with no reasoning (config.py), whose whole job is to pick who does the work. The
conversation agent (respond.py) has already answered the player; when it hands a line off, or an
implementer's report leaves something to do, this call picks one of five, and code places the
work there (loop.py), with no model:

- new_loop: a new brain loop, for what the brain's actions do now, a question only a look at the
  game answers, a mixed ask the brain splits, and anything not clearly an implementer's;
- loop: a loop already on that thing, thinking or ended and still offered (WORK UNDER WAY: its id,
  as L3), which reads the line before its next step: a repeat, a correction, an addition;
- new_implementer: something new no action does completely, with the new task's name, description
  and whole brief, written here as the brain writes one (call.HandOff);
- implementer: a task's implementer already on that thing, or done with it: a change, a fix, an
  addition, or "never mind" (cancel), as the brain's follow-up (call.FollowUp); a line about two
  tasks' things follows each up, one follow-up a task;
- nowhere: nothing to do: the thing is under way already ("hurry up"), or the line asks for nothing
  (a greeting, a reaction, a remark to another player), with a reply of its own or none, said after
  the conversation agent's line, never in its place: the agent's line goes out the moment it
  answers, every time, hand-off or not.

Why a call of its own. Picking the task was the brain's hardest job, and the reason it ran on
Gemini: on the 2026-10-08 replay DeepSeek V4 Flash lumped nine powers into one task, where Gemini
kept one thing to one task (integration/replay/taskcall.md). Here it is the call's only job, with
the brain's own rules for it (FOLLOW-UP OR NEW, the name, the brief), and every loop and implementer
in view, every player's, so a player's second ask lands in the loop already on it, and two players
asking for one thing get one task.

It decides first, as Respond does: `reason`, then `to`, then only the fields that choice uses. A
model that does not reason writes its fields in order, so the choice is made before a brief is
written; and the reason ends with the choice ("… so: implementer lava_moat_sam"), so `to` copies a
decision already written. On the router replay (integration/replay/router/report.md) a reason that
only described the line was twice followed by a `to` it argued against (#600, #128: a duplicate
task), so a reason whose last "so:" names another choice than `to` is sent back. The schema never changes, whatever the actions, the loops and the tasks, so the cached
prefix holds: `to` is a name, and every rule that depends on the moment is a validator
(route_type). Every property is required in the schema (call.all_required), so the model writes
each field, its default included. A validator sends back only what code cannot carry out: a loop
not offered, a hand-off missing or not one, a follow-up missing, a task named twice or not under
TASKS, a brief the brain's own checks refuse (checks.py), a reason that chose otherwise. What the
choice does not use is dropped and logged, never sent back.

The prompt, laid out as a brain step's so it reads the same rows the same way:
1. the opening, marked: the rules (prompts/router.md, the owner named), the menu, every library
   action in full and Minecraft's command list, so it knows what the brain can do now;
2. the conversation, a minute-block at a time, marked at checkpoints (context.py);
3. the tail, never cached: this server's own actions in full, the world, TASKS in full (tasks.board),
   WORK UNDER WAY in full (the core's text: every brain loop, its lines, its state, its steps), and
   the event: THE LINE with the conversation agent's answer, or THE REPORT with what it left.
Its usage row: component router, `#<seq>` for a line, the task's name for a report.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from . import checks
from .actions import Catalog
from .call import REPLY_MAX, FollowUp, HandOff, Opening, all_required, one_line, own_actions
from .config import log
from .context import Context, Row
from .respond import claims_done, promises
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "router.md"
NEW_LOOP, LOOP, NEW_IMPLEMENTER, IMPLEMENTER, NOWHERE = "new_loop", "loop", "new_implementer", "implementer", "nowhere"
CHOICES = (NEW_LOOP, LOOP, NEW_IMPLEMENTER, IMPLEMENTER, NOWHERE)
# Conversation marks in the router's prompt, as a brain step's (call.LOOP_MARKS): the opening takes one,
# and the tail is never marked, since it is the event's own.
MARKS = 2
LOOP_ID = re.compile(r"^\s*L?(\d+)\s*$", re.IGNORECASE)  # "L3", "l3", " 3 ": what a loop id may come as


class RouteBase(BaseModel):
    """The router's answer, decided first: the fields in the order the model writes them."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """What structured() sends: every property of the answer and of what it holds, required."""
        return all_required(super().model_json_schema(*args, **kwargs))

    reason: str = Field(description="first, one or two short sentences: what the line asks for, if anything, "
                                    "and which loop under WORK UNDER WAY or task under TASKS is on that thing "
                                    "already, if any; it ends with your choice and its target: '… so: implementer "
                                    "lava_moat_sam', '… so: loop L3', '… so: nowhere'")
    to: Literal["new_loop", "loop", "new_implementer", "implementer", "nowhere"] = Field(
        description="who does the work: new_loop, a new brain loop; loop, the loop already on it; new_implementer, "
                    "a new implementer with its brief; implementer, the tasks' implementers, a follow-up or a "
                    "cancel each; nowhere, nothing to do: the line asks for nothing, or presses for work under way")
    loop: str = Field("", description="to loop: the loop's id as WORK UNDER WAY shows it (L3); else empty")
    hand_offs: list[HandOff] = Field(default_factory=list, description="to new_implementer: exactly one, the new "
                                                                       "task with its whole brief; else []")
    follow_ups: list[FollowUp] = Field(default_factory=list, description="to implementer: one for each task under "
                                                                         "TASKS the line changes, each task once; "
                                                                         "else []")
    reply: str = Field("", description="to nowhere: the player's one chat line in the AI's own voice, saying it is "
                                       "still in progress or answering the line, or empty when none is needed; "
                                       "else empty")

    def target(self) -> str:
        """Where the work goes, for a log line: the loop's id, the new task's name or every task
        followed up ("cancel " before one cancelled); "" for new_loop and nowhere."""
        if self.to == LOOP:
            return self.loop
        if self.to == NEW_IMPLEMENTER and self.hand_offs:
            return self.hand_offs[0].name.strip()
        if self.to == IMPLEMENTER:
            return ", ".join(f"{'cancel ' if f.cancel else ''}{f.task.strip()}" for f in self.follow_ups)
        return ""


def chosen(reason: str) -> str | None:
    """The choice a reason ends with ("… so: implementer lava_moat_sam" → implementer), or None
    when its last "so:" names none of the five, or it has none: such a reason is not checked."""
    at = reason.lower().rfind("so:")
    if at < 0:
        return None
    words = re.findall(r"[a-z_]+", reason[at + 3:].lower())
    return words[0] if words and words[0] in CHOICES else None


def route_type(catalog: Catalog, *, loops: Collection[str], tasks: Collection[str],
               report: bool = False, said: str = "", under_way: bool = False) -> type[RouteBase]:
    """`Route`, checked against the moment as it is when the answer arrives: `loops`, the ids of
    the loops that can take a line now (WORK UNDER WAY offers them), `tasks`, the names under TASKS,
    and `catalog`, the actions a hand-off's name must not take. `report`: the event is a report's
    hand-off, whose work goes to an implementer, a new loop or nowhere, never a loop (the core offers
    none for it), and follows one task up at most (Brain.report_route follows one). A line's
    follow-ups may be several, one a task (Brain.place follows each up). A nowhere needs no reply:
    with none, the conversation agent's line is the one said, so a line's `nowhere` with no reply is
    sent back when that line (`said`) promises work: the player was told it is on its way, and
    nothing would come (a fresh brain's first line, "give me 64 steak", went nowhere behind "On it,
    chef!"). A status in it ("Still on it.") is one when the speaker has work under way
    (`under_way`). Its JSON schema is the same for every catalog, every loop and task, and both
    events."""
    offered = list(dict.fromkeys(str(x).strip() for x in loops))
    names = frozenset(str(x).strip() for x in tasks)
    agents = said  # the agent's line (the validator's own `said` is the reason's choice)

    def loop_id(given: str) -> str | None:
        """The offered id the model meant: "L3" as written, or as "l3" or "3"; None for one not offered."""
        given = given.strip()
        if given in offered:
            return given
        m = LOOP_ID.match(given)
        if m is None:
            return None
        for x in offered:
            k = LOOP_ID.match(x)
            if k is not None and k.group(1) == m.group(1):
                return x
        return None

    class Route(RouteBase):
        @model_validator(mode="after")
        def _placed(self):
            said = chosen(self.reason)
            if said is not None and said != self.to:
                raise ValueError(f"the reason ends choosing {said}, and to is {self.to}: make them agree, the reason "
                                 "ending with the choice you mean")
            self._drop_what_does_nothing()
            if self.to == LOOP:
                if report:
                    raise ValueError("this is a report's hand-off, and no loop takes one: follow its task up "
                                     "(implementer), start a new_loop, or nowhere")
                got = loop_id(self.loop)
                if got is None:
                    if not offered:
                        raise ValueError("no loop is under WORK UNDER WAY to take a line: choose new_loop")
                    raise ValueError(f"loop {self.loop.strip()!r} is not under WORK UNDER WAY: name one that is "
                                     f"({', '.join(offered)}), or choose new_loop")
                self.loop = got
            elif self.to == NEW_IMPLEMENTER:
                if not self.hand_offs:
                    raise ValueError("new_implementer needs its hand-off: the name, the description and every part "
                                     "of the brief")
                if len(self.hand_offs) > 1:
                    raise ValueError("new_implementer hands off one thing: for several new things, choose new_loop, "
                                     "which splits them")
                checks.hand_offs(self.hand_offs, catalog)
            elif self.to == IMPLEMENTER:
                if not self.follow_ups:
                    raise ValueError("implementer needs its follow-up: the task's name as TASKS shows it, with its "
                                     "brief, or cancel")
                if report and len(self.follow_ups) > 1:
                    raise ValueError("a report's hand-off follows one task up: the one what is left belongs to")
                checks.follow_ups(self.follow_ups, names)
            elif self.to == NOWHERE:
                if (not report and not self.reply.strip() and agents.strip()
                        and promises(agents, "", "-", offers=False, busy=("-",) if under_way else ()) == "a promise"):
                    raise ValueError("the player was told it's on its way: place the work, or write your own line "
                                     "saying why nothing more is done")
                if len(self.reply) > REPLY_MAX:
                    raise ValueError(f"the reply is {len(self.reply)} characters, past the {REPLY_MAX} a chat line "
                                     "may hold: one line, never over 200")
                # A line's goes out in place of the conversation agent's, and nothing is placed: it may say the thing
                # is still on its way, never that something will be done ("I'll fix it") or is done ("Yep, it's
                # fixed", on a line about a thing still broken: the router replay's #711).
                # A report's nowhere follows the agent's line, and may say nothing is left after all.
                # The thing it speaks of is under way (that is why nowhere), so a status ("still on it") is read as one:
                # the speaker counted busy, as the agent's check counts a player with a loop thinking.
                status = promises(self.reply, "", "-", offers=False, busy=("-",))
                if status == "a promise" or (not report and claims_done(self.reply)):
                    raise ValueError("nowhere starts nothing, so its reply may neither promise work nor say a thing is "
                                     "done or fixed: say it is still in progress, or choose who does the work")
            return self

        def _drop_what_does_nothing(self) -> None:
            """The fields this choice would ignore, dropped and logged rather than sent back."""
            dropped = []
            if self.to != LOOP and self.loop.strip():
                dropped.append(f"loop {self.loop.strip()!r}")
            if self.to != NEW_IMPLEMENTER and self.hand_offs:
                dropped.append("hand-off " + ", ".join(h.name.strip() for h in self.hand_offs))
                self.hand_offs = []
            if self.to != IMPLEMENTER and self.follow_ups:
                dropped.append("follow-up of " + ", ".join(f.task.strip() for f in self.follow_ups))
                self.follow_ups = []
            if self.to != NOWHERE and self.reply.strip():
                dropped.append(f"reply {self.reply.strip()!r}")
            if self.to != LOOP:
                self.loop = ""
            if self.to != NOWHERE or not self.reply.strip():
                self.reply = ""
            if dropped:
                log("router", f"to {self.to}: dropped, it would do nothing: {'; '.join(dropped)}")

    return Route


@dataclass
class RouterEvent:
    kind: Literal["LINE", "REPORT"]
    row: Row | None = None        # LINE: the line
    said: str = ""                # the conversation agent's reply ("" = none); for a LINE it already went out
    reason: str = ""              # the conversation agent's reason
    choice: str = ""              # LINE: the agent's choice behind the hand-off, "look" or "do" ("" = it failed)
    task: dict | None = None      # REPORT: the task's row (tasks.py)
    status: str = ""              # REPORT: the task's status as the conversation agent wrote it
    report: str = ""              # REPORT: the implementer's report whole, its DESCRIPTION line cut off
    left: str = ""                # REPORT: what the conversation agent says is left

    def ref(self) -> str:
        """The call's usage ref: `#<seq>` for a line, the task's name for a report."""
        if self.kind == "LINE":
            return f"#{self.row.seq}"
        return str((self.task or {}).get("name") or "report")[:56]

    def show(self) -> str:
        """THE LINE or THE REPORT, the prompt's last block, with what the conversation agent made of it."""
        said = (f"(the conversation agent already answered the player: “{one_line(self.said)}”)" if self.said.strip()
                else "(the conversation agent said nothing)")
        if self.kind == "LINE":
            r = self.row
            how = "typed" if r.source == "chat" else "said"
            text = f"THE LINE #{r.seq}, {r.hms}: {r.who} {how}: {r.text}\n{said}"
            if self.reason.strip():
                to = "looked up" if self.choice == "look" else "done"
                text += f"\n(the conversation agent passed it on to be {to}: {one_line(self.reason)})"
            return text
        t = self.task or {}
        name = t.get("name") or "?"
        return (f"THE REPORT: the implementer of {name} ended; the conversation agent read the report, told the "
                f"player and wrote the task {self.status or '?'}. It handed off what is left.\n"
                f"{name} was for {t.get('player') or 'nobody'}, who said: {t.get('line') or ''}\n"
                f"WHAT IT IS: {t.get('description') or ''}\nITS LATEST BRIEF:\n{t.get('brief') or ''}\n"
                f"ITS REPORT:\n{self.report.strip()}\n{said}\n"
                f"WHAT IS LEFT, as the conversation agent read it: {one_line(self.left) or 'not said'}")


class Router:
    def __init__(self, catalog: Catalog, ask, *, owner: str = "", hidden: frozenset[str] = frozenset(),
                 checkpoint: int = 5, prompt: Path = PROMPT):
        self.catalog = catalog
        self.ask = ask  # structured() with the router's model, reasoning, providers and timeout
        self.hidden = hidden
        self.checkpoint = checkpoint
        rules = prompt.read_text(encoding="utf-8").rstrip("\n")
        self.rules = rules.replace("{owner}", owner or "not named (VFG_OWNER is not set)")
        self._opening = Opening(self.rules, catalog, hidden,
                                menu="THE MENU (each action the brain can run: its name and what it does; this "
                                     "server's own actions are at the end, after the conversation)",
                                commands="MINECRAFT COMMANDS (the brain's command action runs them; name — what it "
                                         "does)",
                                not_loaded="(not loaded yet: the brain writes commands from what it knows of 26.3)")

    def opening(self, refs) -> str:
        """The rules, the menu, every library action in full and the command list: the same for
        every call while the library and the references stay as they are (call.Opening, as a
        brain step's)."""
        return self._opening(refs)

    def own_actions(self) -> str:
        """This server's own actions, made by implementers, in full, after the conversation, as in
        a brain step (call.own_actions)."""
        return own_actions(self.catalog)

    def tail(self, event: RouterEvent, seen: Seen, board: str, under_way: str) -> str:
        """Everything after the conversation, the event's own: the server's own actions, the
        world, TASKS in full, WORK UNDER WAY in full and the event."""
        return f"\n{self.own_actions()}\n\n{seen.show()}\n\n{board}\n\n{under_way}\n\n{event.show()}\n"

    async def route(self, event: RouterEvent, ctx: Context, seen: Seen, board: str, under_way: str, *,
                    loops: Collection[str], tasks: Collection[str], refs, busy: bool = False):
        """An Answer whose value is a Route, checked against `loops` (the ids WORK UNDER WAY offers),
        `tasks` (the names under TASKS) and, for a line, the conversation agent's line (`busy`: the
        speaker has work under way, so a status in it is one); raises LlmError. Its usage row is
        structured()'s: component router, `event.ref()`."""
        line = event.kind == "LINE"
        output = route_type(self.catalog, loops=loops, tasks=tasks, report=not line,
                            said=event.said if line else "", under_way=busy)
        return await self.ask(self.tail(event, seen, board, under_way), output,
                              cached=ctx.cached(self.opening(refs), self.checkpoint, marks=MARKS),
                              component="router", call_id=event.ref()[:64])
