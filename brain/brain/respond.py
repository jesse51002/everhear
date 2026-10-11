"""Respond, the brain's cheap mode: a line Jev classes conversational gets one structured() call
on a cheap model (DeepSeek V4 Flash, reasoning off: config.py) over the same context, which keeps
up the personality and the banter and replies. It can do one other thing: hand the line up to
the brain, which runs the same line on GPT-6.1 Sol. It never acts on the game, so it never
promises or offers anything there.

It answers talk, and a rule or a preference about the future only, which holds because the brain
reads the same conversation. It hands up anything that asks for something now, a change to what
is already going on in the world, a rule the game itself would have to enforce, an answer to the
AI's question, and anything it is unsure of (Prototype_Jev_Routing, _Respond's prompt_;
integration/replay/respond_bench.md). A reply that still offers or promises something in the game
is handed up in code instead of said (promises()).

It decides first: its answer is {reason, hand_up, reply}, in that order, and handing up is the
default. A model with no reasoning writes the fields in order, and with the reply first it
committed to answering before it weighed handing up: on the 2026-10-08 lines it handed up 31% of
the asks, and 77.5% deciding first (integration/replay/gate_bench.md). `reason` is required: left
optional, the model sometimes wrote `hand_up` first and decided with no reason at all (16 of 88
answers in two runs of one prompt; none of 540 once required: respond_bench.md). A wrong hand-up
costs about a second and a fraction of a cent, a missed ask the player's ask. Handing up replaces
its reply, never adds to it, so a player never gets two answers: a hand-up's reply is dropped
here, whatever the model wrote.

Its prompt is laid out as the brain's: the opening (its rules, the owner, the menu's lines,
marked), the conversation in the same minute-blocks (never a context line: context.py), then the
world as last fetched (each player's place and state, no items), the open tasks, what is new and
the line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .actions import Catalog
from .call import library, menu_lines
from .config import log
from .context import Context, Row, show
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "respond.md"

# What the AI does in the game, as verbs in any form ("make", "makes", "making", "makin'"): the verbs
# of Respond's promises and offers on 2026-10-08 and the reviewer's probes. Tuned for catching a
# promise over never handing up a fine reply: a missed promise tells a player something will happen
# that never does, a wrong hand-up costs a brain call and the player still gets one reply.
_STEMS = ("mak", "giv", "build", "spawn", "summon", "set", "fix", "turn", "get", "put", "add", "bring", "pull",
          "teleport", "tp", "clear", "swap", "chang", "upgrad", "boost", "beef", "buff", "toss", "drop", "hook",
          "sort", "redo", "layer", "handl", "do", "craft", "stop", "remov", "kill", "whip", "start", "mov", "send",
          "grab", "enchant", "repair", "freez", "delet", "destroy", "undo", "cancel", "rebuild", "reset", "restor",
          "keep", "heal", "protect", "shrink", "doubl", "spin", "cook", "conjur", "unleash")
_DO = rf"(?:{'|'.join(_STEMS)})(?:e|es|s|ed|\w?ing|\w?in'?)?\b(?!(?<=give) you that\W*$)"  # not "I'll give you that."
_ING = rf"(?:{'|'.join(_STEMS)})\w?(?:ing|in'?)\b"
_I_WILL = r"(?:i'll|i will|i shall|i'm gonna|i am gonna|i'm going to|i am going to|imma|i'ma)"
_START = r"(?:^|[.!?,;:—–]\s*|\s-\s+)"    # the start of the reply or of a clause; never "well-done"
# What an -ing verb is done to, for it to read as the AI doing something: "spawning some cows",
# "getting you a sword", never "doing great", "getting hungry" or "making no promises".
_OBJECT = r"(?:you|your|it|that|them|those|one|some|a|an|the)\b"
# The AI's own talk, which it may promise (a rule for later is Respond's to acknowledge):
# "I'll keep quiet", "I'll stop talking", "I'll turn the chatter down", "I'll change my style".
_OWN_TALK = r"(?! (?:quiet|talking|chatting|(?:the )?chatter|my |it (?:short|brief|down|clean|quiet)))"
# An offer or a promise, whatever is going on: the AI proposes or says it will do something.
OFFERS = tuple(re.compile(p) for p in (
    r"\b(?<!don't )(?<!not )want me to\b", r"\bshall i\b", rf"\bshould i {_DO}", r"\bsay the word\b",
    # a question that waits for the player's yes: the reply's last sentence, not one it answers itself
    r"(?:^|[.!?,]\s+|\bor |\byou )want (it|one|them|that|this|the|a|an|some|more|another|extras?|your own)\b"
    r"[^.!?]*\?\W*$",
    rf"\bi can (?!get (?:why|how|what)\b){_DO}", rf"\bi could {_DO} (you|one|it|that|them|those|some|a|an|the|your)\b",
    rf"\bi (might|may|could) be able to {_DO}", rf"\bhappy to {_DO}",
    rf"\blet me (?!put it this way|get this straight|turn that around){_DO}",
    r"\b(has|have|got) your name on it\b", r"\bwhenever you want it\b"))
PROMISES = tuple(re.compile(p) for p in (
    rf"\b{_I_WILL} (?:(?:just|also|go|be|now|then|quickly|happily|gladly|personally|totally|definitely) )*"
    rf"(?:{_DO}|take care of|see (it|to it|that)|call out|point you|ping you|announce){_OWN_TALK}",
    rf"{_START}gonna {_DO}", rf"\b(i'm|i am|we're|we are) (?:(?:just|now|already|busy) )?{_ING} {_OBJECT}",
    rf"{_START}{_ING} {_OBJECT}[^.!?]*\bnow\b(?! on)",
    rf"(?:{_START}|\b(?:i'm|i am|we're|yep|yes|yeah|sure|sure thing|ok|okay|aye|got it|you got it|no problem|"
    rf"alright|right) +)(?:already |right )?on it\b",
    rf"{_START}(?:will do|consider it done|leave it to me)\b", rf"{_START}(?:all )?done(?=\s*(?:[.!,]|$))",
    r"(?<!not )\bright away\b(?!\s*\?)",
    r"\b(i'll|i will|i've got|i got) (watch )?your back\b", r"\bgive me a (sec|second|minute|moment)\b"))
# Work under way: a status when the line's speaker has a task open, a promise when not.
UNDER_WAY = tuple(re.compile(p) for p in (
    r"(?<!night's )(?<!day's )(?<!dawn's )(?<!morning's )(?<!sun's )\bcoming (right )?(up|at (ya|you))\b",
    r"\bgive (it|her|him|them) a (sec|second|minute|moment|beat)\b", r"\bhang tight\b", r"\bone sec\b",
    r"\bon (its|their) way\b", r"(?<!you)(?<!you )(?<!we)(?<!we )('s|is|are|'re) on the way\b(?! (back|home|out|there))", r"\bit's coming\b",
    r"\b(should|will|'ll) be (done|ready) (in|soon)\b"))
# An open task in the tasks text, as tasks.py writes it: `- T183 for Steve, since 18:22:31, open: …`,
# or `…, an implementer is on it: …`; on the named-tasks branch, `- castle_steve for Steve, open: …`.
OPEN_TASK = re.compile(r"^- \S+ for (\S+), (since \d\d:\d\d:\d\d, )?(open|an implementer is on it): ", re.M)


def promises(reply: str, tasks: str = "", who: str = "") -> str:
    """"an offer" or "a promise" when the reply has one, else "". Respond cannot act on the game, so a
    reply that offers or promises something there is never said: the line goes up instead
    (Prototype_Jev_Routing, Respond's point 1). The prompt says so, and still about one reply in 20
    on 2026-10-08 did (integration/replay/respond_bench.md, "The guard"). Saying that work under way
    is coming is a status when the tasks text (`tasks`) has a task open for the line's speaker
    (`who`), and a promise when it has none for them. A rule acknowledged with an action verb
    ("I'll turn the chatter down") is handed up too: the brain then acknowledges it, a second later."""
    text = " ".join(reply.lower().replace("\u2019", "'").split())
    if any(p.search(text) for p in OFFERS):
        return "an offer"
    if any(p.search(text) for p in PROMISES):
        return "a promise"
    if who not in {m.group(1) for m in OPEN_TASK.finditer(tasks)} and any(p.search(text) for p in UNDER_WAY):
        return "a promise"
    return ""


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
        """An Answer whose value is a Response, never with both a reply and a hand-up, and never a
        reply that promises or offers (promises()); raises LlmError."""
        answer = await self.ask(self.tail(row, seen, tasks, new), Response,
                                cached=ctx.cached(self.opening(), self.checkpoint))
        said = answer.value
        if not said.hand_up and (kind := promises(said.reply, tasks, row.who)):
            log("respond", f"guard #{row.seq} {kind}: {json.dumps(said.reply, ensure_ascii=False)}")
            said.hand_up, said.reason = True, f"{said.reason} · handed up in code: {kind}"
        if said.hand_up and said.reply:
            said.reply = ""
        return answer
