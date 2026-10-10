#!/usr/bin/env python3
"""Scores classes.py's two steps: Jev's four classes and Respond's hand-up, on the 2026-10-08 lines.

    python3 integration/replay/classes_score.py OUT [--sample 15]

Writes OUT/classes.md and OUT/classes.json and prints the first. Standard library only.

A line's label is what the conversation did with it (playtest.py): `task` (a task's context names
it), `chat` (answered in chat, no task), `nothing`, or `dropped` (Jev dropped it then, so the
conversation never saw it and nothing can be said of it).

- JEV: the labels against Jev's classes now (flips of a `not_relevant` line asked about again
  applied), and against Jev's then. Mistakes by kind: an ask (`task`) classed `conversational`,
  `context` or `not_relevant`; talk (`chat`) classed `action_oriented`; and, beside them, the lines
  the conversation did nothing with that are classed `action_oriented` now. Whether the menu and
  the asked actions fit in the 8,000-token state: the calls whose state carried the whole menu, and
  the brain's AI records of the three-hour window that made it in.
- RESPOND, on every line Jev now classes `conversational`: how often it hands up an ask (`task`)
  and how often a line that is not; that no line gets both a reply and a hand-up, from the model
  (before Respond's code drops a hand-up's reply) and after.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

CLASSES = ("action_oriented", "conversational", "context", "not_relevant")
LABELS = ("task", "chat", "nothing", "dropped")
PRICE = 0.000000042  # Jev, a dollar per input token


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def table(rows, cols, cell, head: str) -> list[str]:
    out = [f"| {head} | " + " | ".join(f"`{c}`" for c in cols) + " | all |", "|---" * (len(cols) + 2) + "|"]
    for r in rows:
        vals = [cell(r, c) for c in cols]
        out.append(f"| **{r}** | " + " | ".join(str(v) for v in vals) + f" | {sum(vals)} |")
    return out


def clean(text: str, n: int = 110) -> str:
    return " ".join(text.split())[:n].replace("|", "/")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--sample", type=int, default=15, help="examples listed per kind of mistake")
    args = ap.parse_args()
    out = Path(args.out)
    md: list[str] = []
    w = md.append
    summary: dict = {}

    rows, flips = [], {}
    for x in (out / "jev.jsonl").read_text().splitlines():
        r = json.loads(x)
        if "flip" in r:
            flips[r["flip"]] = r["to"]
        else:
            rows.append(r)
    for r in rows:
        r["final"] = flips.get(r["id"], r["class"])
        r["flipped"] = r["id"] in flips
    called = [r for r in rows if "estimate" in r]
    failed = [r for r in rows if r.get("failed")]
    w(f"# Jev's four classes on the 2026-10-08 lines: {len(rows)} lines, {len(called)} calls, "
      f"{len(failed)} failed (passed as context), {len(flips)} flipped when asked again")

    cm = Counter((r["truth"], r["final"]) for r in rows)
    w("\n## What the conversation did, against Jev's class now\n")
    md += table(LABELS, CLASSES, lambda a, b: cm[(a, b)], "")
    old = Counter((r["truth"], r["old"]) for r in rows)
    w("\nAnd against Jev's class then (three classes; `direct_ask` is `action_oriented`):\n")
    md += table(LABELS, ("action_oriented", "context", "not_relevant"), lambda a, b: old[(a, b)], "")
    oc = Counter((r["old"], r["final"]) for r in rows)
    w("\nJev's class then (rows) against now (columns):\n")
    md += table(("action_oriented", "context", "not_relevant"), CLASSES, lambda a, b: oc[(a, b)], "")

    asks = [r for r in rows if r["truth"] == "task"]
    w(f"\n## Mistakes, by kind\n")
    kinds = {
        "an ask classed `conversational`": [r for r in asks if r["final"] == "conversational"],
        "an ask classed `context`": [r for r in asks if r["final"] == "context"],
        "an ask classed `not_relevant`": [r for r in asks if r["final"] == "not_relevant"],
        "talk answered in chat classed `action_oriented`": [r for r in rows if r["truth"] == "chat"
                                                            and r["final"] == "action_oriented"],
        "a line the conversation did nothing with, classed `action_oriented`": [
            r for r in rows if r["truth"] == "nothing" and r["final"] == "action_oriented"],
        "a line dropped then, classed `action_oriented` now": [
            r for r in rows if r["truth"] == "dropped" and r["final"] == "action_oriented"],
    }
    w(f"Of {len(asks)} asks (lines that became a task): `action_oriented` "
      f"{sum(r['final'] == 'action_oriented' for r in asks)} now, "
      f"{sum(r['old'] == 'action_oriented' for r in asks)} then.\n")
    for k, v in kinds.items():
        w(f"- {k}: {len(v)}")
    summary["jev"] = {"lines": len(rows), "confusion": {f"{a}/{b}": cm[(a, b)] for a in LABELS for b in CLASSES},
                      "mistakes": {k: len(v) for k, v in kinds.items()}, "flips": len(flips), "failed": len(failed)}
    for k, v in kinds.items():
        if v:
            w(f"\n**{k[0].upper() + k[1:]}**, {min(len(v), args.sample)} of {len(v)}:\n")
            for r in v[:args.sample]:
                w(f"- `{r['at'][11:19]}` {r['who']} ({r['final']} {r.get('confidence') or 0:.2f}"
                  f"{', flipped' if r['flipped'] else ''}): {clean(r['text'])}")

    if called:
        whole = sum(1 for r in called if r["menu_full"])
        cut = [r for r in called if r["ai_kept"] < r["ai_avail"]]
        est = [r["estimate"] for r in called]
        backs = [r["back_min"] for r in called if r["history_kept"] < r["history_avail"]]
        meta = json.loads((out / "jev-meta.json").read_text()) if (out / "jev-meta.json").exists() else {}
        w("\n## The 8,000-token state\n")
        w(f"- the menu, {meta.get('menu_lines', '?')} actions, about {meta.get('menu_tokens', '?')} tokens: whole in "
          f"{whole} of {len(called)} calls.")
        w(f"- the state: median about {st.median(est):,.0f} tokens, at most {max(est):,}; the history was cut "
          f"in {sum(1 for r in called if r['history_kept'] < r['history_avail'])} calls, reaching back a "
          f"median {st.median(backs) if backs else 0:.0f} minutes then (shortest {min(backs) if backs else 0:.0f}).")
        w(f"- the brain's AI records (its tasks and results) of the last three hours: all in, in "
          f"{len(called) - len(cut)} of {len(called)} calls; "
          f"{sum(r['ai_kept'] for r in called):,} of {sum(r['ai_avail'] for r in called):,} records over every call; "
          f"in the last call {called[-1]['ai_kept']} of {called[-1]['ai_avail']}.")
        tokens = sum(r.get("input_tokens") or 0 for r in called)
        ms = [r["ms"] for r in called if "ms" in r]
        w(f"- Jev: {tokens:,} input tokens, ${tokens * PRICE:.3f}; a call: median {st.median(ms):.0f} ms.")
        summary["state"] = {"calls": len(called), "menu_whole": whole, "ai_all_in": len(called) - len(cut),
                            "estimate_max": max(est), "jev_cost": tokens * PRICE}

    path = out / "respond.jsonl"
    if path.exists():
        res = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        ok = [r for r in res if "error" not in r]
        w(f"\n# Respond on the {len(res)} lines Jev now classes `conversational`: {len(res) - len(ok)} failed\n")
        by = Counter((r["truth"], r["hand_up"]) for r in ok)
        w("| | Handed up | Replied | Said nothing | all |\n|---|---|---|---|---|")
        for lab in LABELS:
            up = by[(lab, True)]
            replied = sum(1 for r in ok if r["truth"] == lab and not r["hand_up"] and r["reply"].strip())
            quiet = sum(1 for r in ok if r["truth"] == lab and not r["hand_up"] and not r["reply"].strip())
            w(f"| **{lab}** | {up} | {replied} | {quiet} | {up + replied + quiet} |")
        n_ask = sum(1 for r in ok if r["truth"] == "task")
        up_ask = by[("task", True)]
        n_not = sum(1 for r in ok if r["truth"] != "task")
        up_not = sum(1 for r in ok if r["truth"] != "task" and r["hand_up"])
        raw_both = sum(1 for r in ok if r["hand_up"] and r.get("raw_reply", "").strip())
        both = sum(1 for r in ok if r["hand_up"] and r["reply"].strip())
        w(f"\n- an ask (a line that became a task) handed up: {up_ask} of {n_ask} ({pct(up_ask, n_ask)})")
        w(f"- a line that is not an ask handed up: {up_not} of {n_not} ({pct(up_not, n_not)})")
        w(f"- a reply and a hand-up both: {raw_both} as the model wrote them, {both} after Respond's code. "
          "Bar: never.")
        cost = [r["cost"] for r in ok]
        prompt = sum(r["prompt_tokens"] for r in ok)
        cached = sum(r["cached_tokens"] for r in ok)
        ms = [r["ms"] for r in ok]
        if ok:
            w(f"- ${sum(cost):.3f} in all, median ${st.median(cost):.4f} a line; {pct(cached, prompt)} of the "
              f"prompt cached; median {st.median(ms) / 1000:.1f} s.")
        missed = [r for r in ok if r["truth"] == "task" and not r["hand_up"]]
        extra = [r for r in ok if r["truth"] != "task" and r["hand_up"]]
        for name, v in (("Asks Respond answered itself", missed), ("Not asks, handed up", extra)):
            if v:
                w(f"\n**{name}**, {min(len(v), args.sample)} of {len(v)}:\n")
                for r in v[:args.sample]:
                    w(f"- `{r['at'][11:19]}` {r['who']} ({r['truth']}): {clean(r['text'], 100)} → "
                      f"{'hand up: ' + clean(r['reason'], 60) if r['hand_up'] else repr(clean(r['reply'], 80))}")
        summary["respond"] = {"lines": len(res), "errors": len(res) - len(ok), "asks": n_ask, "asks_up": up_ask,
                              "not_asks": n_not, "not_asks_up": up_not, "raw_both": raw_both, "both": both,
                              "cost": sum(cost)}

    text = "\n".join(md) + "\n"
    (out / "classes.md").write_text(text, encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(summary, indent=1))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
