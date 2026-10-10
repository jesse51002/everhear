#!/usr/bin/env python3
"""Scores lines.py's run against Prototype_Brain's bars and what the conversation did on 2026-10-08.

    python3 integration/replay/lines_score.py OUT [--sample 12]

Writes OUT/score.md and OUT/score.json and prints the first. Standard library only.

- ACTED OR TALK: a line the conversation made a task of (its context names the line) or not,
  beside whether the brain acted (any part not `talk`) or only talked. The old one call, with two
  minutes of context and ten tasks: 150, 22 / 62, 100.
- OVER-ACTING: the lines the conversation made no task of that the brain acted on (the old 62);
  every one is listed in score.md, since the bar is Jesse's to set.
- FOLLOW-UPS: the lines the conversation turned into `Change to T<n>` or `Cancel T<n>`, less those
  whose T<n> was written after the line reached the brain (it could not name a task not yet
  there). The brain names the right task when an implementer part's `task` is T<n> (strict); or a
  task of T<n>'s chain, since a `Change to T7` task was a new task only in the conversation's
  world, and in the brain's is a follow-up to T7's own implementer (chain); or when T<n> is
  anywhere in its parts (as linescore.py read it). Bar: 85%. The tasks' times and chains are read
  from the inbox (--inbox).
- ROUTE, on the lines the brain acted on whose own tasks (the context names the line first) are
  labelled: the route is the actions named, sorted, with `+implementer` when an implementer part
  is there too, or `implementer` alone; a label's `orchestrator` reads as `implementer`. Exact as
  linescore.py scored it: the route is one of the tasks' accepted routes; combined: a line the
  conversation split into several tasks is set against every combination of their accepted
  routes. Lost: the brain wrote actions only where every accepted route needs an implementer.
  Slow: an implementer where no accepted route needs one. Bars: 85%, at most 1 lost. Against the
  labels on the brain's action menu (selector-actions-2026-10-10.jsonl), and for comparison the
  older ones (selector.jsonl, its `either` left out).
- REPLIES: every line acted on gets a reply. Bar: every one.
- CACHE: cached tokens over prompt tokens, over every call. Bar: 90%.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

IMPL, TALK = "implementer", "talk"
FOLLOW_UP = re.compile(r"^\W*(change\s+to|cancel)\s+(T\d+)", re.I)
SESSION_H = 2.55  # 05:26 to 07:59


def route(parts: list[dict]) -> str:
    acts = sorted({p["to"] for p in parts if p["to"] not in (IMPL, TALK)})
    impl = any(p["to"] == IMPL for p in parts)
    if not acts:
        return IMPL if impl else TALK
    return ",".join(acts) + ("+" + IMPL if impl else "")


def norm(label: str) -> str:
    label = label.strip()
    if label in ("orchestrator", IMPL):
        return IMPL
    names, plus, _ = label.partition("+")
    names = sorted({n.strip() for n in names.split(",") if n.strip()})
    return ",".join(names) + ("+" + IMPL if plus else "")


def needs_impl(r: str) -> bool:
    return r == IMPL or r.endswith("+" + IMPL)


def combine(routes: tuple[str, ...]) -> str:
    names, impl = set(), False
    for r in routes:
        impl |= needs_impl(r)
        if r != IMPL:
            names |= {n for n in r.partition("+")[0].split(",") if n}
    if not names:
        return IMPL
    return ",".join(sorted(names)) + ("+" + IMPL if impl else "")


def chains(inbox: dict[str, dict]) -> dict[str, str]:
    """Each task id to its chain's first task: `Change to T7` and `Cancel T7` join T7's chain."""
    root: dict[str, str] = {}

    def find(t: str) -> str:
        while root.get(t, t) != t:
            t = root[t]
        return t
    for tid, t in inbox.items():
        m = FOLLOW_UP.match(t["task"])
        if m:
            a, b = find(tid), find(m.group(2))
            if a != b:
                root[a] = b
    return {tid: find(tid) for tid in inbox}


def follow_ups(rows: list[dict], inbox: dict[str, dict]) -> list[dict]:
    """One per `Change to T<n>` or `Cancel T<n>` task whose first-named line the brain was asked:
    what the brain did there, and on any other line the task names."""
    chain = chains(inbox)
    by_n = {r["n"]: r for r in rows}

    def named(r: dict) -> list[str]:
        return [p["task"] for p in r.get("parts", []) if p["to"] == IMPL and p["task"]]
    out = []
    for r in rows:
        for t in r["truth"]:
            m = FOLLOW_UP.match(t["task"])
            if not m:
                continue
            target = m.group(2)
            said = " ".join(f"{p['task']} {p['instruction']} {p['part']}" for p in r.get("parts", []))
            same = [x for x in named(r) if chain.get(x, x) == chain.get(target, target)]
            refs = [int(x) for x in re.findall(r"#(\d+)", inbox.get(t["id"], {}).get("context", ""))]
            others = [by_n[n] for n in refs if n in by_n and n != r["n"]]
            out.append({"n": r["n"], "task": t["id"], "target": target, "text": r["words"],
                        "route": r.get("route", "ERROR"), "named": named(r),
                        "later": inbox.get(target, {}).get("at", "") > r["fed"],
                        "strict": target in named(r), "chain": bool(same),
                        "loose": re.search(rf"\b{target}\b", said) is not None,
                        "any_line_hit": bool(same) or any(chain.get(x, x) == chain.get(target, target)
                                                          for o in others for x in named(o))})
    return out


def plus_s(at: str, seconds: float) -> str:
    t = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


def score_routes(rows: list[dict], key: str, skip=lambda t: False) -> dict:
    exact = comb = side = lost = slow = n = 0
    mis = Counter()
    lost_rows, slow_rows = [], []
    for r in rows:
        tasks = [t for t in r["truth"] if t.get(key) and not skip(t)]
        if not tasks or r["route"] == TALK:
            continue
        n += 1
        got = r["route"]
        each = [{norm(a) for a in t[key]} for t in tasks]
        accepts = set().union(*each)
        combos = {combine(c) for c in product(*each)}
        exact += got in accepts
        comb += got in combos | accepts
        ok_side = {needs_impl(a) for a in combos | accepts}
        side += needs_impl(got) in ok_side
        if not needs_impl(got) and ok_side == {True}:
            lost += 1
            lost_rows.append(r)
        if needs_impl(got) and ok_side == {False}:
            slow += 1
            slow_rows.append(r)
        if got not in combos | accepts:
            mis[(got, " / ".join(sorted(combos)))] += 1
    return {"n": n, "exact": exact, "combined": comb, "side": side, "lost": lost, "slow": slow,
            "top": mis.most_common(10), "lost_rows": lost_rows, "slow_rows": slow_rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--sample", type=int, default=0, help="list at most this many over-acted lines (0: all)")
    ap.add_argument("--inbox", default=str(Path.home() / ".vfg" / "orch" / "inbox.jsonl"),
                    help="the conversation's tasks, for the follow-ups' chains and times")
    args = ap.parse_args()
    out = Path(args.out)
    rows = [json.loads(x) for x in (out / "lines.jsonl").read_text().splitlines() if x.strip()]
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    inbox = {t["id"]: t for t in (json.loads(x) for x in Path(args.inbox).read_text().splitlines() if x.strip())
             if t["at"] < "2026-10-09"}
    ok = [r for r in rows if "error" not in r]
    err = [r for r in rows if "error" in r]
    for r in ok:
        r["route"] = route(r["parts"])
        r["acted"] = r["route"] != TALK
    md: list[str] = []
    w = md.append

    w(f"# The raw-line replay: {len(rows)} lines, {len(err)} failed calls")
    w(f"\n{meta.get('model', '?')} {meta.get('reasoning', '')}, head {meta.get('head', '?')}, "
      f"prompt {meta.get('prompt_sha', '?')}, {meta.get('actions', '?')} actions, owner {meta.get('owner', '?')}.")
    if err:
        w("\nFailed: " + "; ".join(f"#{r['n']}: {r['error'][:100]}" for r in err[:5]))

    cm = Counter((r["covered"], r["acted"]) for r in ok)
    w("\n## Acted or talk\n")
    w("| | The brain acted | The brain said talk |\n|---|---|---|")
    w(f"| **The conversation made a task** | {cm[(True, True)]} | {cm[(True, False)]} |")
    w(f"| **The conversation made none** | {cm[(False, True)]} | {cm[(False, False)]} |")
    w("\nThe old one call (2 minutes of context, 10 tasks), on 334: 150, 22 / 62, 100.")

    over = [r for r in ok if not r["covered"] and r["acted"]]
    kinds = Counter("implementer only" if r["route"] == IMPL else
                    "actions and an implementer" if needs_impl(r["route"]) else "actions only" for r in over)
    w(f"\n## Over-acting: {len(over)} lines the conversation made no task of, the brain acted on (the old 62)\n")
    w(", ".join(f"{k} {v}" for k, v in kinds.most_common()) + ".")
    fups = sum(1 for r in over if any(p["to"] == IMPL and p["task"] for p in r["parts"]))
    near = sum(1 for r in over if any(r["fed"] <= t["at"] <= plus_s(r["fed"], 120) for t in inbox.values()))
    w(f"Of them, {fups} follow up a task the brain named; and the conversation wrote some task in the "
      f"2 minutes after {near} of them, often the same ask, named by a neighbouring line (a hint, not a check).")
    w("\n| # | Who | Said | The brain's route | Its reply |\n|---|---|---|---|---|")
    for r in over[:args.sample or None]:
        said = " ".join(r["words"].split())[:140].replace("|", "/")
        w(f"| {r['n']} | {r['who']} | {said} | {r['route']} | {r['reply'][:90].replace('|', '/')} |")

    fus = follow_ups(ok + err, inbox)
    scored = [f for f in fus if not f["later"]]
    later = [f for f in fus if f["later"]]
    hit = sum(f["strict"] for f in scored)
    chain = sum(f["chain"] for f in scored)
    loose = sum(f["loose"] for f in scored)
    talked = sum(f["route"] == TALK for f in scored)
    w(f"\n## Follow-ups: {len(fus)} lines became `Change to T<n>` or `Cancel T<n>` (bar 85%; the old 23 of 38, 61%)\n")
    w(f"- {len(later)} are left out: their T<n> was written after the line reached the brain, so it could not "
      f"name it ({', '.join(f['task'] + ' for ' + f['target'] + ' on #' + str(f['n']) for f in later) or 'none'}).")
    w(f"- of the other {len(scored)}, the brain sent an implementer follow-up to that very task: {hit} "
      f"({pct(hit, len(scored))}); to it or another task of its chain (one `Change to` another, which in the "
      f"brain's world is one task and one implementer): {chain} ({pct(chain, len(scored))}); named it anywhere in "
      f"its parts, as linescore.py read it: {loose} ({pct(loose, len(scored))}); answered as talk: {talked}.")
    w(f"- by chain, counting a hit on any line the task's context names, not only the first: "
      f"{sum(f['any_line_hit'] for f in scored)} ({pct(sum(f['any_line_hit'] for f in scored), len(scored))}).")
    misses = [f for f in scored if not f["chain"]]
    if misses:
        w("\nMissed, by chain: " + "; ".join(f"#{f['n']} {f['task']} for {f['target']} "
                                              f"'{f['text'][:60]}': {f['route']}"
                                              + (f", named {', '.join(f['named'])}" if f["named"] else "")
                                              for f in misses))
    fu = len(scored)

    res = {}
    for name, key, skip in (("the action menu's labels", "accept", lambda t: False),
                            ("the action menu's labels, unsure ones left out", "accept", lambda t: bool(t.get("unsure"))),
                            ("the older labels (selector.jsonl)", "accept_old", lambda t: bool(t.get("either_old")))):
        s = score_routes(ok, key, skip)
        res[name] = s
        w(f"\n## Route, against {name}: {s['n']} lines the brain acted on\n")
        w(f"- exact, as linescore.py: {s['exact']} ({pct(s['exact'], s['n'])}); combined for a line split into "
          f"several tasks: {s['combined']} ({pct(s['combined'], s['n'])}). Bar 85%; the old 84.6%.")
        w(f"- implementer or not: {s['side']} ({pct(s['side'], s['n'])}); lost (actions only where an implementer "
          f"is needed): {s['lost']} (bar at most 1); slow (an implementer where an action fits): {s['slow']}")
        w("- top disagreements (the brain's route: the accepted): "
          + "; ".join(f"{g} : {a} ×{c}" for (g, a), c in s["top"]))
        if s["lost_rows"]:
            w("- lost: " + "; ".join(f"#{r['n']} {r['route']} for {', '.join(t['id'] for t in r['truth'])} "
                                     f"'{r['truth'][0]['task'][:60]}'" for r in s["lost_rows"]))

    acted = [r for r in ok if r["acted"]]
    replied = sum(1 for r in acted if r["reply"].strip())
    talk_replied = sum(1 for r in ok if not r["acted"] and r["reply"].strip())
    w(f"\n## Replies\n\n- {replied} of {len(acted)} lines acted on got a reply ({pct(replied, len(acted))}); "
      f"bar: every one; the old 22 of 212.\n- {talk_replied} of {len(ok) - len(acted)} answered as talk got one.")

    prompt = sum(r.get("prompt_tokens", 0) for r in ok)
    cached = sum(r.get("cached_tokens", 0) for r in ok)
    rates = [r["cached_tokens"] / r["prompt_tokens"] for r in ok if r.get("prompt_tokens")]
    warm = rates[1:]
    cost = [r.get("cost", 0) for r in ok]
    ms = [r["ms"] for r in rows]
    w(f"\n## Cache\n\n- {pct(cached, prompt)} of {prompt:,} prompt tokens cached over {len(ok)} calls; bar 90%.")
    w(f"- per call: median {st.median(rates):.0%}, the lowest {min(rates):.0%}; "
      f"{sum(1 for x in warm if x >= 0.9)} of {len(warm)} after the first at 90% or more." if rates else "- no calls")
    big = [r["prompt_tokens"] for r in ok if r.get("prompt_tokens")]
    if big:
        w(f"- the prompt grew from {big[0]:,} to {big[-1]:,} tokens.")
    w(f"\n## Cost and time\n\n- ${sum(cost):.2f} in all; a line: median ${st.median(cost):.4f}, mean "
      f"${st.mean(cost):.4f}, p90 ${quantile(cost, 0.9):.4f}; about ${sum(cost) / SESSION_H:.2f} an hour "
      f"for the session's {len(rows)} asks." if cost else "")
    w(f"- a call: median {st.median(ms) / 1000:.1f} s, p90 {quantile(ms, 0.9) / 1000:.1f} s, "
      f"slowest {max(ms) / 1000:.1f} s." if ms else "")

    text = "\n".join(md) + "\n"
    (out / "score.md").write_text(text, encoding="utf-8")
    summary = {"lines": len(rows), "errors": len(err), "acted_or_talk": {f"{'task' if k[0] else 'none'}/"
               f"{'acted' if k[1] else 'talk'}": v for k, v in cm.items()}, "over_acting": len(over),
               "follow_ups": {"n": fu, "strict": hit, "chain": chain, "loose": loose, "later": len(later)},
               "routes": {k: {x: v[x] for x in ("n", "exact", "combined", "side", "lost", "slow")} for k, v in res.items()},
               "replies": {"acted": len(acted), "replied": replied}, "cache": cached / prompt if prompt else None,
               "cost": sum(cost), "ms_median": st.median(ms) if ms else None}
    (out / "score.json").write_text(json.dumps(summary, indent=1))
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
