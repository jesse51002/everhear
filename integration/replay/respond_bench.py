#!/usr/bin/env python3
"""The Respond bench: Respond's prompt before and after Prototype_Jev_Routing's rewrite (_Respond's
prompt_), on the lines D2 sends it from the 2026-10-08 whole asks and on a small invented set; and
the conversation agent of Prototype_Conversation_And_Router (`front`) on the invented set and on the
lines said to the AI on the night of 2026-10-12/13 (`night`). respond_score.py scores it; results in
respond_bench.md.

THE PLAYTEST INPUTS (`playtest`): every whole-ask input (whole_asks_2026-10-08.jsonl) that D2 with
Jev's new state sends to Respond, walked with trees_questions.route_d from its recorded answers
(whole_asks_bench.py's new/D2/rows.jsonl, `--d2`): 92 inputs, 44 in the first half.

THE CONTEXT is the brain's at the input's moment, built as gate_bench.py builds it (playtest.Clock:
every line to the brain or Respond a live read, the 60 s batch timer taking in the rest; the AI's
chat, tasks and results of the playtest; who is online), with D2's own outcomes as the classes:
every input D2 keeps is one LINE row, `action_oriented` (brain), `conversational` (Respond) or
`context` (shown to no model), its text the input's (a merged input's lines joined with " … "),
said at its last line's time and fed when Jev judged that line. A failed D2 call is `context`,
as the filter fails open; a dropped or blank input is not in it.

THE INVENTED SET (`invented`): INVENTED below, 20 lines none of which is from the playtest or is
one of the prompts' own examples, each with a short conversation before it and the world it was
said in, and what Respond should do with it by the spec: answer a rule or preference about the
future only, a question it can answer from knowledge and plain banter; hand up an ask for
something now, a change to something already going on, and a rule the game itself would have to
enforce. For `front` the same lines, their open task shown in today's TASKS and WORK UNDER WAY
saying nothing is under way; the old versions see the OPEN TASKS of their time.

THE NIGHT (`night`, `front` only): the 64 lines Jev sent to the AI on the night of 2026-10-12/13,
each with the conversation up to it from the night's feed, the world as unknown but who is online,
TASKS as the box's table had them then and WORK UNDER WAY as the night's own brain loops would have
shown it with no queue: night.py says how, and the hand labels. `night-to_ai` is the same with every
live line's class written `to_ai`, as after Prototype_Addressing_The_AI. `--night` is the session
folder unpacked from session-logs-2026-10-13-033024.tgz, `--brain-log` the night's brain log.

THE VERSIONS (`VERSIONS`): `old` is Respond on main at 9f32e06 (its prompt and its answer's field
descriptions); each later one a rewrite, its prompt kept in respond_prompts/ so the bench does not
move with the brain's own; v5 is what brain/prompts/respond.md was until this spec. All on DeepSeek
V4 Flash, reasoning off, parasail first (config.CHEAP, config.FAST), with the opening and tail of
Respond before this spec (Legacy), one call at a time, as the loop called it. A failed call is a
hand-up, as the loop made it; a hand-up's reply is kept in the row (`reply`), though the loop
dropped it. `front` is the conversation agent as built: brain.respond.Respond and its Response, its
prompt brain/prompts/respond.md, called through Respond.answer() with the guard, the DONE check and
the cut, on DeepSeek V4.1 Flash with reasoning off on together, coreweave, parasail (config.V41,
V41_HOSTS) and the agent's own timeout. A failed call is a hand-off with no reply, as the core
makes it; the row keeps what the model wrote (`raw_up`, `raw_reply`) beside what code made of it.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; L=integration/replay; DB=DIR/vfg/vfg.db; D2=WHOLE_OUT/new/D2/rows.jsonl
    $PY $L/respond_bench.py run old playtest --db $DB --d2 $D2 --out OUT --limit 5   # a pilot
    $PY $L/respond_bench.py run old playtest --db $DB --d2 $D2 --out OUT --half first
    $PY $L/respond_bench.py run old invented --out OUT
    NIGHT=NIGHT_DIR; LOG=aws-backup/brain-log-2026-10-13.log
    $PY $L/respond_bench.py run front night --night $NIGHT --brain-log $LOG --out OUT
    $PY $L/respond_bench.py recorded --night $NIGHT --brain-log $LOG --out OUT     # V4 Flash's night, no call
    python3 $L/respond_score.py OUT [OUT2 …]                                       # OUT/score.md

Writes OUT/<version>/<set>/rows.jsonl, one row an input: the answer as the model gave it, the
field it wrote first, ms, tokens and cost. A run into a file that has rows goes on after them;
`--dry` writes each prompt to OUT/<version>/<set>/prompts/ and calls nothing. The OpenRouter key
as structured() reads it, never printed; the usage rows queue in OUT (playtest.queue_usage).

THE GUARD (`guard`): brain.respond.promises() on every reply the runs recorded, with no call made:
what it hands up, against the promises and offers judged by hand (JUDGED), and each version's
hand-ups with it and without it.

    $PY $L/respond_bench.py guard OUT/runs OUT/repeat OUT/third --db $DB --d2 $D2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import Clock, Line, Playtest  # noqa: E402

import httpx  # noqa: E402
import vfg_llm  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from brain import actions, config, running  # noqa: E402
from brain.__main__ import asker  # noqa: E402
from brain.call import library, menu_lines  # noqa: E402
from brain.context import LIVE, Context, Row, show  # noqa: E402
from brain.respond import PROMPT as FRONT_PROMPT  # noqa: E402
from brain.respond import Respond, promises  # noqa: E402
from brain.tasks import Tasks  # noqa: E402
from brain.world import Seen  # noqa: E402
from night import UNDER_WAY_NONE  # noqa: E402
from trees_questions import route_d  # noqa: E402

INPUTS = HERE / "whole_asks_2026-10-08.jsonl"
PROMPTS = HERE / "respond_prompts"
CLASS_OF = {"brain": "action_oriented", "respond": "conversational", "context": "context"}
SETS = ("playtest", "invented", "night", "night-to_ai")
NIGHT_SETS = ("night", "night-to_ai")

# ------------------------------------------------------------------ the versions

# Each old version's answer: always {reason, hand_up, reply}, in that order, hand_up true by default;
# the field descriptions (which the model reads in the schema) differ, and from v4 `reason` is
# required, so the schema makes the model write it (REQUIRED). What each version changed:
# - v1: the spec's five points and its examples, and the group sentence;
# - v2: v1, never promise nor offer, nor say "I'll keep an eye out" (v1's replies did);
# - v3: v2, a line only a promise or an offer could answer goes up; an ask put as a question whether
#   it can be done is an ask; an answer to the AI's question is its own rule;
# - v4: v3's prompt, `reason` required (v1 and v3 sometimes skipped it);
# - v5: v4, never copy the AI's lines' promises and offers; say done, fixed or on its way only when
#   the conversation or OPEN TASKS shows it.
FIELDS = {
    "old": {"reason": "first, a few words: what any part of the line wants, if anything, and so whether it is pure "
                      "talk",
            "hand_up": "true unless the line is clearly pure talk: the brain answers it",
            "reply": "your one chat line when you do not hand up, or empty to say nothing; always empty when you "
                     "hand up"},
    "v1": {"reason": "first, a few words: what any part of the line wants, if anything, now or for later, and so "
                     "whether you hand it up",
           "hand_up": "true unless the line is clearly talk, or a rule or a preference about the future only: the "
                      "brain answers it",
           "reply": "your one chat line when you do not hand up, never a promise of anything done in the game, or "
                    "empty to say nothing; always empty when you hand up"},
    "v2": {"reason": "first, a few words: what any part of the line wants, if anything, now or for later, and so "
                     "whether you hand it up",
           "hand_up": "true unless the line is clearly talk, or a rule or a preference about the future only: the "
                      "brain answers it",
           "reply": "your one chat line when you do not hand up, never a promise or an offer, or empty to say "
                    "nothing; always empty when you hand up"},
}
FIELDS["v3"] = FIELDS["v4"] = FIELDS["v5"] = FIELDS["v2"]
PROMPT_OF = {"v4": "v3"}   # v4 is v3's prompt with `reason` required
REQUIRED = {"v4", "v5"}
VERSIONS = {name: (PROMPTS / f"{PROMPT_OF.get(name, name)}.md", fields) for name, fields in FIELDS.items()}
# The conversation agent of Prototype_Conversation_And_Router, as built: its own prompt, answer and checks.
FRONT = "front"
VERSIONS[FRONT] = (FRONT_PROMPT, None)


def model_of(version: str, cfg) -> tuple[str, str, tuple[str, ...], float]:
    """(model, reasoning, providers, timeout) a version runs on: the old ones on V4 Flash as they were
    benched, `front` on V4.1 Flash with no reasoning, on Together first, with the agent's timeout."""
    if version == FRONT:
        return config.V41, "off", config.V41_HOSTS, cfg.respond_timeout_s
    return config.CHEAP, "off", config.FAST, 20.0


def response(fields: dict[str, str], required: bool = False) -> type[BaseModel]:
    class Response(BaseModel):  # Respond's name, so the schema differs from production's only in its words
        reason: str = Field(... if required else "", description=fields["reason"])
        hand_up: bool = Field(True, description=fields["hand_up"])
        reply: str = Field("", description=fields["reply"])
    return Response


class Legacy:
    """Respond's opening and tail before this spec (main at 94e517e, the same as when v1 to v5 were
    benched): what the old versions read, so they do not move with the conversation agent's."""

    def __init__(self, catalog, ask, *, owner: str = "", checkpoint: int = 5, prompt: Path = PROMPTS / "v5.md"):
        self.catalog, self.ask, self.checkpoint = catalog, ask, checkpoint
        rules = prompt.read_text(encoding="utf-8").rstrip("\n")
        self.rules = rules.replace("{owner}", owner or "not named (VFG_OWNER is not set)")

    def opening(self) -> str:
        names = library(self.catalog)
        return (f"{self.rules}\n\nWHAT THE BRAIN CAN DO (its actions; anything else is made new by a "
                f"coding implementer):\n{menu_lines(self.catalog, names)}\n\n"
                "THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                "player's line Jev's class):\n")

    @staticmethod
    def tail(row: Row, seen: Seen, tasks: str, new: list[Row]) -> str:
        how = "typed" if row.source == "chat" else "said"
        return (f"\n{seen.show()}\n\n{tasks}\n\n"
                f"NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n"
                f"{show(new) or 'nothing'}\n\n"
                f"THE LINE #{row.seq}, {row.hms}: {row.who} {how}: {row.text}\n")


# The model's own text, to read the field it wrote first and, for `front`, what it answered before
# the code's checks: structured() parses it away.
_held: list[str] | None = None
_hosts: list[str] = []   # the provider that answered each attempt, as OpenRouter names it
_content = vfg_llm._content


def _content_kept(got: dict) -> str:
    text = _content(got)
    if _held is not None:
        _held.append(text)
        _hosts.append(str(got.get("provider") or ""))
    return text


vfg_llm._content = _content_kept


def first_field(texts: list[str]) -> str:
    try:
        return next(iter(json.loads(texts[-1])), "")
    except (ValueError, TypeError, IndexError, StopIteration):
        return ""


def raw_answer(texts: list[str]) -> dict:
    """What the model wrote in its last attempt, as JSON, or {}."""
    try:
        got = json.loads(texts[-1])
        return got if isinstance(got, dict) else {}
    except (ValueError, TypeError, IndexError):
        return {}


# ------------------------------------------------------------------ the items


@dataclass
class Item:
    """One call: its row's fixed fields, and what Respond is given: the line, the context it is the
    end of, the world, the tasks text, WORK UNDER WAY, what is new and the players with work under way."""
    fixed: dict
    row: Row
    ctx: Context
    seen: Seen
    tasks: str
    new: list[Row]
    under_way: str = ""
    busy: set[str] = field(default_factory=set)


# ------------------------------------------------------------------ the invented set

# Each line: what it tests (`point`), what Respond should do (`want`), its speaker and words, the
# conversation before it (who, the line's class or the AI row's kind, text, an AI row's task; nine
# seconds apart), and the world it was said in: the time and weather, a running copy, an effect, an
# open task. The owner is Sam.
INVENTED_OWNER = "Sam"
INVENTED_AT = "2026-10-11T18:00:00.000Z"
PLAYERS = {"Sam": "112 70 -35", "Riley": "98 64 -12", "Jordan": "140 31 -60"}

INVENTED = [
    # A rule or a preference about the future only, kept by how the AI behaves: answer.
    {"id": "i01", "point": "future rule", "want": "answer", "who": "Riley",
     "text": "AI can you call me Captain Riley from now on",
     "before": [("Riley", "conversational", "ok I'm back, what did I miss"),
                ("AI", "say", "welcome back Riley, Jordan found diamonds")]},
    {"id": "i02", "point": "future rule", "want": "answer", "who": "Sam",
     "text": "don't make any more weapons for Jordan today, he's had enough",
     "before": [("Jordan", "action_oriented", "AI give me another railgun"),
                ("AI", "action", "railgun: give Jordan a railgun — ok: given to Jordan", "T3"),
                ("AI", "say", "railgun number three, Jordan"),
                ("Sam", "conversational", "bro you have like nine guns")]},
    {"id": "i03", "point": "future rule", "want": "answer", "who": "Jordan",
     "text": "keep your messages shorter from now on, they cover half my screen",
     "before": [("AI", "say", "Fun fact: ghasts are actually crying, and their fireballs can be sent back with any "
                              "hit, even a fist, if your timing is right"),
                ("Jordan", "conversational", "dude")]},
    {"id": "i04", "point": "future rule", "want": "answer", "who": "Sam",
     "text": "and don't build anything near spawn unless I say it's ok",
     "before": [("Riley", "action_oriented", "AI build me a tower right here"),
                ("AI", "say", "tower going up, Riley"),
                ("Sam", "conversational", "wait is that at spawn")]},
    {"id": "i05", "point": "future rule", "want": "answer", "who": "Jordan",
     "text": "next time I ask for a sword make it netherite, diamond is boring",
     "before": [("Jordan", "action_oriented", "can I have a sword"),
                ("AI", "action", "command: give Jordan a diamond sword — ok: Gave 1 [Diamond Sword] to Jordan", "T2"),
                ("AI", "say", "diamond sword, all yours")]},
    # A question it can answer from what it knows: answer.
    {"id": "i06", "point": "knowledge", "want": "answer", "who": "Jordan",
     "text": "how do you make a smithing table again",
     "before": [("Riley", "conversational", "I need to upgrade my pick"),
                ("Jordan", "conversational", "same")]},
    {"id": "i07", "point": "knowledge", "want": "answer", "who": "Riley",
     "text": "AI do villagers need beds to breed",
     "before": [("Riley", "conversational", "I'm making a villager farm"),
                ("Sam", "conversational", "good luck with that lol")]},
    {"id": "i08", "point": "knowledge", "want": "answer", "who": "Sam",
     "text": "what's better on an axe, sharpness or smite",
     "before": [("Sam", "conversational", "finally got an enchanting table"),
                ("AI", "say", "nice, bookshelves next")]},
    # Plain banter: answer.
    {"id": "i09", "point": "banter", "want": "answer", "who": "Jordan",
     "text": "AI you're honestly the best teammate on this server",
     "before": [("AI", "action", "summon_around: 20 zombies round each player — ok: summoned 60", "T5"),
                ("Jordan", "conversational", "that horde was insane"),
                ("Riley", "conversational", "I died twice")]},
    {"id": "i10", "point": "banter", "want": "answer", "who": "Riley",
     "text": "good morning AI, did you sleep well",
     "before": [("Sam", "conversational", "morning everyone")]},
    # An ask for something to happen now: hand up.
    {"id": "i11", "point": "now", "want": "up", "who": "Jordan",
     "text": "could I get like a stack of torches, it's so dark down here",
     "before": [("Jordan", "conversational", "this cave goes forever"),
                ("Riley", "conversational", "watch out for skeletons")]},
    {"id": "i12", "point": "now", "want": "up", "who": "Riley",
     "text": "ugh I'm starving and I've got nothing to eat",
     "before": [("Riley", "conversational", "how far is the village"),
                ("Sam", "conversational", "like five minutes north")]},
    {"id": "i13", "point": "now", "want": "up", "who": "Sam",
     "text": "can you skip to morning, there are way too many mobs out",
     "before": [("Sam", "conversational", "it's night already?"),
                ("Riley", "conversational", "zombies everywhere")],
     "clock": "TIME: night (15000, day 2). WEATHER: clear."},
    # A change to something already going on in the world: hand up.
    {"id": "i14", "point": "change", "want": "up", "who": "Riley",
     "text": "the ice thing is wrecking my farm, all my crops are ice now",
     "before": [("Riley", "action_oriented", "AI turn everything I walk on into ice"),
                ("AI", "action", "ice_walk: ice under Riley as she walks — ok: running as ice_walk_1", "T4"),
                ("AI", "say", "frozen feet, Riley")],
     "running": {"ice_walk_1": {"action": "ice_walk", "values": {"player": "Riley"}, "task": "T4"}}},
    {"id": "i15", "point": "change", "want": "up", "who": "Riley",
     "text": "this speed is way too much, I keep running off cliffs",
     "before": [("Riley", "action_oriented", "give me speed five forever"),
                ("AI", "action", "keep_effects: speed V on Riley until stopped — ok: running as keep_effects_1",
                 "T6"),
                ("AI", "say", "speed five, go go go")],
     "running": {"keep_effects_1": {"action": "keep_effects", "values": {"player": "Riley"}, "task": "T6"}},
     "effects": {"Riley": "speed V ∞"}},
    {"id": "i16", "point": "change", "want": "up", "who": "Sam",
     "text": "oh and for the castle, put a moat around it",
     "before": [("Sam", "action_oriented", "AI build me a big stone castle at my base"),
                ("AI", "task", "T7: build a big stone castle at Sam's base, for Sam", "T7"),
                ("AI", "say", "on it, castle coming up at your base")],
     "tasks": [("T7", "Sam", "build a big stone castle at Sam's base, for Sam")]},
    {"id": "i17", "point": "change", "want": "up", "who": "Jordan",
     "text": "the iron golem you gave me … he keeps wandering off, make him stay with me",
     "before": [("Jordan", "action_oriented", "can I have an iron golem bodyguard"),
                ("AI", "action", "command: summon an iron golem by Jordan — ok: Summoned new Iron Golem", "T8"),
                ("AI", "say", "your bodyguard has arrived")]},
    # A rule the game itself would have to enforce: hand up.
    {"id": "i18", "point": "game rule", "want": "up", "who": "Sam",
     "text": "from now on nobody loses their stuff when they die",
     "before": [("Riley", "conversational", "NO I lost everything in the lava"),
                ("Jordan", "conversational", "rip")]},
    {"id": "i19", "point": "game rule", "want": "up", "who": "Jordan",
     "text": "no more phantoms for the rest of the game please",
     "before": [("Jordan", "conversational", "these phantoms keep diving at me"),
                ("Sam", "conversational", "just sleep lol")]},
    {"id": "i20", "point": "game rule", "want": "up", "who": "Riley",
     "text": "every time someone gets blown up by a creeper, hit them with lightning lol",
     "before": [("Jordan", "conversational", "a creeper just got me again"),
                ("Riley", "conversational", "that's so funny")]},
]


def at_plus(base: str, seconds: float) -> str:
    t = datetime.fromisoformat(base.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def invented_tasks(k: int, it: dict, legacy: bool) -> tuple[str, set[str]]:
    """The line's open task, an implementer on it, as the old versions saw it (OPEN TASKS, the form
    before named tasks) or as the conversation agent does (Tasks.show); and its players."""
    at = at_plus(INVENTED_AT, 300 * k + 9)
    tasks = it.get("tasks", [])
    if legacy:
        rows = [f"- {tid} for {player}, since {at[11:19]}, an implementer is on it: {brief}"
                for tid, player, brief in tasks]
        return "OPEN TASKS:\n" + ("\n".join(rows) or "none"), {p for _, p, _ in tasks}
    shown = Tasks([{"name": tid, "description": brief, "player": player, "status": "open", "created_at": at,
                    "updated_at": at} for tid, player, brief in tasks], clock=lambda: at)
    return shown.show([tid for tid, _, _ in tasks]), {p for _, p, _ in tasks}


def invented_items(catalog, limit: int = 0, legacy: bool = True):
    for k, it in enumerate(INVENTED[:limit or None]):
        base = at_plus(INVENTED_AT, 300 * k)   # each line its own few minutes
        ctx = Context()
        said = it["before"] + [(it["who"], "conversational", it["text"])]
        for n, (who, kind, text, *task) in enumerate(said, 1):
            at = at_plus(base, 9 * n)
            if who == "AI":
                ctx.add(Row(n, at, "AI", text, kind=kind, task=task[0] if task else "", processed=True))
            else:
                ctx.add(Row(n, at, "LINE", text, who=who, source="voice", cls=kind, processed=n < len(said)))
        row = ctx.rows[-1]
        effects = it.get("effects", {})
        players = "\n".join(f"- {p} at {pos} in overworld: 20/20 health, 20/20 food, armor 9, level 14, survival; "
                            + (f"effects: {effects[p]}" if p in effects else "no effects")
                            for p, pos in PLAYERS.items())
        seen = Seen(players=f"PLAYERS ONLINE:\n{players}",
                    clock=it.get("clock", "TIME: day (6000, day 2). WEATHER: clear."),
                    running=running.block(it.get("running", {}), catalog), places="", incantations="INCANTATIONS: none")
        tasks, busy = invented_tasks(k, it, legacy)
        fixed = {"id": it["id"], "set": "invented", "point": it["point"], "want": it["want"], "who": it["who"],
                 "text": it["text"], "context_rows": len(ctx.rows)}
        yield Item(fixed, row, ctx, seen, tasks, [], UNDER_WAY_NONE, busy)


# ------------------------------------------------------------------ the playtest inputs


def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def d2_outcomes(path: Path) -> dict[str, dict]:
    """{input id: {out, path}} from D2's recorded answers; a failed call is context, a blank input dropped."""
    got = {}
    for r in read_rows(path):
        if r.get("blank"):
            got[r["id"]] = {"out": "dropped", "path": "blank"}
        elif r.get("error") or not r.get("answers"):
            got[r["id"]] = {"out": "context", "path": "failed"}
        else:
            got[r["id"]] = route_d(r["answers"], "D2")
    return got


def playtest_items(pt: Playtest, inputs: list[dict], d2: dict[str, dict], want: set[str]):
    """For each input of `want`, in time order, the call Respond would get at its moment (the docstring).
    The context is the Clock's own, as it is at the item's moment: use each item before the next."""
    entries = pt.entries({})   # every GAME, SYS and AI row; no line, the inputs' rows go in below
    by_entry = {}
    for inp in inputs:
        out = d2.get(inp["id"], {"out": "context"})["out"]
        if out not in CLASS_OF:
            continue
        members = [pt.by_id[i] for i in inp["lines"]]
        last = members[-1]
        peaks = [m.peak for m in members if m.peak is not None]
        ln = Line(inp["id"], 0, last.at, last.who, inp["text"], CLASS_OF[out], None, inp["judged"], inp["judged"],
                  max(peaks) if peaks else None, last.ended)
        e = pt._line_entry(ln, CLASS_OF[out], inp["judged"], None)
        entries.append(e)
        by_entry[inp["id"]] = inp
    entries.sort(key=lambda e: (e.seen, e.tie))
    for i, e in enumerate(entries, 1):
        e.row.seq = i
    clock = Clock(entries, 60.0)
    for e in entries:
        if e.row.type != "LINE" or e.row.cls not in LIVE:
            continue
        clock.until(e.seen)
        if e.line_id in want:
            inp = by_entry[e.line_id]
            new = [r for r in clock.ctx.unprocessed() if r.seq != e.row.seq]
            fixed = {"id": inp["id"], "set": "playtest", "lines": inp["lines"], "at": inp["at"], "who": inp["who"],
                     "label": inp["label"], "half": inp["half"], "merged": inp["merged"],
                     "d2": d2[inp["id"]]["path"], "text": inp["text"], "new": len(new),
                     "context_rows": len(clock.ctx.rows)}
            yield Item(fixed, e.row, clock.ctx, pt.seen(e.seen, inp["who"], items=False), pt.open_tasks(e.seen),
                       new)
        clock.read(e.seen)


# ------------------------------------------------------------------ the night


def night_items(args, recorded: dict[int, dict]):
    """The night's 64 lines (night.py), in feed order, each Item with its hand label and whether V4
    Flash answered it that night."""
    from night import HAND, Night
    nt = Night(Path(args.night), Path(args.brain_log))
    classes = "to_ai" if args.set == "night-to_ai" else "night"
    for c in nt.calls(classes):
        fixed = {"id": f"#{c.n}", "set": args.set, "n": c.n, "label": c.label, "hand": HAND[c.label],
                 "who": c.row.who, "text": c.row.text, "typed": c.row.source == "chat", "at": c.at,
                 "night_class": next((r.cls for r in nt_rows(nt, c.n)), ""), "v4": c.n in recorded,
                 "new": len(c.new), "under_way": c.loops, "busy": sorted(c.busy), "context_rows": len(c.ctx.rows)}
        yield Item(fixed, c.row, c.ctx, c.seen, c.tasks, c.new, c.under_way, c.busy)


def nt_rows(nt, n: int):
    """The line's own feed row, as the night had it (its class before any rewrite)."""
    from brain.context import parse
    return [parse(line, seq=seq, at=at) for seq, at, line in nt.feed if seq == n]


def recorded_night(args) -> None:
    """V4 Flash's own answers on the night (night.py), written as a version's rows, OUT/v4-night/night/
    rows.jsonl, beside the runs': no call is made. Its replies are said as they were (the guard, which
    never fired that night, is in them); a line it never had (Jev classed it action_oriented, or #177,
    dropped at the stop) has no row."""
    from night import HAND, LABEL_OF, Night
    nt = Night(Path(args.night), Path(args.brain_log))
    rec = nt.recorded()
    out = Path(args.out) / "v4-night" / "night"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in sorted(rec):
        r = rec[n]
        line = nt_rows(nt, n)[0]
        rows.append({"id": f"#{n}", "set": "night", "n": n, "label": LABEL_OF[n], "hand": HAND[LABEL_OF[n]],
                     "who": line.who, "text": line.text, "typed": line.source == "chat",
                     "night_class": line.cls, "v4": True, "up": r["up"], "raw_up": r["up"] and not r["guard"],
                     "reason": r["reason"], "reply": r["reply"], "raw_reply": r["reply"],
                     "guard": "fired" if r["guard"] else "", "done_drop": False, "cut": False,
                     "ms": r["ms"], "cost": r["cost"], "prompt_tokens": r["prompt_tokens"],
                     "cached_tokens": r["cached_tokens"], "completion_tokens": r["completion_tokens"],
                     "first": "reason", "recorded": r["at"]})
    (out / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                    encoding="utf-8")
    (out / "meta-all.json").write_text(json.dumps({
        "version": "v4-night", "set": "night", "what": "Respond's answers on the night, from its brain log",
        "model": "~deepseek/deepseek-v4-flash-latest", "reasoning": "off", "brain_log": str(args.brain_log),
        "calls": len(rows)}, indent=1))
    print(f"v4-night: {len(rows)} recorded answers, {sum(r['up'] for r in rows)} handed up, "
          f"{sum(bool(r['guard']) for r in rows)} by the guard, to {out / 'rows.jsonl'}")


# ------------------------------------------------------------------ the run


async def ask_front(respond: Respond, it: Item) -> dict:
    """One call of the conversation agent, as the core makes it: Respond.answer() with its checks,
    the model's own answer kept beside what code made of it, and the log lines the checks wrote."""
    global _held
    _held = []
    _hosts.clear()
    logged = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(logged):
            answer = await respond.answer(it.row, it.ctx, it.seen, it.tasks, it.under_way, it.new, busy=it.busy)
    except Exception as err:  # noqa: BLE001 — an LlmError in production: a hand-off with no reply
        row = {"up": True, "reply": "", "error": f"{type(err).__name__}: {err}"[:300],
               "ms": int((time.perf_counter() - t0) * 1000), "cost": float(getattr(err, "cost", 0.0) or 0.0)}
    else:
        v, raw = answer.value, raw_answer(_held)
        # respond's log lines (`<time> respond guard #58 …`), the area and time cut off
        logs = [x.split(" ", 2)[2] for x in logged.getvalue().splitlines() if x.count(" ") >= 2]
        # the guard handing a reply off (`guard #58 a promise, handed off, …`), and the checks beside a
        # hand-off (`guard #58 beside a hand-off, dropped: an offer|it says it is done: …`)
        mine = [x for x in logs if x.startswith(f"guard #{it.row.seq} ")]
        fired = [x for x in mine if "beside a hand-off" not in x]
        guard = ("" if not fired else "an offer" if "an offer" in fired[0] else
                 "a promise" if "a promise" in fired[0] else "fired")
        raw_up = bool(raw.get("hand_off", True))
        raw_reply = str(raw.get("reply", "") or "").strip()
        row = {"up": bool(v.hand_off), "reason": v.reason, "reply": v.reply, "raw_up": raw_up, "raw_reply": raw_reply,
               "guard": guard or ("fired" if not raw_up and v.hand_off else ""),
               "done_drop": any("says it is done" in x for x in mine),
               "offer_drop": any("beside a hand-off" in x and "an offer" in x for x in mine),
               "cut": any("cut to" in x for x in logs), "logs": logs,
               "ms": answer.ms, "cost": answer.cost, "prompt_tokens": answer.prompt_tokens,
               "cached_tokens": answer.cached_tokens, "completion_tokens": answer.completion_tokens,
               "first": first_field(_held), "attempts": len(_held), "host": _hosts[-1] if _hosts else ""}
    _held = None
    return row


async def ask_old(respond: Legacy, it: Item, output, version: str) -> dict:
    global _held
    _held = []
    t0 = time.perf_counter()
    tail = respond.tail(it.row, it.seen, it.tasks, it.new)
    try:
        answer = await respond.ask(tail, output, cached=it.ctx.cached(respond.opening(), respond.checkpoint),
                                   component="respond", call_id=f"replay respond {version} {it.fixed['id']}"[:64])
    except Exception as err:  # noqa: BLE001 — an LlmError in production; any failure is a hand-up
        row = {"up": True, "error": f"{type(err).__name__}: {err}"[:300],
               "ms": int((time.perf_counter() - t0) * 1000), "cost": 0.0}
    else:
        v = answer.value
        row = {"up": bool(v.hand_up), "reason": v.reason, "reply": v.reply, "ms": answer.ms, "cost": answer.cost,
               "prompt_tokens": answer.prompt_tokens, "cached_tokens": answer.cached_tokens,
               "completion_tokens": answer.completion_tokens, "first": first_field(_held), "attempts": len(_held)}
    _held = None
    return row


def prompt_text(respond, it: Item) -> str:
    """The whole prompt a call sends, for --dry."""
    if isinstance(respond, Legacy):
        tail = respond.tail(it.row, it.seen, it.tasks, it.new)
    else:
        tail = respond.tail(row=it.row, seen=it.seen, tasks=it.tasks, new=it.new, under_way=it.under_way)
    return "".join(b for b, _ in it.ctx.cached(respond.opening(), respond.checkpoint)) + tail


async def run(args) -> None:
    front = args.version == FRONT
    if args.set in NIGHT_SETS and not front:
        sys.exit("night: the conversation agent's set; the old versions ran on V4 Flash before it (respond_bench.md)")
    prompt, fields = VERSIONS[args.version]
    cfg = config.load()
    model, reasoning, providers, timeout = model_of(args.version, cfg)
    timeout = args.timeout or timeout
    catalog = actions.load(cfg.repo, cfg.server)
    ask = playtest.replayed(asker(model, reasoning, timeout, providers)) if front else \
        asker(model, reasoning, timeout, providers)
    out = Path(args.out) / args.version / args.set
    out.mkdir(parents=True, exist_ok=True)
    playtest.queue_usage(out)
    output = None if front else response(fields, args.version in REQUIRED)
    if args.set == "invented":
        owner = INVENTED_OWNER
        items = invented_items(catalog, args.limit, legacy=not front)
        total = min(args.limit or len(INVENTED), len(INVENTED))
    elif args.set in NIGHT_SETS:
        if not args.night or not args.brain_log:
            sys.exit("night: --night and --brain-log are needed")
        from night import Night
        nt = Night(Path(args.night), Path(args.brain_log))
        owner, recorded = nt.owner, nt.recorded()
        items = night_items(args, recorded)
        total = 64
        if args.limit:
            items = (it for k, it in enumerate(items) if k < args.limit)
            total = min(args.limit, total)
    else:
        if not args.db or not args.d2:
            sys.exit("playtest: --db and --d2 are needed")
        pt = Playtest(Path(args.db))
        inputs = read_rows(Path(args.inputs))
        d2 = d2_outcomes(Path(args.d2))
        chosen = [i for i in inputs if d2.get(i["id"], {}).get("out") == "respond"
                  and args.half in ("all", i["half"])]
        if args.limit:
            chosen = chosen[:args.limit]
        items = playtest_items(pt, inputs, d2, {i["id"] for i in chosen})
        total, owner = len(chosen), cfg.owner
    respond = Respond(catalog, ask, owner=owner, checkpoint=cfg.checkpoint, prompt=prompt) if front else \
        Legacy(catalog, ask, owner=owner, checkpoint=cfg.checkpoint, prompt=prompt)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    spent = sum(r.get("cost", 0) for r in have)
    if args.dry:
        (out / "prompts").mkdir(exist_ok=True)
        for it in items:
            (out / "prompts" / f"{it.fixed['id'].lstrip('#')}.txt").write_text(prompt_text(respond, it),
                                                                                encoding="utf-8")
        print(f"{args.version} {args.set}: {total} prompts written to {out / 'prompts'}")
        return
    (out / f"meta-{args.half}{'-limit' if args.limit else ''}.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "version": args.version, "set": args.set,
        "prompt": str(prompt), "fields": fields, "reason_required": front or args.version in REQUIRED,
        "model": model, "reasoning": reasoning, "providers": providers, "timeout": timeout, "owner": owner,
        "checkpoint": cfg.checkpoint, "repo": str(cfg.repo), "server": cfg.server, "half": args.half,
        "inputs": total, "already": len(done), "d2": args.d2, "night": args.night, "brain_log": args.brain_log,
        "sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                   for p in (Path(prompt), Path(sys.modules[Respond.__module__].__file__))}},
        indent=1, default=str))
    print(f"{args.version} {args.set}: {total} inputs ({args.half}), {len(done)} already, ${spent:.3f}; "
          f"{model}, reasoning {reasoning}, {','.join(providers)}", flush=True)
    async with httpx.AsyncClient(timeout=timeout + 10) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for it in items:
                if it.fixed["id"] in done:
                    continue
                got = await (ask_front(respond, it) if front else ask_old(respond, it, output, args.version))
                if args.stop_402 and " 402" in got.get("error", ""):
                    print(f"{it.fixed['id']}: OpenRouter 402, stopping with nothing written for it "
                          f"({got['error'][:120]})", flush=True)
                    vfg_llm.client = None
                    sys.exit(3)
                row = {**it.fixed, **got}
                spent += row["cost"]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                reply = row.get("reply", "").strip()
                what = ("ERROR " + row["error"][:60] if "error" in row else
                        ("UP" + (f" {reply[:40]!r}" if reply else "") + (f" [guard {row['guard']}]"
                                                                         if row.get("guard") else "")
                         + (" [done dropped]" if row.get("done_drop") else "")) if row["up"] else
                        f"reply {reply[:60]!r}" if reply else "silent")
                tag = row.get("label") or f"{row['point']}>{row['want']}"
                print(f"{row['id'][-6:]:>6} {tag[:14]:14} {row['ms']:5} ms ${row['cost']:.4f} | {what} | "
                      f"{row['text'][:50]!r}", flush=True)
                if spent > args.budget:
                    print(f"stopping: ${spent:.3f} spent, over --budget {args.budget}", flush=True)
                    break
        vfg_llm.client = None
    print(f"{args.version} {args.set}: ${spent:.3f} in this file")


# ------------------------------------------------------------------ the guard, on the recorded runs

LABELS = HERE / "labels_2026-10-08.jsonl"
WANTED = ("yes_and_done", "yes_not_done")
# The promises and offers judged by hand in respond_bench.md, every run: (run, version, input id's
# end, a piece of the reply that tells it apart).
JUDGED = (
    ("repeat", "old", "EEEHXQ", "see it's handled"), ("repeat", "old", "BPDCZ5", "building takes a minute"),
    ("repeat", "old", "7DZGBF", "has your name on it"), ("repeat", "old", "9W9794", "watch your back"),
    ("third", "old", "1759JG", "point you"), ("runs", "old", "9BE9TH", "want me to give"),
    ("repeat", "old", "9BE9TH", "i can add theme"), ("third", "old", "9BE9TH", "epic or goofy"),
    ("repeat", "old", "6YC3SA", "want me to clear"), ("repeat", "old", "4W6A9A", "sort it"),
    ("repeat", "old", "71TJRG", "ping you"), ("repeat", "old", "K2KVTB", "announce"),
    ("repeat", "old", "5DD0BM", "want it stopped"), ("third", "old", "6PNV2F", "stakes raised"),
    ("third", "old", "QE65VC", "toss one back"), ("third", "old", "RGPW3X", "want me to make you"),
    ("runs", "old", "i18", "off the hook"),
    ("runs", "v1", "3JDWFZ", "keep it spooky"), ("runs", "v1", "9W9794", "call out"),
    ("runs", "v1", "M82VE3", "happy to spawn"), ("runs", "v1", "BRYFSG", "want it tried"),
    ("runs", "v1", "BPDCZ5", "want extras"), ("runs", "v2", "3JDWFZ", "want me to keep"),
    ("runs", "v2", "9W9794", "redo it"), ("runs", "v3", "BRYFSG", "hook something"),
    ("repeat", "v3", "EEEHXQ", "say the word"),
    ("runs", "v4", "7DZGBF", "whenever you want it"), ("runs", "v4", "4W6A9A", "fix it"),
    ("runs", "v4", "AEVA7W", "want your own copy"), ("runs", "v4", "DJA4ZC", "say the word"),
    ("repeat", "v4", "6PNV2F", "beef"), ("repeat", "v4", "9KKTDT", "want one made"),
    ("repeat", "v4", "71TJRG", "pull you"), ("repeat", "v4", "RQSSB7", "say the word"),
    ("runs", "v5", "TFJFMK", "hook extra damage"), ("third", "v5", "7DZGBF", "name on it"),
    ("third", "v5", "9W9794", "keep alive"), ("runs", "v5", "4W6A9A", "make it happen"),
    ("runs", "v5", "8JXEP2", "pass it along"), ("runs", "v5", "E1PFWX", "blast craters"),
    ("runs", "v5", "9BE9TH", "want a theme"), ("repeat", "v5", "KEFT7T", "sort out"),
)


def open_tasks(args) -> dict[str, str]:
    """OPEN TASKS as each input's call showed it: the playtest's at the input's moment, the invented set's own."""
    out = {it["id"]: invented_tasks(k, it, legacy=True)[0] for k, it in enumerate(INVENTED)}
    pt = Playtest(Path(args.db))
    inputs = read_rows(Path(args.inputs))
    d2 = d2_outcomes(Path(args.d2))
    want = {i["id"] for i in inputs if d2.get(i["id"], {}).get("out") == "respond"}
    for item in playtest_items(pt, inputs, d2, want):
        out[item.fixed["id"]] = item.tasks
    return out


def guard(args) -> None:
    tasks = open_tasks(args)
    change = {r["id"]: (r.get("members", {}).get("change") or [""])[0] for r in read_rows(LABELS)}
    said = []   # (run, version, set, row) for every reply a player would have seen
    for out in map(Path, args.outs):
        for f in sorted(out.glob("*/*/rows.jsonl")):
            if f.parts[-3] in (FRONT, "v4-night"):
                continue   # the conversation agent's rows: respond_score.py reads its checks from them
            said += [(out.name, f.parts[-3], f.parts[-2], r) for r in read_rows(f)
                     if not r["up"] and r.get("reply", "").strip()]
    judged = {}
    for run, v, end, piece in JUDGED:
        hit = [r for (rn, vv, _, r) in said if (rn, vv) == (run, v) and r["id"].endswith(end)
               and piece in r["reply"].lower().replace("’", "'")]
        if len(hit) == 1:
            judged[(run, v, hit[0]["id"])] = piece
    fires = {(run, v, r["id"]): promises(r["reply"], tasks.get(r["id"], ""), r["who"]) for run, v, _, r in said}
    caught = [k for k in judged if fires.get(k)]
    print(f"{len(said)} replies said; {len(judged)} judged a promise or an offer by hand, of {len(JUDGED)} listed; "
          f"the guard hands up {sum(1 for k in fires if fires[k])}: {len(caught)} of the judged, "
          f"{sum(1 for k in fires if fires[k] and k not in judged)} others\n")
    print("Judged by hand, missed by the guard:")
    for run, v, _, r in said:
        if (run, v, r["id"]) in judged and not fires[(run, v, r["id"])]:
            print(f"- {run} {v} {r['id'][-6:]}: {r['reply']}")
    print("\nHanded up by the guard, not judged by hand:")
    for run, v, _, r in said:
        if fires[(run, v, r["id"])] and (run, v, r["id"]) not in judged:
            print(f"- {run} {v} {r['id'][-6:]} ({fires[(run, v, r['id'])]}): {r['reply']}")
    print("\nEach version, its runs pooled: handed up without the guard -> with it")
    print("| Version | Half | Runs | Asks | Wanted a change | All hand-ups | The guard's own | Judged promises left |")
    print("|---|---|---|---|---|---|---|---|")
    for v in sorted({v for _, v, _, _ in said}, key=lambda n: (n != "old", n)):
        for half in ("first", "second"):
            rows = [(out.name, r) for out in map(Path, args.outs)
                    for r in read_rows(out / v / "playtest" / "rows.jsonl") if r["half"] == half]
            if not rows:
                continue
            n, c = len({run for run, _ in rows}), Counter()
            for run, r in rows:
                g = bool(fires.get((run, v, r["id"])))
                ask = r["label"] in ("request", "answer_to_ai")
                wanted = any(change.get(x) in WANTED for x in r["lines"])
                for key, on in (("asks", ask), ("wanted", wanted), ("all", True)):
                    c[key] += on
                    c[key + "_up"] += on and r["up"]
                    c[key + "_guard"] += on and (r["up"] or g)
                c["guard"] += g
                c["left"] += (run, v, r["id"]) in judged and not g
            print(f"| {v} | {half} | {n} | {c['asks_up']} -> {c['asks_guard']} of {c['asks']} | {c['wanted_up']} -> "
                  f"{c['wanted_guard']} of {c['wanted']} | {c['all_up']} -> {c['all_guard']} of {c['all']} | "
                  f"{c['guard']} | {c['left']} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one version on one set")
    r.add_argument("version", choices=sorted(VERSIONS))
    r.add_argument("set", choices=SETS)
    r.add_argument("--out", required=True)
    r.add_argument("--db", help="the playtest's vfg.db (playtest)")
    r.add_argument("--d2", help="whole_asks_bench.py's new/D2/rows.jsonl: D2's recorded answers (playtest)")
    r.add_argument("--inputs", default=str(INPUTS))
    r.add_argument("--night", help="the night's session folder, unpacked from session-logs-2026-10-13-033024.tgz")
    r.add_argument("--brain-log", help="the night's brain log, brain-log-2026-10-13.log")
    r.add_argument("--half", choices=("first", "second", "all"), default="all", help="playtest: the inputs' half")
    r.add_argument("--limit", type=int, default=0, help="the first N inputs only (a pilot)")
    r.add_argument("--timeout", type=float, default=0.0,
                   help="a call's timeout, s; default the version's: 20 for the old ones, the agent's own for front")
    r.add_argument("--budget", type=float, default=0.6, help="stop past this many dollars in the file")
    r.add_argument("--dry", action="store_true", help="write the prompts, call nothing")
    r.add_argument("--stop-402", action="store_true",
                   help="on OpenRouter's 402 (no credit), write nothing for that input and exit 3, to go on later")
    n = sub.add_parser("recorded", help="V4 Flash's own answers on the night, as rows: no call")
    n.add_argument("--night", required=True)
    n.add_argument("--brain-log", required=True)
    n.add_argument("--out", required=True)
    g = sub.add_parser("guard", help="brain.respond.promises() on the recorded runs, no call made")
    g.add_argument("outs", nargs="+", help="the runs' OUTs")
    g.add_argument("--db", required=True, help="the playtest's vfg.db")
    g.add_argument("--d2", required=True, help="whole_asks_bench.py's new/D2/rows.jsonl")
    g.add_argument("--inputs", default=str(INPUTS))
    args = ap.parse_args()
    if args.cmd == "guard":
        guard(args)
    elif args.cmd == "recorded":
        recorded_night(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
