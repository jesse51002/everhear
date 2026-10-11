#!/usr/bin/env python3
"""Scores routing on whole asks (whole_asks_2026-10-08.jsonl): Jev today and the five trees, each with
Jev's old state and its new one, twelve routings on the same inputs.

    python3 integration/replay/whole_asks_score.py --inputs integration/replay/whole_asks_2026-10-08.jsonl \
        --bench BENCH_OUT --trees TREES_OUT --a TREE_OUT --jev CLASSES_OUT/jev.jsonl \
        --gate-b GATE_OUT/B/rows.jsonl --to integration/replay/whole_asks_score.md --json OUT.json

Standard library only.

THE OUTCOME OF AN INPUT, for each routing:
- old state, an input of one line: the answer recorded for that line (trees_score.py's walk: Jev
  today's class with its flips applied, A walked from the tree bench's v2 run, B2, C1, D2 and E1 from
  the trees bench, E1 with rules r2);
- old state, a merged input: whole_asks_bench.py's call on the merged text, in the old state;
- new state: whole_asks_bench.py's call on every input, in the new state.
`brain`, `respond`, `context` or `dropped`; a failed call is context (the filter fails open), a blank
input dropped.

THE LABELS are the inputs': the strongest of their lines' hindsight labels. Asks are `request` and
`answer_to_ai`, and belong at the brain; `talk_to_ai` at Respond; `context` in context; `noise`
dropped.

RESPOND B. An ask sent to Respond reaches the brain when Respond B (the gate bench's recorded run, 592
of the 843 lines) handed it up. A merged input is handed up when B handed up any of its lines it ran,
and answered in chat when B ran some of its lines and handed up none. An input none of whose lines B
ran is counted at B's hand-up rate for its label, on the lines B ran.

COST a 2.5 h session (the inputs cover all 843 lines), as trees_score.py counts it: brain calls x
$0.0085 + Respond calls x $0.0015 + Jev's input tokens x $42 per billion. A brain call is an input
sent straight to the brain, or one Respond B handed up. Jev's tokens: old state, the recorded tokens
of a single-line input and the new call's of a merged one; A's, from nine-question calls, scaled by
the share a three-question call took on the tree bench's sample (trees_score.py); new state, the
calls' own (A asks its three questions alone). LATENCY is the Jev calls behind the routing's answers:
the recorded calls and the merged inputs' for the old state, A's nine-question calls included.
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
from tree_score import clean, lines_from, p90, pct, read  # noqa: E402
from trees_questions import ROUTES  # noqa: E402

LABELS = ("request", "answer_to_ai", "talk_to_ai", "context", "noise")
ASKS = ("request", "answer_to_ai")
TALK = ("talk_to_ai", "noise")
OUTS = ("brain", "respond", "context", "dropped")
OUT_NAMES = {"brain": "brain", "respond": "Respond", "context": "context", "dropped": "dropped"}
WANT = {"request": "brain", "answer_to_ai": "brain", "talk_to_ai": "respond", "context": "context",
        "noise": "dropped"}
JEV_OUT = {"action_oriented": "brain", "conversational": "respond", "context": "context", "not_relevant": "dropped"}
BRAIN_USD = 0.0085
RESPOND_USD = 0.0015
JEV_USD = 0.000000042
# (key in ROUTES, the version the walk takes, the bench's routing name, the recorded rows' dir, name)
ROUTINGS = (("jev", "today", "jev", None, "Jev today"), ("A", "v2", "A", "v2", "A (v2)"),
            ("B", "B2", "B2", "B2", "B2"), ("C", "C1", "C1", "C1", "C1"), ("D", "D2", "D2", "D2", "D2"),
            ("E", "E1/r2", "E1", "E1", "E1 rules r2"))
STATES = ("old", "new")


# ------------------------------------------------------------------ the data


def route(key: str, version: str, row: dict | None) -> dict:
    """{out, path[, failed|blank]} for one call's row."""
    if row is None:
        return {"out": "context", "path": "missing", "failed": True}
    if row.get("blank"):
        return {"out": "dropped", "path": "blank", "blank": True}
    a = row.get("answers") or {}
    if row.get("error") or not a:
        return {"out": "context", "path": "failed", "failed": True}
    if key == "jev":
        cls = (a.get("line_1") or [None])[0]
        return {"out": JEV_OUT[cls], "path": cls} if cls in JEV_OUT else {"out": "context", "path": "failed",
                                                                            "failed": True}
    return ROUTES[key](a, version)


class Data:
    def __init__(self, args):
        self.inputs = read(Path(args.inputs))
        self.jev_lines = {r["id"]: r for r in lines_from(Path(args.jev))}
        trees, tree = Path(args.trees), Path(args.a)
        self.recorded = {"v2": {r["id"]: r for r in read(tree / "v2" / "rows.jsonl")}}
        for d in ("B2", "C1", "D2", "E1"):
            self.recorded[d] = {r["id"]: r for r in read(trees / d / "rows.jsonl")}
        sample = read(tree / "sample-v1" / "rows.jsonl")
        t3 = sum(r["calls"]["tree3"].get("tokens", 0) for r in sample)
        t9 = sum(r["calls"]["tree9"].get("tokens", 0) for r in sample)
        self.a_share = t3 / t9 if t9 else 1.0
        bench = Path(args.bench)
        self.bench = {(s, r): {x["id"]: x for x in read(bench / s / r / "rows.jsonl")}
                      for s in STATES for _, _, r, _, _ in ROUTINGS}
        gate = read(Path(args.gate_b))
        self.up = {r["id"]: bool(r["up"]) for r in gate}
        lab = {}
        for inp in self.inputs:
            for i, x in zip(inp["lines"], inp["labels"]):
                lab[i] = x
        self.rate = {}
        for name in LABELS:
            ran = [i for i in self.up if lab.get(i) == name]
            self.rate[name] = sum(self.up[i] for i in ran) / len(ran) if ran else 0.0

    def hand_up(self, inp: dict) -> bool | None:
        """Respond B's hand-up for the input: True, False, or None when B ran none of its lines."""
        known = [self.up[i] for i in inp["lines"] if i in self.up]
        return any(known) if known else None

    def outcome(self, state: str, routing: tuple, inp: dict) -> tuple[dict, float, int | None]:
        """(outcome, Jev tokens, ms) for one input."""
        key, version, bname, rdir, _ = routing
        if state == "old" and not inp["merged"]:
            i = inp["lines"][0]
            if key == "jev":
                r = self.jev_lines[i]
                return ({"out": JEV_OUT[r["final"]], "path": r["final"]}, r.get("input_tokens") or 0, r.get("ms"))
            row = self.recorded[rdir].get(i)
            tok = (row or {}).get("tokens", 0) * (self.a_share if key == "A" else 1)
            return route(key, version, row), tok, (row or {}).get("ms") or None
        row = self.bench[(state, bname)].get(inp["id"])
        tok = (row or {}).get("tokens", 0) * (self.a_share if key == "A" and state == "old" else 1)
        return route(key, version, row), tok, (row or {}).get("ms") or None


# ------------------------------------------------------------------ the tally


def tally(d: Data, inputs: list[dict], got: dict, tokens: float, ms: list[int]) -> dict:
    lab = {i["id"]: i["label"] for i in inputs}
    cm = Counter((lab[i["id"]], got[i["id"]]["out"]) for i in inputs)
    resp = [i for i in inputs if got[i["id"]]["out"] == "respond"]
    up = {i["id"]: d.hand_up(i) for i in resp}
    up_known = sum(1 for i in resp if up[i["id"]] is True)
    up_est = sum(d.rate[lab[i["id"]]] for i in resp if up[i["id"]] is None)
    brain = sum(1 for i in inputs if got[i["id"]]["out"] == "brain")

    def where(ids: list[str]) -> dict:
        c = Counter(got[i]["out"] for i in ids)
        rs = [i for i in ids if got[i]["out"] == "respond"]
        return {"n": len(ids), "brain": c["brain"], "respond": c["respond"],
                "respond_up": sum(1 for i in rs if up[i] is True),
                "respond_kept": sum(1 for i in rs if up[i] is False),
                "respond_unknown": sum(1 for i in rs if up[i] is None),
                "context": c["context"], "dropped": c["dropped"]}

    asks = where([i["id"] for i in inputs if lab[i["id"]] in ASKS])
    asks["reach"] = asks["brain"] + asks["respond_up"]
    asks["lost"] = asks["n"] - asks["reach"]
    merged_asks = where([i["id"] for i in inputs if lab[i["id"]] in ASKS and i["merged"]])
    merged_asks["reach"] = merged_asks["brain"] + merged_asks["respond_up"]
    talk = where([i["id"] for i in inputs if lab[i["id"]] in TALK])
    noise = where([i["id"] for i in inputs if lab[i["id"]] == "noise"])
    noise_chat = noise["respond_kept"] + sum(1 - d.rate["noise"] for i in resp
                                             if lab[i["id"]] == "noise" and up[i["id"]] is None)
    talk_up = sum(1 for i in resp if lab[i["id"]] in TALK and up[i["id"]] is True)
    right = sum(1 for i in inputs if got[i["id"]]["out"] == WANT[lab[i["id"]]])
    brain_calls = brain + up_known + up_est
    jev_usd = tokens * JEV_USD
    return {
        "inputs": len(inputs), "cm": {f"{a}/{o}": cm[(a, o)] for a in LABELS for o in OUTS},
        "asks": asks, "merged_asks": merged_asks, "talk": talk, "noise": noise, "noise_chat": noise_chat,
        "talk_up": talk_up, "right": right, "brain_direct": brain, "respond": len(resp),
        "respond_up_known": up_known, "respond_up_est": up_est, "brain_calls": brain_calls,
        "jev_tokens": tokens, "jev_usd": jev_usd, "usd_brain": brain_calls * BRAIN_USD,
        "usd_respond": len(resp) * RESPOND_USD, "usd": brain_calls * BRAIN_USD + len(resp) * RESPOND_USD + jev_usd,
        "failed": sum(1 for i in inputs if got[i["id"]].get("failed")),
        "ms_median": st.median(ms) if ms else 0, "ms_p90": p90(ms) if ms else 0, "calls": len(ms),
    }


# ------------------------------------------------------------------ tables


def heat_md(cm: dict) -> list[str]:
    out = ["| label | " + " | ".join(OUT_NAMES[o] for o in OUTS) + " | all |", "|---" * (len(OUTS) + 2) + "|"]
    for lab in LABELS:
        n = sum(cm[f"{lab}/{o}"] for o in OUTS)
        cells = []
        for o in OUTS:
            c = cm[f"{lab}/{o}"]
            cell = f"{c} ({pct(c, n)})"
            cells.append(f"**{cell}**" if o == WANT[lab] else cell)
        out.append(f"| {lab} | " + " | ".join(cells) + f" | {n} |")
    return out


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
            ring = ";outline:2px solid #16a34a;outline-offset:-2px" if o == WANT[lab] else ""
            cells.append(f'<td style="background:{bg};color:{fg};text-align:right{ring}">{c}<br>'
                         f'<small>{pct(c, n)}</small></td>')
        out.append(f"<tr><th>{lab}</th>" + "".join(cells) + f'<td style="text-align:right">{n}</td></tr>')
    out.append("</table>")
    return out


def lost_cells(a: dict) -> str:
    return (f"{a['respond_kept']} / {a['context']} / {a['dropped']}"
            + (f", +{a['respond_unknown']}" if a["respond_unknown"] else ""))


# ------------------------------------------------------------------ the report


def report(d: Data, w) -> dict:
    views = {"all": d.inputs, "second": [i for i in d.inputs if i["half"] == "second"]}
    summary: dict = {"a_share": d.a_share, "rate": d.rate, "routings": {}}
    gots = {}
    for state in STATES:
        for routing in ROUTINGS:
            name = f"{routing[4]}, {state} state"
            got, toks, mss = {}, {}, {}
            for inp in d.inputs:
                got[inp["id"]], toks[inp["id"]], mss[inp["id"]] = d.outcome(state, routing, inp)
            gots[name] = got
            for view, ins in views.items():
                ms = [mss[i["id"]] for i in ins if mss[i["id"]]]
                t = tally(d, ins, got, sum(toks[i["id"]] for i in ins), ms)
                summary["routings"].setdefault(name, {})[view] = t

    n_in = len(d.inputs)
    merged = [i for i in d.inputs if i["merged"]]
    c = Counter(i["label"] for i in d.inputs)
    w(f"{n_in} inputs from 843 lines: {len(merged)} merged inputs hold {sum(len(i['lines']) for i in merged)} lines. "
      "Labels: " + ", ".join(f"{c[x]} {x}" for x in LABELS) + f"; {sum(c[x] for x in ASKS)} asks. Second half: "
      f"{len(views['second'])} inputs, {sum(1 for i in views['second'] if i['label'] in ASKS)} asks.\n")
    w("Respond B's hand-up rate by label, on the lines it ran: "
      + ", ".join(f"{x} {100 * d.rate[x]:.0f}%" for x in LABELS) + ".\n")

    w("## Summary\n")
    w("*Asks* are `request` and `answer_to_ai` inputs. *Lost* is chat / context / dropped: answered in chat by "
      "Respond B, kept as context, dropped (+ to Respond on lines outside B's set). *Talk and noise to the brain* "
      "is `talk_to_ai` and `noise` inputs sent straight to the brain. *Noise to Respond*: noise inputs sent to "
      "Respond, and how many of them B answers in chat. Cost is a 2.5 h session. Latency is a Jev call's, median / "
      "p90.\n")
    w("| Routing | State | Asks reach the brain | direct + via B | Lost: chat / context / dropped | Talk and noise to "
      "the brain | Noise to Respond (answered in chat) | Inputs where they belong | Cost a session | Latency | "
      "Second half: asks reach | Second half: talk and noise to the brain |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for routing in ROUTINGS:
        for state in STATES:
            t = summary["routings"][f"{routing[4]}, {state} state"]["all"]
            h = summary["routings"][f"{routing[4]}, {state} state"]["second"]
            a, k, nz, ha, hk = t["asks"], t["talk"], t["noise"], h["asks"], h["talk"]
            w(f"| {routing[4]} | {state} | **{a['reach']} of {a['n']} ({pct(a['reach'], a['n'])})** | "
              f"{a['brain']} + {a['respond_up']} | {lost_cells(a)} | {k['brain']} of {k['n']} ({pct(k['brain'], k['n'])}) | "
              f"{nz['respond']} ({t['noise_chat']:.0f}) | {t['right']} ({pct(t['right'], t['inputs'])}) | "
              f"**${t['usd']:.2f}** | {t['ms_median']:.0f} / {t['ms_p90']:.0f} ms | "
              f"{ha['reach']} of {ha['n']} ({pct(ha['reach'], ha['n'])}) | {hk['brain']} of {hk['n']} "
              f"({pct(hk['brain'], hk['n'])}) |")

    w("\nThe held-out second half alone (the wording of the grouping and the trees was set on the first; cost is "
      "the half's):\n")
    w("| Routing | State | Asks reach the brain | direct + via B | Lost: chat / context / dropped | Talk and noise to "
      "the brain | Noise to Respond (answered in chat) | Inputs where they belong | Cost of the half | Latency |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for routing in ROUTINGS:
        for state in STATES:
            t = summary["routings"][f"{routing[4]}, {state} state"]["second"]
            a, k, nz = t["asks"], t["talk"], t["noise"]
            w(f"| {routing[4]} | {state} | **{a['reach']} of {a['n']} ({pct(a['reach'], a['n'])})** | "
              f"{a['brain']} + {a['respond_up']} | {lost_cells(a)} | {k['brain']} of {k['n']} ({pct(k['brain'], k['n'])}) | "
              f"{nz['respond']} ({t['noise_chat']:.0f}) | {t['right']} ({pct(t['right'], t['inputs'])}) | "
              f"${t['usd']:.2f} | {t['ms_median']:.0f} / {t['ms_p90']:.0f} ms |")

    w("\nThe merged inputs alone (the asks among them, where the old state's answers are new calls too):\n")
    w("| Routing | Old state: merged asks reach | New state: merged asks reach |\n|---|---|---|")
    for routing in ROUTINGS:
        o = summary["routings"][f"{routing[4]}, old state"]["all"]["merged_asks"]
        nn = summary["routings"][f"{routing[4]}, new state"]["all"]["merged_asks"]
        w(f"| {routing[4]} | {o['reach']} of {o['n']} | {nn['reach']} of {nn['n']} |")

    w("\n## What the new state moved\n")
    w("For each routing, the inputs whose outcome changed from the old state to the new one. *Asks gained* reach the "
      "brain only in the new state, *asks lost* only in the old; the noise columns count noise inputs sent to the "
      "brain or to Respond in one state and not the other.\n")
    w("| Routing | Inputs moved | Asks gained | Asks lost | Noise off the brain / onto it | Noise off Respond / onto it "
      "| Talk to the AI off Respond / onto it |\n|---|---|---|---|---|---|---|")

    def reach(g: dict, i: dict) -> bool:
        return g["out"] == "brain" or (g["out"] == "respond" and d.hand_up(i) is True)

    for routing in ROUTINGS:
        go, gn = gots[f"{routing[4]}, old state"], gots[f"{routing[4]}, new state"]
        moved = [i for i in d.inputs if go[i["id"]]["out"] != gn[i["id"]]["out"]]
        asks = [i for i in d.inputs if i["label"] in ASKS]
        gained = sum(1 for i in asks if reach(gn[i["id"]], i) and not reach(go[i["id"]], i))
        lost_ = sum(1 for i in asks if reach(go[i["id"]], i) and not reach(gn[i["id"]], i))

        def flow(label: str, out: str) -> str:
            ins = [i for i in d.inputs if i["label"] == label]
            off = sum(1 for i in ins if go[i["id"]]["out"] == out and gn[i["id"]]["out"] != out)
            on = sum(1 for i in ins if gn[i["id"]]["out"] == out and go[i["id"]]["out"] != out)
            return f"{off} / {on}"

        w(f"| {routing[4]} | {len(moved)} | {gained} | {lost_} | {flow('noise', 'brain')} | {flow('noise', 'respond')} | "
          f"{flow('talk_to_ai', 'respond')} |")
        summary["routings"][f"{routing[4]}, new state"]["moved"] = {"inputs": len(moved), "asks_gained": gained,
                                                                   "asks_lost": lost_}

    w("\n## Each routing\n")
    w("Rows are the inputs' labels, columns where the routing sent them, with the row's share; the cell where the "
      "label belongs is in bold. All inputs.\n")
    for routing in ROUTINGS:
        for state in STATES:
            name = f"{routing[4]}, {state} state"
            t = summary["routings"][name]["all"]
            a, k = t["asks"], t["talk"]
            w(f"\n### {name}\n")
            w("\n".join(heat_html(Counter(t["cm"]), f"{name}: label (rows) by outcome (columns)")))
            w("")
            w("\n".join(heat_md(Counter(t["cm"]))))
            w(f"\n- **Asks reaching the brain: {a['reach']} of {a['n']} ({pct(a['reach'], a['n'], 1)})**: {a['brain']} "
              f"straight, {a['respond_up']} through Respond B's hand-up.")
            w(f"- **Asks lost: {a['lost']}**: {a['respond_kept']} answered in chat by Respond, {a['context']} kept as "
              f"context, {a['dropped']} dropped"
              + (f", {a['respond_unknown']} to Respond outside B's set" if a["respond_unknown"] else "") + ".")
            w(f"- **Talk and noise sent to the brain: {k['brain']} of {k['n']} ({pct(k['brain'], k['n'], 1)})** "
              f"straight; {k['respond']} to Respond, of which B handed up {t['talk_up']}.")
            w(f"- **Noise to Respond: {t['noise']['respond']}**, about {t['noise_chat']:.0f} answered in chat.")
            w(f"- **Cost: ${t['usd']:.2f} a session**: {t['brain_direct']} brain calls straight, {t['respond']} "
              f"Respond calls, of which {t['respond_up_known']} handed up by B"
              + (f" and about {t['respond_up_est']:.1f} more outside B's set" if t["respond_up_est"] else "")
              + f" ({t['brain_calls']:.0f} brain calls ${t['usd_brain']:.2f}, Respond ${t['usd_respond']:.2f}), Jev "
                f"{t['jev_tokens']:,.0f} tokens ${t['jev_usd']:.3f}.")
            w(f"- Latency: {t['ms_median']:.0f} ms median, {t['ms_p90']:.0f} ms p90, over {t['calls']} Jev calls"
              + (f"; {t['failed']} calls failed (kept as context)" if t["failed"] else "") + ".")

    w("\n## Every ask that does not reach the brain\n")
    for routing in ROUTINGS:
        for state in STATES:
            name = f"{routing[4]}, {state} state"
            got = gots[name]
            lost = [i for i in d.inputs if i["label"] in ASKS and not (
                got[i["id"]]["out"] == "brain" or (got[i["id"]]["out"] == "respond" and d.hand_up(i) is True))]
            summary["routings"][name]["lost"] = [{"id": i["id"], "out": got[i["id"]]["out"], "path": got[i["id"]]["path"]}
                                                 for i in lost]
            w(f"\n**{name}**: {len(lost)}\n")
            for i in lost:
                g = got[i["id"]]
                hu = d.hand_up(i)
                where = OUT_NAMES[g["out"]] + (" (B answered in chat)" if g["out"] == "respond" and hu is False else
                                               " (outside B's set)" if g["out"] == "respond" else "")
                w(f"- `{i['at'][11:19]}` {i['who']} [{i['label']}{', ' + str(len(i['lines'])) + ' lines' if i['merged'] else ''}"
                  f"{', second half' if i['half'] == 'second' else ''}] → **{where}** `{g['path']}`: {clean(i['text'], 200)}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inputs", required=True, help="whole_asks_2026-10-08.jsonl")
    ap.add_argument("--bench", required=True, help="whole_asks_bench.py's OUT (old/, new/)")
    ap.add_argument("--trees", required=True, help="the trees bench's OUT (B2/, C1/, D2/, E1/rows.jsonl)")
    ap.add_argument("--a", required=True, help="the tree bench's OUT (v2/ and sample-v1/rows.jsonl)")
    ap.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev today")
    ap.add_argument("--gate-b", required=True, help="the gate bench's B/rows.jsonl: Respond B's hand-ups")
    ap.add_argument("--to", required=True, help="the report (.md)")
    ap.add_argument("--json", help="its numbers, as JSON")
    args = ap.parse_args()
    d = Data(args)
    missing = [(s, r[2], len([i for i in d.inputs if (s == "new" or i["merged"]) and i["id"] not in d.bench[(s, r[2])]]))
               for s in STATES for r in ROUTINGS]
    missing = [m for m in missing if m[2]]
    md: list[str] = []
    w = md.append
    w("# Routing on whole asks, scored\n")
    w("The 2026-10-08 lines with each split ask merged into one input (whole_asks_build.py), routed by Jev today "
      "and the five trees with Jev's old state and its new one (whole_asks_bench.py, state_v2.py).\n")
    if missing:
        w("**Missing calls: " + ", ".join(f"{s} {r} {n}" for s, r, n in missing) + "** (counted as failed).\n")
    summary = report(d, w)
    Path(args.to).write_text("\n".join(md) + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write("\n".join(md[:40]) + "\n")


if __name__ == "__main__":
    main()
