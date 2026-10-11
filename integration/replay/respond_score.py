#!/usr/bin/env python3
"""Scores the Respond bench (respond_bench.py): every version on the playtest inputs D2 sends Respond
and on the invented set, side by side, over one or more runs (each run its own OUT).

    python3 integration/replay/respond_score.py OUT1 [OUT2 …] --to OUT1/score.md   # and score.json beside it

Standard library only. A failed call counts as a hand-up, as the loop makes it.

- **Hand-ups by clean label** (the inputs' labels: request, answer_to_ai, talk_to_ai, context,
  noise), each run and each half.
- **Asks**: the `request` and `answer_to_ai` inputs. **Wanted a change**: the inputs any of whose
  lines the hindsight labels' `change` member says wanted something changed in the game
  (`yes_and_done`, `yes_not_done`; labels_build.py), whatever the label: the nearest the labels
  come to the spec's "an ask for something now, or for a change to what is going on". For each,
  how often each version handed it up, over its runs.
- **Every reply said** (not handed up, not empty), each run, for reading by hand; a reply with a
  word a promise often has (PROMISE) is marked, which is only a pointer: the judging is by hand,
  and its result is in respond_bench.md.
- **The invented set line by line**: how often each version did what the spec wants, over its runs.
- **Respond B** (`--gate-b`): on single-line inputs, how often each run of `old` agrees with the gate
  bench's recorded run of nearly the same prompt on the same line (gate_bench.py B, its context
  built from Jev today's classes, not D2's): how much the run and the context move a hand-up alone.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ("request", "answer_to_ai", "talk_to_ai", "context", "noise")
ASKS = ("request", "answer_to_ai")
WANTED = ("yes_and_done", "yes_not_done")
HALVES = ("first", "second")
PROMISE = re.compile(r"\b(on it|coming|i'?ll|i will|i can|let me|want (me|it|one|a)|say the word|if you want|"
                     r"whenever you want|here you go|there you go|done|fixed|on the way|right away|in a (sec|second|"
                     r"minute|moment|bit)|give it a)\b", re.I)


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else 0


def clean(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


def said(r: dict) -> str:
    """What the player would see: the reply, unless handed up."""
    return "" if r["up"] else r.get("reply", "").strip()


def order(names) -> list[str]:
    return sorted(set(names), key=lambda n: (n != "old", n))


class Runs:
    """Every run's rows: play[(version, run)] and inv[(version, run)], a run being an OUT's name."""

    def __init__(self, outs: list[Path], labels: Path):
        self.runs = [p.name for p in outs]
        self.play: dict[tuple[str, str], list[dict]] = {}
        self.inv: dict[tuple[str, str], list[dict]] = {}
        for out in outs:
            for d in sorted(x for x in out.iterdir() if x.is_dir()):
                for kind, into in (("playtest", self.play), ("invented", self.inv)):
                    rows = read(d / kind / "rows.jsonl")
                    if rows:
                        into[(d.name, out.name)] = rows
        self.versions = order(v for v, _ in list(self.play) + list(self.inv))
        change = {r["id"]: (r.get("members", {}).get("change") or [""])[0] for r in read(labels)}
        self.inputs = {}
        for rows in self.play.values():
            for r in rows:
                self.inputs.setdefault(r["id"], r)
        self.wanted = {i for i, r in self.inputs.items() if any(change.get(x) in WANTED for x in r["lines"])}

    def of(self, v: str, half: str) -> list[tuple[str, list[dict]]]:
        """[(run, rows of the half)] for each run of `v` that has that half."""
        out = []
        for run in self.runs:
            rows = [r for r in self.play.get((v, run), []) if r["half"] == half]
            if rows:
                out.append((run, rows))
        return out


def tally(rows: list[dict], wanted: set[str]) -> dict:
    by = {lab: [r for r in rows if r["label"] == lab] for lab in LABELS}
    asks = [r for r in rows if r["label"] in ASKS]
    change = [r for r in rows if r["id"] in wanted]
    ms = [r["ms"] for r in rows if "error" not in r]
    return {
        "n": len(rows),
        "up": {lab: [sum(r["up"] for r in by[lab]), len(by[lab])] for lab in LABELS},
        "asks_up": [sum(r["up"] for r in asks), len(asks)],
        "change_up": [sum(r["up"] for r in change), len(change)],
        "replies": sum(1 for r in rows if said(r)), "silent": sum(1 for r in rows if not r["up"] and not said(r)),
        "both": sum(1 for r in rows if r["up"] and r.get("reply", "").strip()),
        "failed": sum(1 for r in rows if "error" in r),
        "first": dict(Counter(r.get("first", "") for r in rows if "error" not in r)),
        "ms": ms, "cost": sum(r.get("cost", 0) for r in rows),
    }


def row_md(v: str, half: str, run: str, t: dict, bold: bool = False) -> str:
    cells = " | ".join(f"{a} of {b}" for a, b in (t["up"][lab] for lab in LABELS))
    (a, b), (ca, cb) = t["asks_up"], t["change_up"]
    head = f"**{v}** | **{half}** | **{run}**" if bold else f"{v} | {half} | {run}"
    return (f"| {head} | {cells} | **{a} of {b} ({pct(a, b)})** | **{ca} of {cb} ({pct(ca, cb)})** | "
            f"{t['replies']} | {t['silent']} | {t['both']} | {t['failed']} | "
            f"{t['first'].get('reason', 0)} of {t['n'] - t['failed']} | "
            f"{st.median(t['ms']) / 1000:.2f} s, {p90(t['ms']) / 1000:.2f} s | ${t['cost']:.3f} |")


def report(R: Runs, w, gate_b: Path | None = None) -> dict:
    summary: dict = {"runs": R.runs, "versions": {}}
    base = list(R.inputs.values())
    c = Counter(r["label"] for r in base)
    w("## The playtest inputs D2 sends Respond\n")
    w(f"{len(base)} inputs: " + ", ".join(f"{c[x]} {x}" for x in LABELS)
      + f"; {sum(1 for r in base if r['merged'])} merged; {len(R.wanted)} wanted a change. "
      + "; ".join(f"{h} half {sum(1 for r in base if r['half'] == h)} inputs, "
                  f"{sum(1 for r in base if r['half'] == h and r['label'] in ASKS)} asks, "
                  f"{sum(1 for r in base if r['half'] == h and r['id'] in R.wanted)} wanted a change"
                  for h in HALVES) + ".\n")
    w("Handed up, of all, by label. *Asks*: `request` and `answer_to_ai`. *Wanted a change*: the hindsight "
      "`change` member says so, whatever the label.\n")
    w("| Version | Half | Run | request | answer_to_ai | talk_to_ai | context | noise | **Asks** | "
      "**Wanted a change** | Replies said | Said nothing | Reply beside a hand-up | Failed | `reason` first | "
      "Latency median, p90 | Cost |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in R.versions:
        for half in HALVES:
            runs = R.of(v, half)
            for run, rows in runs:
                t = tally(rows, R.wanted)
                summary["versions"].setdefault(v, {}).setdefault(half, {})[run] = {
                    k: x for k, x in t.items() if k != "ms"}
                w(row_md(v, half, run, t))
            if len(runs) > 1:
                t = tally([r for _, rows in runs for r in rows], R.wanted)
                summary["versions"][v][half]["all"] = {k: x for k, x in t.items() if k != "ms"}
                w(row_md(v, half, f"all {len(runs)}", t, bold=True))

    w("\n### Each ask, and each input that wanted a change: how often each version handed it up\n")
    w("| Input | Half | Label | Wanted a change | Text | " + " | ".join(R.versions) + " |")
    w("|---|---|---|---|---|" + "---|" * len(R.versions))
    for r in sorted(base, key=lambda r: r["at"]):
        if r["label"] not in ASKS and r["id"] not in R.wanted:
            continue
        cells = []
        for v in R.versions:
            got = [x for run in R.runs for x in R.play.get((v, run), []) if x["id"] == r["id"]]
            cells.append(f"{sum(x['up'] for x in got)} of {len(got)}" if got else "-")
        w(f"| `{r['at'][11:19]}` `{r['id'][-6:]}` | {r['half']} | {r['label']} | "
          f"{'yes' if r['id'] in R.wanted else 'no'} | {clean(r['text'], 150)} | " + " | ".join(cells) + " |")

    w("\n### Every reply said\n")
    w("Every reply a player would see, each run, for reading by hand; ⚑ marks a word a promise often has (only a "
      "pointer).\n")
    for v in R.versions:
        for run in R.runs:
            replies = [r for r in R.play.get((v, run), []) if said(r)]
            if not replies:
                continue
            w(f"\n**{v}, {run}**: {len(replies)} replies, {sum(1 for r in replies if PROMISE.search(said(r)))} "
              f"marked\n")
            for r in replies:
                w(f"- {'⚑ ' if PROMISE.search(said(r)) else ''}`{r['id'][-6:]}` [{r['label']}, {r['half']}] "
                  f"{clean(r['text'], 90)} → **{said(r)}**")

    inv_versions = order(v for v, _ in R.inv)
    if inv_versions:
        w("\n## The invented set\n")
        items: dict[str, dict] = {}
        for rows in R.inv.values():
            for r in rows:
                items.setdefault(r["id"], r)
        w("Each cell: how often the version did what the spec wants, over its runs, and its last answer.\n")
        w("| Line | Point | Wants | Said | " + " | ".join(inv_versions) + " |")
        w("|---|---|---|---|" + "---|" * len(inv_versions))
        right, answers = Counter(), Counter()
        for i, it in sorted(items.items()):
            cells = []
            for v in inv_versions:
                got = [x for run in R.runs for x in R.inv.get((v, run), []) if x["id"] == i]
                ok = sum(1 for x in got if x["up"] == (it["want"] == "up"))
                right[v] += ok
                answers[v] += len(got)
                last = got[-1] if got else None
                what = ("-" if last is None else "UP" if last["up"] else
                        f"“{clean(said(last), 70)}”" if said(last) else "(nothing)")
                cells.append(f"{ok} of {len(got)}: {what}")
            w(f"| {i} | {it['point']} | {it['want']} | {it['who']}: “{it['text']}” | " + " | ".join(cells) + " |")
        w("\nRight, of all runs' answers: " + ", ".join(f"{v} {right[v]} of {answers[v]}" for v in inv_versions)
          + ".\n")
        points = list(dict.fromkeys(r["point"] for r in items.values()))
        w("| Version | Runs | " + " | ".join(points) + " | Cost |")
        w("|---|---|" + "---|" * (len(points) + 1))
        for v in inv_versions:
            got = [x for run in R.runs for x in R.inv.get((v, run), [])]
            cells = []
            for p in points:
                xs = [x for x in got if x["point"] == p]
                cells.append(f"{sum(1 for x in xs if x['up'] == (x['want'] == 'up'))} of {len(xs)}")
            runs = sum(1 for run in R.runs if (v, run) in R.inv)
            w(f"| {v} | {runs} | " + " | ".join(cells) + f" | ${sum(x.get('cost', 0) for x in got):.3f} |")
            summary["versions"].setdefault(v, {})["invented"] = {"right": right[v], "of": answers[v], "runs": runs}

    if gate_b is not None:
        gate = {r["id"]: bool(r["up"]) for r in read(gate_b)}
        w("\n## old against Respond B's recorded run\n")
        for run in R.runs:
            single = [r for r in R.play.get(("old", run), []) if not r["merged"] and r["lines"][0] in gate]
            if not single:
                continue
            agree = sum(1 for r in single if gate[r["lines"][0]] == r["up"])
            w(f"- {run}: on the {len(single)} single-line inputs B also ran, old agrees with B on {agree} "
              f"({pct(agree, len(single))}); B handed up {sum(1 for r in single if gate[r['lines'][0]])}, old "
              f"{sum(1 for r in single if r['up'])}.")
            summary.setdefault("gate_b", {})[run] = {"n": len(single), "agree": agree}

    total = sum(r.get("cost", 0) for rows in list(R.play.values()) + list(R.inv.values()) for r in rows)
    w(f"\nOpenRouter in these rows: ${total:.3f}.")
    summary["cost"] = total
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outs", nargs="+", help="respond_bench.py's OUTs, one a run")
    ap.add_argument("--labels", default=str(HERE / "labels_2026-10-08.jsonl"), help="the hindsight labels")
    ap.add_argument("--gate-b", help="the gate bench's B/rows.jsonl: Respond B's recorded answers")
    ap.add_argument("--to", help="the report (default the first OUT's score.md)")
    args = ap.parse_args()
    R = Runs([Path(p) for p in args.outs], Path(args.labels))
    md = ["# The Respond bench, scored\n", f"Versions: {', '.join(R.versions)}; runs: {', '.join(R.runs)}.\n"]
    summary = report(R, md.append, Path(args.gate_b) if args.gate_b else None)
    to = Path(args.to) if args.to else Path(args.outs[0]) / "score.md"
    to.write_text("\n".join(md) + "\n", encoding="utf-8")
    to.with_suffix(".json").write_text(json.dumps(summary, indent=1))
    print("\n".join(md[:60]))


if __name__ == "__main__":
    main()
