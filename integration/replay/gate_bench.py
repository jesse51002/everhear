#!/usr/bin/env python3
"""The gate bench: ways to decide whether a player's line needs the brain (it asks for something
to happen) or is just talk, every one run on the same lines of the 2026-10-08 playtest.

THE SET (`set`, written once to gate_set.jsonl and read by every option): every line the
conversation made a task (an ask), every line it answered in chat, every line it did nothing
with, and a seeded random sample of 150 of the lines Jev dropped then (labels: playtest.py). Each
row carries Jev's class now (classes.py's `jev` step, flips applied), so the scorer can show the
subset Jev classes `conversational`: what reaches the gate in production.

THE OPTIONS (`run X`):
- A. Respond today: brain.respond's prompt and answer, {reply, hand_up, reason}, on DeepSeek V4
  Flash with reasoning off through Respond's providers.
- B. Respond, decide first: the same model, the answer's fields in the order {reason, hand_up,
  reply} and the prompt gate_prompts/respond_decide.md, where handing up is the default.
- C. B with reasoning low, its calls 8 at a time.
- D. B on Gemini 3.8 Flash, reasoning low.
- E. The Jev council: one Jev call per line with four yes/no `choice` questions (COUNCIL),
  sharing one state built as the filter builds it (its rules, the brain's lookout, the menu, the
  history). Brain if any is yes; the scorer also reads "at least two".
- F. The council as four separate Jev calls, in parallel, the same state each.

THE CONTEXT, for A to D, is the brain's as production would have it at the line's moment, as
classes.py builds it: every kept line fed when Jev now judged it, with its class, a live read at
every action_oriented or conversational line, the batch timer taking in the rest. The line asked
about is shown as `conversational`, as it would be on reaching the gate, whatever Jev classes it
now; a line Jev now drops is put in at its moment for its own call alone. A line that is not a
live read in production reads NEW without marking it read. A failed call is a hand-up, as the
loop makes it. For E and F the state is the filter's, as classes.py's `jev` step builds it, at
the moment Jev judged the line then.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; JEV=CLASSES_OUT/jev.jsonl
    $PY integration/replay/gate_bench.py set --db $DB --jev $JEV
    $PY integration/replay/gate_bench.py run A --db $DB --jev $JEV --out OUT --limit 20   # a pilot
    $PY integration/replay/gate_bench.py run A --db $DB --jev $JEV --out OUT              # goes on
    python3 integration/replay/gate_score.py OUT

A run into OUT/X that has rows goes on after them. In OUT/X: rows.jsonl (one row per line: the
decision, what the model or Jev answered, ms, cost) and meta.json. The keys: OpenRouter's as
structured() reads it; TypeSafe's as classes.py reads it, never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import Clock, Playtest, iso, when  # noqa: E402

sys.path.insert(0, str(playtest.REPO / "jev"))
import httpx  # noqa: E402
import vfg_llm  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from brain import actions, config  # noqa: E402
from brain.__main__ import asker  # noqa: E402
from brain.context import LIVE  # noqa: E402
from brain.loop import jev_menu  # noqa: E402
from brain.respond import Respond  # noqa: E402
from classes import HISTORY_H, STATE_TOKENS, TRIES, jev_rows, typesafe_key  # noqa: E402
from jev_api import ENDPOINT, MODEL, PRICE_PER_INPUT_TOKEN, Jev, JevDown, JevRefused  # noqa: E402
from prompt import QUOTE_MAX_CHARS, cut, estimate, one_line, read_lookout, read_menu  # noqa: E402
from prompt import when as jev_when  # noqa: E402
from state_v1 import LOOKOUT, build_state  # noqa: E402  (the old state, as benched)

SET = HERE / "gate_set.jsonl"
# Both prompts as they were benchmarked, kept here so the bench does not move with the brain's own.
TODAY_PROMPT = HERE / "gate_prompts" / "respond_reply_first.md"   # brain/prompts/respond.md before 2026-10-11
DECIDE_PROMPT = HERE / "gate_prompts" / "respond_decide.md"
SEED = 20261008
DROPPED = 150
GEMINI = "google/gemini-3.8-flash"


# The model's own text, to read the order it wrote the fields in: structured() parses it away.
RAW: contextvars.ContextVar[dict | None] = contextvars.ContextVar("raw", default=None)
_content = vfg_llm._content


def _content_kept(got: dict) -> str:
    text = _content(got)
    held = RAW.get()
    if held is not None:
        held.setdefault("texts", []).append(text)
    return text


vfg_llm._content = _content_kept


def first_field(texts: list[str]) -> str:
    """The first key of the model's last answer, as it wrote it."""
    try:
        return next(iter(json.loads(texts[-1])), "")
    except (ValueError, TypeError, IndexError, StopIteration):
        return ""


def _reply_first():
    class Response(BaseModel):  # Respond's answer before 2026-10-11, the reply first
        reply: str = Field("", description="your one chat line, or empty to say nothing; always empty when you "
                                           "hand up")
        hand_up: bool = Field(False, description="true when the line, or a context line under NEW, asks for "
                                                 "something to happen in the game, or when you are unsure: the "
                                                 "brain answers it")
        reason: str = Field("", description="a few words: why you hand it up, or why not")
    return Response


def _decide_first():
    class Response(BaseModel):  # the same name as Respond's, so the schema differs only in order
        reason: str = Field("", description="first, a few words: what any part of the line wants, if anything, "
                                            "and so whether it is pure talk")
        hand_up: bool = Field(True, description="true unless the line is clearly pure talk: the brain answers it")
        reply: str = Field("", description="your one chat line when you do not hand up, or empty to say "
                                           "nothing; always empty when you hand up")
    return Response


ResponseToday = _reply_first()
ResponseDecide = _decide_first()

OPTIONS = {
    "A": {"kind": "respond", "about": "Respond today: {reply, hand_up, reason}, today's prompt",
          "prompt": "today", "model": config.CHEAP, "reasoning": "off", "providers": config.FAST,
          "parallel": 1, "timeout": 20.0},
    "B": {"kind": "respond", "about": "Respond, decide first: {reason, hand_up, reply}, hand up by default",
          "prompt": "decide", "model": config.CHEAP, "reasoning": "off", "providers": config.FAST,
          "parallel": 1, "timeout": 20.0},
    "C": {"kind": "respond", "about": "B with reasoning low", "prompt": "decide", "model": config.CHEAP,
          "reasoning": "low", "providers": config.FAST, "parallel": 8, "timeout": 60.0},
    "D": {"kind": "respond", "about": "B on Gemini 3.8 Flash, reasoning low", "prompt": "decide",
          "model": GEMINI, "reasoning": "low", "providers": (), "parallel": 1, "timeout": 20.0},
    "E": {"kind": "council", "about": "the Jev council: four yes/no questions in one call", "split": False},
    "F": {"kind": "council", "about": "the Jev council: the four questions as four parallel calls", "split": True},
}

# The council: four focused yes/no questions about the line, each with its own criteria.
COUNCIL = {
    "ask": ("Does it ask the AI for something to happen in the game?",
            "It asks the AI, however casually, indirectly or half-heard, to do, give, build, spawn, change, move, "
            "fix, stop or undo something in the game, or says it wants something the AI could make happen.",
            "It asks for nothing to happen in the game: talk, thanks, a joke, a question answered from what anyone "
            "knows, a reaction, or words meant for another player."),
    "complaint": ("Is it a complaint that something the AI made or gave is not working, too weak, too slow, in "
                  "the wrong place or gone?",
                  "It says, however casually, that something the AI made, gave, built, spawned or set up does not "
                  "work, is too weak or too slow, is in the wrong place, or has disappeared.",
                  "It is no such complaint: no complaint at all, or one about the game itself, about other players "
                  "or about something the AI did not make or give."),
    "followup": ("Does it follow up or change earlier work by the AI?",
                 "It is about something the AI made, did or is working on, or about an earlier ask, and wants it "
                 "again, more, bigger, smaller, different, moved, faster, finished, stopped or undone, or it "
                 "answers a question the AI asked about it.",
                 "It wants nothing about the AI's earlier work: new talk, another topic, or only a reaction to "
                 "what the AI did."),
    "live": ("Does answering it need the live game?",
             "A right answer needs the game as it is now: where someone or something is, what someone holds or "
             "has, someone's health, whether something the AI made exists, is done or works, the time or the "
             "weather.",
             "It can be answered from the conversation or from what anyone knows about Minecraft, or it needs no "
             "answer."),
}


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def jev_now(path: Path) -> dict[str, dict]:
    """Jev's class now, from classes.py's jev.jsonl, flips applied: {id: {class, fed, flipped}}.
    A line is fed when it was judged, or when a later call flipped it."""
    got: dict[str, dict] = {}
    for r in read_rows(path):
        if "flip" in r:
            got[r["flip"]] = {"class": r["to"], "fed": r["at"], "flipped": True}
        else:
            got[r["id"]] = {"class": r["class"], "fed": r["judged"], "flipped": False}
    return got


# ------------------------------------------------------------------ the set


def make_set(args) -> None:
    pt = Playtest(Path(args.db))
    chat = pt.answered()
    jev = jev_now(Path(args.jev))
    by_label: dict[str, list] = {}
    for ln in pt.lines:
        by_label.setdefault(pt.truth(ln, chat), []).append(ln)
    dropped = sorted(by_label.get("dropped", []), key=lambda ln: ln.id)
    sample = random.Random(args.seed).sample(dropped, min(args.dropped, len(dropped)))
    chosen = by_label.get("task", []) + by_label.get("chat", []) + by_label.get("nothing", []) + sample
    chosen.sort(key=lambda ln: (ln.at, ln.id))
    rows = [{"id": ln.id, "n": ln.n, "at": ln.at, "who": ln.who, "label": pt.truth(ln, chat),
             "jev": jev[ln.id]["class"], "fed": jev[ln.id]["fed"]} for ln in chosen]
    write(Path(args.set), rows)
    counts = {k: sum(1 for r in rows if r["label"] == k) for k in ("task", "chat", "nothing", "dropped")}
    conv = {k: sum(1 for r in rows if r["label"] == k and r["jev"] == "conversational") for k in counts}
    print(f"{len(rows)} lines to {args.set}: {counts}; Jev now conversational: {conv} (seed {args.seed}, "
          f"{len(sample)} of {len(dropped)} dropped)")


def read_set(path: Path, limit: int = 0, view: str = "all") -> list[dict]:
    rows = read_rows(path)
    if not rows:
        sys.exit(f"no set at {path}: run `gate_bench.py set` first")
    if view == "conversational":
        rows = [r for r in rows if r["jev"] == "conversational"]
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ A to D: Respond


def respond_items(pt: Playtest, bench: list[dict], jev: dict[str, dict], cfg, respond: Respond):
    """For each line of `bench`, in time order: (set row, the tail, the cached blocks, NEW's size,
    the context's size), the context the brain's at the line's moment (see the docstring)."""
    classes = {i: (v["class"], v["fed"]) for i, v in jev.items()}
    entries = pt.entries(classes)
    clock = Clock(entries, getattr(cfg, "batch_s", 60.0))
    want = {r["id"]: r for r in bench}
    events = []  # (moment, 0, index, entry) for a context row; (moment, 1, id, None) for a line not kept
    in_ctx = set()
    for i, e in enumerate(entries):
        if e.line_id in want or (e.row.type == "LINE" and e.row.cls in LIVE):
            events.append((e.seen, 0, i, e))
            in_ctx.add(e.line_id)
    for r in bench:
        if r["id"] not in in_ctx:
            events.append((r["fed"], 1, r["id"], None))
    events.sort(key=lambda x: (x[0], x[1], x[2]))
    for moment, _, key, e in events:
        clock.until(moment)
        if e is not None:
            live = e.row.cls in LIVE
            if e.line_id in want:
                row = e.row
                new = [r for r in clock.ctx.unprocessed() if r.seq != row.seq]
                cls, row.cls = row.cls, "conversational"
                item = build(pt, want[e.line_id], row, new, clock, respond, moment)
                row.cls = cls
                yield item
            if live:
                clock.read(moment)
        else:
            ln = pt.by_id[key]
            row = pt._line_entry(ln, "conversational", moment, None).row
            row.seq = clock.ctx.last_seq + 1
            new = list(clock.ctx.unprocessed())
            clock.ctx.rows.append(row)
            item = build(pt, want[key], row, new, clock, respond, moment)
            clock.ctx.rows.pop()
            yield item


def build(pt: Playtest, srow: dict, row, new, clock: Clock, respond: Respond, moment: str):
    ln = pt.by_id[srow["id"]]
    tail = respond.tail(row, pt.seen(moment, ln.who, items=False), pt.open_tasks(moment), new)
    blocks = clock.ctx.cached(respond.opening(), respond.checkpoint)
    return srow, tail, blocks, len(new), len(clock.ctx.rows)


async def run_respond(name: str, opt: dict, args) -> None:
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    playtest.queue_usage(out)
    pt = Playtest(Path(args.db))
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    jev = jev_now(Path(args.jev))
    bench = read_set(Path(args.set), args.limit, args.view)
    prompt = DECIDE_PROMPT if opt["prompt"] == "decide" else TODAY_PROMPT
    output = ResponseDecide if opt["prompt"] == "decide" else ResponseToday
    timeout = args.timeout or opt["timeout"]
    parallel = args.parallel or opt["parallel"]
    ask = asker(opt["model"], opt["reasoning"], timeout, opt["providers"])
    respond = Respond(catalog, ask, owner=cfg.owner, checkpoint=cfg.checkpoint, prompt=prompt)
    rows_file = out / "rows.jsonl"
    done = {r["id"] for r in read_rows(rows_file)}
    spent = sum(r.get("cost", 0) for r in read_rows(rows_file))
    (out / "meta.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "option": name, **opt,
        "prompt_file": str(prompt), "output": list(output.model_fields), "timeout": timeout, "parallel": parallel,
        "owner": cfg.owner, "checkpoint": cfg.checkpoint, "lines": len(bench), "already": len(done),
        "view": args.view}, indent=1, default=str))
    print(f"{name}: {opt['about']}; {opt['model']} {opt['reasoning']}, {parallel} at a time; {len(bench)} lines "
          f"({len(done)} already, ${spent:.3f})", flush=True)
    sem = asyncio.Semaphore(parallel)
    lock = asyncio.Lock()
    stop = False
    tasks: set[asyncio.Task] = set()

    async def one(f, srow, tail, blocks, n_new, n_ctx):
        nonlocal spent, stop
        row = {"id": srow["id"], "label": srow["label"], "jev": srow["jev"], "new": n_new, "context_rows": n_ctx}
        held: dict = {}
        RAW.set(held)
        t0 = time.perf_counter()
        try:
            answer = await respond.ask(tail, output, cached=blocks, component="respond",
                                       call_id=f"replay gate {name} {srow['id']}"[:64])
        except Exception as err:  # noqa: BLE001 — an LlmError in production; any failure is a hand-up
            row.update(up=True, error=f"{type(err).__name__}: {err}"[:300], ms=int((time.perf_counter() - t0) * 1000),
                       cost=0.0)
        else:
            v = answer.value
            row.update(up=bool(v.hand_up), hand_up=v.hand_up, reply=v.reply, reason=v.reason, ms=answer.ms,
                       cost=answer.cost, prompt_tokens=answer.prompt_tokens, cached_tokens=answer.cached_tokens,
                       completion_tokens=answer.completion_tokens, first=first_field(held.get("texts", [])),
                       attempts=len(held.get("texts", [])))
        async with lock:
            spent += row["cost"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            what = ("ERROR " + row["error"][:60] if "error" in row else
                    ("UP" + (" +reply" if row["reply"].strip() else "")) if row["up"] else f"reply {row['reply'][:40]!r}")
            print(f"{srow['at'][11:19]} {srow['label']:7} {srow['jev'][:6]:6} {row['ms']:5} ms ${row['cost']:.4f} "
                  f"{row.get('prompt_tokens', 0)} in {row.get('cached_tokens', 0)} cached | {what} | "
                  f"{pt.by_id[srow['id']].text[:50]!r}", flush=True)
            if spent > args.budget and not stop:
                stop = True
                print(f"stopping: ${spent:.3f} spent, over --budget {args.budget}", flush=True)
        sem.release()

    async with httpx.AsyncClient(timeout=max(30.0, timeout + 5)) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for srow, tail, blocks, n_new, n_ctx in respond_items(pt, bench, jev, cfg, respond):
                if srow["id"] in done:
                    continue
                await sem.acquire()
                if stop:
                    sem.release()
                    break
                t = asyncio.create_task(one(f, srow, tail, blocks, n_new, n_ctx))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
            if tasks:
                await asyncio.gather(*tasks)
        vfg_llm.client = None
    print(f"{name}: ${spent:.3f} in all")


# ------------------------------------------------------------------ E and F: the Jev council


def council_questions(row: dict, names=tuple(COUNCIL)) -> dict:
    who = "AI" if row["source"] == "ai" else row["player_name"]
    quote = cut(one_line(row["text"]).strip(), QUOTE_MAX_CHARS)
    head = f"Line [1], {who} at {jev_when(row['at'])}: “{quote}”. "
    return {k: {"type": "choice", "instructions": head + COUNCIL[k][0],
                "criteria": {"yes": COUNCIL[k][1], "no": COUNCIL[k][2]}} for k in names}


def jev_call(jev: Jev, lookout: str, menu: str, as_row: dict, others: list[dict], names) -> dict:
    """One Jev call on the filter's state, as the filter asks it: a refused request once more with
    half the history; a failing one tried again, a second apart. {answers, ms, tokens, error}."""
    share, err = 1.0, ""
    for _ in range(TRIES):
        state, kept = build_state(lookout, [as_row], others, STATE_TOKENS, share, menu)
        try:
            res = jev.ask(state, council_questions(as_row, names), required=None)
        except JevRefused as e:
            err = f"refused: {e}"
            if share < 1:
                break
            share = 0.5
        except JevDown as e:
            err = f"down: {e}"
            time.sleep(1.0)
        else:
            return {"answers": {k: [v[0], round(v[1], 3)] for k, v in res.answers.items()}, "ms": res.ms,
                    "tokens": res.input_tokens or 0, "estimate": estimate(state), "history_kept": kept}
    return {"answers": {}, "ms": 0, "tokens": 0, "error": err}


def run_council(name: str, opt: dict, args) -> None:
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    pt = Playtest(Path(args.db))
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    menu_path = out / "menu.md"
    menu_path.write_text(jev_menu(catalog) + "\n", encoding="utf-8")
    menu, _ = read_menu(str(menu_path))
    lookout, _ = read_lookout(str(LOOKOUT))
    jev = Jev(typesafe_key(args.env), url=ENDPOINT, model=MODEL, timeout=args.jev_timeout)
    jevs = [Jev(jev.key, url=ENDPOINT, model=MODEL, timeout=args.jev_timeout) for _ in COUNCIL]  # one a thread
    said, records = jev_rows(pt)
    bench = read_set(Path(args.set), args.limit, args.view)
    rows_file = out / "rows.jsonl"
    done = {r["id"] for r in read_rows(rows_file)}
    tokens = sum(r.get("tokens", 0) for r in read_rows(rows_file))
    (out / "meta.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "option": name, **opt, "model": MODEL,
        "state_tokens": STATE_TOKENS, "history_hours": HISTORY_H, "council": COUNCIL, "lines": len(bench),
        "already": len(done), "view": args.view, "jev_timeout": args.jev_timeout}, indent=1))
    print(f"{name}: {opt['about']}; {len(bench)} lines ({len(done)} already)", flush=True)
    pool = ThreadPoolExecutor(max_workers=len(COUNCIL))
    with rows_file.open("a", encoding="utf-8") as f:
        for srow in bench:
            if srow["id"] in done:
                continue
            ln = pt.by_id[srow["id"]]
            began = ln.judged
            since = iso(when(began) - timedelta(hours=HISTORY_H))
            others = sorted([r for r in said + records if r["id"] != ln.id and since <= r["at"]
                             and r["visible"] <= began], key=lambda r: (r["at"], r["id"]), reverse=True)
            as_row = next(r for r in said if r["id"] == ln.id)
            row = {"id": ln.id, "label": srow["label"], "jev": srow["jev"]}
            if opt["split"]:
                t0 = time.perf_counter()
                futs = {k: pool.submit(jev_call, j, lookout, menu, as_row, others, (k,))
                        for k, j in zip(COUNCIL, jevs)}
                got = {k: fu.result() for k, fu in futs.items()}
                row.update(answers={k: g["answers"].get(k) for k, g in got.items()},
                           ms=max(g["ms"] for g in got.values()), wall_ms=int((time.perf_counter() - t0) * 1000),
                           calls_ms={k: g["ms"] for k, g in got.items()},
                           tokens=sum(g["tokens"] for g in got.values()),
                           errors={k: g["error"] for k, g in got.items() if g.get("error")})
            else:
                g = jev_call(jev, lookout, menu, as_row, others, tuple(COUNCIL))
                row.update(answers={k: g["answers"].get(k) for k in COUNCIL}, ms=g["ms"], tokens=g["tokens"],
                           estimate=g.get("estimate"), errors={"call": g["error"]} if g.get("error") else {})
            row["cost"] = row["tokens"] * PRICE_PER_INPUT_TOKEN
            yes = [k for k, a in row["answers"].items() if a and a[0] == "yes"]
            row.update(yes=yes, up=bool(yes), up2=len(yes) >= 2)
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{ln.at[11:19]} {srow['label']:7} {srow['jev'][:6]:6} {row['ms']:4} ms {row['tokens']:6} tok "
                  f"{'UP ' + ','.join(yes) if yes else 'talk'}{' ERR' if row['errors'] else ''} | {ln.text[:50]!r}",
                  flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    pool.shutdown()
    print(f"{name}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in all")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="write the fixed benchmark set")
    s.add_argument("--db", required=True)
    s.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev's classes now")
    s.add_argument("--set", default=str(SET))
    s.add_argument("--seed", type=int, default=SEED)
    s.add_argument("--dropped", type=int, default=DROPPED)
    r = sub.add_parser("run", help="run one option on the set")
    r.add_argument("option", choices=sorted(OPTIONS))
    r.add_argument("--db", required=True)
    r.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev's classes now (A-D's context)")
    r.add_argument("--set", default=str(SET))
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=0, help="the first N lines of the set only (a pilot)")
    r.add_argument("--view", choices=("all", "conversational"), default="all",
                   help="conversational: only the lines Jev now classes conversational")
    r.add_argument("--parallel", type=int, default=0, help="calls at a time (the option's own by default)")
    r.add_argument("--timeout", type=float, default=0, help="a Respond call's timeout, s (the option's own)")
    r.add_argument("--budget", type=float, default=2.0, help="Respond: stop past this many dollars")
    r.add_argument("--env", help="the file that holds TYPESAFE_API_KEY (E, F)")
    r.add_argument("--jev-timeout", type=float, default=10.0, help="a Jev call's timeout, s")
    r.add_argument("--jev-budget", type=float, default=0.5, help="E, F: stop past this many dollars of Jev")
    args = ap.parse_args()
    if args.cmd == "set":
        make_set(args)
        return
    opt = OPTIONS[args.option]
    if opt["kind"] == "respond":
        asyncio.run(run_respond(args.option, opt, args))
    else:
        run_council(args.option, opt, args)


if __name__ == "__main__":
    main()
