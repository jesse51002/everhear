#!/usr/bin/env python3
"""Rescores Jev today and the five routing trees against the hindsight labels (labels_build.py), on
the 843 lines of the 2026-10-08 playtest, from their recorded answers: no routing call is made.

    python3 integration/replay/labels_score.py --labels integration/replay/labels_2026-10-08.jsonl \
        --trees TREES_OUT --a TREE_OUT --jev CLASSES_OUT/jev.jsonl --gate-b GATE_OUT/B/rows.jsonl \
        --repeat LABELS_REPEAT.jsonl --to integration/replay/labels_score.md

Standard library only. Writes the report to --to, and its numbers to --json.

THE ROUTINGS, each as trees_score.py walks it: Jev today (its flips applied), A (the tree bench's
v2, member A), B2, C1, D2, E1 with rules r2, and D2 with B's net (a line D2 drops or keeps as
context that B2 calls a request to the AI goes to Respond).

THE LABELS are labels_build.py's five, each with where it should go: `request` and `answer_to_ai`
to the brain, `talk_to_ai` to Respond, `context` to context, `noise` dropped. An ask (`request` or
`answer_to_ai`) that goes to Respond reaches the brain when Respond B handed that line up (the gate
bench's recorded decision, on 592 of the lines); one outside B's set is counted apart.

COST a 2.5 h session (the 843 lines), as trees_score.py counts it: brain calls x $0.0085 + Respond
calls x $0.0015 + Jev's input tokens as measured x $42 per billion. A brain call is a line sent
straight to the brain, or one Respond B handed up; a Respond line outside B's set counts at B's
hand-up rate for its label on the lines B ran. D2 with B's net is one Jev call with both question
sets: D2's tokens plus B2's questions, estimated at their JSON's bytes / 3.

THE OLD LABELS (playtest.py's `truth`: task, chat, nothing, dropped) are scored beside the new, so
the report can say which numbers moved and why.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tree_score import Gate, clean, pct, read  # noqa: E402
from trees_questions import B2, NAMES  # noqa: E402
from trees_score import Bench, outcomes  # noqa: E402

LABELS = ("request", "answer_to_ai", "talk_to_ai", "context", "noise")
ASKS = ("request", "answer_to_ai")
TALK = ("talk_to_ai", "noise")
OLD = ("task", "chat", "nothing", "dropped")
OUTS = ("brain", "respond", "context", "dropped")
OUT_NAMES = {"brain": "brain", "respond": "Respond", "context": "context", "dropped": "dropped"}
WANT = {"request": "brain", "answer_to_ai": "brain", "talk_to_ai": "respond", "context": "context",
        "noise": "dropped"}
BRAIN_USD = 0.0085
RESPOND_USD = 0.0015
JEV_USD = 0.000000042
ROUTINGS = (("jev", "today", "Jev today"), ("A", "v2", "A (v2)"), ("B", "B2", "B2"), ("C", "C1", "C1"),
            ("D", "D2", "D2"), ("E", "E1/r2", "E1 rules r2"), ("DB", "D2+B2", "D2 + B's net"))


# ------------------------------------------------------------------ the data


def labels_from(path: Path) -> dict[str, dict]:
    return {r["id"]: r for r in read(path)}


def b_question_tokens() -> int:
    """B2's questions, as one call would add them to D2's: their JSON's bytes / 3, rounded up."""
    qs = {k: {"type": "choice", "instructions": "Line [1], Eclipszr at 00:00:00: “….”. " + q, "criteria": dict(o)}
          for k, (q, o) in B2.items()}
    return math.ceil(len(json.dumps(qs, ensure_ascii=False).encode("utf-8")) / 3)


def routed(b: Bench, lines: list[dict], key: str, version: str) -> dict[str, dict]:
    if key != "DB":
        return outcomes(lines, b.rows_of(key, version), key, version)
    d = outcomes(lines, b.rows_of("D", "D2"), "D", "D2")
    bb = outcomes(lines, b.rows_of("B", "B2"), "B", "B2")
    out = {}
    for i, g in d.items():
        if g["out"] in ("dropped", "context") and bb[i]["out"] == "brain":
            out[i] = {"out": "respond", "path": f"D {g['path']} > B net"}
        else:
            out[i] = g
    return out


def jev_tokens(b: Bench, lines: list[dict], key: str, version: str) -> float:
    if key != "DB":
        return b.jev_cost(key, version, lines)[0]
    tok, calls = b.jev_cost("D", "D2", lines)
    return tok + calls * b_question_tokens()


# ------------------------------------------------------------------ the tally


class Rates:
    """Respond B's hand-up rate for each new label, on the lines B ran."""

    def __init__(self, gate: Gate, lab: dict[str, dict]):
        self.gate = gate
        gate.reply = {r["id"]: bool((r.get("reply") or "").strip()) and not r["up"] for r in gate.rows}
        self.rate = {}
        for name in LABELS:
            ran = [i for i in gate.up if i in lab and lab[i]["label"] == name]
            self.rate[name] = sum(gate.up[i] for i in ran) / len(ran) if ran else 0.0


def tally(lines: list[dict], got: dict[str, dict], lab: dict[str, dict], rates: Rates, tokens: float) -> dict:
    gate = rates.gate
    have = [r for r in lines if r["id"] in got]
    new = {r["id"]: lab[r["id"]]["label"] for r in have}
    cm = Counter((new[r["id"]], got[r["id"]]["out"]) for r in have)
    old = Counter((r["label"], got[r["id"]]["out"]) for r in have)
    resp = [r for r in have if got[r["id"]]["out"] == "respond"]
    up_known = sum(1 for r in resp if gate.up.get(r["id"]) is True)
    up_est = sum(rates.rate[new[r["id"]]] for r in resp if r["id"] not in gate.up)
    brain = sum(1 for r in have if got[r["id"]]["out"] == "brain")

    def where(ids: list[str]) -> dict:
        d = Counter(got[i]["out"] for i in ids)
        rs = [i for i in ids if got[i]["out"] == "respond"]
        return {"brain": d["brain"], "respond": d["respond"],
                "respond_up": sum(1 for i in rs if gate.up.get(i) is True),
                "respond_kept": sum(1 for i in rs if gate.up.get(i) is False),
                "respond_unknown": sum(1 for i in rs if i not in gate.up),
                "context": d["context"], "dropped": d["dropped"], "n": len(ids)}

    asks = where([r["id"] for r in have if new[r["id"]] in ASKS])
    asks["reach"] = asks["brain"] + asks["respond_up"]
    asks["lost"] = asks["n"] - asks["reach"]
    firm = where([r["id"] for r in have if new[r["id"]] in ASKS and not lab[r["id"]]["disputed"]])
    firm["reach"] = firm["brain"] + firm["respond_up"]
    noise_resp = [r["id"] for r in resp if new[r["id"]] == "noise"]
    noise_chat = (sum(1 for i in noise_resp if gate.reply.get(i)) +
                  sum(1 - rates.rate["noise"] for i in noise_resp if i not in gate.up))
    talk = where([r["id"] for r in have if new[r["id"]] in TALK])
    ctx = where([r["id"] for r in have if new[r["id"]] == "context"])
    old_asks = where([r["id"] for r in have if r["label"] == "task"])
    old_asks["reach"] = old_asks["brain"] + old_asks["respond_up"]
    old_talk = where([r["id"] for r in have if r["label"] in ("chat", "nothing")])
    right = sum(1 for r in have if got[r["id"]]["out"] == WANT[new[r["id"]]])
    brain_calls = brain + up_known + up_est
    jev_usd = tokens * JEV_USD
    return {
        "lines": len(have), "cm": {f"{a}/{o}": cm[(a, o)] for a in LABELS for o in OUTS},
        "cm_old": {f"{a}/{o}": old[(a, o)] for a in OLD for o in OUTS},
        "asks": asks, "firm_asks": firm, "noise_chat": noise_chat, "talk": talk, "context": ctx,
        "old_asks": old_asks, "old_talk": old_talk,
        "right": right, "brain_direct": brain, "respond": len(resp), "respond_up_known": up_known,
        "respond_up_est": up_est, "brain_calls": brain_calls, "jev_tokens": tokens, "jev_usd": jev_usd,
        "usd_brain": brain_calls * BRAIN_USD, "usd_respond": len(resp) * RESPOND_USD,
        "usd": brain_calls * BRAIN_USD + len(resp) * RESPOND_USD + jev_usd,
        "talk_brain_up": sum(1 for r in resp if new[r["id"]] in TALK and gate.up.get(r["id"]) is True),
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


def lost_line(a: dict) -> str:
    return (f"{a['respond_kept']} answered in chat by Respond, {a['context']} kept as context, "
            f"{a['dropped']} dropped" + (f", {a['respond_unknown']} to Respond outside B's set"
                                         if a["respond_unknown"] else ""))


# ------------------------------------------------------------------ the report


def label_section(lab: dict[str, dict], lines: list[dict], repeat: dict[str, dict] | None, w) -> dict:
    c = Counter(lab[r["id"]]["label"] for r in lines)
    d = Counter(lab[r["id"]]["label"] for r in lines if lab[r["id"]]["disputed"])
    cross = Counter((r["label"], lab[r["id"]]["label"]) for r in lines)
    any_ = next(iter(lab.values()))
    w(f"## The labels ({any_['version']}, rules {any_['rules']})\n")
    w("| label | lines | disputed | goes to |\n|---|---|---|---|")
    for name in LABELS:
        w(f"| {name} | {c[name]} | {d[name]} | {OUT_NAMES[WANT[name]]} |")
    w(f"| all | {len(lines)} | {sum(d.values())} | |")
    w("\nThe old labels (rows) against the new (columns):\n")
    w("| old | " + " | ".join(LABELS) + " | all |\n" + "|---" * (len(LABELS) + 2) + "|")
    for o in OLD:
        n = sum(cross[(o, x)] for x in LABELS)
        w(f"| {o} | " + " | ".join(str(cross[(o, x)]) for x in LABELS) + f" | {n} |")
    asks_old = sum(1 for r in lines if r["label"] == "task")
    asks_new = sum(c[x] for x in ASKS)
    w(f"\n**Asks: {asks_old} by the old labels, {asks_new} by the new.** Of the old `chat` and `nothing` "
      f"lines, {sum(cross[(o, x)] for o in ('chat', 'nothing') for x in ASKS)} are asks by the new labels; of "
      f"the lines the old filter dropped (the conversation never saw them), "
      f"{sum(cross[('dropped', x)] for x in ASKS)} are.")
    out = {"counts": dict(c), "disputed": dict(d), "asks_old": asks_old, "asks_new": asks_new}
    if repeat:
        both = [r for r in lines if r["id"] in repeat]
        same = sum(1 for r in both if repeat[r["id"]]["label"] == lab[r["id"]]["label"])
        g = {"request": "brain", "answer_to_ai": "brain", "talk_to_ai": "respond", "context": "context",
             "noise": "dropped"}
        same_g = sum(1 for r in both if g[repeat[r["id"]]["label"]] == g[lab[r["id"]]["label"]])
        rc = Counter(repeat[r["id"]]["label"] for r in both)
        w(f"\n**Run-to-run noise:** the same Jev questions asked again on all {len(both)} lines give the same label "
          f"on {same} ({pct(same, len(both), 1)}) and the same destination on {same_g} "
          f"({pct(same_g, len(both), 1)}); the second run counts " + ", ".join(f"{rc[x]} {x}" for x in LABELS)
          + ".")
        out["repeat"] = {"same": same, "same_dest": same_g, "lines": len(both), "counts": dict(rc)}
    return out


def report(b: Bench, lab: dict[str, dict], repeat: dict[str, dict] | None, w) -> dict:
    lines = [r for r in b.lines if r["id"] in lab]
    rates = Rates(b.gate, lab)
    summary: dict = {"labels": label_section(lab, lines, repeat, w), "rates": rates.rate, "routings": {}}
    w("\nRespond B's hand-up rate by new label, on the lines it ran: "
      + ", ".join(f"{x} {100 * rates.rate[x]:.0f}%" for x in LABELS) + ".")
    gots = {}
    for key, version, name in ROUTINGS:
        got = routed(b, lines, key, version)
        gots[key] = got
        t = tally(lines, got, lab, rates, jev_tokens(b, lines, key, version))
        summary["routings"][name] = t
        for half in ("second",):
            hl = [r for r in lines if r["half"] == half]
            th = tally(hl, {i: got[i] for i in (r["id"] for r in hl)}, lab, rates, jev_tokens(b, hl, key, version))
            summary["routings"][name][half] = {"asks": th["asks"], "talk": th["talk"], "usd": th["usd"],
                                               "old_asks": th["old_asks"], "old_talk": th["old_talk"]}

    ran = [i for i in b.gate.up if lab[i]["label"] == "noise"]
    noise_ran, noise_up = len(ran), sum(b.gate.up[i] for i in ran)
    noise_quiet = noise_ran - noise_up - sum(1 for i in ran if b.gate.reply.get(i))
    w("\n## Summary: all 843 lines, a 2.5 h session\n")
    w("Asks are `request` and `answer_to_ai`; talk is `talk_to_ai` and `noise`. *Undisputed asks* leaves out the "
      "asks whose council disagreed (the hand reading found disputed labels right about half the time). *Noise "
      f"answered in chat* is the noise lines sent to Respond that Respond B answered in chat rather than handing up "
      f"(on the {noise_ran} noise lines B ran, it handed up {noise_up} and stayed silent on {noise_quiet}). *Old* is "
      "the same routing scored against the old labels (an ask is a line a task names; talk is `chat` and "
      "`nothing`).\n")
    w("| Routing | Asks reach the brain | direct + via B | Asks lost: chat / context / dropped | Undisputed asks "
      "reach | Talk and noise to the brain | Noise answered in chat | Lines where they belong | Cost | *Old: asks "
      "reach* | *Old: talk to the brain* |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, _, name in ROUTINGS:
        t = summary["routings"][name]
        a, k, oa, ot = t["asks"], t["talk"], t["old_asks"], t["old_talk"]
        w(f"| {name} | **{a['reach']} of {a['n']} ({pct(a['reach'], a['n'])})** | {a['brain']} + {a['respond_up']} | "
          f"{a['respond_kept']} / {a['context']} / {a['dropped']}"
          + (f" (+{a['respond_unknown']} outside B's set)" if a["respond_unknown"] else "")
          + f" | {t['firm_asks']['reach']} of {t['firm_asks']['n']} ({pct(t['firm_asks']['reach'], t['firm_asks']['n'])})"
            f" | {k['brain']} of {k['n']} ({pct(k['brain'], k['n'])}) | {t['noise_chat']:.0f} | "
            f"{t['right']} ({pct(t['right'], t['lines'])}) | "
            f"**${t['usd']:.2f}** | *{oa['reach']} of {oa['n']} ({pct(oa['reach'], oa['n'])})* | "
            f"*{ot['brain']} of {ot['n']} ({pct(ot['brain'], ot['n'])})* |")
    w("\nThe held-out second half (the trees' wording was set on the first):\n")
    w("| Routing | Asks reach the brain | Talk and noise to the brain | Cost of the half | *Old: asks reach* | "
      "*Old: talk to the brain* |\n|---|---|---|---|---|---|")
    for _, _, name in ROUTINGS:
        t = summary["routings"][name]["second"]
        a, k, oa, ot = t["asks"], t["talk"], t["old_asks"], t["old_talk"]
        w(f"| {name} | {a['reach']} of {a['n']} ({pct(a['reach'], a['n'])}) | {k['brain']} of {k['n']} "
          f"({pct(k['brain'], k['n'])}) | ${t['usd']:.2f} | *{oa['reach']} of {oa['n']} ({pct(oa['reach'], oa['n'])})* | "
          f"*{ot['brain']} of {ot['n']} ({pct(ot['brain'], ot['n'])})* |")

    w("\n## Each routing\n")
    w("Rows are the new labels, columns where the routing sent the line, with the row's share; the cell where the "
      "label belongs is in bold.\n")
    for key, version, name in ROUTINGS:
        t = summary["routings"][name]
        a, k, cx = t["asks"], t["talk"], t["context"]
        title = name if key in ("jev", "DB") else f"{NAMES[key]}: {name}"
        w(f"\n### {title}\n")
        w("\n".join(heat_html(Counter(t["cm"]), f"{name}: new label (rows) by outcome (columns)")))
        w("")
        w("\n".join(heat_md(Counter(t["cm"]))))
        w(f"\n- **Asks reaching the brain: {a['reach']} of {a['n']} ({pct(a['reach'], a['n'], 1)})**: {a['brain']} "
          f"straight, {a['respond_up']} through Respond B's hand-up.")
        w(f"- **Asks lost: {a['lost']}**: {lost_line(a)}.")
        w(f"- **Talk and noise sent to the brain: {k['brain']} of {k['n']} ({pct(k['brain'], k['n'], 1)})** straight; "
          f"{k['respond']} to Respond, of which B handed up {t['talk_brain_up']}; about {t['noise_chat']:.0f} noise "
          f"lines answered in chat by Respond.")
        w(f"- Context lines: {cx['n']}, kept as context {cx['context']}, to the brain {cx['brain']}, to Respond "
          f"{cx['respond']}, dropped {cx['dropped']}.")
        w(f"- **Cost: ${t['usd']:.2f} a session**: {t['brain_direct']} brain calls straight, {t['respond']} Respond calls "
          f"of which {t['respond_up_known']} handed up by B"
          + (f" and about {t['respond_up_est']:.1f} more outside B's set" if t["respond_up_est"] else "")
          + f" ({t['brain_calls']:.0f} brain calls ${t['usd_brain']:.2f}, Respond ${t['usd_respond']:.2f}), Jev "
            f"{t['jev_tokens']:,.0f} tokens ${t['jev_usd']:.3f}.")

    w("\n## Every ask that does not reach the brain\n")
    for key, _, name in ROUTINGS:
        got = gots[key]
        lost = [r for r in lines if lab[r["id"]]["label"] in ASKS and not (
            got[r["id"]]["out"] == "brain" or (got[r["id"]]["out"] == "respond" and b.gate.up.get(r["id"]) is True))]
        w(f"\n**{name}**: {len(lost)}\n")
        for r in lost:
            g = got[r["id"]]
            where = OUT_NAMES[g["out"]] + (" (B answered in chat)" if g["out"] == "respond" and r["id"] in b.gate.up
                                           else " (outside B's set)" if g["out"] == "respond" else "")
            x = lab[r["id"]]
            w(f"- `{r['at'][11:19]}` {r['who']} [{r['label']} → {x['label']}{', disputed' if x['disputed'] else ''}] "
              f"→ **{where}** `{g['path']}`: {clean(r['text'], 160)}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--labels", required=True, help="labels_build.py's labels file")
    ap.add_argument("--trees", required=True, help="the trees bench's OUT (B2/, C1/, D2/, E1/rows.jsonl)")
    ap.add_argument("--a", required=True, help="the tree bench's OUT (v2/ and sample-v1/rows.jsonl)")
    ap.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev today")
    ap.add_argument("--gate-b", required=True, help="the gate bench's B/rows.jsonl: Respond B's hand-ups")
    ap.add_argument("--repeat", help="a second labels file from the same questions asked again (run-to-run noise)")
    ap.add_argument("--to", required=True, help="the report (.md)")
    ap.add_argument("--json", help="its numbers, as JSON")
    args = ap.parse_args()
    b = Bench(Path(args.trees), Path(args.a), Path(args.jev), Path(args.gate_b))
    b.gate.rows = read(Path(args.gate_b))
    lab = labels_from(Path(args.labels))
    missing = [r["id"] for r in b.lines if r["id"] not in lab]
    if missing:
        sys.exit(f"{len(missing)} lines have no label")
    md: list[str] = []
    w = md.append
    w("# Jev today and the routing trees, rescored on the hindsight labels\n")
    w("The 843 lines of the 2026-10-08 playtest, labelled by Jev with hindsight (labels_build.py: what the AI said "
      "and did in the 90 s after each line, three questions a line combined by written rules, checked by hand). "
      "Every routing is scored from the answers it gave on the trees bench; no routing call was made again.\n")
    summary = report(b, lab, labels_from(Path(args.repeat)) if args.repeat else None, w)
    text = "\n".join(md) + "\n"
    out = Path(args.to)
    out.write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
