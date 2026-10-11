#!/usr/bin/env python3
"""Scores the trees bench (trees_bench.py): Jev today and the five approaches A–E, on the same
2026-10-08 lines.

    python3 integration/replay/trees_score.py OUT --jev CLASSES_OUT/jev.jsonl --gate-b GATE_OUT/B/rows.jsonl \
        --a TREE_OUT --tune                                   # OUT/tune.md: the first half, every version
    python3 integration/replay/trees_score.py OUT --jev ... --gate-b ... --a TREE_OUT \
        --final B2 C1 D2 E1/r2 --repeat OUT2 --combos         # OUT/score.md, OUT/score.json

Standard library only.

THE OUTCOMES: `brain`, `respond` (which may hand up to the brain), `context` (stored, no model call)
and `dropped`. Jev today: `action_oriented` is brain, `conversational` Respond, `context` context,
`not_relevant` dropped, its flips applied. A call that failed is context (the filter fails open);
a blank line is dropped.

A is the tree bench's v2 run (TREE_OUT/v2/rows.jsonl), walked with member A's three questions. That
run asked all nine questions in one call, so A's Jev cost is the 9-question tokens scaled by the
share a 3-question call took on the tree bench's 100-line sample (TREE_OUT/sample-v1), and A's
latency is the sample's 3-question calls.

THE LABELS are classes.py's: `task` (an ask), `chat`, `nothing`, `dropped` (Jev dropped it then).
An ask that goes to Respond reaches the brain when Respond B handed that line up (the gate bench's
recorded decision); a Respond line outside B's set (only `dropped` lines) counts at B's rate.

COST a 2.5 h session (the 843 lines): brain calls x $0.0085 + Respond calls x $0.0015 + Jev's
input tokens as measured x $42 per billion.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tree_score import Gate, clean, lines_from, p90, pct, read  # noqa: E402
from trees_questions import E_RULES, NAMES, ROUTES, VERSIONS, approach_of  # noqa: E402

LABELS = ("task", "chat", "nothing", "dropped")
OUTS = ("brain", "respond", "context", "dropped")
OUT_NAMES = {"brain": "brain", "respond": "Respond", "context": "context", "dropped": "dropped"}
JEV_OUT = {"action_oriented": "brain", "conversational": "respond", "context": "context", "not_relevant": "dropped"}
BRAIN_USD = 0.0085
RESPOND_USD = 0.0015
JEV_USD = 0.000000042
SHORT = {"jev": "Jev today", "A": "A", "B": "B", "C": "C", "D": "D", "E": "E"}


# ------------------------------------------------------------------ the data


def all_versions() -> list[str]:
    """Every scored version: the wording versions, and for E each wording with each rule set."""
    out = []
    for approach, vs in VERSIONS.items():
        for v in vs:
            out += [f"{v}/{r}" for r in E_RULES] if approach == "E" else [v]
    return out


def rows_dir(version: str) -> str:
    return version.split("/")[0]


def outcomes(lines: list[dict], rows: dict[str, dict] | None, approach: str, version: str) -> dict[str, dict]:
    """{id: {out, path[, failed|blank]}} for one approach and version; `jev` is Jev today."""
    got = {}
    route = ROUTES.get(approach)
    for r in lines:
        if approach == "jev":
            got[r["id"]] = {"out": JEV_OUT[r["final"]], "path": r["final"]}
            continue
        t = (rows or {}).get(r["id"])
        if t is None:
            continue
        if t.get("blank"):
            got[r["id"]] = {"out": "dropped", "path": "blank", "blank": True}
        elif t.get("error") or not t.get("answers"):
            got[r["id"]] = {"out": "context", "path": "failed", "failed": True}
        else:
            got[r["id"]] = route(t["answers"], version)
    return got


def tally(lines: list[dict], got: dict[str, dict], gate: Gate, jev_tokens: float, jev_calls: int) -> dict:
    have = [r for r in lines if r["id"] in got]
    cm = Counter((r["label"], got[r["id"]]["out"]) for r in have)
    resp = [r for r in have if got[r["id"]]["out"] == "respond"]
    known = [r for r in resp if r["id"] in gate.up]
    up_known = sum(1 for r in known if gate.up[r["id"]])
    up_est = sum(gate.rate[r["label"]] for r in resp if r["id"] not in gate.up)
    brain = sum(1 for r in have if got[r["id"]]["out"] == "brain")
    asks = [r for r in have if r["label"] == "task"]
    ask_resp = [r for r in asks if got[r["id"]]["out"] == "respond"]
    talk = [r for r in have if r["label"] in ("chat", "nothing")]
    old_dropped = [r for r in have if r["label"] == "dropped"]
    brain_calls = brain + up_known + up_est
    jev_usd = jev_tokens * JEV_USD
    t = {
        "lines": len(have), "cm": {f"{a}/{b}": cm[(a, b)] for a in LABELS for b in OUTS},
        "asks": len(asks),
        "asks_brain": sum(1 for r in asks if got[r["id"]]["out"] == "brain"),
        "asks_respond": len(ask_resp),
        "asks_respond_up": sum(1 for r in ask_resp if gate.up.get(r["id"]) is True),
        "asks_respond_kept": sum(1 for r in ask_resp if gate.up.get(r["id"]) is False),
        "asks_respond_unknown": sum(1 for r in ask_resp if r["id"] not in gate.up),
        "asks_context": sum(1 for r in asks if got[r["id"]]["out"] == "context"),
        "asks_dropped": sum(1 for r in asks if got[r["id"]]["out"] == "dropped"),
        "talk": len(talk), "talk_brain": sum(1 for r in talk if got[r["id"]]["out"] == "brain"),
        "old_dropped": len(old_dropped),
        "old_dropped_dropped": sum(1 for r in old_dropped if got[r["id"]]["out"] == "dropped"),
        "brain_direct": brain, "respond": len(resp), "respond_unknown": len(resp) - len(known),
        "respond_up_known": up_known, "respond_up_est": up_est, "brain_calls": brain_calls,
        "jev_calls": jev_calls, "jev_tokens": jev_tokens, "jev_usd": jev_usd,
        "usd": brain_calls * BRAIN_USD + len(resp) * RESPOND_USD + jev_usd,
        "usd_brain": brain_calls * BRAIN_USD, "usd_respond": len(resp) * RESPOND_USD,
        "failed": sum(1 for r in have if got[r["id"]].get("failed")),
    }
    t["asks_reach"] = t["asks_brain"] + t["asks_respond_up"]
    t["asks_lost"] = t["asks"] - t["asks_reach"]
    return t


class Bench:
    """Every approach's rows, and Jev's cost and latency for each."""

    def __init__(self, out: Path, a_dir: Path, jev_path: Path, gate_path: Path):
        self.out = out
        self.lines = lines_from(jev_path)
        self.gate = Gate(gate_path)
        self.rows = {"A/v2": {r["id"]: r for r in read(a_dir / "v2" / "rows.jsonl")}}
        for v in {rows_dir(v) for v in all_versions()}:
            p = out / v / "rows.jsonl"
            if p.exists():
                self.rows[v] = {r["id"]: r for r in read(p)}
        sample = read(a_dir / "sample-v1" / "rows.jsonl")
        t3 = sum(r["calls"]["tree3"].get("tokens", 0) for r in sample)
        t9 = sum(r["calls"]["tree9"].get("tokens", 0) for r in sample)
        self.a_share = t3 / t9 if t9 else 1.0
        self.a_ms = [r["calls"]["tree3"]["ms"] for r in sample if r["calls"]["tree3"].get("ms")]
        self.a9_ms = [r["calls"]["tree9"]["ms"] for r in sample if r["calls"]["tree9"].get("ms")]

    def rows_of(self, approach: str, version: str) -> dict | None:
        if approach == "jev":
            return None
        return self.rows["A/v2"] if approach == "A" else self.rows.get(rows_dir(version))

    def has(self, approach: str, version: str, ls: list[dict]) -> bool:
        rows = self.rows_of(approach, version)
        return approach == "jev" or (rows is not None and all(r["id"] in rows for r in ls))

    def jev_cost(self, approach: str, version: str, ls: list[dict]) -> tuple[float, int]:
        """(input tokens, calls) on these lines."""
        if approach == "jev":
            return sum(r.get("input_tokens") or 0 for r in ls), sum(1 for r in ls if "estimate" in r)
        rows = self.rows_of(approach, version)
        got = [rows[r["id"]] for r in ls if r["id"] in rows]
        tok = sum(t.get("tokens", 0) for t in got)
        return (tok * self.a_share if approach == "A" else tok), sum(1 for t in got if not t.get("blank"))

    def ms(self, approach: str, version: str) -> list[int]:
        if approach == "jev":
            return [r["ms"] for r in self.lines if r.get("ms")]
        if approach == "A":
            return self.a_ms
        return [t["ms"] for t in self.rows_of(approach, version).values() if t.get("ms")]

    def score(self, approach: str, version: str, ls: list[dict]) -> tuple[dict, dict]:
        got = outcomes(ls, self.rows_of(approach, version), approach, version)
        tok, calls = self.jev_cost(approach, version, ls)
        return tally(ls, got, self.gate, tok, calls), got


# ------------------------------------------------------------------ tables


def heat_html(cm: dict, title: str) -> list[str]:
    out = ['<table>', f'<caption>{html.escape(title)}</caption>',
           "<tr><th></th>" + "".join(f"<th>{OUT_NAMES[o]}</th>" for o in OUTS) + "<th>all</th></tr>"]
    for lab in LABELS:
        n = sum(cm[f"{lab}/{o}"] for o in OUTS)
        cells = []
        for o in OUTS:
            c = cm[f"{lab}/{o}"]
            share = c / n if n else 0
            bg = f"rgba(37, 99, 235, {0.08 + 0.85 * share:.2f})" if c else "transparent"
            fg = "#fff" if share >= 0.45 else "inherit"
            cells.append(f'<td style="background:{bg};color:{fg};text-align:right">{c}<br>'
                         f'<small>{pct(c, n)}</small></td>')
        out.append(f"<tr><th>{lab}</th>" + "".join(cells) + f'<td style="text-align:right">{n}</td></tr>')
    out.append("</table>")
    return out


def heat_md(cm: dict) -> list[str]:
    out = ["| label | " + " | ".join(OUT_NAMES[o] for o in OUTS) + " | all |", "|---" * (len(OUTS) + 2) + "|"]
    for lab in LABELS:
        n = sum(cm[f"{lab}/{o}"] for o in OUTS)
        out.append(f"| **{lab}** | " + " | ".join(f"{cm[f'{lab}/{o}']} ({pct(cm[f'{lab}/{o}'], n)})" for o in OUTS)
                   + f" | {n} |")
    return out


def asks_line(t: dict) -> str:
    return (f"{t['asks_brain']} straight to the brain; {t['asks_respond']} to Respond, of which "
            f"{t['asks_respond_up']} handed up by B and {t['asks_respond_kept']} answered in chat"
            + (f", {t['asks_respond_unknown']} not in B's set" if t["asks_respond_unknown"] else "")
            + f"; {t['asks_context']} in context; {t['asks_dropped']} dropped. "
              f"**{t['asks_reach']} of {t['asks']} reach the brain ({pct(t['asks_reach'], t['asks'], 1)}).**")


def cost_line(t: dict, view: str) -> str:
    return (f"{t['brain_direct']} brain calls straight, {t['respond']} Respond calls of which "
            f"{t['respond_up_known']} handed up by B"
            + (f" and about {t['respond_up_est']:.1f} more of the {t['respond_unknown']} outside B's set"
               if t["respond_unknown"] else "")
            + f": {t['brain_calls']:.0f} brain calls ${t['usd_brain']:.2f}, Respond ${t['usd_respond']:.2f}, "
              f"Jev {t['jev_calls']} calls {t['jev_tokens']:,.0f} tokens ${t['jev_usd']:.3f}. "
              f"**${t['usd']:.2f}**" + (" a session." if view == "all" else " this half."))


# ------------------------------------------------------------------ the reports


def tune(b: Bench, w) -> dict:
    first = [r for r in b.lines if r["half"] == "first"]
    w("## Every version scored, on the first half only\n")
    w(f"{len(first)} lines, {sum(r['label'] == 'task' for r in first)} asks, "
      f"{sum(r['label'] in ('chat', 'nothing') for r in first)} talk (chat and nothing), "
      f"{sum(r['label'] == 'dropped' for r in first)} dropped by the old filter. Cost is the first half's.\n")
    w("| Approach | Version | Asks to brain | + via B | = reach | Lost: chat / context / dropped | Talk to brain | "
      "Old-dropped dropped | Brain calls | Respond calls | Jev | Cost |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    res = {}
    todo = [("jev", "today"), ("A", "v2")] + [(approach_of(v), v) for v in all_versions()]
    for approach, v in todo:
        if not b.has(approach, v, first):
            continue
        t, _ = b.score(approach, v, first)
        res[f"{approach}/{v}"] = t
        w(f"| {SHORT[approach]} | {v} | {t['asks_brain']} | {t['asks_respond_up']} | **{t['asks_reach']}** | "
          f"{t['asks_respond_kept']} / {t['asks_context']} / {t['asks_dropped']} | "
          f"{t['talk_brain']} ({pct(t['talk_brain'], t['talk'])}) | "
          f"{t['old_dropped_dropped']} ({pct(t['old_dropped_dropped'], t['old_dropped'])}) | "
          f"{t['brain_calls']:.0f} | {t['respond']} | ${t['jev_usd']:.3f} | **${t['usd']:.2f}** |")
    return res


def paths(b: Bench, approach: str, v: str, ls: list[dict], w) -> None:
    """Which branch each label's lines took: how the approach reached its outcomes."""
    _, got = b.score(approach, v, ls)
    c = Counter((got[r["id"]]["out"], got[r["id"]]["path"], r["label"]) for r in ls if r["id"] in got)
    by = {}
    for (out, path, lab), n in c.items():
        by.setdefault((out, path), Counter())[lab] += n
    w(f"\n### {NAMES.get(approach, approach)} ({v})\n")
    w("| Outcome | Path | task | chat | nothing | dropped |\n|---|---|---|---|---|---|")
    for (out, path), labs in sorted(by.items(), key=lambda kv: (OUTS.index(kv[0][0]), -sum(kv[1].values()))):
        w(f"| {out} | `{path}` | " + " | ".join(str(labs[lab]) for lab in LABELS) + " |")


def disagreements(b: Bench, final: dict, gots: dict, ls: list[dict], n: int) -> list[dict]:
    """The lines where the six disagree most: the most distinct outcomes, then the most even split
    between the brain and the rest, asks and talk first."""
    scored = []
    for r in ls:
        outs = [gots[k][r["id"]]["out"] for k in final if r["id"] in gots[k]]
        if len(outs) < len(final):
            continue
        brains = sum(o == "brain" for o in outs)
        scored.append((len(set(outs)), -abs(brains - len(outs) / 2), r["label"] in ("task", "chat", "nothing"),
                       r["at"], r))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
    picked, labs = [], Counter()
    for *_, r in scored:
        if labs[r["label"]] >= 2:
            continue
        picked.append(r)
        labs[r["label"]] += 1
        if len(picked) == n:
            break
    return picked


def report(b: Bench, final: dict, w, args) -> dict:
    """final: {approach: version}, Jev today first."""
    views = {"second": [r for r in b.lines if r["half"] == "second"], "all": b.lines}
    summary: dict = {"final": final, "a_share": b.a_share}
    gots = {}
    for view, ls in views.items():
        w(f"\n## {'The held-out second half' if view == 'second' else 'All 843 lines'}: {len(ls)} lines, "
          f"{sum(r['label'] == 'task' for r in ls)} asks\n")
        for approach, v in final.items():
            t, got = b.score(approach, v, ls)
            if view == "all":
                gots[approach] = got
            summary.setdefault(view, {})[approach] = t
            name = "Jev today" if approach == "jev" else f"{NAMES[approach]} ({v})"
            w(f"\n### {name}\n")
            b_html = heat_html(Counter(t["cm"]), f"{name}: label (rows) by outcome (columns), {view}")
            w("\n".join(b_html))
            w("")
            w("\n".join(heat_md(Counter(t["cm"]))))
            w(f"\n- **Asks:** {asks_line(t)}")
            w(f"- **Cost:** {cost_line(t, view)}")
            if approach == "A":
                w(f"- A's Jev is the 9-question call's tokens x {b.a_share:.2f}, the share a 3-question call took on "
                  "the tree bench's sample.")
            if t["failed"]:
                w(f"- {t['failed']} calls failed (kept as context).")

    w("\n## Side by side\n")
    w("| | View | Cost | Brain calls | Respond calls | Jev | Asks to brain direct | + via B | = reach | "
      "Lost: chat / context / dropped | Talk sent to the brain | Old filter's dropped, dropped | Latency median / p90 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for view in views:
        for approach, v in final.items():
            t = summary[view][approach]
            ms = b.ms(approach, v)
            t["ms_median"], t["ms_p90"] = (st.median(ms), p90(ms)) if ms else (0, 0)
            name = "Jev today" if approach == "jev" else f"{approach} ({v})"
            w(f"| {name} | {view} | **${t['usd']:.2f}** | {t['brain_calls']:.0f} | {t['respond']} | "
              f"${t['jev_usd']:.3f} | {t['asks_brain']} | {t['asks_respond_up']} | **{t['asks_reach']} of "
              f"{t['asks']}** | {t['asks_respond_kept']} / {t['asks_context']} / {t['asks_dropped']} | "
              f"{t['talk_brain']} of {t['talk']} ({pct(t['talk_brain'], t['talk'])}) | {t['old_dropped_dropped']} of "
              f"{t['old_dropped']} ({pct(t['old_dropped_dropped'], t['old_dropped'])}) | "
              f"{t['ms_median']:.0f} / {t['ms_p90']:.0f} ms |")

    w("\n## Latency per Jev call\n")
    w("| | Where measured | Calls | Median | p90 |\n|---|---|---|---|---|")
    for approach, v in final.items():
        ms = b.ms(approach, v)
        where = {"jev": "classes.py's run (one question, with not_relevant lines asked again)",
                 "A": "the tree bench's 100-line sample, A's three questions alone"}.get(approach, f"the {v} run")
        w(f"| {SHORT[approach]} | {where} | {len(ms)} | {st.median(ms):.0f} ms | {p90(ms):.0f} ms |")
    w(f"| A, 9-question call | the tree bench's sample | {len(b.a9_ms)} | {st.median(b.a9_ms):.0f} ms | "
      f"{p90(b.a9_ms):.0f} ms |")

    w("\n## Every ask that does not reach the brain (all 843 lines)\n")
    for approach, v in final.items():
        got = gots[approach]
        lost = [(r, got[r["id"]]) for r in b.lines if r["label"] == "task" and r["id"] in got
                and not (got[r["id"]]["out"] == "brain" or (got[r["id"]]["out"] == "respond"
                                                            and b.gate.up.get(r["id"]) is True))]
        name = "Jev today" if approach == "jev" else f"{NAMES[approach]} ({v})"
        w(f"\n**{name}**: {len(lost)}\n")
        for r, g in lost:
            where = OUT_NAMES[g["out"]] + (" (B answered in chat)" if g["out"] == "respond" else "")
            w(f"- `{r['at'][11:19]}` {r['who']} [{r['half']}] → **{where}** `{g['path']}`: {clean(r['text'])}")

    w("\n## Where they disagree most (all 843 lines)\n")
    picks = disagreements(b, final, gots, b.lines, args.examples)
    w("| Line | Label | " + " | ".join(SHORT[a] for a in final) + " |\n|---|---|" + "---|" * len(final))
    for r in picks:
        w(f"| `{r['at'][11:19]}` {r['who']}: {clean(r['text'], 160)} | {r['label']} | "
          + " | ".join(gots[a][r["id"]]["out"] for a in final) + " |")
    w("\nThe paths behind them:\n")
    for r in picks:
        w(f"- `{r['at'][11:19]}`: " + "; ".join(f"{SHORT[a]} `{gots[a][r['id']]['path']}`" for a in final))
    summary["disagree"] = [{"id": r["id"], "at": r["at"], "who": r["who"], "label": r["label"], "text": r["text"],
                            "outs": {a: gots[a][r["id"]] for a in final}} for r in picks]
    agree = Counter(len({gots[a][r["id"]]["out"] for a in final if a != "jev"}) for r in b.lines)
    w(f"\nA–E give the same outcome on {agree[1]} of {len(b.lines)} lines; two outcomes on {agree[2]}, three on "
      f"{agree[3]}, four on {agree[4]}.")
    summary["agree"] = dict(agree)

    if args.repeat:
        repeat(b, Path(args.repeat), final, w, summary)
    if args.combos:
        combos(b, final, w, summary)

    w("\n## How each approach reached its outcomes (all 843 lines)\n")
    for approach, v in final.items():
        if approach != "jev":
            paths(b, approach, v, b.lines, w)
    return summary


COMBOS = {
    "D + B's net": "D, but a line D drops or keeps as context that B calls a request to the AI goes to Respond",
    "D + C's net": "D, but a line D drops or keeps as context that C's rung 2 calls an explicit request goes to Respond",
    "D and B, three keys": "the brain only when D's two keys and B's request agree; D's act without B to Respond; "
                           "then B's net",
    "D and C, three keys": "the brain only when D's two keys and C's rung 2 agree; D's act without C to Respond; "
                           "then C's net",
    "D and A's who": "the brain only when D's two keys agree and A's level 1 says the AI; else Respond",
}


def combo_out(name: str, i: str, g: dict) -> str:
    d, bb, c, a = g["D"][i], g["B"][i], g["C"][i], g["A"][i]
    b_req, c_req = bb["out"] == "brain", c["path"].startswith("c2_")
    net = (b_req if "B" in name else c_req)
    if name in ("D + B's net", "D + C's net"):
        return "respond" if d["out"] in ("dropped", "context") and net else d["out"]
    if name.endswith("three keys"):
        if d["out"] == "brain":
            return "brain" if net else "respond"
        return "respond" if d["out"] in ("dropped", "context") and net else d["out"]
    if d["out"] == "brain":
        return "brain" if a["path"].startswith("the_ai") else "respond"
    return d["out"]


def combos(b: Bench, final: dict, w, summary: dict) -> None:
    """Combinations of the final versions' answers, scored offline: each approach's answers come from its
    own call. Jev is left out: one call asking both sets would cost about the two calls' questions over
    one state, a few cents a session more than either."""
    if not all(a in final for a in "ABCD"):
        return
    w("\n## Combinations, scored offline\n")
    w("Each combination walks answers already collected, from each approach's own call. The choice among them "
      "would be made on the first half. Cost leaves Jev out (one call with both question sets would add a few "
      "cents a session).\n")
    w("| Combination | Half | Asks reach | Lost: chat / context / dropped | Brain calls | Respond calls | "
      "Talk to brain | Old-dropped dropped | Cost without Jev |\n|---|---|---|---|---|---|---|---|---|")
    for half in ("first", "second", "all"):
        ls = [r for r in b.lines if half == "all" or r["half"] == half]
        g = {a: outcomes(ls, b.rows_of(a, v), a, v) for a, v in final.items() if a in "ABCD"}
        rows = [("D alone", {r["id"]: g["D"][r["id"]] for r in ls})]
        rows += [(n, {r["id"]: {"out": combo_out(n, r["id"], g), "path": ""} for r in ls}) for n in COMBOS]
        for name, got in rows:
            t = tally(ls, got, b.gate, 0, 0)
            summary.setdefault("combos", {}).setdefault(half, {})[name] = t
            w(f"| {name} | {half} | {t['asks_reach']} of {t['asks']} | {t['asks_respond_kept']} / {t['asks_context']} / "
              f"{t['asks_dropped']} | {t['brain_calls']:.0f} | {t['respond']} | {t['talk_brain']} | "
              f"{pct(t['old_dropped_dropped'], t['old_dropped'])} | ${t['usd']:.2f} |")
    w("")
    for n, about in COMBOS.items():
        w(f"- **{n}**: {about}.")


def repeat(b: Bench, rdir: Path, final: dict, w, summary: dict) -> None:
    """Run-to-run noise: each version found in RDIR, asked again on the same lines, against the main run."""
    w("\n## Run-to-run noise\n")
    for approach, v in final.items():
        p = rdir / rows_dir(v) / "rows.jsonl"
        if approach in ("jev", "A") or not p.exists():
            continue
        again = {r["id"]: r for r in read(p)}
        ls = [r for r in b.lines if r["id"] in again]
        g1 = outcomes(ls, b.rows_of(approach, v), approach, v)
        g2 = outcomes(ls, again, approach, v)
        t1 = tally(ls, g1, b.gate, *b.jev_cost(approach, v, ls))
        t2 = tally(ls, g2, b.gate, sum(again[r["id"]].get("tokens", 0) for r in ls), len(ls))
        same = sum(g1[i]["out"] == g2[i]["out"] for i in g1)
        w(f"{v} asked again on the same {len(ls)} lines: the same outcome on **{same} of {len(ls)}**. Asks reaching "
          f"the brain {t1['asks_reach']} then {t2['asks_reach']} of {t1['asks']}; brain calls {t1['brain_calls']:.0f} "
          f"then {t2['brain_calls']:.0f}; Respond calls {t1['respond']} then {t2['respond']}; cost ${t1['usd']:.2f} "
          f"then ${t2['usd']:.2f}. Differences of a couple of asks or a few cents between approaches are inside "
          "this noise.")
        summary.setdefault("repeat", {})[v] = {"same": same, "lines": len(ls), "reach": [t1["asks_reach"], t2["asks_reach"]],
                                               "usd": [t1["usd"], t2["usd"]]}


def goes_to(approach: str, q: str, opt: str) -> str:
    from trees_questions import B_MAP, C_RUNGS, D_MAP
    if approach == "B":
        o = B_MAP[q][opt]
        return "level 2" if o == "level2" else OUT_NAMES.get(o, o)
    if approach == "C":
        rung = [i for i, (name, _, _) in enumerate(C_RUNGS) if name == q][0]
        _, fires, out = C_RUNGS[rung]
        return f"rung {rung + 1} fires: {OUT_NAMES[out]}" if opt in fires else f"on to rung {rung + 2}"
    if approach == "D":
        o = D_MAP[q][opt]
        return {"act": "brain if Q2 agrees, else Respond" if q == "d_what" else "agrees: act",
                "reply": "disagrees", "not_ai": "disagrees"}.get(o, OUT_NAMES.get(o, o))
    return "a feature, to the rules"


def questions_md(final: dict, w) -> None:
    from trees_questions import questions_of
    w("\n## The questions\n")
    w("Each question is a `choice` about line [1], its instructions `Line [1], <player> at <time>: “<the line>”. "
      "<question>`, all of an approach's questions in one call on the filter's state.\n")
    for approach, v in final.items():
        if approach in ("jev", "A"):
            continue
        w(f"\n**{NAMES[approach]} ({v})**\n")
        w("| Question | Option | Goes to | Description |\n|---|---|---|---|")
        for q, (text, opts) in questions_of(rows_dir(v)).items():
            for i, (opt, desc) in enumerate(opts.items()):
                w(f"| {f'`{q}`: {text}' if i == 0 else ''} | `{opt}` | {goes_to(approach, q, opt)} | {desc} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev today, the lines and their labels")
    ap.add_argument("--gate-b", required=True, help="the gate bench's B/rows.jsonl: Respond B's hand-ups")
    ap.add_argument("--a", required=True, help="the tree bench's OUT: v2/rows.jsonl and sample-v1/rows.jsonl")
    ap.add_argument("--final", nargs="*", default=[], help="the version reported for each of B–E, e.g. B1 C1 D1 E1/r1")
    ap.add_argument("--tune", action="store_true", help="the first half's table, every version")
    ap.add_argument("--paths", default="", help="with --tune: also each listed version's paths on the first half")
    ap.add_argument("--examples", type=int, default=5)
    ap.add_argument("--repeat", help="an OUT holding a final version asked again (run-to-run noise)")
    ap.add_argument("--combos", action="store_true", help="also score combinations of the final versions' answers")
    args = ap.parse_args()
    out = Path(args.out)
    b = Bench(out, Path(args.a), Path(args.jev), Path(args.gate_b))
    md: list[str] = []
    w = md.append
    if args.tune:
        res = tune(b, w)
        first = [r for r in b.lines if r["half"] == "first"]
        for v in filter(None, args.paths.split(",")):
            paths(b, "A" if v == "v2" else approach_of(v), v, first, w)
        text = "\n".join(md) + "\n"
        (out / "tune.md").write_text(text, encoding="utf-8")
        (out / "tune.json").write_text(json.dumps(res, indent=1, default=str))
        sys.stdout.write(text)
        return
    final = {"jev": "today", "A": "v2"}
    for v in args.final:
        final[approach_of(v)] = v
    w(f"# The trees bench: {len(b.lines)} lines, Jev today and five approaches\n")
    w("Versions: " + ", ".join(f"{a} {v}" for a, v in final.items() if a != "jev") + ". Halves: first "
      f"{sum(r['half'] == 'first' for r in b.lines)} lines (wording and E's rules set here), second "
      f"{sum(r['half'] == 'second' for r in b.lines)} (held out).")
    missing = [a for a, v in final.items() if not b.has(a, v, b.lines)]
    if missing:
        w(f"\n**Missing lines for {', '.join(missing)}.**")
    summary = report(b, final, w, args)
    questions_md(final, w)
    text = "\n".join(md) + "\n"
    (out / "score.md").write_text(text, encoding="utf-8")
    (out / "score.json").write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
