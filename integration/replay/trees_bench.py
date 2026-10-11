#!/usr/bin/env python3
"""The trees bench: five genuinely different ways to build Jev's routing tree (trees_questions.py),
each ONE Jev call per line, on the 843 lines of the 2026-10-08 playtest.

A (who, then what) is the tree bench's v2, member A; it is not asked again. This runs B, C, D and
E, a wording version at a time, with the state Jev builds today (tree_bench.Bench: Jev's rules,
the brain's lookout and menu, three hours of history in the 8,000-token state, at the moment Jev
judged the line then). A refused request is asked once more with half the history; a failing one
is tried again, a second apart, five times.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db
    $PY integration/replay/trees_bench.py B1 --db $DB --out OUT --half first --limit 20   # a pilot
    $PY integration/replay/trees_bench.py B1 --db $DB --out OUT --half first             # goes on
    $PY integration/replay/trees_bench.py B1 --db $DB --out OUT --half second            # held out
    python3 integration/replay/trees_score.py OUT ...

Writes OUT/<version>/rows.jsonl, one row per line: every answer as Jev gave it ([choice,
confidence, probabilities]), the call's ms and input tokens. A run into a file that has rows goes
on after them. The TypeSafe key as classes.py reads it, never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tree_bench import Bench, ask, read_rows  # noqa: E402  (puts brain/ and jev/ on the path)
from jev_api import PRICE_PER_INPUT_TOKEN  # noqa: E402
from prompt import QUOTE_MAX_CHARS, cut, one_line  # noqa: E402
from prompt import when as jev_when  # noqa: E402
from trees_questions import ROUTES, VERSIONS, approach_of, questions_of  # noqa: E402

ALL_VERSIONS = sorted(v for vs in VERSIONS.values() for v in vs)


def build_questions(row: dict, version: str) -> dict:
    """The approach's questions about line [1], each a `choice` with its own options' descriptions."""
    who = "AI" if row["source"] == "ai" else row["player_name"]
    quote = cut(one_line(row["text"]).strip(), QUOTE_MAX_CHARS)
    head = f"Line [1], {who} at {jev_when(row['at'])}: “{quote}”. "
    return {k: {"type": "choice", "instructions": head + q, "criteria": dict(opts)}
            for k, (q, opts) in questions_of(version).items()}


def run(args) -> None:
    b = Bench(args)
    out = Path(args.out) / args.version
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    tokens = sum(r.get("tokens", 0) for r in have)
    todo = [ln for ln in b.lines if args.half == "all" or b.half[ln.id] == args.half]
    if args.limit:
        todo = todo[:args.limit]
    b.meta(out / f"meta-{args.half}.json", version=args.version, half=args.half, lines=len(todo),
           already=len(done), questions=questions_of(args.version))
    route = ROUTES[approach_of(args.version)]
    nq = len(questions_of(args.version))
    print(f"{args.version}: {len(todo)} lines ({args.half}), {len(done)} already, "
          f"${tokens * PRICE_PER_INPUT_TOKEN:.4f}", flush=True)
    with rows_file.open("a", encoding="utf-8") as f:
        for ln in todo:
            if ln.id in done:
                continue
            row = b.base(ln)
            if not ln.text.strip():
                row.update(blank=True, answers={}, ms=0, tokens=0)
            else:
                as_row, others = b.context(ln)
                row.update(ask(b.jev, b.lookout, b.menu, as_row, others, build_questions(as_row, args.version)))
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            a = row["answers"]
            pick = " ".join(f"{(a.get(k) or ['-'])[0][:14]}" for k in questions_of(args.version))
            out_ = route(a, args.version)["out"] if a else "-"
            print(f"{ln.at[11:19]} {row['label']:7} {row['ms']:4} ms {row['tokens']:5} tok {len(a)}/{nq} "
                  f"{out_:8} | {pick:60} | {ln.text[:50]!r}{' ERR ' + row['error'] if row.get('error') else ''}",
                  flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"{args.version}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("version", choices=ALL_VERSIONS, help="a wording version: B1, C1, D1, E1, …")
    ap.add_argument("--db", required=True, help="the playtest's vfg.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--half", choices=("first", "second", "all"), default="first")
    ap.add_argument("--limit", type=int, default=0, help="the first N lines of the half only (a pilot)")
    ap.add_argument("--env", help="the file that holds TYPESAFE_API_KEY")
    ap.add_argument("--jev-timeout", type=float, default=10.0, help="a Jev call's timeout, s")
    ap.add_argument("--jev-budget", type=float, default=0.35, help="stop past this many dollars of Jev in the file")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
