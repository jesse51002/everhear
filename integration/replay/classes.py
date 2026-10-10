#!/usr/bin/env python3
"""Jev's four classes and Respond's hand-up, on the 2026-10-08 lines: Prototype_Brain's second
required test. Two steps, each one call per line, in order:

1. `jev`: every line said on 2026-10-08 (843, the ones Jev kept then and the ones it dropped)
   through the real Jev filter's prompt (jev/prompt.py: the four classes, the brain's lookout
   (brain/prompts/lookout.md), menu.md as the brain sets it (`jev_menu`, every action's line),
   and the history with the brain's AI rows: the AI's chat, and the conversation's tasks and their
   results as the brain's `AI task` and `AI result` records), against TypeSafe, as filter.py asks
   it: the 8,000-token state (VFG_JEV_STATE_TOKENS), three hours of history, the `not_relevant`
   lines of the last two minutes asked about again (a flip is kept), a refused request asked once
   more with half the history, and a line Jev fails on passed as `context`, as the filter fails
   open. Each line is judged when Jev judged it then, with the history it had by that moment.
2. `respond`: every line Jev now classes `conversational` through Respond (brain.respond.Respond,
   its real model and prompt), with the context as the brain would have it under the new classes
   (playtest.py, each kept line fed when it was judged): every action_oriented or conversational
   line is a live read, as the loop makes it, and the batch timer takes in the rest. Respond's
   answer is recorded as the model gave it, before Respond's code drops a hand-up's reply.

Each line is labelled by what the conversation did with it (playtest.py): a task, a chat answer,
nothing, or dropped (Jev dropped it then, so the conversation never saw it). classes_score.py
scores both steps.

    (cd brain && uv sync --frozen --inexact)
    brain/.venv/bin/python integration/replay/classes.py jev --db DIR/vfg/vfg.db --out OUT --limit 20   # a pilot
    brain/.venv/bin/python integration/replay/classes.py jev --db DIR/vfg/vfg.db --out OUT
    brain/.venv/bin/python integration/replay/classes.py respond --db DIR/vfg/vfg.db --out OUT
    python3 integration/replay/classes_score.py OUT

The TypeSafe key: TYPESAFE_API_KEY in the environment, else from `--env FILE` (KEY=value lines;
default realmc's stand-in box's, integration/realmc/.server/shared/env in this checkout or the
script-library worktree); it is never printed. A second run into the same OUT goes on where the
last stopped. In OUT: jev.jsonl, respond.jsonl and their meta files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import Clock, Playtest, iso, when  # noqa: E402

sys.path.insert(0, str(playtest.REPO / "jev"))
import httpx  # noqa: E402
import vfg_llm  # noqa: E402
from brain import actions, config  # noqa: E402
from brain.__main__ import LOOKOUT, asker  # noqa: E402
from brain.context import LIVE  # noqa: E402
from brain.loop import jev_menu  # noqa: E402
from brain.respond import PROMPT as RESPOND_PROMPT  # noqa: E402
from brain.respond import Respond  # noqa: E402
from jev_api import ENDPOINT, MODEL, Jev, JevDown, JevRefused  # noqa: E402
from prompt import KEPT, build_state, estimate, questions, read_lookout, read_menu  # noqa: E402

ENV_FILES = [playtest.REPO / "integration" / "realmc" / ".server" / "shared" / "env",
             playtest.REPO.parent / "script-library" / "integration" / "realmc" / ".server" / "shared" / "env"]
STATE_TOKENS = 8000      # VFG_JEV_STATE_TOKENS's default
HISTORY_H = 3.0          # VFG_JEV_HISTORY_HOURS's default
ASK_AGAIN_S = 120.0      # filter.py's ASK_AGAIN_WINDOW
ASK_AGAIN_MAX = 5
TRIES = 5                # a call that fails (JevDown) is tried again this often, a second apart
RECORDS = ("action", "task", "result")


def typesafe_key(env_file: str | None) -> str:
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return os.environ["TYPESAFE_API_KEY"].strip()
    for path in [Path(env_file)] if env_file else ENV_FILES:
        if path.is_file():
            for line in path.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "TYPESAFE_API_KEY" and v.strip():
                    return v.strip().strip('"').strip("'")
    sys.exit("no TYPESAFE_API_KEY: set it, or name the file that holds it with --env")


def done_ids(path: Path, key: str = "id") -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(x)[key] for x in path.read_text().splitlines() if x.strip() and key in json.loads(x)}


# ------------------------------------------------------------------ step 1: Jev


def jev_rows(pt: Playtest) -> tuple[list[dict], list[dict]]:
    """The history Jev reads (its store's `history` and `ai_records`): every line said, the AI's
    included, and the brain's AI records, as dicts with the columns prompt.render_line reads."""
    said = [{"id": ln.id, "at": ln.at, "player_name": ln.who, "source": "voice", "text": ln.text,
             "peak_dbfs": ln.peak, "visible": ln.ended} for ln in pt.lines]
    said += [{"id": a["id"], "at": a["at"], "player_name": "AI", "source": "ai", "text": a["text"],
              "peak_dbfs": None, "visible": a["at"]} for a in pt.ai]
    records = [{"id": f"AI#{e.row.seq}", "at": e.row.at, "player_name": "AI", "source": e.row.kind,
                "text": e.row.text, "peak_dbfs": None, "visible": e.seen}
               for e in pt.entries() if e.row.type == "AI" and e.row.kind in RECORDS]
    return said, records


def run_jev(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pt = Playtest(Path(args.db))
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    menu_path = out / "menu.md"
    menu_path.write_text(jev_menu(catalog) + "\n", encoding="utf-8")
    menu, _ = read_menu(str(menu_path))
    lookout, _ = read_lookout(str(LOOKOUT))
    jev = Jev(typesafe_key(args.env), url=ENDPOINT, model=MODEL, timeout=args.timeout)
    said, records = jev_rows(pt)
    chat = pt.answered()
    rows_file = out / "jev.jsonl"
    done = done_ids(rows_file)
    # the verdicts so far, from an earlier run into OUT: {id: (class, confidence, judged at)}
    verdicts: dict[str, tuple[str, float | None, str]] = {}
    if rows_file.exists():
        for x in rows_file.read_text().splitlines():
            r = json.loads(x)
            if "flip" in r:
                verdicts[r["flip"]] = (r["to"], r["confidence"], r["at"])
            elif "id" in r:
                verdicts[r["id"]] = (r["class"], r["confidence"], r["judged"])
    todo = sorted(pt.lines, key=lambda ln: (ln.at, ln.id))
    if args.limit:
        todo = todo[:args.limit]
    menu_tokens = estimate({"actions": menu})
    (out / "jev-meta.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "db": str(args.db), "model": MODEL,
        "state_tokens": STATE_TOKENS, "history_hours": HISTORY_H, "menu_lines": len(menu.splitlines()),
        "menu_tokens": menu_tokens, "lookout": str(LOOKOUT), "lines": len(todo), "already": len(done)}, indent=1))
    print(f"{len(todo)} lines ({len(done)} already); the menu is {len(menu.splitlines())} lines, about "
          f"{menu_tokens} tokens", flush=True)
    calls = tokens = 0
    with rows_file.open("a", encoding="utf-8") as f:
        for ln in todo:
            if ln.id in done:
                continue
            began = ln.judged
            row = {"id": ln.id, "n": ln.n, "at": ln.at, "who": ln.who, "text": ln.text, "old": ln.old,
                   "old_confidence": ln.old_conf, "truth": pt.truth(ln, chat), "judged": began}
            if not ln.text.strip():
                verdicts[ln.id] = ("not_relevant", None, began)
                row.update({"class": "not_relevant", "confidence": None, "blank": True})
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                continue
            low = iso(when(began) - timedelta(seconds=ASK_AGAIN_S))
            again = sorted((pt.by_id[i] for i, (c, conf, at) in verdicts.items()
                            if c == "not_relevant" and conf is not None and at >= low),
                           key=lambda x: (x.at, x.id), reverse=True)[:ASK_AGAIN_MAX]
            asked = [ln] + again
            since = iso(when(began) - timedelta(hours=HISTORY_H))
            asked_ids = {a.id for a in asked}
            others = sorted([r for r in said + records if r["id"] not in asked_ids and since <= r["at"]
                             and r["visible"] <= began], key=lambda r: (r["at"], r["id"]), reverse=True)
            as_rows = [next(r for r in said if r["id"] == a.id) for a in asked]
            result, share, err = None, 1.0, ""
            for attempt in range(TRIES):
                state, kept = build_state(lookout, as_rows, others, STATE_TOKENS, share, menu)
                try:
                    result = jev.ask(state, questions(as_rows))
                    calls += 1
                    break
                except JevRefused as e:
                    calls += 1
                    err = f"refused: {e}"
                    if share < 1:
                        break
                    share, as_rows, asked = 0.5, as_rows[:1], asked[:1]
                except JevDown as e:
                    err = f"down: {e}"
                    time.sleep(1.0)
            ai_avail = sum(1 for r in others if r["player_name"] == "AI" and r["source"] in RECORDS)
            ai_kept = sum(1 for r in others[:kept] if r["player_name"] == "AI" and r["source"] in RECORDS)
            row.update(asked=len(as_rows), estimate=estimate(state), menu_full=state.get("actions") == menu,
                       menu_lines=len((state.get("actions") or "").splitlines()), history_kept=kept,
                       history_avail=len(others), ai_kept=ai_kept, ai_avail=ai_avail,
                       back_min=round((when(began) - when(others[kept - 1]["at"])).total_seconds() / 60, 1)
                       if kept else 0.0)
            if result is None:
                verdicts[ln.id] = ("context", None, began)
                row.update({"class": "context", "confidence": None, "failed": err})
            else:
                cls, conf = result.answers["line_1"]
                verdicts[ln.id] = (cls, conf, began)
                tokens += result.input_tokens or 0
                row.update({"class": cls, "confidence": round(conf, 3), "ms": result.ms,
                            "input_tokens": result.input_tokens})
                for k, a in enumerate(asked[1:], 2):
                    got = result.answers.get(f"line_{k}")
                    if got is not None and got[0] in KEPT:
                        verdicts[a.id] = (got[0], got[1], began)
                        f.write(json.dumps({"flip": a.id, "to": got[0], "confidence": round(got[1], 3),
                                            "at": began, "by": ln.id}) + "\n")
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{ln.at[11:19]} {ln.who[:8]:8} {ln.old:15} -> {row['class']:15} {row.get('confidence') or 0:.2f} "
                  f"{row.get('ms', 0)} ms {row['estimate']} tok, history {kept}/{len(others)}, AI rows "
                  f"{ai_kept}/{ai_avail}, menu {'whole' if row['menu_full'] else row['menu_lines']} | {ln.text[:50]!r}",
                  flush=True)
    print(f"{calls} Jev calls, {tokens} input tokens, ${tokens * 0.000000042:.4f}")


# ------------------------------------------------------------------ step 2: Respond


def final_classes(out: Path) -> dict[str, tuple[str, str, float | None, bool]]:
    """{id: (class, when it was fed, confidence, flipped)} from jev.jsonl, flips applied."""
    got: dict[str, tuple[str, str, float | None, bool]] = {}
    for x in (out / "jev.jsonl").read_text().splitlines():
        r = json.loads(x)
        if "flip" in r:
            got[r["flip"]] = (r["to"], r["at"], r["confidence"], True)
        else:
            got[r["id"]] = (r["class"], r["judged"], r.get("confidence"), False)
    return got


async def run_respond(args) -> None:
    out = Path(args.out)
    pt = Playtest(Path(args.db))
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    classes = final_classes(out)
    entries = pt.entries({i: (c, fed) for i, (c, fed, _, _) in classes.items()})
    clock = Clock(entries, cfg.batch_s)
    chat = pt.answered()
    base = asker(cfg.respond_model, cfg.respond_reasoning, cfg.timeout_s)
    raw: dict = {}

    async def ask(*a, **k):
        answer = await base(*a, **k)
        raw.update(reply=answer.value.reply, hand_up=answer.value.hand_up)
        return answer

    respond = Respond(catalog, ask, owner=cfg.owner, checkpoint=cfg.checkpoint)
    live = [e for e in entries if e.row.type == "LINE" and e.row.cls in LIVE]
    convo = [e for e in live if e.row.cls == "conversational"]
    if args.limit:
        convo = convo[:args.limit]
    upto = live.index(convo[-1]) + 1 if convo else 0
    rows_file = out / "respond.jsonl"
    done = done_ids(rows_file)
    (out / "respond-meta.json").write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "model": cfg.respond_model,
        "reasoning": cfg.respond_reasoning, "owner": cfg.owner, "checkpoint": cfg.checkpoint,
        "prompt": str(RESPOND_PROMPT), "conversational": len(convo), "already": len(done)}, indent=1))
    print(f"{len(convo)} conversational lines ({len(done)} already); {cfg.respond_model} {cfg.respond_reasoning}",
          flush=True)
    spent = 0.0
    want = {e.line_id for e in convo}
    async with httpx.AsyncClient(timeout=30) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for e in live[:upto]:
                new = [r for r in clock.read(e.seen) if r.seq != e.row.seq]
                if e.line_id not in want or e.line_id in done:
                    continue
                ln = pt.by_id[e.line_id]
                cls, fed, conf, flipped = classes[ln.id]
                row = {"id": ln.id, "n": ln.n, "at": ln.at, "who": ln.who, "text": ln.text, "old": ln.old,
                       "truth": pt.truth(ln, chat), "confidence": conf, "flipped": flipped, "new": len(new),
                       "context_rows": len(clock.ctx.rows)}
                raw.clear()
                try:
                    answer = await respond.answer(e.row, clock.ctx, pt.seen(e.seen, ln.who, items=False),
                                                  pt.open_tasks(e.seen), new)
                except vfg_llm.LlmError as err:
                    row.update(error=str(err)[:300])
                else:
                    v = answer.value
                    row.update(hand_up=v.hand_up, reply=v.reply, reason=v.reason, raw_reply=raw.get("reply", ""),
                               ms=answer.ms, cost=answer.cost, prompt_tokens=answer.prompt_tokens,
                               cached_tokens=answer.cached_tokens, completion_tokens=answer.completion_tokens)
                    spent += answer.cost
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                what = "ERROR" if "error" in row else "HAND UP" if row["hand_up"] else f"reply {row['reply'][:50]!r}"
                print(f"{ln.at[11:19]} {row['truth']:8} {row.get('ms', 0)} ms ${row.get('cost', 0):.4f} "
                      f"{row.get('prompt_tokens', 0)} in, {row.get('cached_tokens', 0)} cached | {what} | {ln.text[:50]!r}",
                      flush=True)
                if spent > args.budget:
                    print(f"stopping: ${spent:.3f} spent, over --budget {args.budget}")
                    break
        vfg_llm.client = None
    print(f"${spent:.3f} on Respond")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("jev", "respond"))
    ap.add_argument("--db", required=True, help="the playtest's vfg.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="the first N lines only")
    ap.add_argument("--env", help="the file that holds TYPESAFE_API_KEY (jev)")
    ap.add_argument("--timeout", type=float, default=3.0, help="a Jev call's timeout, s (VFG_JEV_TIMEOUT: 3)")
    ap.add_argument("--budget", type=float, default=1.0, help="respond: stop past this many dollars (1)")
    args = ap.parse_args()
    if args.step == "jev":
        run_jev(args)
    else:
        asyncio.run(run_respond(args))


if __name__ == "__main__":
    main()
