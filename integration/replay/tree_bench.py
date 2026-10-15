#!/usr/bin/env python3
"""The tree bench: Jev routing each line through a decision tree of class questions, all asked in
ONE Jev call, on the 843 lines of the 2026-10-08 playtest.

THE TREE (tree_questions.py): level 1 asks who the line is said to; a line not said to the AI goes
to level 2 (keep it as context, or drop it), a line said to the AI to level 3 (the brain, Respond,
or context with no reply). Each level has three members, three differently framed questions; the
no-council variant reads member A alone, the council weighs all three. One call asks all nine, so
one run gives both variants (tree_score.py walks them).

THE STATE is the filter's, as classes.py's `jev` step builds it: Jev's rules, the brain's lookout,
the menu as the brain sets it, and three hours of history with the brain's AI rows (its chat, its
tasks and their results), in the 8,000-token state, at the moment Jev judged the line then. Only
the line itself is asked about: no `not_relevant` line is asked again. A refused request is asked
once more with half the history; a failing one is tried again, a second apart, five times.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db
    $PY integration/replay/tree_bench.py run v1 --db $DB --out OUT --limit 20        # a pilot
    $PY integration/replay/tree_bench.py run v1 --db $DB --out OUT --half first      # goes on
    $PY integration/replay/tree_bench.py sample v1 --db $DB --out OUT --n 100        # 1, 3 and 9 questions
    python3 integration/replay/tree_score.py OUT ...

`run V` writes OUT/V/rows.jsonl, one row per line: every answer as Jev gave it ([choice,
confidence, probabilities]), the call's ms and input tokens. `sample V` asks a seeded sample of the
first half three ways, in one loop, the order turning from line to line: today's single question
(prompt.questions, the line alone), member A's three questions alone, and all nine; into
OUT/sample-V/rows.jsonl. A run into a file that has rows goes on after them. The TypeSafe key as
classes.py reads it, never printed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import Playtest, iso, when  # noqa: E402

sys.path.insert(0, str(playtest.REPO / "jev"))
from brain import actions, config  # noqa: E402
from brain.loop import jev_menu  # noqa: E402
from classes import HISTORY_H, STATE_TOKENS, TRIES, jev_rows, typesafe_key  # noqa: E402
from jev_api import ENDPOINT, MODEL, PRICE_PER_INPUT_TOKEN, Jev, JevDown, JevRefused  # noqa: E402
from prompt import QUOTE_MAX_CHARS, cut, estimate, one_line  # noqa: E402
from prompt import read_lookout, read_menu  # noqa: E402
from prompt import when as jev_when  # noqa: E402
from state_v1 import LOOKOUT, build_state, questions  # noqa: E402  (the old state, as benched)
from tree_questions import A_ONLY, NAMES, VERSIONS  # noqa: E402

SEED = 20261010


def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def ordered(pt: Playtest) -> list:
    """Every line said, in time order; the first 843 // 2 are the first half."""
    return sorted(pt.lines, key=lambda ln: (ln.at, ln.id))


def halves(lines: list) -> dict[str, str]:
    h = len(lines) // 2
    return {ln.id: ("first" if i < h else "second") for i, ln in enumerate(lines)}


def tree_questions(row: dict, version: str, names=NAMES) -> dict:
    """The questions about line [1], each a `choice` with its own options' descriptions."""
    who = "AI" if row["source"] == "ai" else row["player_name"]
    quote = cut(one_line(row["text"]).strip(), QUOTE_MAX_CHARS)
    head = f"Line [1], {who} at {jev_when(row['at'])}: “{quote}”. "
    qs = VERSIONS[version]
    return {k: {"type": "choice", "instructions": head + qs[k][0], "criteria": dict(qs[k][1])} for k in names}


def raw_answers(body: bytes | None, qs: dict) -> dict:
    """{name: [choice, confidence, {option: probability}]} for each well-formed answer, read from
    Jev's body so the probabilities are kept (jev_api keeps only the choice and confidence)."""
    try:
        got = json.loads((body or b"").decode("utf-8")).get("answers") or {}
    except (ValueError, AttributeError):
        return {}
    out = {}
    for k, q in qs.items():
        a = got.get(k)
        if not isinstance(a, dict) or a.get("choice") not in q["criteria"]:
            continue
        probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
        conf = a.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            conf = probs.get(a["choice"])
        if not isinstance(conf, (int, float)):
            continue
        out[k] = [a["choice"], round(float(conf), 4),
                  {o: round(float(p), 4) for o, p in probs.items() if isinstance(p, (int, float))}]
    return out


def ask(jev: Jev, lookout: str, menu: str, as_row: dict, others: list[dict], qs: dict) -> dict:
    """One Jev call on the filter's state. {answers, ms, tokens, estimate, history_kept[, error]}."""
    share, err = 1.0, ""
    for _ in range(TRIES):
        state, kept = build_state(lookout, [as_row], others, STATE_TOKENS, share, menu)
        try:
            res = jev.ask(state, qs, required=None)
        except JevRefused as e:
            err = f"refused: {e}"
            if share < 1:
                break
            share = 0.5
        except JevDown as e:
            err = f"down: {e}"
            time.sleep(1.0)
        else:
            return {"answers": raw_answers(jev.last_body, qs), "ms": res.ms, "tokens": res.input_tokens or 0,
                    "estimate": estimate(state), "history_kept": kept, "share": share}
    return {"answers": {}, "ms": 0, "tokens": 0, "error": err}


class Bench:
    """The playtest, the filter's state for any line, and the Jev client."""

    def __init__(self, args):
        self.pt = Playtest(Path(args.db))
        cfg = config.load()
        catalog = actions.load(cfg.repo)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        menu_path = out / "menu.md"
        menu_path.write_text(jev_menu(catalog) + "\n", encoding="utf-8")
        self.menu, _ = read_menu(str(menu_path))
        self.lookout, _ = read_lookout(str(LOOKOUT))
        self.jev = Jev(typesafe_key(args.env), url=ENDPOINT, model=MODEL, timeout=args.jev_timeout)
        self.said, self.records = jev_rows(self.pt)
        self.said_by_id = {r["id"]: r for r in self.said}
        self.chat = self.pt.answered()
        self.lines = ordered(self.pt)
        self.half = halves(self.lines)

    def context(self, ln) -> tuple[dict, list[dict]]:
        """The line as the filter renders it, and the rest of its history, newest first: what
        Jev had when it judged the line then."""
        began = ln.judged
        since = iso(when(began) - timedelta(hours=HISTORY_H))
        others = sorted([r for r in self.said + self.records if r["id"] != ln.id and since <= r["at"]
                         and r["visible"] <= began], key=lambda r: (r["at"], r["id"]), reverse=True)
        return self.said_by_id[ln.id], others

    def base(self, ln) -> dict:
        return {"id": ln.id, "n": ln.n, "at": ln.at, "who": ln.who, "label": self.pt.truth(ln, self.chat),
                "half": self.half[ln.id]}

    def meta(self, path: Path, **more) -> None:
        path.write_text(json.dumps({
            "started": datetime.now(UTC).isoformat(timespec="seconds"), "model": MODEL, "state_tokens": STATE_TOKENS,
            "history_hours": HISTORY_H, "lookout": str(LOOKOUT), "menu_lines": len(self.menu.splitlines()),
            "jev_timeout": self.jev.timeout, **more}, indent=1, ensure_ascii=False))


def run_tree(args) -> None:
    b = Bench(args)
    out = Path(args.out) / args.version
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    done = {r["id"] for r in read_rows(rows_file)}
    tokens = sum(r.get("tokens", 0) for r in read_rows(rows_file))
    todo = [ln for ln in b.lines if args.half == "all" or b.half[ln.id] == args.half]
    if args.limit:
        todo = todo[:args.limit]
    b.meta(out / "meta.json", version=args.version, half=args.half, lines=len(todo), already=len(done),
           questions=VERSIONS[args.version])
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
                row.update(ask(b.jev, b.lookout, b.menu, as_row, others, tree_questions(as_row, args.version)))
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            a = row["answers"]
            pick = " ".join(f"{(a.get(k) or ['-'])[0][:12]}" for k in A_ONLY)
            print(f"{ln.at[11:19]} {row['label']:7} {row['ms']:4} ms {row['tokens']:5} tok {len(a)}/9 | {pick:40} | "
                  f"{ln.text[:50]!r}{' ERR ' + row['error'] if row.get('error') else ''}", flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"{args.version}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def run_sample(args) -> None:
    """Today's question, A's three and all nine, on the same lines, in one loop."""
    b = Bench(args)
    out = Path(args.out) / f"sample-{args.version}"
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    done = {r["id"] for r in read_rows(rows_file)}
    tokens = sum(sum(c.get("tokens", 0) for c in r["calls"].values()) for r in read_rows(rows_file))
    pool = [ln for ln in b.lines if b.half[ln.id] == args.half and ln.text.strip()]
    chosen = sorted(random.Random(args.seed).sample(pool, min(args.n, len(pool))), key=lambda ln: (ln.at, ln.id))
    b.meta(out / "meta.json", version=args.version, half=args.half, seed=args.seed, lines=len(chosen),
           already=len(done), ids=[ln.id for ln in chosen])
    kinds = ("today1", "tree3", "tree9")
    print(f"sample {args.version}: {len(chosen)} lines of the {args.half} half ({len(done)} already)", flush=True)
    with rows_file.open("a", encoding="utf-8") as f:
        for i, ln in enumerate(chosen):
            if ln.id in done:
                continue
            as_row, others = b.context(ln)
            row = {**b.base(ln), "calls": {}}
            order = kinds[i % 3:] + kinds[:i % 3]
            for kind in order:
                qs = (questions([as_row]) if kind == "today1" else
                      tree_questions(as_row, args.version, A_ONLY if kind == "tree3" else NAMES))
                row["calls"][kind] = ask(b.jev, b.lookout, b.menu, as_row, others, qs)
            row["order"] = list(order)
            tokens += sum(c.get("tokens", 0) for c in row["calls"].values())
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            c = row["calls"]
            print(f"{ln.at[11:19]} {row['label']:7} " + " ".join(f"{k} {c[k]['ms']:4} ms {c[k]['tokens']:5} tok"
                                                              for k in kinds) + f" | {ln.text[:40]!r}", flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"sample: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def run_batches(args) -> None:
    """The brain's batch calls under each variant: no model call is asked about here. Each line
    goes into the brain's context as its outcome's class (brain: action_oriented, Respond:
    conversational, context with or without a reply: context, dropped: not_relevant), when Jev
    judged it then (Jev today: when it judged or flipped it now); every action_oriented or
    conversational line is a live read, and the batch timer (playtest.Clock, the brain's
    batch_s) makes a brain call of what no read took in. Into OUT/batches-V.json."""
    LIVE = ("action_oriented", "conversational")  # Jev's two classes for the AI then (to_ai now)
    from playtest import Clock
    from tree_score import lines_from, outcomes
    pt = Playtest(Path(args.db))
    cfg = config.load()
    lines = lines_from(Path(args.jev))
    tree = {r["id"]: r for r in read_rows(Path(args.out) / args.version / "rows.jsonl")}
    fed = {}
    for r in read_rows(Path(args.jev)):
        if "flip" in r:
            fed[r["flip"]] = r["at"]
        else:
            fed.setdefault(r["id"], r["judged"])
    judged = {r["id"]: r["judged"] for r in lines}
    cls = {"brain": "action_oriented", "respond": "conversational", "noreply": "context", "context": "context",
           "dropped": "not_relevant"}
    result = {"batch_s": getattr(cfg, "batch_s", 60.0), "version": args.version, "margin": args.margin}
    for variant in ("jev", "tree", "council"):
        got = outcomes(lines, tree if variant != "jev" else None, variant, args.margin)
        classes = {i: (cls[g["out"]], fed[i] if variant == "jev" else judged[i]) for i, g in got.items()}
        entries = pt.entries(classes)
        clock = Clock(entries, getattr(cfg, "batch_s", 60.0))
        reads = 0
        for e in entries:
            if e.row.type == "LINE" and e.row.cls in LIVE:
                clock.read(e.seen)
                reads += 1
        clock.until(iso(when(entries[-1].seen) + timedelta(seconds=getattr(cfg, "batch_s", 60.0) + 1)))
        result[variant] = {"batches": len(clock.batches), "reads": reads,
                           "rows_in_batches": sum(n for _, n in clock.batches),
                           "times": [at for at, _ in clock.batches]}
        print(f"{variant}: {reads} live reads, {len(clock.batches)} batch calls taking in "
              f"{result[variant]['rows_in_batches']} rows")
    (Path(args.out) / f"batches-{args.version}.json").write_text(json.dumps(result, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("batches", help="the brain's batch calls under each variant (no model calls)")
    s.add_argument("version", choices=sorted(VERSIONS))
    s.add_argument("--db", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--jev", required=True, help="classes.py's jev.jsonl")
    s.add_argument("--margin", type=float, default=0.0, help="the council's clash margin")
    for name, about in (("run", "all nine questions on every line of a half"),
                        ("sample", "today's question, A's three and all nine on a sample")):
        s = sub.add_parser(name, help=about)
        s.add_argument("version", choices=sorted(VERSIONS))
        s.add_argument("--db", required=True, help="the playtest's vfg.db")
        s.add_argument("--out", required=True)
        s.add_argument("--half", choices=("first", "second", "all"), default="first" if name == "sample" else "all")
        s.add_argument("--env", help="the file that holds TYPESAFE_API_KEY")
        s.add_argument("--jev-timeout", type=float, default=10.0, help="a Jev call's timeout, s")
        s.add_argument("--jev-budget", type=float, default=0.35, help="stop past this many dollars of Jev")
        if name == "run":
            s.add_argument("--limit", type=int, default=0, help="the first N lines only (a pilot)")
        else:
            s.add_argument("--n", type=int, default=100)
            s.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    if args.cmd == "run":
        run_tree(args)
    elif args.cmd == "sample":
        run_sample(args)
    else:
        run_batches(args)


if __name__ == "__main__":
    main()
