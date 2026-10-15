"""The conversation agent (the class is still Respond, its usage rows `respond`): every line said
to the AI comes to it first, whatever Jev made of it, and it answers at once, in one structured()
call on a cheap model with no reasoning (DeepSeek V4.1 Flash: config.py) over the shared context. It
keeps up the personality and the banter. It ends a line in one of three ways: a reply; a reply and
a hand-off, or a hand-off alone; or silence (Prototype_Conversation_And_Router, _The conversation
agent answers first_). It has no actions and picks nothing: when it hands a line off, the router
(router.py) picks who does the work, a brain loop or an implementer, new or already on it, and a
loop does it behind the player's turn. An implementer's report comes to it too, at once, never
queued: it tells the player what they have, writes the task's record and status, and hands what is
left to the router (report()).

What changed, and why. Until this spec Jev's class picked the agent: an action-oriented line went
straight to the brain's loop, and only a conversational one came here, to be answered or handed UP
to a loop that then ran in the player's turn, so a player waited behind every loop, up to its time
limit (on the night of 2026-10-12, 19 lines and 3 reports waited 366 s in all). Now the turn is two
quick calls, this one and the router's, and a hand-off may carry a reply: a short acknowledgement
("On it!", "Still on it."), said at once, or held until the router has answered while the router
does not reason (the core's choice, loop.py). The agent sees what is running (WORK UNDER WAY: every
brain loop, every player's; TASKS: the builds), so "is it done yet?" is answered from it, and a
repeat of an ask under way is handed off with "Still on it." for the router to judge.

It decides first: its answer is {reason, hand_off, reply}, in that order, and handing off is the
default. A model with no reasoning writes the fields in order, and with the reply first it
committed to answering before it weighed handing up: on the 2026-10-08 lines it handed up 31% of
the asks, and 77.5% deciding first (integration/replay/gate_bench.md). `reason` is required: left
optional, the model sometimes wrote the flag first and decided with no reason at all (16 of 88
answers in two runs of one prompt; none of 540 once required: respond_bench.md). A wrong hand-off
costs about a second and a fraction of a cent, a missed ask the player's ask.

Three checks in code, after the call, none sent back (one call must be fast):
- the guard (promises()): a reply with no hand-off that offers or promises something in the game is
  handed off, since nothing would come of it otherwise (respond_bench.md, _The guard_). A promise is
  kept whole as the hand-off's acknowledgement ("Sure, I'll make it rain!" is one, once the work is
  handed off), unless it says the thing is done; an offer is dropped, since a question that
  waits for a yes is wrong beside work already started. A status of work under way passes for a
  player who has some (`busy`, or an open task);
- the checks beside a hand-off: a reply that says the thing is done, ready, gone or there
  (claims_done()) is dropped, since nothing has happened yet, and so is one that offers (promises()),
  since the work is already handed off ("I can make it bigger" went out once on the night's bench);
  "On it", "Coming up" and "Still on it" go out;
- Jev unnamed (unnamed()): a sentence that names Jev or the filter is dropped, a report's reply's too;
- the cut: a reply past REPLY_MAX characters is cut at a word to REPLY_CUT, as one chat line.

Its prompt is laid out as the brain's: the opening (its rules, the owner, the menu's lines, marked),
the conversation in the same minute-blocks (never a context line: context.py), then the world as
last fetched (each player's place and state, no items), TASKS one line a task, WORK UNDER WAY (the
core's text), what is new, and the line or the report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .actions import Catalog
from .call import all_required, menu_lines, rules_text
from .config import log
from .context import Context, Row, show
from .world import Seen

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "respond.md"
# A reply's length: the prompt asks for under 80 characters and never over 200; one a little over goes
# out as written, and only one past REPLY_MAX, no longer one chat line, is cut, at a word, to REPLY_CUT.
REPLY_MAX, REPLY_CUT = 300, 200
UNDER_WAY_HEAD = "WORK UNDER WAY (every ask being done now, or done a moment ago; every player's):"

# What the AI does in the game, as verbs in any form ("make", "makes", "making", "makin'"): the verbs
# of Respond's promises and offers on 2026-10-08 and the reviewer's probes. Tuned for catching a
# promise over never handing off a fine reply: a missed promise tells a player something will happen
# that never does, a wrong hand-off costs a router call and the player still gets one reply.
_STEMS = ("mak", "giv", "build", "spawn", "summon", "set", "fix", "turn", "get", "put", "add", "bring", "pull",
          "teleport", "tp", "clear", "swap", "chang", "upgrad", "boost", "beef", "buff", "toss", "drop", "hook",
          "sort", "redo", "layer", "handl", "do", "craft", "stop", "remov", "kill", "whip", "start", "mov", "send",
          "grab", "enchant", "repair", "freez", "delet", "destroy", "undo", "cancel", "rebuild", "reset", "restor",
          "keep", "heal", "protect", "shrink", "doubl", "spin", "cook", "conjur", "unleash", "tun", "crank", "dial",
          "bump", "tweak", "bind", "unbind")
_DO = rf"(?:{'|'.join(_STEMS)})(?:e|es|s|ed|\w?ing|\w?in'?)?\b(?!(?<=give) you that\W*$)"  # not "I'll give you that."
_ING = rf"(?:{'|'.join(_STEMS)})\w?(?:ing|in'?)\b"
_I_WILL = r"(?:i'll|i will|i shall|i'm gonna|i am gonna|i'm going to|i am going to|imma|i'ma)"
_START = r"(?:^|[.!?,;:—–]\s*|\s-\s+)"    # the start of the reply or of a clause; never "well-done"
# What an -ing verb is done to, for it to read as the AI doing something: "spawning some cows",
# "getting you a sword", never "doing great", "getting hungry" or "making no promises".
_OBJECT = r"(?:you|your|it|that|them|those|one|some|a|an|the)\b"
# A yes to the ask, then what is being done: "Got it, making sure it explodes", "Sure, adding a moat".
_ACK = (r"(?:got it|gotcha|sure|sure thing|ok|okay|alright|all right|yep|yeah|yes|aye|you got it|no problem|"
        r"will do|understood|roger)")
# The AI's own talk, which it may promise (a rule for later is the agent's to acknowledge):
# "I'll keep quiet", "I'll stop talking", "I'll turn the chatter down", "I'll change my style".
_OWN_TALK = r"(?! (?:quiet|talking|chatting|(?:the )?chatter|my |it (?:short|brief|down|clean|quiet)))"
# "Nothing I can do", "not much I can do", "little I can do": no offer.
_NONE = r"(?<!nothing )(?<!not much )(?<!little )"
# An offer or a promise, whatever is going on: the AI proposes or says it will do something.
OFFERS = tuple(re.compile(p) for p in (
    r"\b(?<!don't )(?<!not )want me to\b", r"\bshall i\b", rf"\bshould i {_DO}", r"\bsay the word\b",
    # a question that waits for the player's yes: the reply's last sentence, not one it answers itself
    r"(?:^|[.!?,]\s+|\bor |\byou )want (it|one|them|that|this|the|a|an|some|more|another|extras?|your own)\b"
    r"[^.!?]*\?\W*$",
    rf"(?:^|[.!?,]\s+|\bor |\byou )want to {_DO} (?:it|one|them|that|some|a|an|the)\b[^.!?]*\?\W*$",
    rf"{_NONE}\bi can (?!get (?:why|how|what)\b){_DO}", rf"{_NONE}\bi can \w+ (?:it|that|them) (?:up|down|back)\b",
    rf"\bi could {_DO} (you|one|it|that|them|those|some|a|an|the|your)\b",
    rf"\bi (might|may|could) be able to {_DO}", rf"\bhappy to {_DO}",
    rf"\blet me (?!put it this way|get this straight|turn that around){_DO}",
    r"\b(has|have|got) your name on it\b(?! next)", r"\bwhenever you want it\b"))  # "…on it next": a threat
PROMISES = tuple(re.compile(p) for p in (
    rf"\b{_I_WILL} (?:(?:just|also|go|be|now|then|quickly|happily|gladly|personally|totally|definitely) )*"
    rf"(?:{_DO}|take care of|see (it|to it|that)|call out|point you|ping you|announce){_OWN_TALK}",
    rf"{_START}gonna {_DO}", rf"\b(i'm|i am|we're|we are) (?:(?:just|now|already|busy) )?{_ING} {_OBJECT}",
    rf"{_START}{_ING} {_OBJECT}[^.!?]*\bnow\b(?! on)",
    rf"(?:{_START}|\b(?:i'm|i am|we're|yep|yes|yeah|sure|sure thing|ok|okay|aye|got it|you got it|no problem|"
    rf"alright|right) +)(?:already |right )?on it\b",
    rf"{_START}(?:will do|consider it done|leave it to me)\b", rf"{_START}(?:all )?done(?=\s*(?:[.!,]|$))",
    r"(?<!not )\bright away\b(?!\s*\?)",
    r"\b(i'll|i will|i've got|i got) (watch )?your back\b", r"\bgive me a (sec|second|minute|moment)\b",
    rf"{_START}{_ACK}\W+{_ING} (?:sure\b|{_OBJECT})",
    # work to come, promised for later: "we can look into smaller models", "I'll dig into it"
    r"\b(?:i|we)(?:'ll| will| can| could|'re going to| are going to|'re gonna| are gonna)(?: \w+)? "
    r"(?:look into|dig into)\b"))
# Work under way: a status when the line's speaker has work under way, a promise when not.
UNDER_WAY = tuple(re.compile(p) for p in (
    r"(?<!night's )(?<!day's )(?<!dawn's )(?<!morning's )(?<!sun's )\bcoming (right )?(up|at (ya|you))\b",
    r"\bgive (it|her|him|them) a (sec|second|minute|moment|beat)\b", r"\bhang tight\b", r"\bone sec\b",
    r"\bon (its|their) way\b", r"(?<!you)(?<!you )(?<!we)(?<!we )('s|is|are|'re) on the way\b(?! (back|home|out|there))", r"\bit's coming\b",
    r"\b(should|will|'ll) be (done|ready) (in|soon)\b", r"\bstill on it\b"))
# An open task in the tasks text, as tasks.py writes it: `- castle_steve for Steve, open: …`, or `…, an
# implementer is on it: …`; the optional `since HH:MM:SS, ` is the form before named tasks.
OPEN_TASK = re.compile(r"^- \S+ for (\S+), (since \d\d:\d\d:\d\d, )?(open|an implementer is on it): ", re.M)

# A reply that says the thing is done, ready, gone or there: never true beside a hand-off, whose work
# has not even been placed yet. Read a clause at a time: a clause that hedges ("not done yet", "ready
# soon", "when it's done", "still building it", "fixed up next") or asks ("all set?") claims nothing.
# "gone" and "is unbound" came from the night's bench: 4 of 125 acknowledgements claimed so, and passed.
DONE = tuple(re.compile(p) for p in (
    r"(?<![\w-])(?:all )?done\b", r"\ball set\b", r"\b(?:there|here) (?:you go|ya go|it is)\b", r"\bfixed\b",
    r"(?<!get )(?<!getting )\bready\b", r"\bgood to go\b", r"\ball yours\b", r"\bgone\b",
    r"\b(?:is|are|'s|now) (?:unbound|removed|stopped|off(?! to\b)|cleared|gone)\b",  # never "Alex's off to the nether"
    r"^(?:teleported|spawned|summoned|healed|sorted|unbound|removed|stopped|cleared|killed)\b",
    r"\b(?:i|i've|i have|we've|just) (?:\w+ )?(?:gave|made|built|spawned|summoned|teleported|added|cleared|sent|"
    r"moved|stopped|removed|changed|healed|turned)\b"))
HEDGE = re.compile(r"\b(?:not|never|yet|soon|when|once|until|till|almost|nearly|will|should|gonna|going to|"
                   r"about to|in a|still|being|getting|next)\b|n't\b|'ll\b")
# A reply that says it is looking ("checking where Alex is", "looking — it's off, checking…") reports
# what it sees while it looks: no clause of it claims the work done.
LOOKING = re.compile(r"\b(?:checking|looking(?! good| great| fine| sharp)|seeing|finding)\b")
CLAUSE = re.compile(r"(?<=[.!?;:])\s+|,\s+|\s+[-—–]+\s+|[—–]")


def plain(text: str) -> str:
    """The reply as the checks read it: lower case, straight apostrophes, one space."""
    return " ".join(text.lower().replace("\u2019", "'").split())


def promises(reply: str, tasks: str = "", who: str = "", *, offers: bool = True,
             busy: Collection[str] = ()) -> str:
    """"an offer" or "a promise" when the reply has one, else "". The conversation agent cannot act
    on the game, so a reply of its own that offers or promises something there is never said: the
    line is handed off instead (Prototype_Jev_Routing, Respond's point 1; Prototype_Conversation_And_
    Router). The prompt says so, and still about one reply in 20 on 2026-10-08 did (integration/
    replay/respond_bench.md, "The guard"). Saying that work under way is coming is a status for a
    player who has work under way, and a promise for one who has none: work under way is a task
    open for the line's speaker (`who`) in the tasks text (`tasks`), or the speaker among `busy`, the
    players the core says have a brain loop thinking or an implementer running. A rule acknowledged
    with an action verb ("I'll turn the chatter down") is handed off too: the brain then
    acknowledges it, a second later. With `offers` False, a promise alone: the brain's check of a
    reply that starts nothing (call.step_type), for which an offer is a question it may ask."""
    text = plain(reply)
    if offers and any(p.search(text) for p in OFFERS):
        return "an offer"
    if any(p.search(text) for p in PROMISES):
        return "a promise"
    working = {m.group(1) for m in OPEN_TASK.finditer(tasks)} | set(busy)
    if who not in working and any(p.search(text) for p in UNDER_WAY):
        return "a promise"
    return ""


def claims_done(reply: str) -> bool:
    """Whether the reply says the thing is done, ready, fixed or there ("Done!", "All set", "There
    you go", "Fixed it", "It's ready", "Teleported you!"). Beside a hand-off that is never true: the
    work has not even been placed. A clause that hedges or asks claims nothing, so "Not done yet,
    still on it", "It'll be ready soon" and "All set?" pass, and so do "On it!" and "Coming up!". A
    reply that is looking (LOOKING) claims nothing at all."""
    if LOOKING.search(plain(reply)):
        return False
    for clause in CLAUSE.split(plain(reply)):
        clause = clause.strip()
        if not clause or clause.endswith("?") or HEDGE.search(clause):
            continue
        if any(p.search(clause) for p in DONE):
            return True
    return False


# Jev, the filter in front of the AI, is never named to a player: the prompt says so, and still 0 to 2
# replies a bench round did ("Ask Jev for the receipts."). Never "jeval", never "filtered water".
NAMES_JEV = re.compile(r"\bjev\b|\bthe filters?\b", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def unnamed(reply: str, ref: str) -> str:
    """The reply without any sentence that names Jev or the filter, each dropped logged; "" when
    nothing is left."""
    kept = []
    for sentence in SENTENCE.split(reply.strip()):
        if NAMES_JEV.search(sentence):
            log("respond", f"guard {ref} names Jev, sentence dropped: {json.dumps(sentence, ensure_ascii=False)}")
        else:
            kept.append(sentence)
    return " ".join(kept).strip()


def cut(text: str, most: int = REPLY_CUT) -> str:
    """One chat line of at most `most` characters: whitespace made single, then cut at a word, with "…"."""
    text = " ".join(text.split())
    if len(text) <= most:
        return text
    head = text[:most - 1]
    if text[most - 1] != " " and " " in head[most // 2:]:  # a word cut through: back to its start
        head = head[:head.rindex(" ")]
    return head.rstrip(" ,;:-—–") + "…"


class Response(BaseModel):  # decide first: a model writes the fields in this order, `reason` always
    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict:
        """What structured() sends: every property required, its default kept (call.all_required). A
        field left out is its default, and with a reply allowed beside a hand-off an answer that left
        `hand_off` out would hand off its talk."""
        return all_required(super().model_json_schema(*args, **kwargs))

    reason: str = Field(description="first, a few words: what the line wants, now or for later; whether anything "
                                    "must be done; whether it is already under way (WORK UNDER WAY, TASKS)")
    hand_off: bool = Field(True, description="true when the line needs anything done or changed in the game, "
                                             "answers the AI's question, sets a rule the game must keep, asks what "
                                             "only a look at the game can tell, or you are unsure; false for talk, a "
                                             "rule or a preference about the future only, or a question about work "
                                             "under way")
    reply: str = Field("", description="one chat line, said at once: the answer; with a hand-off a short "
                                       "acknowledgement (\"On it!\", \"Still on it.\") that never says it is done; "
                                       "empty to say nothing")


class ReportAnswer(BaseModel):  # decide first, as Response
    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict:
        """What structured() sends: every property required, its default kept, as Response's."""
        return all_required(super().model_json_schema(*args, **kwargs))

    reason: str = Field(description="first: what the report says landed, and what is left that the player asked for")
    hand_off: bool = Field(False, description="true when something the player asked for is left to fix: a DONE WHEN "
                                              "item the report says failed or is missing, a gap, a \"couldn't\"")
    left: str = Field("", description="with hand_off: what is left, in plain words, naming the DONE WHEN items; "
                                      "else empty")
    status: Literal["done", "failed"] = Field(description="as the report says; done for a cancel that put things back")
    reply: str = Field(description="one chat line to the player: what they have now and how they use it; a failure "
                                   "plainly, offering one simpler thing; with a hand-off, what works now and that "
                                   "the rest is coming, never that it is all done")
    record: str = Field(description="one short line for the log, starting with the task's name and the status: "
                                    "\"<name> done: …\" or \"<name> failed: …\"")


@dataclass
class ReportEvent:
    """An implementer's report, as the conversation agent reads it: the task's row (tasks.py), the
    implementer's status, its report with the DESCRIPTION line cut off (it never reaches the
    context), and whether a follow-up to the task already runs (`following`: the task stays open,
    and the reply never says it is all done)."""
    task: dict
    status: str
    report: str
    following: bool = False

    @property
    def name(self) -> str:
        return str((self.task or {}).get("name") or "")

    def ref(self) -> str:
        """Its usage row's call_id and its log lines' ref: the task's name."""
        return (self.name or "report")[:56]

    def show(self) -> str:
        t = self.task or {}
        name = self.name or "?"
        more = (f"\nA FOLLOW-UP to {name} is already under way, so the task stays open: say how this run went, never "
                "that it is all done." if self.following else "")
        return (f"THE REPORT: the implementer of {name} ended, {self.status}.\n"
                f"{name} is for {t.get('player') or 'nobody'}, who said: {t.get('line') or ''}\n"
                f"WHAT IT IS: {t.get('description') or ''}\nITS LATEST BRIEF:\n{t.get('brief') or ''}\n"
                f"ITS REPORT:\n{self.report.strip()}{more}\n")


class Respond:
    def __init__(self, catalog: Catalog, ask, *, owner: str = "", checkpoint: int = 5, prompt: Path = PROMPT):
        self.catalog = catalog
        self.ask = ask  # structured() with the conversation agent's model, reasoning, providers and timeout
        self.checkpoint = checkpoint
        rules = rules_text(prompt)
        self.rules = rules.replace("{owner}", owner or "not named (VFG_OWNER is not set)")
        self._opening: tuple[tuple, str] | None = None

    def opening(self) -> str:
        names = self.catalog.names()
        key = tuple(names)
        if self._opening is None or self._opening[0] != key:
            text = (f"{self.rules}\n\nWHAT THE AI CAN DO (its actions, done in seconds, on what ENGINE lists; anything "
                    f"else is built new, which takes minutes):\n{menu_lines(self.catalog, names)}\n\n"
                    "THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                    "player's line Jev's class):\n")
            self._opening = (key, text)
        return self._opening[1]

    @staticmethod
    def lead_in(seen: Seen, tasks: str, under_way: str, new: list[Row]) -> str:
        """The tail before the event: the world, TASKS, WORK UNDER WAY (the core's text; given
        without its heading, the heading is put on) and what is new."""
        under_way = under_way.strip() or "WORK UNDER WAY: nothing"
        if not under_way.startswith("WORK UNDER WAY"):
            under_way = f"{UNDER_WAY_HEAD}\n{under_way}"
        return (f"\n{seen.show()}\n\n{tasks}\n\n{under_way}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n"
                f"{show(new) or 'nothing'}\n\n")

    @classmethod
    def tail(cls, row: Row, seen: Seen, tasks: str, new: list[Row], under_way: str = "") -> str:
        """A line's tail; `under_way` last, so integration/replay/respond_bench.py's calls still read."""
        how = "typed" if row.source == "chat" else "said"
        return (cls.lead_in(seen, tasks, under_way, new)
                + f"THE LINE #{row.seq}, {row.hms}: {row.who} {how}: {row.text}\n")

    async def answer(self, row: Row, ctx: Context, seen: Seen, tasks: str, under_way: str, new: list[Row], *,
                     busy: Collection[str] = ()):
        """An Answer whose value is a Response: a reply, a hand-off with or without a reply, or
        silence; raises LlmError. After the call, in code: a reply with no hand-off that offers or
        promises (promises(), `busy` the players with work under way) is handed off: a promise kept
        whole as its acknowledgement, an offer dropped; a hand-off's reply that offers, or says it
        is done (claims_done()), is dropped; a reply past REPLY_MAX is cut to REPLY_CUT. Its usage
        row is structured()'s: component respond, `#<seq>`."""
        answer = await self.ask(self.tail(row, seen, tasks, new, under_way), Response,
                                cached=ctx.cached(self.opening(), self.checkpoint),
                                component="respond", call_id=f"#{row.seq}")
        said, ref = answer.value, f"#{row.seq}"
        said.reply = unnamed(said.reply, ref)
        if not said.hand_off and (kind := promises(said.reply, tasks, row.who, busy=busy)):
            said.hand_off, said.reason = True, f"{said.reason} · handed off in code: {kind}"
            why = ("dropped: an offer" if kind == "an offer" else
                   "dropped: it says it is done" if claims_done(said.reply) else "kept as the acknowledgement")
            log("respond", f"guard {ref} {kind}, handed off, {why}: {json.dumps(said.reply, ensure_ascii=False)}")
            said.reply = said.reply if why.startswith("kept") else ""
        if said.hand_off and said.reply and (why := "an offer" if promises(said.reply) == "an offer" else
                                             "it says it is done" if claims_done(said.reply) else ""):
            log("respond", f"guard {ref} beside a hand-off, dropped: {why}: "
                           f"{json.dumps(said.reply, ensure_ascii=False)}")
            said.reply = ""
        said.reply = self.one_line(said.reply, ref)
        return answer

    async def report(self, event: ReportEvent, ctx: Context, seen: Seen, tasks: str, under_way: str,
                     new: list[Row]):
        """An Answer whose value is a ReportAnswer: the player's line, the task's record and status,
        and what is left, handed off; raises LlmError. After the call, in code: a hand-off with no
        `left` takes its reason as what is left; a `left` with no hand-off is dropped; a record not
        starting with the task's name gets it, with the status; a reply past REPLY_MAX is cut. An
        empty reply or record stays empty, for the caller to write one. No guard: a report's reply
        may offer the player one simpler thing, and a hand-off's partial "done" is true. Its usage
        row is structured()'s: component respond, the task's name."""
        ref = event.ref()
        answer = await self.ask(self.lead_in(seen, tasks, under_way, new) + event.show(), ReportAnswer,
                                cached=ctx.cached(self.opening(), self.checkpoint),
                                component="respond", call_id=ref)
        said = answer.value
        said.left = " ".join(said.left.split())
        if said.hand_off and not said.left:
            said.left = said.reason.strip()
        elif said.left and not said.hand_off:
            log("respond", f"{ref} report: left dropped, nothing is handed off: "
                           f"{json.dumps(said.left, ensure_ascii=False)}")
            said.left = ""
        record, name = " ".join(said.record.split()), event.name
        if record and name and not record.startswith(name):
            record = f"{name} {record}" if record.lower().startswith(("done", "failed")) else \
                f"{name} {said.status}: {record}"
        said.record = record
        said.reply = self.one_line(unnamed(said.reply, ref), ref)
        return answer

    @staticmethod
    def one_line(reply: str, ref: str) -> str:
        """The reply as said, on one line: past REPLY_MAX characters, cut at a word to REPLY_CUT, logged."""
        reply = " ".join(reply.split())
        if len(reply) <= REPLY_MAX:
            return reply
        log("respond", f"{ref} reply of {len(reply)} characters cut to {REPLY_CUT}")
        return cut(reply)
