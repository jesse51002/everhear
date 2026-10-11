"""Respond, the brain's cheap mode: a line Jev classes conversational gets one structured() call
on a cheap model (DeepSeek V4 Flash, reasoning off: config.py) over the same context, which keeps
up the personality and the banter and replies. It can do one other thing: hand the line up to
the brain, which runs the same line on GPT-6.1 Sol. It never acts on the game, so it never
promises or offers anything there.

It answers talk, and a rule or a preference about the future only, which holds because the brain
reads the same conversation. It hands up anything that asks for something now, a change to what
is already going on in the world, a rule the game itself would have to enforce, an answer to the
AI's question, and anything it is unsure of (Prototype_Jev_Routing, _Respond's prompt_;
integration/replay/respond_bench.md).

It decides first: its answer is {reason, hand_up, reply}, in that order, and handing up is the
default. A model with no reasoning writes the fields in order, and with the reply first it
committed to answering before it weighed handing up: on the 2026-10-08 lines it handed up 31% of
the asks, and 77.5% deciding first (integration/replay/gate_bench.md). `reason` is required: left
optional, the model sometimes wrote `hand_up` first and decided with no reason at all (16 of 88
answers in two runs of one prompt; none of 460 once required: respond_bench.md). A wrong hand-up
costs about a second and a fraction of a cent, a missed ask the player's ask. Handing up replaces
its reply, never adds to it, so a player never gets two answers: a hand-up's reply is dropped
here, whatever the model wrote.

Its prompt is laid out as the brain's: the opening (its rules, the owner, the menu's lines,
marked), the conversation in the same minute-blocks (never a context line: context.py), then the
world as last fetched (each player's place and state, no items), the open tasks, what is new and
the line.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .actions import Catalog
from .call import library, menu_lines
from .context import Context, Row, show
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "respond.md"


class Response(BaseModel):  # decide first: a model writes the fields in this order, `reason` always
    reason: str = Field(description="first, a few words: what any part of the line wants, if anything, now or "
                                    "for later, and so whether you hand it up")
    hand_up: bool = Field(True, description="true unless the line is clearly talk, or a rule or a preference about "
                                            "the future only: the brain answers it")
    reply: str = Field("", description="your one chat line when you do not hand up, never a promise or an offer, "
                                       "or empty to say nothing; always empty when you hand up")


class Respond:
    def __init__(self, catalog: Catalog, ask, *, owner: str = "", checkpoint: int = 5, prompt: Path = PROMPT):
        self.catalog = catalog
        self.ask = ask  # structured() with Respond's model, reasoning and timeout
        self.checkpoint = checkpoint
        rules = prompt.read_text(encoding="utf-8").rstrip("\n")
        self.rules = rules.replace("{owner}", owner or "not named (VFG_OWNER is not set)")
        self._opening: tuple[tuple, str] | None = None

    def opening(self) -> str:
        names = library(self.catalog)
        key = tuple(names)
        if self._opening is None or self._opening[0] != key:
            text = (f"{self.rules}\n\nWHAT THE BRAIN CAN DO (its actions; anything else is made new by a "
                    f"coding implementer):\n{menu_lines(self.catalog, names)}\n\n"
                    "THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                    "player's line Jev's class):\n")
            self._opening = (key, text)
        return self._opening[1]

    @staticmethod
    def tail(row: Row, seen: Seen, tasks: str, new: list[Row]) -> str:
        how = "typed" if row.source == "chat" else "said"
        return (f"\n{seen.show()}\n\n{tasks}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n"
                f"{show(new) or 'nothing'}\n\n"
                f"THE LINE #{row.seq}, {row.hms}: {row.who} {how}: {row.text}\n")

    async def answer(self, row: Row, ctx: Context, seen: Seen, tasks: str, new: list[Row]):
        """An Answer whose value is a Response, never with both a reply and a hand-up; raises LlmError."""
        answer = await self.ask(self.tail(row, seen, tasks, new), Response,
                                cached=ctx.cached(self.opening(), self.checkpoint))
        if answer.value.hand_up and answer.value.reply:
            answer.value.reply = ""
        return answer
