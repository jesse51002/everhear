#!/usr/bin/env python3
"""Scores the Respond bench (respond_bench.py): every version on the playtest inputs D2 sends Respond
and on the invented set, side by side, over one or more runs (each run its own OUT); and the
conversation agent (`front`) on the night's lines to the AI, beside V4 Flash's own answers that
night (`v4-night`, written by `respond_bench.py recorded`).

    python3 integration/replay/respond_score.py OUT1 [OUT2 …] --to OUT1/score.md   # and score.json beside it

Standard library only. A failed call counts as a hand-up, as the loop makes it. For `front` a
hand-up is a hand-off (`up` is the answer's hand_off after the code's checks), and its reply, an
acknowledgement, is said beside it.

- **The night** (`night`, `night-to_ai`), by hand label (night.py): `do` lines handed off (the bar:
  every one), `look`, the talk lines (`F` and T `talk`: a wrong hand-off costs a router call), `U`;
  the replies said with no hand-off, the acknowledgements said with one, silences; how often the
  guard and the DONE check fired; latency median and p90 (the bar: p90 at most 3 s); cost. Then the
  same on the 29 lines V4 Flash answered that night, beside its own answers, and every line, run
  by run, for reading by hand; the misses with the agent's reason.

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
# A word a claim that something is done or there often has: only a pointer, as PROMISE.
CLAIM = re.compile(r"\b(done|all set|ready|fixed|there you go|here you go|all yours|gone|gave|made|built|spawned|"
                   r"teleported|stopped|removed|unbound|bound|set to|turned off|is off|off now|cleared)\b", re.I)
# An offer beside a hand-off, which the prompt rules out: a pointer.
OFFER = re.compile(r"\b(want me|i can|i could|say the word|if you want)\b", re.I)
NIGHT_LABELS = ("do", "look", "talk", "F", "U")
NIGHT_SETS = ("night", "night-to_ai")
FRONT, RECORDED = "front", "v4-night"
P90_BAR_MS = 3000


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
    """What the player would see: the reply, unless handed up; the conversation agent's (`raw_up` in its
    rows) beside a hand-off too, as its acknowledgement."""
    if "raw_up" in r:
        return r.get("reply", "").strip()
    return "" if r["up"] else r.get("reply", "").strip()


def alone(r: dict) -> str:
    """A reply said with no hand-off: the one that must never promise or claim."""
    return "" if r["up"] else r.get("reply", "").strip()


def ms_of(rows: list[dict]) -> list[int]:
    return [r["ms"] for r in rows if "error" not in r]


def order(names) -> list[str]:
    return sorted(set(names), key=lambda n: (n != "old", n))


class Runs:
    """Every run's rows: play[(version, run)] and inv[(version, run)], a run being an OUT's name."""

    def __init__(self, outs: list[Path], labels: Path):
        self.runs = [p.name for p in outs]
        self.play: dict[tuple[str, str], list[dict]] = {}
        self.inv: dict[tuple[str, str], list[dict]] = {}
        self.night: dict[str, dict[tuple[str, str], list[dict]]] = {s: {} for s in NIGHT_SETS}
        for out in outs:
            for d in sorted(x for x in out.iterdir() if x.is_dir()):
                for kind, into in (("playtest", self.play), ("invented", self.inv), *self.night.items()):
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
    if R.play:
        playtest_report(R, w, summary)
    inv_versions = order(v for v, _ in R.inv)
    if inv_versions:
        invented_report(R, w, summary, inv_versions)
    for s in NIGHT_SETS:
        if R.night[s]:
            summary.setdefault("night", {})[s] = night_report(R, w, s)
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

    every = list(R.play.values()) + list(R.inv.values()) + [rows for s in NIGHT_SETS for (v, _), rows in
                                                             R.night[s].items() if v != RECORDED]
    total = sum(r.get("cost", 0) for rows in every for r in rows)
    w(f"\nOpenRouter in these rows (the night's own recorded answers left out): ${total:.3f}.")
    summary["cost"] = total
    return summary


def playtest_report(R: Runs, w, summary: dict) -> None:
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


def invented_report(R: Runs, w, summary: dict, inv_versions: list[str]) -> None:
    w("\n## The invented set\n")
    items: dict[str, dict] = {}
    for rows in R.inv.values():
        for r in rows:
            items.setdefault(r["id"], r)
    w("Each cell: how often the version did what the spec wants, over its runs, and its last answer (UP: handed "
      "up or off, with the acknowledgement said beside it).\n")
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
            what = ("-" if last is None else
                    ("UP" + (f" “{clean(said(last), 40)}”" if said(last) else "")) if last["up"] else
                    f"“{clean(said(last), 70)}”" if said(last) else "(nothing)")
            cells.append(f"{ok} of {len(got)}: {what}")
        w(f"| {i} | {it['point']} | {it['want']} | {it['who']}: “{it['text']}” | " + " | ".join(cells) + " |")
    w("\nRight, of all runs' answers: " + ", ".join(f"{v} {right[v]} of {answers[v]}" for v in inv_versions)
      + ".\n")
    points = list(dict.fromkeys(r["point"] for r in items.values()))
    w("| Version | Runs | " + " | ".join(points) + " | Latency median, p90 | Cost |")
    w("|---|---|" + "---|" * (len(points) + 2))
    for v in inv_versions:
        got = [x for run in R.runs for x in R.inv.get((v, run), [])]
        cells = []
        for p in points:
            xs = [x for x in got if x["point"] == p]
            cells.append(f"{sum(1 for x in xs if x['up'] == (x['want'] == 'up'))} of {len(xs)}")
        runs = sum(1 for run in R.runs if (v, run) in R.inv)
        ms = ms_of(got)
        w(f"| {v} | {runs} | " + " | ".join(cells) + f" | {st.median(ms) / 1000:.2f} s, {p90(ms) / 1000:.2f} s | "
          f"${sum(x.get('cost', 0) for x in got):.3f} |")
        summary["versions"].setdefault(v, {})["invented"] = {"right": right[v], "of": answers[v], "runs": runs}
    fronts = [x for run in R.runs for x in R.inv.get((FRONT, run), [])]
    if fronts:
        w("")
        checks(fronts, w, "The conversation agent on the invented set")


# ------------------------------------------------------------------ the night


def night_tally(rows: list[dict]) -> dict:
    """One version's rows on the night: hand-offs by label, the replies, the checks, time and cost."""
    by = {lab: [r for r in rows if r["label"] == lab] for lab in NIGHT_LABELS}
    talk = by["F"] + by["talk"]
    ms = ms_of(rows)
    prompt = sum(r.get("prompt_tokens", 0) for r in rows)
    return {
        "n": len(rows),
        "up": {lab: [sum(r["up"] for r in by[lab]), len(by[lab])] for lab in NIGHT_LABELS},
        "do_model": sum(1 for r in by["do"] if r.get("raw_up", r["up"])),
        "talk_up": [sum(r["up"] for r in talk), len(talk)],
        "alone": sum(1 for r in rows if alone(r)), "silent": sum(1 for r in rows if not r["up"] and not said(r)),
        "acks": sum(1 for r in rows if r["up"] and said(r)), "bare": sum(1 for r in rows if r["up"] and not said(r)),
        "guard": sum(1 for r in rows if r.get("guard")), "done_drop": sum(1 for r in rows if r.get("done_drop")),
        "offer_drop": sum(1 for r in rows if r.get("offer_drop")),
        "cut": sum(1 for r in rows if r.get("cut")), "failed": sum(1 for r in rows if "error" in r),
        "reason_first": sum(1 for r in rows if r.get("first") == "reason"),
        "ms": ms, "median": st.median(ms) if ms else 0, "p90": p90(ms), "max": max(ms) if ms else 0,
        "cost": sum(r.get("cost", 0) for r in rows),
        "cached": sum(r.get("cached_tokens", 0) for r in rows) / prompt if prompt else 0,
    }


def night_md(name: str, run: str, t: dict, bold: bool = False) -> str:
    (d, dn), (k, kn) = t["up"]["do"], t["talk_up"]
    cells = " | ".join(f"{a} of {b}" for a, b in (t["up"][lab] for lab in ("look", "talk", "F", "U")))
    head = f"**{name}** | **{run}**" if bold else f"{name} | {run}"
    bar = "" if t["p90"] <= P90_BAR_MS else " ✗"
    return (f"| {head} | {t['n']} | **{d} of {dn}**{'' if d == dn else ' ✗'} ({t['do_model']} by the model) | "
            f"{cells} | **{k} of {kn} "
            f"({pct(k, kn)})** | {t['alone']} | {t['acks']} | {t['bare']} | {t['silent']} | {t['guard']} | "
            f"{t['done_drop']} | {t['offer_drop']} | {t['failed']} | {t['median'] / 1000:.2f} s, "
            f"**{t['p90'] / 1000:.2f} s**{bar} | "
            f"${t['cost']:.4f} ({t['cost'] / max(1, t['n']):.5f} a call, {100 * t['cached']:.0f}% cached) |")


NIGHT_HEAD = ("| Version | Run | Calls | **do: handed off** | look | talk | F | U | "
              "**Talk lines (F and talk) handed off** | "
              "Replies alone | Acknowledgements with a hand-off | Hand-offs with nothing said | Silences | Guard "
              "fired | DONE check fired | Offer beside a hand-off dropped | Failed | Latency median, **p90** | Cost |")


def checks(rows: list[dict], w, what: str) -> None:
    """Every reply said with no hand-off, every acknowledgement said beside one, and every time the
    guard or the DONE check fired: for reading by hand."""
    w(f"\n**{what}: every reply said with no hand-off** (⚑ a word a promise often has, ◆ one a claim of "
      "something done often has: pointers only, the judging is by hand)\n")
    for r in rows:
        if alone(r):
            mark = ("⚑ " if PROMISE.search(alone(r)) else "") + ("◆ " if CLAIM.search(alone(r)) else "")
            w(f"- {mark}`{r['id']}` [{r.get('label') or r.get('point')}] {clean(r['text'], 80)} → **{alone(r)}**")
    acks = Counter(said(r) for r in rows if r["up"] and said(r))
    w(f"\n**{what}: the acknowledgements said with a hand-off**, by how often: "
      + "; ".join(f"“{a}” {n}" for a, n in acks.most_common()) + "\n")
    w(f"\n**{what}: the acknowledgements with a word a claim or an offer often has** (◆ a claim, ⚑ a promise "
      "or an offer: pointers, for reading by hand; beside a hand-off nothing is done yet)\n")
    for r in rows:
        ack = said(r) if r["up"] else ""
        if ack and (CLAIM.search(ack) or OFFER.search(ack)):
            w(f"- {'◆ ' if CLAIM.search(ack) else ''}{'⚑ ' if OFFER.search(ack) else ''}`{r['id']}` "
              f"[{r.get('label') or r.get('point')}] {clean(r['text'], 70)} → **{ack}**")
    for r in rows:
        if r.get("guard") or r.get("done_drop") or r.get("offer_drop") or r.get("cut"):
            w(f"- `{r['id']}` [{r.get('label') or r.get('point')}] guard {r.get('guard') or '-'}, DONE check "
              f"{'dropped' if r.get('done_drop') else '-'}{', offer dropped' if r.get('offer_drop') else ''}"
              f"{', cut' if r.get('cut') else ''}: the model wrote "
              f"hand_off {str(r.get('raw_up')).lower()}, “{clean(r.get('raw_reply', ''), 100)}”")


def night_report(R: Runs, w, s: str) -> dict:
    got = R.night[s]
    runs = [run for run in R.runs if (FRONT, run) in got]
    rec = next((rows for (v, _), rows in R.night["night"].items() if v == RECORDED), [])
    fronts = [r for run in runs for r in got[(FRONT, run)]]
    out: dict = {"runs": runs}
    w(f"\n## The night of 2026-10-12/13 (`{s}`)\n")
    base = got[(FRONT, runs[0])] if runs else rec
    c = Counter(r["label"] for r in base)
    w(f"{len(base)} lines Jev sent to the AI: " + ", ".join(f"{c[x]} {x}" for x in NIGHT_LABELS)
      + " (night.py: `do` asks for something in the game, the bar; `look` a question only a look answers; `talk` "
      "said to the AI, talk; `F` not said to the AI; `U` unclear). V4 Flash's own answers that night "
      f"(`{RECORDED}`) are on the {len(rec)} lines Jev classed conversational; the others went to the brain.\n")
    w(NIGHT_HEAD)
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        t = night_tally(got[(FRONT, run)])
        out[run] = {k: x for k, x in t.items() if k != "ms"}
        w(night_md(FRONT, run, t))
    if len(runs) > 1:
        t = night_tally(fronts)
        out["all"] = {k: x for k, x in t.items() if k != "ms"}
        w(night_md(FRONT, f"all {len(runs)}", t, bold=True))
    if s == "night" and rec:
        w("\nOn the lines V4 Flash answered that night, the same lines only:\n")
        w(NIGHT_HEAD)
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        t = night_tally(rec)
        out[RECORDED] = {k: x for k, x in t.items() if k != "ms"}
        w(night_md(f"{RECORDED} (the night)", "1", t))
        mine = [r for r in fronts if r.get("v4")]
        t = night_tally(mine)
        out["front_on_v4_lines"] = {k: x for k, x in t.items() if k != "ms"}
        w(night_md(FRONT, f"all {len(runs)}, same lines", t, bold=True))

    w("\n### Every line, run by run\n")
    w("UP: handed off, with the acknowledgement said beside it; a quote alone: a reply with no hand-off; "
      "(silent): nothing said, nothing handed off.\n")
    names = ([RECORDED] if s == "night" and rec else []) + [f"{FRONT} {run}" for run in runs]
    w("| Line | Label | Jev that night | Who | Text | " + " | ".join(names) + " |")
    w("|---|---|---|---|---|" + "---|" * len(names))
    by_rec = {r["id"]: r for r in rec}
    for r in base:
        cells = []
        for name in names:
            x = by_rec.get(r["id"]) if name == RECORDED else next(
                (y for y in got[(FRONT, name.split()[1])] if y["id"] == r["id"]), None)
            if x is None:
                cells.append("-")
                continue
            text = ("UP" + (f" “{clean(said(x), 40)}”" if said(x) else "")) if x["up"] else \
                f"“{clean(said(x), 70)}”" if said(x) else "(silent)"
            flags = "".join(f" [{k}]" for k in ("guard", "done_drop") if x.get(k))
            flags += " [failed]" if "error" in x else ""
            cells.append(text + flags)
        w(f"| `{r['id']}` | {r['label']} | {r.get('night_class', '')}{' typed' if r.get('typed') else ''} | "
          f"{r['who']} | {clean(r['text'], 70)} | " + " | ".join(cells) + " |")

    w("\n### The misses: `do` lines not handed off, with the agent's reason\n")
    misses = [(run, r) for run in runs for r in got[(FRONT, run)]]
    misses += [(RECORDED, r) for r in rec] if s == "night" else []
    for run, r in misses:
        if r["label"] == "do" and not r["up"]:
            why = clean(r.get("reason", ""), 300)
            w(f"- {run} `{r['id']}` {clean(r['text'], 90)} → “{said(r)}” — reason: {why}")
    w("\n### Talk and unclear lines handed off, with the agent's reason\n")
    for run in runs:
        for r in got[(FRONT, run)]:
            if r["label"] in ("F", "talk", "U") and r["up"]:
                w(f"- {run} `{r['id']}` [{r['label']}] {clean(r['text'], 70)} → UP “{said(r)}” — "
                  f"{clean(r.get('reason', ''), 200)}")
    if fronts:
        checks(fronts, w, f"The conversation agent on `{s}`")
    hosts = Counter(r.get("host") or "?" for r in fronts if "error" not in r)
    w("\nHosts that answered: " + ", ".join(f"{h} {n}" for h, n in hosts.most_common()) + ".")
    return out



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
