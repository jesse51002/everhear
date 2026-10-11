#!/usr/bin/env python3
"""Routing on whole asks (whole_asks_2026-10-08.jsonl): Jev today and the five trees, each with Jev's
old state and with its new one (state_v2.py), one Jev call an input. whole_asks_score.py scores them.

THE ROUTINGS and their questions, all on line [1]:
- `jev`: Jev today's four-class question (prompt.questions: action_oriented, conversational,
  context, not_relevant), the line alone: no `not_relevant` line is asked again.
- `A`: the tree bench's v2, member A (tree_questions.py). The old state asks all nine of v2's
  questions, as the recorded run did; the new state asks A's three alone, as it would run.
- `B2`, `C1`, `D2`, `E1`: the trees bench's final versions (trees_questions.py); E1 is walked with
  rules r2 when scored.

THE STATES:
- `old`: the state Jev builds today (prompt.build_state: Jev's rules, the brain's lookout.md, the
  brain's menu, the history), asked ONLY on the merged inputs: a single-line input keeps the
  answer recorded on the trees bench (or classes.py's, for Jev today).
- `new`: state_v2.build_state (neutral rules, a lookout that only says what to watch for, the menu,
  the examples block), asked on EVERY input.

THE LINE ASKED ABOUT is the input: a merged input's text is its lines joined with " … ", said by its
speaker at its last line's time, as one line marked [1]. Its history is what Jev had when it judged
the input's last line then, three hours back, with the input's own lines left out (they are in
[1]); every other line is in the history as it was said. A blank input is dropped with no call. A
refused request is asked once more with half the history; a failing one is tried again, a second
apart, five times.

    PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; L=integration/replay
    $PY $L/whole_asks_bench.py run old D2 --db $DB --out OUT --limit 20     # a pilot
    $PY $L/whole_asks_bench.py run old D2 --db $DB --out OUT                # the merged inputs
    $PY $L/whole_asks_bench.py run new D2 --db $DB --out OUT --half first --limit 20
    $PY $L/whole_asks_bench.py run new D2 --db $DB --out OUT                # every input (goes on)

Writes OUT/<state>/<routing>/rows.jsonl, one row an input: every answer as Jev gave it ([choice,
confidence, probabilities]), the call's ms and input tokens. A run into a file that has rows goes on
after them. The TypeSafe key as classes.py reads it, never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tree_bench import Bench, raw_answers, read_rows, tree_questions  # noqa: E402  (puts brain/, jev/ on the path)
from classes import HISTORY_H, STATE_TOKENS, TRIES  # noqa: E402
from jev_api import PRICE_PER_INPUT_TOKEN, JevDown, JevRefused  # noqa: E402
from playtest import iso, when  # noqa: E402
from prompt import build_state, estimate, questions  # noqa: E402
from tree_questions import A_ONLY, NAMES  # noqa: E402
from trees_bench import build_questions  # noqa: E402
import state_v2  # noqa: E402

ROUTINGS = ("jev", "A", "B2", "C1", "D2", "E1")
STATES = ("old", "new")


def read_inputs(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def qs_for(routing: str, state: str, as_row: dict) -> dict:
    if routing == "jev":
        return questions([as_row])
    if routing == "A":
        return tree_questions(as_row, "v2", NAMES if state == "old" else A_ONLY)
    return build_questions(as_row, routing)


class Inputs:
    """The playtest's history for any input: the input as one line [1], and everything else."""

    def __init__(self, b: Bench, inputs: list[dict]):
        self.b = b
        self.inputs = inputs

    def context(self, inp: dict) -> tuple[dict, list[dict]]:
        b = self.b
        members = [b.said_by_id[i] for i in inp["lines"]]
        last = members[-1]
        peaks = [m["peak_dbfs"] for m in members if m["peak_dbfs"] is not None]
        as_row = {"id": inp["id"], "at": last["at"], "player_name": last["player_name"], "source": "voice",
                  "text": inp["text"], "peak_dbfs": max(peaks) if peaks else None, "visible": last["visible"]}
        began = inp["judged"]
        since = iso(when(began) - timedelta(hours=HISTORY_H))
        skip = set(inp["lines"])
        others = sorted([r for r in b.said + b.records if r["id"] not in skip and since <= r["at"]
                         and r["visible"] <= began], key=lambda r: (r["at"], r["id"]), reverse=True)
        return as_row, others


def ask(b: Bench, state: str, as_row: dict, others: list[dict], qs: dict) -> dict:
    share, err = 1.0, ""
    for _ in range(TRIES):
        if state == "old":
            st, kept = build_state(b.lookout, [as_row], others, STATE_TOKENS, share, b.menu)
        else:
            st, kept = state_v2.build_state([as_row], others, STATE_TOKENS, share, b.menu)
        try:
            res = b.jev.ask(st, qs, required=None)
        except JevRefused as e:
            err = f"refused: {e}"
            if share < 1:
                break
            share = 0.5
        except JevDown as e:
            err = f"down: {e}"
            time.sleep(1.0)
        else:
            return {"answers": raw_answers(b.jev.last_body, qs), "ms": res.ms, "tokens": res.input_tokens or 0,
                    "estimate": estimate(st), "history_kept": kept, "share": share}
    return {"answers": {}, "ms": 0, "tokens": 0, "error": err}


def run(args) -> None:
    b = Bench(args)
    src = Inputs(b, read_inputs(Path(args.inputs)))
    out = Path(args.out) / args.state / args.routing
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    tokens = sum(r.get("tokens", 0) for r in have)
    todo = [i for i in src.inputs if (args.state == "new" or i["merged"])
            and (args.half == "all" or i["half"] == args.half)]
    if args.limit:
        todo = todo[:args.limit]
    b.meta(out / f"meta-{args.half}{'-limit' if args.limit else ''}.json", state=args.state, routing=args.routing,
           half=args.half, inputs=len(todo), already=len(done), inputs_file=str(args.inputs),
           rules=state_v2.RULES_V2 if args.state == "new" else "prompt.RULES",
           lookout=state_v2.LOOKOUT_V2 if args.state == "new" else str(b.lookout),
           examples=state_v2.EXAMPLES_V2 if args.state == "new" else "")
    print(f"{args.state} {args.routing}: {len(todo)} inputs ({args.half}), {len(done)} already, "
          f"${tokens * PRICE_PER_INPUT_TOKEN:.4f}", flush=True)
    with rows_file.open("a", encoding="utf-8") as f:
        for inp in todo:
            if inp["id"] in done:
                continue
            row = {"id": inp["id"], "lines": inp["lines"], "at": inp["at"], "who": inp["who"], "label": inp["label"],
                   "half": inp["half"], "merged": inp["merged"]}
            if not inp["text"].strip():
                row.update(blank=True, answers={}, ms=0, tokens=0)
            else:
                as_row, others = src.context(inp)
                row.update(ask(b, args.state, as_row, others, qs_for(args.routing, args.state, as_row)))
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            a = row["answers"]
            pick = " ".join(f"{v[0][:16]}" for v in a.values())
            print(f"{inp['at'][11:19]} {inp['label']:12} {len(inp['lines'])} {row['ms']:4} ms {row['tokens']:5} tok | "
                  f"{pick[:70]:70} | {inp['text'][:50]!r}{' ERR ' + row['error'] if row.get('error') else ''}",
                  flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"{args.state} {args.routing}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="one routing in one state")
    s.add_argument("state", choices=STATES)
    s.add_argument("routing", choices=ROUTINGS)
    s.add_argument("--db", required=True, help="the playtest's vfg.db")
    s.add_argument("--out", required=True)
    s.add_argument("--inputs", default=str(HERE / "whole_asks_2026-10-08.jsonl"))
    s.add_argument("--half", choices=("first", "second", "all"), default="all")
    s.add_argument("--limit", type=int, default=0, help="the first N inputs only (a pilot)")
    s.add_argument("--env", help="the file that holds TYPESAFE_API_KEY")
    s.add_argument("--jev-timeout", type=float, default=10.0, help="a Jev call's timeout, s")
    s.add_argument("--jev-budget", type=float, default=0.3, help="stop past this many dollars of Jev in the file")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
