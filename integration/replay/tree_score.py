#!/usr/bin/env python3
"""Scores the tree bench (tree_bench.py): Jev today, the tree with no council and the tree with
council, on the same 2026-10-08 lines.

    python3 integration/replay/tree_score.py OUT --jev CLASSES_OUT/jev.jsonl --gate-b GATE_OUT/B/rows.jsonl \
        --final v1 --margin 0.2            # OUT/score.md, OUT/score.json
    python3 integration/replay/tree_score.py OUT --jev ... --gate-b ... --tune      # the first half only

Standard library only.

THE OUTCOMES: `brain` (a brain call), `respond` (a Respond call, which may hand up to the brain),
`noreply` (context with no reply: said to the AI, nothing to answer), `context` (kept as context,
no model call) and `dropped`. Jev today: `action_oriented` is brain, `conversational` Respond,
`context` context and `not_relevant` dropped, its flips (a `not_relevant` line asked again and
kept) applied.

THE WALK. No council: member A at each level. Council: each level's three members vote for the
outcome their answer maps onto, weighted by their confidence; the most weight wins. At level 2,
A's `ai_work_bad` keeps the line alone. A clash (a tie, or the top two within `--margin` of each
other) goes to the cheaper mistake: the AI at level 1, keep at level 2, Respond at level 3. A
missing answer is no vote; with no vote at a level, the cheaper mistake.

THE LABELS are classes.py's (playtest.py): `task` (the conversation made the line a task: an ask),
`chat`, `nothing`, `dropped` (Jev dropped it then). An ask that goes to Respond reaches the brain
when Respond B (the gate bench's option B, now Respond's own) handed that line up; B ran on 592
of the 843 lines, every ask, chat and nothing line among them, and 150 of the 401 dropped lines.
A dropped line outside the set is counted at B's rate on the dropped lines it ran.

THE HALVES: the lines in time order, the first 843 // 2 the first half, where the wording and the
margin were set; the second half is held out.

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
from tree_questions import A_ONLY, CHEAPER, KEEP_ALONE, MAP, MEMBERS, VERSIONS  # noqa: E402

LABELS = ("task", "chat", "nothing", "dropped")
OUTS = ("brain", "respond", "noreply", "context", "dropped")
OUT_NAMES = {"brain": "brain", "respond": "Respond", "noreply": "context, no reply", "context": "context kept",
             "dropped": "dropped"}
JEV_OUT = {"action_oriented": "brain", "conversational": "respond", "context": "context", "not_relevant": "dropped"}
BRAIN_USD = 0.0085
RESPOND_USD = 0.0015
JEV_USD = 0.000000042
MARGINS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def pct(a: float, b: float, d: int = 0) -> str:
    return f"{100 * a / b:.{d}f}%" if b else "-"


def p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else 0


def clean(text: str, n: int = 140) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


# ------------------------------------------------------------------ the data


def lines_from(jev_path: Path) -> list[dict]:
    """Every line, in time order, with its label, half, and Jev's class now (flips applied)."""
    rows, flips = [], {}
    for r in read(jev_path):
        if "flip" in r:
            flips[r["flip"]] = r["to"]
        else:
            rows.append(r)
    rows.sort(key=lambda r: (r["at"], r["id"]))
    h = len(rows) // 2
    for i, r in enumerate(rows):
        r["half"] = "first" if i < h else "second"
        r["final"] = flips.get(r["id"], r["class"])
        r["label"] = r["truth"]
    return rows


# ------------------------------------------------------------------ the walk


def vote(level: str, answers: dict, members=MEMBERS, margin: float = 0.0) -> tuple[str, bool, dict]:
    """(outcome, clash, weights) for one level."""
    if level == "l2":
        a = answers.get(KEEP_ALONE[0])
        if a and a[0] == KEEP_ALONE[1]:
            return "keep", False, {"keep": a[1]}
    w: dict[str, float] = {}
    for m in members:
        a = answers.get(f"{level}_{m}")
        if a:
            o = MAP[f"{level}_{m}"][a[0]]
            w[o] = w.get(o, 0.0) + a[1]
    if not w:
        return CHEAPER[level], True, w
    top = sorted(w.items(), key=lambda kv: -kv[1])
    if len(top) > 1 and top[0][1] - top[1][1] <= margin + 1e-9:
        return CHEAPER[level], True, w
    return top[0][0], False, w


def walk(answers: dict, council: bool, margin: float = 0.0) -> dict:
    """{out, l1, l2|l3, clash: [levels]} for one line's answers."""
    members = MEMBERS if council else ("a",)
    m = margin if council else 0.0
    l1, c1, _ = vote("l1", answers, members, m)
    clash = ["l1"] if c1 else []
    if l1 == "ai":
        l3, c3, _ = vote("l3", answers, members, m)
        clash += ["l3"] if c3 else []
        return {"out": l3, "l1": l1, "l3": l3, "clash": clash}
    l2, c2, _ = vote("l2", answers, members, m)
    clash += ["l2"] if c2 else []
    return {"out": "context" if l2 == "keep" else "dropped", "l1": l1, "l2": l2, "clash": clash}


def outcomes(lines: list[dict], tree: dict[str, dict] | None, variant: str, margin: float) -> dict[str, dict]:
    """{id: walk} for one variant: `jev`, `tree` (no council) or `council`."""
    got = {}
    for r in lines:
        if variant == "jev":
            got[r["id"]] = {"out": JEV_OUT[r["final"]], "l1": "ai" if r["final"] in ("action_oriented",
                                                                                    "conversational") else "not_ai",
                            "clash": []}
            continue
        t = (tree or {}).get(r["id"])
        if t is None:
            continue
        if t.get("blank"):
            got[r["id"]] = {"out": "dropped", "l1": "not_ai", "clash": [], "blank": True}
        elif t.get("error") or not t.get("answers"):
            got[r["id"]] = {"out": "context", "l1": "not_ai", "clash": [], "failed": True}   # the filter fails open
        else:
            got[r["id"]] = walk(t["answers"], variant == "council", margin)
    return got


# ------------------------------------------------------------------ Respond B


class Gate:
    """Respond B's recorded hand-ups; a line it did not run is counted at its rate for the label."""

    def __init__(self, path: Path):
        self.up = {r["id"]: bool(r["up"]) for r in read(path)}
        rows = read(path)
        self.rate = {lab: (sum(1 for r in rows if r["label"] == lab and r["up"]) /
                           max(1, sum(1 for r in rows if r["label"] == lab))) for lab in LABELS}


def tally(lines: list[dict], got: dict[str, dict], gate: Gate, jev_tokens: int, jev_calls: int) -> dict:
    have = [r for r in lines if r["id"] in got]
    cm = Counter((r["label"], got[r["id"]]["out"]) for r in have)
    resp = [r for r in have if got[r["id"]]["out"] == "respond"]
    known = [r for r in resp if r["id"] in gate.up]
    up_known = sum(1 for r in known if gate.up[r["id"]])
    up_est = sum(gate.rate[r["label"]] for r in resp if r["id"] not in gate.up)
    brain = sum(1 for r in have if got[r["id"]]["out"] == "brain")
    asks = [r for r in have if r["label"] == "task"]
    ask_resp = [r for r in asks if got[r["id"]]["out"] == "respond"]
    brain_calls = brain + up_known + up_est
    jev_usd = jev_tokens * JEV_USD
    return {
        "lines": len(have), "cm": {f"{a}/{b}": cm[(a, b)] for a in LABELS for b in OUTS},
        "asks": len(asks),
        "asks_brain": sum(1 for r in asks if got[r["id"]]["out"] == "brain"),
        "asks_respond": len(ask_resp),
        "asks_respond_up": sum(1 for r in ask_resp if gate.up.get(r["id"]) is True),
        "asks_respond_kept": sum(1 for r in ask_resp if gate.up.get(r["id"]) is False),
        "asks_respond_unknown": sum(1 for r in ask_resp if r["id"] not in gate.up),
        "asks_noreply": sum(1 for r in asks if got[r["id"]]["out"] == "noreply"),
        "asks_context": sum(1 for r in asks if got[r["id"]]["out"] == "context"),
        "asks_dropped": sum(1 for r in asks if got[r["id"]]["out"] == "dropped"),
        "brain_direct": brain, "respond": len(resp), "respond_unknown": len(resp) - len(known),
        "respond_up_known": up_known, "respond_up_est": up_est, "brain_calls": brain_calls,
        "jev_calls": jev_calls, "jev_tokens": jev_tokens, "jev_usd": jev_usd,
        "usd": brain_calls * BRAIN_USD + len(resp) * RESPOND_USD + jev_usd,
        "usd_brain": brain_calls * BRAIN_USD, "usd_respond": len(resp) * RESPOND_USD,
        "clash": dict(Counter(lv for r in have for lv in got[r["id"]].get("clash", []))),
        "failed": sum(1 for r in have if got[r["id"]].get("failed")),
    }


def jev_cost(lines: list[dict], tree: dict[str, dict] | None) -> tuple[int, int]:
    """(input tokens, calls) on these lines: Jev today's from jev.jsonl, the tree's from its rows."""
    if tree is None:
        return sum(r.get("input_tokens") or 0 for r in lines), sum(1 for r in lines if "estimate" in r)
    rows = [tree[r["id"]] for r in lines if r["id"] in tree]
    return sum(t.get("tokens", 0) for t in rows), sum(1 for t in rows if not t.get("blank"))


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
    up = t["asks_brain"] + t["asks_respond_up"]
    return (f"{t['asks_brain']} straight to the brain; {t['asks_respond']} to Respond, of which "
            f"{t['asks_respond_up']} handed up by B, {t['asks_respond_kept']} answered in chat"
            + (f", {t['asks_respond_unknown']} not in B's set" if t["asks_respond_unknown"] else "")
            + f"; {t['asks_noreply']} context with no reply, {t['asks_context']} context kept, "
              f"{t['asks_dropped']} dropped. **{up} of {t['asks']} reach the brain ({pct(up, t['asks'], 1)}).**")


# ------------------------------------------------------------------ the report


def tune(lines: list[dict], trees: dict[str, dict], gate: Gate, w) -> dict:
    first = [r for r in lines if r["half"] == "first"]
    w("## Tuning, on the first half only\n")
    w(f"{len(first)} lines, {sum(r['label'] == 'task' for r in first)} asks. Cost here is the first half's, not a "
      "session's.\n")
    w("| Version | Variant | Margin | Asks to brain | via B | Asks lost: context / dropped | Asks answered "
      "by Respond | Brain calls | Respond calls | Clashes l1 / l2 / l3 | Cost |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    res = {}
    for v, tree in trees.items():
        if not all(r["id"] in tree for r in first):
            continue
        tok, calls = jev_cost(first, tree)
        for variant, margins in (("tree", (0.0,)), ("council", MARGINS)):
            for m in margins:
                t = tally(first, outcomes(first, tree, variant, m), gate, tok, calls)
                res[f"{v}/{variant}/{m}"] = t
                cl = t["clash"]
                w(f"| {v} | {variant} | {m if variant == 'council' else '-'} | {t['asks_brain']} | "
                  f"{t['asks_respond_up']} | {t['asks_noreply'] + t['asks_context']} / {t['asks_dropped']} | "
                  f"{t['asks_respond_kept']} | {t['brain_calls']:.0f} | {t['respond']} | "
                  f"{cl.get('l1', 0)} / {cl.get('l2', 0)} / {cl.get('l3', 0)} | ${t['usd']:.2f} |")
    tok, calls = jev_cost(first, None)
    t = tally(first, outcomes(first, None, "jev", 0), gate, tok, calls)
    res["jev"] = t
    w(f"| Jev today | | | {t['asks_brain']} | {t['asks_respond_up']} | {t['asks_noreply'] + t['asks_context']} / "
      f"{t['asks_dropped']} | {t['asks_respond_kept']} | {t['brain_calls']:.0f} | {t['respond']} | | ${t['usd']:.2f} |")
    return res


def judge(lines: list[dict], tree: dict[str, dict], db: Path, n: int, margin: float, w) -> None:
    """Seeded samples of the held-out half to judge by hand, each line with what was said around
    it: lines labelled chat or nothing that level 1 sends to the AI, and level 2's keeps and drops."""
    import random
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    said = [(at, who, src, t) for at, who, src, t in con.execute(
        "SELECT at, player_name, source, text FROM lines ORDER BY at, id")]
    con.close()
    at_index = {at: i for i, (at, *_) in enumerate(said)}
    second = [r for r in lines if r["half"] == "second" and r["id"] in tree]
    for v in ("tree", "council"):
        g = outcomes(second, tree, v, margin)
        picks = {
            "level 1 says the AI, labelled chat or nothing": [r for r in second if r["label"] in ("chat", "nothing")
                                                              and g[r["id"]]["l1"] == "ai"],
            "level 2 keeps": [r for r in second if g[r["id"]]["l1"] == "not_ai" and g[r["id"]]["out"] == "context"],
            "level 2 drops": [r for r in second if g[r["id"]]["l1"] == "not_ai" and g[r["id"]]["out"] == "dropped"
                              and not g[r["id"]].get("blank")],
        }
        for name, pool in picks.items():
            chosen = sorted(random.Random(20261010).sample(pool, min(n, len(pool))), key=lambda r: r["at"])
            w(f"\n## {v}: {name}: {len(chosen)} of {len(pool)}\n")
            for k, r in enumerate(chosen, 1):
                a = tree[r["id"]]["answers"]
                i = at_index.get(r["at"], 0)
                w(f"{k}. `{r['at'][11:19]}` [{r['label']}] → {g[r['id']]['out']} · "
                  + " ".join(f"{q}={a[q][0]} {a[q][1]:.2f}" for q in sorted(a) if q[:2] in ("l1", "l2")))
                for at, who, src, t in said[max(0, i - 4):i]:
                    w(f"    - {at[11:19]} {'AI' if src == 'ai' else who}: {clean(t, 110)}")
                w(f"    - **{r['who']}: {clean(r['text'], 200)}**")
                for at, who, src, t in said[i + 1:i + 2]:
                    w(f"    - {at[11:19]} {'AI' if src == 'ai' else who}: {clean(t, 110)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--jev", required=True, help="classes.py's jev.jsonl: Jev today, the lines and their labels")
    ap.add_argument("--gate-b", required=True, help="the gate bench's B/rows.jsonl: Respond B's hand-ups")
    ap.add_argument("--final", default="v1", help="the wording version reported")
    ap.add_argument("--margin", type=float, default=0.0, help="the council's clash margin")
    ap.add_argument("--tune", action="store_true", help="only the first half's table, every version and margin")
    ap.add_argument("--judge", type=int, default=0, help="write OUT/judge.md: N seeded lines of each kind to "
                                                          "judge by hand (needs --db)")
    ap.add_argument("--db", help="the playtest's vfg.db, for --judge's surrounding lines")
    args = ap.parse_args()
    out = Path(args.out)
    lines = lines_from(Path(args.jev))
    gate = Gate(Path(args.gate_b))
    trees = {v: {r["id"]: r for r in read(out / v / "rows.jsonl")} for v in VERSIONS
             if (out / v / "rows.jsonl").exists()}
    md: list[str] = []
    w = md.append
    summary: dict = {"margin": args.margin, "final": args.final}

    if args.tune:
        summary["tune"] = tune(lines, trees, gate, w)
        text = "\n".join(md) + "\n"
        (out / "tune.md").write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return

    tree = trees[args.final]
    if args.judge:
        judge(lines, tree, Path(args.db), args.judge, args.margin, w)
        (out / "judge.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"{out / 'judge.md'}: {len(md)} lines")
        return
    views = {"all": lines, "second": [r for r in lines if r["half"] == "second"],
             "first": [r for r in lines if r["half"] == "first"]}
    have_all = all(r["id"] in tree for r in lines)
    variants = {"jev": "Jev today", "tree": f"Tree, no council ({args.final})",
                "council": f"Tree with council ({args.final}, margin {args.margin})"}
    got = {v: outcomes(lines, tree if v != "jev" else None, v, args.margin) for v in variants}
    sample = read(out / f"sample-{args.final}" / "rows.jsonl") or read(out / "sample-v1" / "rows.jsonl")
    # the no-council tree needs only A's three questions: their share of the nine's tokens, on the sample
    t3 = sum(r["calls"]["tree3"].get("tokens", 0) for r in sample)
    t9 = sum(r["calls"]["tree9"].get("tokens", 0) for r in sample)
    three = t3 / t9 if t9 else None
    batches = json.loads((out / f"batches-{args.final}.json").read_text()) \
        if (out / f"batches-{args.final}.json").exists() else {}
    second_from = min(r["at"] for r in views["second"])

    w(f"# The tree bench: {len(lines)} lines, three ways\n")
    w(f"Wording {args.final}; council margin {args.margin}. Halves: first {len(views['first'])} lines (wording "
      f"and margin set here), second {len(views['second'])} (held out)."
      + ("" if have_all else f" **The tree has only {sum(r['id'] in tree for r in lines)} lines.**"))

    for view in ("second", "all"):
        ls = views[view]
        w(f"\n## {'The held-out second half' if view == 'second' else 'All 843 lines'}: {len(ls)} lines, "
          f"{sum(r['label'] == 'task' for r in ls)} asks\n")
        for v, name in variants.items():
            tok, calls = jev_cost(ls, tree if v != "jev" else None)
            t = tally(ls, got[v], gate, tok, calls)
            summary.setdefault(view, {})[v] = t
            cm = Counter(t["cm"])
            w(f"\n### {name}\n")
            md.extend(heat_html(cm, f"{name}: label (rows) by outcome (columns), {view}"))
            w("")
            md.extend(heat_md(cm))
            w(f"\n- **Asks:** {asks_line(t)}")
            w(f"- **Cost:** {t['brain_direct']} brain calls straight, {t['respond']} Respond calls of which "
              f"{t['respond_up_known']} handed up by B" + (f" and about {t['respond_up_est']:.1f} more of the "
                                                         f"{t['respond_unknown']} not in B's set"
                                                         if t["respond_unknown"] else "")
              + f": {t['brain_calls']:.0f} brain calls ${t['usd_brain']:.2f}, Respond ${t['usd_respond']:.2f}, "
                f"Jev {t['jev_calls']} calls {t['jev_tokens']:,} tokens ${t['jev_usd']:.3f}. "
                f"**${t['usd']:.2f}**" + (" a session." if view == "all" else " this half."))
            if v == "tree" and three:
                t["jev_usd_3q"] = t["jev_usd"] * three
                t["usd_3q"] = t["usd"] - t["jev_usd"] + t["jev_usd_3q"]
                w(f"- Asked as production would ask it, A's three questions alone (the sample's 3-question calls "
                  f"took {100 * three:.0f}% of the 9-question calls' tokens): Jev about ${t['jev_usd_3q']:.3f}, "
                  f"**${t['usd_3q']:.2f}**.")
            if batches.get(v):
                times = batches[v]["times"]
                n = len(times) if view == "all" else sum(1 for x in times if x >= second_from)
                t["batches"] = n
                t["usd_batches"] = t["usd"] + n * BRAIN_USD
                w(f"- Beyond the formula: the brain's batch timer ({batches['batch_s']:.0f} s with no live read and "
                  f"something unread) would make **{n} batch calls** on the context this leaves it "
                  f"(${n * BRAIN_USD:.2f}): ${t['usd_batches']:.2f} with them.")
            if v == "council":
                w(f"- Clashes sent to the cheaper mistake: {t['clash']}.")
            if t["failed"]:
                w(f"- {t['failed']} lines failed (passed as context).")

    # the summary table
    w("\n## Side by side\n")
    w("| | View | Asks to brain direct | + via Respond B | = reach the brain | Asks in context | Asks dropped | "
      "Asks answered in chat | Brain calls | Respond calls | Jev | Cost | Batch calls | Cost with batches |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for view in ("second", "all"):
        for v, name in variants.items():
            t = summary[view][v]
            up = t["asks_brain"] + t["asks_respond_up"]
            w(f"| {name} | {view} | {t['asks_brain']} | {t['asks_respond_up']} | **{up} of {t['asks']}** | "
              f"{t['asks_noreply'] + t['asks_context']} | {t['asks_dropped']} | {t['asks_respond_kept']} | "
              f"{t['brain_calls']:.0f} | {t['respond']} | ${t['jev_usd']:.3f} | **${t['usd']:.2f}** | "
              f"{t.get('batches', '-')} | {'$%.2f' % t['usd_batches'] if 'usd_batches' in t else '-'} |")

    # every ask lost
    w("\n## Every ask that does not reach the brain (all 843 lines)\n")
    for v, name in variants.items():
        lost = [(r, got[v][r["id"]]) for r in lines if r["label"] == "task" and r["id"] in got[v]
                and not (got[v][r["id"]]["out"] == "brain" or (got[v][r["id"]]["out"] == "respond"
                                                              and gate.up.get(r["id"]) is True))]
        w(f"\n**{name}**: {len(lost)}\n")
        for r, g in lost:
            where = OUT_NAMES[g["out"]] + (" (B answered in chat)" if g["out"] == "respond" else "")
            w(f"- `{r['at'][11:19]}` {r['who']} [{r['half']}] → **{where}**: {clean(r['text'])}")

    # latency
    w("\n## Latency per Jev call\n")
    w("| Call | Where measured | Calls | Median | p90 |\n|---|---|---|---|---|")
    for kind, name in (("today1", "today's single question"), ("tree3", "A's three questions"),
                       ("tree9", "all nine questions")):
        ms = [r["calls"][kind]["ms"] for r in sample if r["calls"].get(kind, {}).get("ms")]
        if ms:
            w(f"| {name} | the 100-line sample, the three calls in turn | {len(ms)} | {st.median(ms):.0f} ms | "
              f"{p90(ms):.0f} ms |")
    ms = [t["ms"] for t in tree.values() if t.get("ms")]
    w(f"| all nine questions | the {args.final} run | {len(ms)} | {st.median(ms):.0f} ms | {p90(ms):.0f} ms |")
    ms = [r["ms"] for r in lines if r.get("ms")]
    w(f"| today's question, with the lines asked again | classes.py's run | {len(ms)} | {st.median(ms):.0f} ms | "
      f"{p90(ms):.0f} ms |")
    toks = {k: [r["calls"][k]["tokens"] for r in sample if r["calls"].get(k, {}).get("tokens")]
            for k in ("today1", "tree3", "tree9")}
    if toks["tree9"]:
        w("\nInput tokens a call on the sample, median: " + ", ".join(f"{k} {st.median(v):,.0f}" for k, v in
                                                                     toks.items() if v) + ".")

    # fairness: A's answers in the 3-question call and the 9-question call
    if sample:
        w("\n## Is the shared call fair to the no-council tree?\n")
        w("A's three questions asked alone (3 questions) against the same three in the 9-question call, on the "
          "same 100 lines of the first half. The 9-question call was asked twice: in the sample's own loop, "
          "and in the main run.\n")
        w("| Question | 3 vs 9 (sample) | 3 vs 9 (main run) | 9 (sample) vs 9 (main run) |\n|---|---|---|---|")
        main = trees.get("v1", {})
        for q in A_ONLY:
            pairs = [(r["calls"]["tree3"]["answers"].get(q), r["calls"]["tree9"]["answers"].get(q),
                      (main.get(r["id"]) or {}).get("answers", {}).get(q)) for r in sample]
            a = [x for x in pairs if x[0] and x[1]]
            b = [x for x in pairs if x[0] and x[2]]
            c = [x for x in pairs if x[1] and x[2]]
            w(f"| `{q}` | {sum(x[0][0] == x[1][0] for x in a)} of {len(a)} | {sum(x[0][0] == x[2][0] for x in b)} "
              f"of {len(b)} | {sum(x[1][0] == x[2][0] for x in c)} of {len(c)} |")
        same = sum(1 for r in sample if walk({k: v for k, v in r["calls"]["tree3"]["answers"].items()}, False)["out"]
                   == walk(r["calls"]["tree9"]["answers"], False)["out"])
        w(f"\nThe no-council outcome, walking A's answers: the same from both calls on {same} of {len(sample)} lines.")
        summary["fair"] = {"same_outcome": same, "lines": len(sample)}

    # level 1 and level 2 mistakes
    for v in ("tree", "council"):
        g = got[v]
        w(f"\n## {variants[v]}: level 1 and level 2\n")
        for lab in ("chat", "nothing", "dropped"):
            ls = [r for r in lines if r["label"] == lab and r["id"] in g]
            ai = [r for r in ls if g[r["id"]]["l1"] == "ai"]
            w(f"- level 1 says the AI for {len(ai)} of {len(ls)} `{lab}` lines ({pct(len(ai), len(ls))}); "
              f"Jev today says action_oriented or conversational for "
              f"{sum(1 for r in ls if r['final'] in ('action_oriented', 'conversational'))}.")
        not_ai = [r for r in lines if r["id"] in g and g[r["id"]]["l1"] == "not_ai" and not g[r["id"]].get("blank")]
        kept = [r for r in not_ai if g[r["id"]]["out"] == "context"]
        w(f"- level 2 saw {len(not_ai)} lines: kept {len(kept)}, dropped {len(not_ai) - len(kept)}. Kept, by label: "
          + ", ".join(f"{lab} {sum(1 for r in kept if r['label'] == lab)}" for lab in LABELS) + "; dropped: "
          + ", ".join(f"{lab} {sum(1 for r in not_ai if r['label'] == lab and r not in kept)}" for lab in LABELS))
        if v == "tree":
            ka = Counter(tree[r["id"]]["answers"].get("l2_a", ["-"])[0] for r in not_ai)
            w(f"- level 2 member A's answers on those lines: {dict(ka)}")
            ka = Counter(tree[r["id"]]["answers"].get("l3_a", ["-"])[0] for r in lines
                         if r["id"] in g and g[r["id"]]["l1"] == "ai")
            w(f"- level 3 member A's answers on the lines said to the AI: {dict(ka)}")

    text = "\n".join(md) + "\n"
    (out / "score.md").write_text(text, encoding="utf-8")
    (out / "score.json").write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
