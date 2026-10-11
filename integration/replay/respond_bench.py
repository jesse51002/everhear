#!/usr/bin/env python3
"""The Respond bench: Respond's prompt before and after Prototype_Jev_Routing's rewrite (_Respond's
prompt_), on the lines D2 sends it from the 2026-10-08 whole asks and on a small invented set.
respond_score.py scores it; results in respond_bench.md.

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
enforce.

THE VERSIONS (`VERSIONS`): `old` is Respond on main at 9f32e06 (its prompt and its answer's field
descriptions); each later one a rewrite, its prompt kept in respond_prompts/ so the bench does not
move with the brain's own; v5 is brain/prompts/respond.md (but for its group sentence, made
neutral after the bench) and brain.respond.Response. All on Respond's production model and
providers (config.py: DeepSeek V4 Flash, reasoning off, parasail first), one call at a time, as
the loop calls it. A failed call is a hand-up, as the loop makes it; a hand-up's reply is kept in
the row (`reply`), though the loop drops it.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; L=integration/replay; DB=DIR/vfg/vfg.db; D2=WHOLE_OUT/new/D2/rows.jsonl
    $PY $L/respond_bench.py run old playtest --db $DB --d2 $D2 --out OUT --limit 5   # a pilot
    $PY $L/respond_bench.py run old playtest --db $DB --d2 $D2 --out OUT --half first
    $PY $L/respond_bench.py run old invented --out OUT
    python3 $L/respond_score.py OUT [OUT2 …]                                       # OUT/score.md

Writes OUT/<version>/<set>/rows.jsonl, one row an input: the answer as the model gave it, the
field it wrote first, ms, tokens and cost. A run into a file that has rows goes on after them;
`--dry` writes each prompt to OUT/<version>/<set>/prompts/ and calls nothing. The OpenRouter key
as structured() reads it, never printed.

THE GUARD (`guard`): brain.respond.promises() on every reply the runs recorded, with no call made:
what it hands up, against the promises and offers judged by hand (JUDGED), and each version's
hand-ups with it and without it.

    $PY $L/respond_bench.py guard OUT/runs OUT/repeat OUT/third --db $DB --d2 $D2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
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
from brain.context import LIVE, Context, Row  # noqa: E402
from brain.respond import Respond, promises  # noqa: E402
from brain.tasks import Tasks  # noqa: E402
from brain.world import Seen  # noqa: E402
from trees_questions import route_d  # noqa: E402

INPUTS = HERE / "whole_asks_2026-10-08.jsonl"
PROMPTS = HERE / "respond_prompts"
CLASS_OF = {"brain": "action_oriented", "respond": "conversational", "context": "context"}
SETS = ("playtest", "invented")

# ------------------------------------------------------------------ the versions

# Each version's answer: always {reason, hand_up, reply}, in that order, hand_up true by default;
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


def response(fields: dict[str, str], required: bool = False) -> type[BaseModel]:
    class Response(BaseModel):  # Respond's name, so the schema differs from production's only in its words
        reason: str = Field(... if required else "", description=fields["reason"])
        hand_up: bool = Field(True, description=fields["hand_up"])
        reply: str = Field("", description=fields["reply"])
    return Response


# The model's own text, to read the field it wrote first: structured() parses it away.
_held: list[str] | None = None
_content = vfg_llm._content


def _content_kept(got: dict) -> str:
    text = _content(got)
    if _held is not None:
        _held.append(text)
    return text


vfg_llm._content = _content_kept


def first_field(texts: list[str]) -> str:
    try:
        return next(iter(json.loads(texts[-1])), "")
    except (ValueError, TypeError, IndexError, StopIteration):
        return ""


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


@dataclass
class Item:
    """One call: its row's fixed fields, the tail, the cached blocks."""
    row: dict
    tail: str
    blocks: list


def invented_items(respond: Respond, catalog, limit: int = 0):
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
        tasks = Tasks(Path("/nonexistent/tasks.json"))
        for tid, player, brief in it.get("tasks", []):
            tasks.tasks[tid] = {"id": tid, "at": at_plus(base, 9), "player": player, "line": "", "task": brief,
                                "status": "open"}
        tail = respond.tail(row, seen, tasks.show(list(tasks.tasks)), [])
        fixed = {"id": it["id"], "set": "invented", "point": it["point"], "want": it["want"], "who": it["who"],
                 "text": it["text"], "context_rows": len(ctx.rows)}
        yield Item(fixed, tail, ctx.cached(respond.opening(), respond.checkpoint))


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


def playtest_items(pt: Playtest, inputs: list[dict], d2: dict[str, dict], respond: Respond, want: set[str]):
    """For each input of `want`, in time order, the call Respond would get at its moment (the docstring)."""
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
            tail = respond.tail(e.row, pt.seen(e.seen, inp["who"], items=False), pt.open_tasks(e.seen), new)
            fixed = {"id": inp["id"], "set": "playtest", "lines": inp["lines"], "at": inp["at"], "who": inp["who"],
                     "label": inp["label"], "half": inp["half"], "merged": inp["merged"],
                     "d2": d2[inp["id"]]["path"], "text": inp["text"], "new": len(new),
                     "context_rows": len(clock.ctx.rows)}
            yield Item(fixed, tail, clock.ctx.cached(respond.opening(), respond.checkpoint))
        clock.read(e.seen)


# ------------------------------------------------------------------ the run


async def run(args) -> None:
    global _held
    prompt, fields = VERSIONS[args.version]
    output = response(fields, args.version in REQUIRED)
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    ask = asker(cfg.respond_model, cfg.respond_reasoning, args.timeout, cfg.respond_providers)
    out = Path(args.out) / args.version / args.set
    out.mkdir(parents=True, exist_ok=True)
    playtest.queue_usage(out)
    if args.set == "invented":
        respond = Respond(catalog, ask, owner=INVENTED_OWNER, checkpoint=cfg.checkpoint, prompt=prompt)
        items = invented_items(respond, catalog, args.limit)
        total, owner = min(args.limit or len(INVENTED), len(INVENTED)), INVENTED_OWNER
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
        respond = Respond(catalog, ask, owner=cfg.owner, checkpoint=cfg.checkpoint, prompt=prompt)
        items = playtest_items(pt, inputs, d2, respond, {i["id"] for i in chosen})
        total, owner = len(chosen), cfg.owner
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    spent = sum(r.get("cost", 0) for r in have)
    if args.dry:
        (out / "prompts").mkdir(exist_ok=True)
        for it in items:
            text = "".join(b for b, _ in it.blocks) + it.tail
            (out / "prompts" / f"{it.row['id']}.txt").write_text(text, encoding="utf-8")
        print(f"{args.version} {args.set}: {total} prompts written to {out / 'prompts'}")
        return
    (out / f"meta-{args.half}{'-limit' if args.limit else ''}.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "version": args.version, "set": args.set,
        "prompt": str(prompt), "fields": fields, "reason_required": args.version in REQUIRED,
        "model": cfg.respond_model, "reasoning": cfg.respond_reasoning,
        "providers": cfg.respond_providers, "timeout": args.timeout, "owner": owner, "checkpoint": cfg.checkpoint,
        "half": args.half, "inputs": total, "already": len(done), "d2": args.d2}, indent=1, default=str))
    print(f"{args.version} {args.set}: {total} inputs ({args.half}), {len(done)} already, ${spent:.3f}", flush=True)
    async with httpx.AsyncClient(timeout=args.timeout + 10) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for it in items:
                if it.row["id"] in done:
                    continue
                row = dict(it.row)
                _held = []
                t0 = time.perf_counter()
                try:
                    answer = await respond.ask(it.tail, output, cached=it.blocks, component="respond",
                                               call_id=f"replay respond {args.version} {it.row['id']}"[:64])
                except Exception as err:  # noqa: BLE001 — an LlmError in production; any failure is a hand-up
                    row.update(up=True, error=f"{type(err).__name__}: {err}"[:300],
                               ms=int((time.perf_counter() - t0) * 1000), cost=0.0)
                else:
                    v = answer.value
                    row.update(up=bool(v.hand_up), reason=v.reason, reply=v.reply, ms=answer.ms, cost=answer.cost,
                               prompt_tokens=answer.prompt_tokens, cached_tokens=answer.cached_tokens,
                               completion_tokens=answer.completion_tokens, first=first_field(_held),
                               attempts=len(_held))
                _held = None
                spent += row["cost"]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                what = ("ERROR " + row["error"][:60] if "error" in row else
                        ("UP" + (" +reply" if row["reply"].strip() else "")) if row["up"] else
                        f"reply {row['reply'][:60]!r}")
                tag = row.get("label") or f"{row['point']}>{row['want']}"
                print(f"{row['id'][-6:]} {tag[:14]:14} {row['ms']:5} ms ${row['cost']:.4f} | {what} | "
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
    out = {}
    for k, it in enumerate(INVENTED):   # as invented_items shows them, in Tasks.show()'s own lines
        shown = Tasks(Path("/nonexistent/tasks.json"))
        for tid, player, brief in it.get("tasks", []):
            shown.tasks[tid] = {"id": tid, "at": at_plus(INVENTED_AT, 300 * k + 9), "player": player, "line": "",
                                "task": brief, "status": "open"}
        out[it["id"]] = shown.show(list(shown.tasks))
    pt = Playtest(Path(args.db))
    inputs = read_rows(Path(args.inputs))
    d2 = d2_outcomes(Path(args.d2))
    want = {i["id"] for i in inputs if d2.get(i["id"], {}).get("out") == "respond"}
    respond = Respond(actions.load(config.load().repo, config.SERVER), None)
    for item in playtest_items(pt, inputs, d2, respond, want):
        out[item.row["id"]] = "OPEN TASKS:" + item.tail.split("\nOPEN TASKS:")[1].split("\n\nNEW SINCE")[0]
    return out


def guard(args) -> None:
    tasks = open_tasks(args)
    change = {r["id"]: (r.get("members", {}).get("change") or [""])[0] for r in read_rows(LABELS)}
    said = []   # (run, version, set, row) for every reply a player would have seen
    for out in map(Path, args.outs):
        for f in sorted(out.glob("*/*/rows.jsonl")):
            said += [(out.name, f.parts[-3], f.parts[-2], r) for r in read_rows(f)
                     if not r["up"] and r.get("reply", "").strip()]
    judged = {}
    for run, v, end, piece in JUDGED:
        hit = [r for (rn, vv, _, r) in said if (rn, vv) == (run, v) and r["id"].endswith(end)
               and piece in r["reply"].lower().replace("\u2019", "'")]
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
    r.add_argument("--half", choices=("first", "second", "all"), default="all", help="playtest: the inputs' half")
    r.add_argument("--limit", type=int, default=0, help="the first N inputs only (a pilot)")
    r.add_argument("--timeout", type=float, default=20.0, help="a call's timeout, s (the brain's own)")
    r.add_argument("--budget", type=float, default=0.6, help="stop past this many dollars in the file")
    r.add_argument("--dry", action="store_true", help="write the prompts, call nothing")
    g = sub.add_parser("guard", help="brain.respond.promises() on the recorded runs, no call made")
    g.add_argument("outs", nargs="+", help="the runs' OUTs")
    g.add_argument("--db", required=True, help="the playtest's vfg.db")
    g.add_argument("--d2", required=True, help="whole_asks_bench.py's new/D2/rows.jsonl")
    g.add_argument("--inputs", default=str(INPUTS))
    args = ap.parse_args()
    if args.cmd == "guard":
        guard(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
