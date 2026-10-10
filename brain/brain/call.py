"""The one call: every part of a task routed and, for an action, the action itself written, in
one structured() call (VFG_DISPATCH_ONE_CALL=on), instead of the selector then a planner call
per pick.

It sees what the selector sees (its rules and menu) and what the action planner sees for every
action at once (each action's full description, the planner's rules, Minecraft's command list),
with the saved places and every online player's items, by slot, as `listing.readable` names
them, so an item change needs no listing first. Each action part comes back with its action
ready to run, or a drop. run_script, whose values are shown only for the script named, comes
back without one and is planned by the planner as before; so is a command that Minecraft
refuses, retried with its reference. The selector's code rules still hold: earlier work goes
to the orchestrator whole, a script that does not exist hands the task on, and so does block
work with no orchestrator part.
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator
from vfg_llm import Answer

from .action_planner import PROMPT as PLANNER_PROMPT
from .actions import Catalog
from .config import log
from .selector import (BLOCK_WORK, INSTRUCTION_MAX, MAX_PICKS, NO_MOVE, ORCHESTRATOR_MAX, PART_MAX, PLACEHOLDER,
                       PROMPT as SELECTOR_PROMPT, WHOLE_TASK, distinct, menu, unknown_scripts)

# Planned after the one call, by the planner: its values are shown only for the script it names.
DEFERRED = ("run_script",)

# The selector's and the planner's rules about listing a player's items first: in the one call
# the items are already in PLAYERS, so an item change is one part, its slot named directly.
LISTING_RULES = ("To change an item a player already has (enchant, rename, repair), answer two command parts",
                 "- To list a player's items,")

ROUTER = """
AS THE ROUTER
You hear a player's line first: LINE is their own words as speech-to-text heard them, which may mishear ("T" can be TNT), not a task anyone wrote up. Besides the actions and the orchestrator, a part can go to conversation, the AI that talks with the players: send it there when it is talk rather than something to do (a question, a greeting, a joke, a plan to discuss, a complaint), or needs judgement about the players and the game rather than an action or a build. When the whole line is talk, answer one conversation part with an empty instruction.
RECENT is what was said in the last two minutes, oldest first, by the players and the AI. TASKS is the latest work and how it ended, newest last, each by its id. Use them for "again", "that", "it", "make it bigger", "stop it": a change, fix, undo or stop of earlier work is one orchestrator part, "Change to T<n>: ..." or "Cancel T<n>: ...", with that task's id. Write players' names, never "me", "my" or "us": the speaker is the player LINE names.
`reply` is the one chat line the players see once the actions are done: short, plain, in the AI's own voice ("Power V is on your bow"). Leave it empty when a part goes to conversation, which answers itself.
"""

HOW = """
WRITING EACH ACTION
For every action part, also write the action itself in `action`: that action with its options chosen, exactly as it will run, and one sentence in `say` for the conversation saying what it does, for whom. PLAYERS below lists every online player's items by slot: an item change is one command part that names the slot directly, never a part that lists. Leave `action` null for run_script (it is written after you) and for an orchestrator part. When a part cannot be done because something is missing, such as an item the player does not have, set `drop` to why, in words a player understands, and leave `action` null. Where the rules below say to hand a part on, send that part to orchestrator instead. These rules are the action writer's:
"""


class OnePick(BaseModel):
    """One action to run, as the dispatcher keeps it: with its action already written, or a drop."""
    action: str
    instruction: str
    say: str = ""
    planned: dict[str, Any] | None = None  # the action's options, ready to run; None: the planner writes it
    drop: str = ""


class Decided(BaseModel):
    """What the one call decided: the picks in order, the orchestrator's part and, as the
    router (`first`), the conversation's part and the chat line for the players."""
    picks: list[OnePick] = Field(default_factory=list)
    orchestrator: str = ""
    conversation: str = ""
    reply: str = ""


def decision_type(catalog: Catalog, *, first: bool = False) -> type[BaseModel]:
    """`Decision`, what the model answers: every part of the task, each routed, and an action
    part with its action (one of the catalog's, but run_script's) or a drop. As the router
    (`first`), a part may also go to conversation, and the answer carries a `reply`."""
    To = Literal[tuple(catalog) + ("orchestrator",) + (("conversation",) if first else ())]  # type: ignore[valid-type]
    written = tuple(cls for name, cls in catalog.items() if name not in DEFERRED)
    Act = Union[written] if len(written) > 1 else written[0]  # type: ignore[valid-type]

    class Part(BaseModel):
        part: str = Field(min_length=1, max_length=PART_MAX,
                          description="the words of the task this part covers, short, as the task says them")
        to: To = Field(description="the action that does this part completely, right now, or orchestrator")  # type: ignore[valid-type]
        instruction: str = Field(
            "", max_length=ORCHESTRATOR_MAX,
            description="for an action: one short sentence in plain words, the player names and amounts exactly "
                        "as the task says them; for the orchestrator: the part as a short task, or empty when "
                        "every part is the orchestrator's")
        action: Act | None = Field(  # type: ignore[valid-type]
            None, description="for an action part: that action, its options chosen, ready to run; null for "
                              "run_script, for an orchestrator part and for a part you drop")
        drop: str = Field("", description="only when the part cannot be done because something is missing: "
                                          "why, in words a player understands")
        say: str = Field("", description="for an action part: one sentence for the conversation, what it does, "
                                         "for whom")

        @model_validator(mode="after")
        def _consistent(self):
            said = self.instruction.strip()
            if self.to in ("orchestrator", "conversation"):
                if self.action is not None:
                    raise ValueError(f"the {self.to} part {self.part!r} carries an action: leave it null")
            else:
                if not said:
                    raise ValueError(f"the {self.to} part {self.part!r} has no instruction")
                if len(said) > INSTRUCTION_MAX:
                    raise ValueError(f"the {self.to} instruction is {len(said)} characters: one short sentence "
                                     f"of at most {INSTRUCTION_MAX}; the commands go in action")
                if self.action is not None and getattr(self.action, "action", None) != self.to:
                    raise ValueError(f"the {self.to} part carries a {getattr(self.action, 'action', '?')} action")
                if self.to not in DEFERRED and self.action is None and not self.drop.strip():
                    raise ValueError(f"the {self.to} part {self.part!r} has no action: write it, or drop it "
                                     "and say why")
            if said and PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {self.instruction!r} does nothing: leave the part out")
            if self.to == "teleport" and NO_MOVE.search(said):
                raise ValueError(f"{self.instruction!r} moves nobody: leave the part out")
            return self

    class Decision(BaseModel):
        parts: list[Part] = Field(min_length=1, description="every part of the task, in the order they run")

    class RouterDecision(Decision):
        reply: str = Field("", max_length=300, description="the one chat line the players see once the actions "
                                                           "are done, in the AI's own voice; empty when a part "
                                                           "goes to conversation or nothing needs saying")

    RouterDecision.__name__ = "Decision"
    return RouterDecision if first else Decision


def decided(decision) -> Decided:
    """The parts as picks, each its own (none merged), and the orchestrator text; more than
    MAX_PICKS picks, or none, is the whole task handed on."""
    picks, orchestrator, conversation = [], [], []
    for p in decision.parts:
        if p.to == "orchestrator":
            orchestrator.append((p.instruction or p.part).strip())
            continue
        if p.to == "conversation":
            conversation.append((p.instruction or p.part).strip())
            continue
        planned = p.action.model_dump(mode="json") if p.action is not None and not p.drop.strip() else None
        picks.append(OnePick(action=p.to, instruction=p.instruction.strip(), say=p.say.strip(), planned=planned,
                             drop=p.drop.strip()))
    picks = distinct(picks)
    talk = "; ".join(x for x in conversation if x)
    reply = getattr(decision, "reply", "").strip()
    if len(picks) > MAX_PICKS or (not picks and not orchestrator and not conversation):
        return Decided(conversation=talk)
    return Decided(picks=picks, orchestrator="; ".join(x for x in orchestrator if x), conversation=talk, reply=reply)


def without(text: str, starts: tuple[str, ...]) -> str:
    """`text` without its paragraphs and bullets that start with any of `starts`."""
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(starts))


class OneCall:
    def __init__(self, catalog: Catalog, ask, model: str, planner, *, scripts=(), hidden=frozenset(),
                 first: bool = False):
        if not catalog:
            raise ValueError("the one call needs at least one action")
        self.ask = ask
        self.model = model
        self.scripts = frozenset(scripts)
        self.hidden = hidden
        self.first = first  # the router: raw lines in, with RECENT and TASKS; three ways out
        self.Decision = decision_type(catalog, first=first)
        routing = without(SELECTOR_PROMPT.read_text(encoding="utf-8").rstrip("\n"), LISTING_RULES)
        writing = PLANNER_PROMPT.read_text(encoding="utf-8").split("\n", 1)[1].strip("\n")  # its rules, not its role
        writing = without(writing, LISTING_RULES).replace("the listing under EARLIER IN THIS TASK", "PLAYERS below")
        full = "\n".join(planner.describe(cls) for name, cls in catalog.items() if name not in DEFERRED)
        self.rules = (f"{routing}\n{menu(catalog, scripts)}\n{ROUTER if first else ''}{HOW}{writing}\n\n"
                      f"THE ACTIONS IN FULL:\n{full}")

    def fixed(self, refs) -> str:
        """The opening every call shares, sent for the prompt cache: the rules, the actions, the commands."""
        commands = (refs.menu(exclude=self.hidden) if refs is not None
                    else "(not loaded yet: no command can run, so send what needs one to orchestrator)")
        return f"{self.rules}\nMINECRAFT COMMANDS (name — what it does):\n{commands}\n\n"

    @staticmethod
    def task(task: str, context: str, places: str, players: str) -> str:
        """What changes from call to call."""
        return f"TASK: {task}\nCONTEXT: {context or '(none)'}\n{places}\n{players}\n"

    def prompt(self, task: str, context: str, places: str, players: str, refs) -> str:
        return self.fixed(refs) + self.task(task, context, places, players)

    async def decide(self, task: str, context: str, places: str, players: str, refs, caught: list[str] | None = None):
        """An Answer whose value is a Decided; raises LlmError. No picks is the whole task handed on."""
        if WHOLE_TASK.match(task):
            return Answer[Decided](value=Decided(), cost=0.0, ms=0)
        answer = await self.ask(self.task(task, context, places, players), self.Decision, model=self.model,
                                cached=self.fixed(refs))
        d = decided(answer.value)
        if bad := unknown_scripts(d, self.scripts):
            log("select", f"a script that is not in scripts/ ({'; '.join(bad)[:200]}): the whole task is handed on")
            d = Decided()
            if caught is not None:
                caught.extend(bad)
        elif d.picks and not d.orchestrator and BLOCK_WORK.search(task):
            log("select", "a task that builds or changes blocks, with picks and no orchestrator part: "
                          "the whole task is handed on")
            d = Decided()
        return Answer[Decided](value=d, cost=answer.cost, ms=answer.ms)


    async def decide_line(self, line: str, recent: str, tasks: str, places: str, players: str, refs):
        """As the router: one raw LINE, with RECENT and TASKS. An Answer whose value is a Decided."""
        text = f"LINE: {line}\nRECENT:\n{recent or '(nothing)'}\nTASKS:\n{tasks or '(none)'}\n{places}\n{players}\n"
        answer = await self.ask(text, self.Decision, model=self.model, cached=self.fixed(refs))
        d = decided(answer.value)
        if bad := unknown_scripts(d, self.scripts):
            log("select", f"a script that is not in scripts/ ({'; '.join(bad)[:200]}): handed on whole")
            d = Decided(orchestrator=line, conversation=d.conversation)
        return Answer[Decided](value=d, cost=answer.cost, ms=answer.ms)


def describe(d: Decided) -> str:
    """One log line: `command [written]: give … | run_script: … || orchestrator: …`."""
    def one(p: OnePick) -> str:
        mark = " [dropped]" if p.drop else " [written]" if p.planned else ""
        return f"{p.action}{mark}: {p.instruction}"
    picks = " | ".join(one(p) for p in d.picks) or "no picks"
    return (picks + (f" || orchestrator: {d.orchestrator}" if d.orchestrator.strip() else "")
            + (f" || conversation: {d.conversation}" if d.conversation.strip() else "")
            + (f" || reply: {d.reply}" if d.reply.strip() else ""))
