"""Respond, the brain's cheap mode: a line Jev classes conversational gets one structured() call
on a cheap model (Gemini 3.8 Flash, low reasoning) over the same context, which keeps up the
personality and the banter and replies. It can do one other thing: hand the line up to the
brain, which runs the same line on GPT-6.1 Sol. It never acts on the game.

It hands up anything that asks for something to happen in the game, and anything it is unsure
of: a wrong hand-up costs about a second and a fraction of a cent, a missed ask the player's
ask. Handing up replaces its reply, never adds to it, so a player never gets two answers: a
hand-up's reply is dropped here, whatever the model wrote.

Its prompt is laid out as the brain's: the opening (its rules, the owner, the menu's lines,
marked), the conversation in the same minute-blocks, then the world as last fetched (no items),
the open tasks, what is new and the line.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .actions import Catalog
from .call import library, menu_lines
from .context import Context, Row, show
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "respond.md"


class Response(BaseModel):
    reply: str = Field("", description="your one chat line, or empty to say nothing; always empty when you hand up")
    hand_up: bool = Field(False, description="true when the line, or a context line under NEW, asks for something "
                                             "to happen in the game, or when you are unsure: the brain answers it")
    reason: str = Field("", description="a few words: why you hand it up, or why not")


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
                f"{show(new) if new else 'nothing'}\n\n"
                f"THE LINE #{row.seq}, {row.hms}: {row.who} {how}: {row.text}\n")

    async def answer(self, row: Row, ctx: Context, seen: Seen, tasks: str, new: list[Row]):
        """An Answer whose value is a Response, never with both a reply and a hand-up; raises LlmError."""
        answer = await self.ask(self.tail(row, seen, tasks, new), Response,
                                cached=ctx.cached(self.opening(), self.checkpoint))
        if answer.value.hand_up and answer.value.reply:
            answer.value.reply = ""
        return answer
