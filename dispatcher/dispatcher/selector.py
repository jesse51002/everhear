"""The complexity selector: at most one structured() call per task.

It sees the task, its context, the saved places and the menu (each action's name and
its docstring's first line, and for run_script and stop_script the names of the scripts
in scripts/) and nothing more. It answers the task's PARTS, every one, each routed to an
action or to the orchestrator, so a part it leaves out is a part it never wrote rather
than one that slipped between a pick and the orchestrator text. Code turns the parts into
the Picked the dispatcher runs: the action parts as picks, in order, adjacent command
parts merged into one pick (at most MERGE of them), and the orchestrator parts joined
into its text. More than four picks, or no pick, is the whole task to the orchestrator.

Some rules are code, not the model's, because the replay and the end-to-end runs showed
the model breaking them:
- a task that opens with `Change to T7:` or `Cancel T7:` (the conversation's own forms for
  earlier work), `Undo`, `Fix` or `Build` goes to the orchestrator whole, with no model call;
- a part whose instruction is a placeholder ("nothing"), an action part with none, or a
  teleport to where the player already is, is refused, so structured() asks once more;
- a selection that starts or stops a script not in scripts/ is handed on whole, and so is
  one with picks and no orchestrator part for a task that builds or places, removes or
  replaces blocks;
- a pick that repeats an earlier one, the same action and the same instruction, is dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from vfg_llm import Answer

from .actions import Catalog, menu_line
from .config import log

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "selector.md"

# The actions that take a script's name: their menu line lists the names, so the selector
# never picks one for a script that does not exist.
SCRIPT_ACTIONS = ("run_script", "stop_script")

# Always the orchestrator's, whole: earlier work, in the two fixed forms conversation/CLAUDE.md
# gives the conversation. Anything else ("Fix the weather", "undo the rain", "build a tower")
# goes to the selector, whose parts and prompt decide.
WHOLE_TASK = re.compile(r"^\W*(change\s+to\s+T\d+|cancel\s+T\d+)\b", re.IGNORECASE)

# A task that changes blocks: with picks, it must also carry an orchestrator part. "build" the
# verb, never "building" the noun ("a building kit").
BLOCK_WORK = re.compile(r"\bbuilds?\b|\b(place|remove|replace|break|destroy)\b.*\bblocks?\b", re.IGNORECASE)

# An instruction that does nothing: refused.
PLACEHOLDER = re.compile(r"^\W*(nothing( to do)?|none( needed)?|n/?a|no[ -]?op|skip|not needed|do nothing|"
                         r"no action( needed)?|empty|placeholder|tbd)?\W*$", re.IGNORECASE)
# A teleport to where the player already is: a pick invented to fill the list.
NO_MOVE = re.compile(r"\bto (his|her|their|its|your) (own )?((current|present) )?(position|location)\b"
                     r"|\bto (his|her|their|its|your) (own )?(current|present) (spot|place)\b"
                     r"|\bto where (he|she|they|it) (is|are|stands?|stood)\b", re.IGNORECASE)

# At most this many picks run; a task with more goes to the orchestrator whole.
MAX_PICKS = 4
# At most this many adjacent command parts become one command pick (it runs five commands).
MERGE = 3

# An action part's instruction: one short sentence. The action planner writes the commands;
# a long one is a slow answer and usually a command written out. An orchestrator part may be
# long (it is the task the orchestrator does), and so may the part's own words.
INSTRUCTION_MAX = 160
ORCHESTRATOR_MAX = 1500
PART_MAX = 300


class Pick(BaseModel):
    """One action to run: what the dispatcher keeps in state.json."""
    action: str
    instruction: str


class Picked(BaseModel):
    """The selection as the dispatcher runs it: no picks is the whole task handed on."""
    picks: list[Pick] = Field(default_factory=list)
    orchestrator: str = ""


def selection_type(names: list[str]) -> type[BaseModel]:
    """`Selection`, what the model answers: every part of the task, each with `to` a
    Literal of the catalog's names or "orchestrator"."""
    To = Literal[tuple(names) + ("orchestrator",)]  # type: ignore[valid-type]

    class Part(BaseModel):
        part: str = Field(min_length=1, max_length=PART_MAX,
                          description="the words of the task this part covers, short, as the task says them")
        to: To = Field(description="the action that does this part completely, right now, or orchestrator")  # type: ignore[valid-type]
        instruction: str = Field(
            "", max_length=ORCHESTRATOR_MAX,
            description="for an action: one short sentence in plain words, the player names and amounts "
                        "exactly as the task says them, never a command; for the orchestrator: the part "
                        "as a short task, or empty when every part is the orchestrator's",
        )

        @model_validator(mode="after")
        def _does_something(self):
            said = self.instruction.strip()
            if self.to != "orchestrator" and not said:
                raise ValueError(f"the {self.to} part {self.part!r} has no instruction")
            if self.to != "orchestrator" and len(said) > INSTRUCTION_MAX:
                raise ValueError(f"the {self.to} instruction is {len(said)} characters: an action's is one short "
                                 f"sentence of at most {INSTRUCTION_MAX}, never a command written out")
            if said and PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {self.instruction!r} does nothing: leave the part out")
            if self.to == "teleport" and NO_MOVE.search(said):
                raise ValueError(f"{self.instruction!r} moves nobody: leave the part out")
            return self

    class Selection(BaseModel):
        parts: list[Part] = Field(min_length=1, description="every part of the task, in the order they run")

    return Selection


def picked(selection) -> Picked:
    """The parts as picks and the orchestrator text. Adjacent command parts become one pick;
    more than MAX_PICKS picks, or none, is the whole task to the orchestrator."""
    picks: list[Pick] = []
    run: list[str] = []  # the instructions of the command parts being merged
    orchestrator: list[str] = []

    def close() -> None:
        if run:
            picks.append(Pick(action="command", instruction="; ".join(run)))
            run.clear()

    for p in selection.parts:
        if p.to == "orchestrator":
            close()
            orchestrator.append((p.instruction or p.part).strip())
        elif p.to == "command":
            if len(run) == MERGE:
                close()
            run.append(p.instruction.strip())
        else:
            close()
            picks.append(Pick(action=p.to, instruction=p.instruction.strip()))
    close()
    picks = distinct(picks)
    if not picks or len(picks) > MAX_PICKS:
        return Picked()
    return Picked(picks=picks, orchestrator="; ".join(x for x in orchestrator if x))


def menu(catalog: Catalog, scripts: Iterable[str] = ()) -> str:
    """One line per action; run_script and stop_script also name every script there is."""
    names = ", ".join(sorted(scripts)) or "none"
    lines = []
    for name, cls in catalog.items():
        line = f"- {name}: {menu_line(cls)}"
        if name in SCRIPT_ACTIONS:
            line += f" The only scripts: {names}."
        lines.append(line)
    return "\n".join(lines)


class Selector:
    def __init__(self, catalog: Catalog, ask, model: str, prompt: Path = PROMPT, scripts: Iterable[str] = ()):
        if not catalog:
            raise ValueError("the selector needs at least one action")
        self.ask = ask
        self.model = model
        self.scripts = frozenset(scripts)
        self.Selection = selection_type(list(catalog))
        # The rules and the menu open the one user message, the task closes it. No system
        # prompt: in the replay, DeepSeek V4 Flash without reasoning followed the same rules
        # far less often from a system message.
        self.rules = prompt.read_text(encoding="utf-8").rstrip("\n") + "\n" + menu(catalog, scripts) + "\n"

    @staticmethod
    def whole(task: str) -> bool:
        """The orchestrator's whole, with no model call: `Change to T<n>` and `Cancel T<n>`."""
        return bool(WHOLE_TASK.match(task))

    @staticmethod
    def prompt(task: str, context: str, places: str) -> str:
        return f"TASK: {task}\nCONTEXT: {context or '(none)'}\n{places}\n"

    async def select(self, task: str, context: str, places: str, caught: list[str] | None = None):
        """An Answer whose value is a Picked; raises LlmError. No picks is the whole task
        handed on. `caught`, when given, collects the picks of a script not in scripts/."""
        if WHOLE_TASK.match(task):
            return Answer[Picked](value=Picked(), cost=0.0, ms=0)
        answer = await self.ask(self.rules + "\n" + self.prompt(task, context, places), self.Selection, model=self.model)
        sel = picked(answer.value)
        if bad := unknown_scripts(sel, self.scripts):
            log("select", f"a script that is not in scripts/ ({'; '.join(bad)[:200]}): the whole task is handed on")
            sel = Picked()
            if caught is not None:
                caught.extend(bad)
        elif sel.picks and not sel.orchestrator and BLOCK_WORK.search(task):
            log("select", "a task that builds or changes blocks, with picks and no orchestrator part: "
                          "the whole task is handed on")
            sel = Picked()
        return Answer[Picked](value=sel, cost=answer.cost, ms=answer.ms)


def distinct(picks: list) -> list:
    """The picks, each (action, instruction) once, in order; the instruction compared in
    lower case, its spaces and closing punctuation ignored."""
    seen, out = set(), []
    for p in picks:
        key = (p.action, " ".join(p.instruction.lower().split()).rstrip(".!;, "))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def unknown_scripts(selection, scripts: Iterable[str]) -> list[str]:
    """The run_script and stop_script instructions that name no script in scripts/; a name
    said with spaces, "tnt rain", counts as tnt_rain."""
    said = [re.compile(r"\b" + r"[_ ]".join(map(re.escape, n.split("_"))) + r"\b", re.IGNORECASE) for n in scripts]
    return [f"{p.action}: {p.instruction}" for p in selection.picks
            if p.action in SCRIPT_ACTIONS and not any(r.search(p.instruction) for r in said)]


def describe(selection) -> str:
    """One log line: `command: give … | teleport: … || orchestrator: …`."""
    picks = " | ".join(f"{p.action}: {p.instruction}" for p in selection.picks) or "no picks"
    orch = selection.orchestrator.strip()
    return picks + (f" || orchestrator: {orch}" if orch else "")
