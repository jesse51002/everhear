#!/usr/bin/env python3
"""Scores the gate bench (gate_bench.py): every option on the same set, in two views.

    python3 integration/replay/gate_score.py OUT --db DIR/vfg/vfg.db [--set gate_set.jsonl]

Writes OUT/score.md and OUT/score.json and prints the first. Standard library only.

A line goes to the brain (`up`) when Respond hands it up or fails (the loop hands a failed call
up), or, for the council, when any of its four questions answers yes (E, F) or at least two do
(E2, F2). Its label is what the conversation did with it (playtest.py): `task` is an ask.

- **Asks caught**: task lines sent up, of all task lines (recall).
- **Talk sent up**: lines that are not a task sent up, of all of them; by label beside it.
- **Reply and hand-up both**: Respond's raw answer had `hand_up` true and a reply all the same.
- **Latency**: a decision's time: Respond's call; one Jev call (E); the slowest of four parallel
  Jev calls (F). Median and p90.
- **Cost**: dollars a line, mean, from OpenRouter's usage.cost or Jev's input tokens.

Two views: **all**, the whole set, and **conversational**, the lines Jev now classes
`conversational`: what reaches the gate in production. A third line per option, **end to end**,
is production's whole path on the set: a line reaches the brain when Jev now classes it
`action_oriented`, or classes it `conversational` and the gate sends it up.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {
    "A": "A. Respond today (DeepSeek V4 Flash, off)",
    "B": "B. Respond, decide first (DeepSeek, off)",
    "C": "C. B, reasoning low (DeepSeek)",
    "D": "D. B on Gemini 3.8 Flash, low",
    "E": "E. Jev council, one call: any yes",
    "E2": "E2. Jev council, one call: 2+ yes",
    "F": "F. Jev council, 4 calls: any yes",
    "F2": "F2. Jev council, 4 calls: 2+ yes",
}
QUESTIONS = ("ask", "complaint", "followup", "live")
LABELS = ("task", "chat", "nothing", "dropped")


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


def p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else 0


def clean(text: str, n: int = 140) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


def decisions(out: Path) -> dict[str, dict[str, dict]]:
    """{option: {line id: row with `up`}}, E2 and F2 read from E's and F's answers."""
    got: dict[str, dict[str, dict]] = {}
    for name in ("A", "B", "C", "D", "E", "F"):
        rows = read(out / name / "rows.jsonl")
        if not rows:
            continue
        got[name] = {r["id"]: r for r in rows}
        if name in ("E", "F"):
            got[name + "2"] = {r["id"]: {**r, "up": r["up2"]} for r in rows}
    return got


def score(rows: dict[str, dict], lines: list[dict]) -> dict:
    have = [(s, rows[s["id"]]) for s in lines if s["id"] in rows]
    asks = [r for s, r in have if s["label"] == "task"]
    talk = [(s, r) for s, r in have if s["label"] != "task"]
    respond = [r for _, r in have if "hand_up" in r or "error" in r]
    ms = [r["ms"] for _, r in have if r.get("ms")]
    out = {
        "lines": len(have), "of": len(lines),
        "asks": len(asks), "asks_up": sum(1 for r in asks if r["up"]),
        "talk": len(talk), "talk_up": sum(1 for _, r in talk if r["up"]),
        "talk_up_by": {lab: [sum(1 for s, r in talk if s["label"] == lab and r["up"]),
                             sum(1 for s, _ in talk if s["label"] == lab)] for lab in LABELS[1:]},
        "ms_median": st.median(ms) if ms else 0, "ms_p90": p90(ms),
        "cost": sum(r.get("cost", 0) for _, r in have),
        "errors": sum(1 for _, r in have if r.get("error") or r.get("errors")),
        "missed": [s["id"] for s, r in have if s["label"] == "task" and not r["up"]],
    }
    if respond:
        out["both"] = sum(1 for r in respond if r.get("hand_up") and (r.get("reply") or "").strip())
        out["respond"] = len(respond)
        firsts = [r.get("first") for r in respond if r.get("first")]
        out["first"] = {k: firsts.count(k) for k in sorted(set(firsts))}
    return out


def end_to_end(rows: dict[str, dict], lines: list[dict]) -> dict:
    """Production's path: Jev's action_oriented lines go to the brain; its conversational ones
    through the gate; the rest never get a live call."""
    def up(s):
        if s["jev"] == "action_oriented":
            return True
        return s["jev"] == "conversational" and rows.get(s["id"], {}).get("up", False)
    covered = all(s["id"] in rows for s in lines if s["jev"] == "conversational")
    asks = [s for s in lines if s["label"] == "task"]
    talk = [s for s in lines if s["label"] != "task"]
    return {"covered": covered, "asks": len(asks), "asks_up": sum(1 for s in asks if up(s)),
            "talk": len(talk), "talk_up": sum(1 for s in talk if up(s))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--db", required=True, help="the playtest's vfg.db, for the lines' words")
    ap.add_argument("--set", default=str(HERE / "gate_set.jsonl"))
    args = ap.parse_args()
    out = Path(args.out)
    bench = read(Path(args.set))
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    text = {i: (at, who, t) for i, at, who, t in con.execute("SELECT id, at, player_name, text FROM lines")}
    con.close()
    views = {"all": bench, "conversational": [s for s in bench if s["jev"] == "conversational"]}
    got = decisions(out)
    summary: dict = {"set": {v: len(ls) for v, ls in views.items()}, "options": {}}
    md: list[str] = []
    w = md.append
    counts = {v: {lab: sum(1 for s in ls if s["label"] == lab) for lab in LABELS} for v, ls in views.items()}
    w(f"# The gate bench: {len(bench)} lines, every option on the same ones\n")
    w(f"- **all**: {len(bench)} lines: " + ", ".join(f"{counts['all'][k]} {k}" for k in LABELS) + ".")
    w(f"- **conversational** (Jev now classes it so: what reaches the gate in production): "
      f"{len(views['conversational'])} lines: " + ", ".join(f"{counts['conversational'][k]} {k}" for k in LABELS) + ".")

    for view, lines in views.items():
        w(f"\n## {view.capitalize()}: {len(lines)} lines, {counts[view]['task']} asks\n")
        w("| Option | Lines | Asks caught | Talk sent up | chat / nothing / dropped up | Reply and hand-up both "
          "| Latency median, p90 | Cost a line | Failed |")
        w("|---|---|---|---|---|---|---|---|---|")
        for name, rows in got.items():
            s = score(rows, lines)
            summary["options"].setdefault(name, {})[view] = s
            by = " / ".join(f"{a} of {b}" for a, b in s["talk_up_by"].values())
            both = f"{s['both']} of {s['respond']}" if "both" in s else "-"
            w(f"| {NAMES[name]} | {s['lines']} of {s['of']} | **{s['asks_up']} of {s['asks']}, "
              f"{pct(s['asks_up'], s['asks'])}** | **{s['talk_up']} of {s['talk']}, {pct(s['talk_up'], s['talk'])}** "
              f"| {by} | {both} | {s['ms_median'] / 1000:.2f} s, {s['ms_p90'] / 1000:.2f} s "
              f"| ${s['cost'] / max(s['lines'], 1):.5f} | {s['errors']} |")

    w("\n## End to end: Jev now, then the gate on its conversational lines\n")
    w("A line reaches the brain when Jev now classes it `action_oriented`, or `conversational` and the gate "
      "sends it up.\n")
    w("| Option | Asks reaching the brain | Talk reaching the brain |\n|---|---|---|")
    for name, rows in got.items():
        e = end_to_end(rows, bench)
        summary["options"][name]["end_to_end"] = e
        if e["covered"]:
            w(f"| {NAMES[name]} | {e['asks_up']} of {e['asks']}, {pct(e['asks_up'], e['asks'])} "
              f"| {e['talk_up']} of {e['talk']}, {pct(e['talk_up'], e['talk'])} |")
    jev_only = {"asks_up": sum(1 for s in bench if s["label"] == "task" and s["jev"] == "action_oriented"),
                "talk_up": sum(1 for s in bench if s["label"] != "task" and s["jev"] == "action_oriented")}
    w(f"| Jev alone (no gate: nothing conversational goes up) | {jev_only['asks_up']} of {counts['all']['task']}, "
      f"{pct(jev_only['asks_up'], counts['all']['task'])} | {jev_only['talk_up']} of "
      f"{len(bench) - counts['all']['task']}, {pct(jev_only['talk_up'], len(bench) - counts['all']['task'])} |")

    for name in ("E", "F"):
        if name not in got:
            continue
        rows = got[name]
        w(f"\n## {NAMES[name].split(':')[0]}: each question on its own (all {len(rows)} lines it ran)\n")
        w("| Question | Asks it says yes to | Talk it says yes to |\n|---|---|---|")
        by_id = {s["id"]: s for s in bench}
        for q in QUESTIONS:
            a = [r for r in rows.values() if by_id[r["id"]]["label"] == "task"]
            t = [r for r in rows.values() if by_id[r["id"]]["label"] != "task"]
            ya = sum(1 for r in a if (r["answers"].get(q) or [""])[0] == "yes")
            yt = sum(1 for r in t if (r["answers"].get(q) or [""])[0] == "yes")
            w(f"| {q} | {ya} of {len(a)}, {pct(ya, len(a))} | {yt} of {len(t)}, {pct(yt, len(t))} |")

    w("\n## Respond: the field the model wrote first\n")
    for name in ("A", "B", "C", "D"):
        if name in got:
            f = summary["options"][name]["all"].get("first", {})
            w(f"- {NAMES[name]}: " + (", ".join(f"`{k}` {v}" for k, v in f.items()) or "not recorded"))

    w("\n## The asks each option missed (whole set)\n")
    for name, rows in got.items():
        missed = summary["options"][name]["all"]["missed"]
        w(f"\n**{NAMES[name]}**: {len(missed)}\n")
        for i in missed:
            at, who, t = text[i]
            jev = next(s["jev"] for s in bench if s["id"] == i)
            r = rows[i]
            why = r.get("reason") or ("no: " + ", ".join(k for k in QUESTIONS if k not in r.get("yes", []))
                                      if "answers" in r else "")
            w(f"- `{at[11:19]}` {who} (Jev: {jev}): {clean(t)}" + (f" — *{clean(why, 90)}*" if why else ""))

    text_md = "\n".join(md) + "\n"
    (out / "score.md").write_text(text_md, encoding="utf-8")
    (out / "score.json").write_text(json.dumps(summary, indent=1))
    sys.stdout.write(text_md)


if __name__ == "__main__":
    main()
