#!/usr/bin/env python3
"""Scores taskcall.py's run of the orchestrator against the named-tasks bars.

    python3 integration/replay/taskcall_score.py OUT [OUT2 …]

Writes OUT/score.md and OUT/score.json for each and prints them. Standard library only.

- BY SCRIPT, the cleaner truth, first: the run's own record of what each conversation task's
  implementer did (tasks.jsonl's `run.result`: "NEW ice_beam", "edit ice_walk"). A task that edited
  a script an earlier task wrote is a follow-up to it: right when one of the line's parts named a
  task whose chain held a task that wrote or edited that script before it. A task that wrote a NEW
  script, edited nothing and is not a `Change to` is new: right when a part said "new". Tasks that
  ran commands only, or edited a script no earlier task wrote ("edit both"), are in neither. The conversation's labels below miss many follow-ups: it often
  wrote a fix or a change as a fresh task, with no `Change to T<x>` head.
- FOLLOW-UPS, by the conversation's labels: each of the conversation's `Change to T<x>` and `Cancel T<x>` tasks whose line the
  brain sent to an implementer, less those whose every target was written after the line reached
  the brain (it could not be named), and less those whose target no task's chain held (the brain
  ran actions for that line, so in its world there is no task to follow up; listed apart, with
  what the orchestrator said). Right when one of the line's parts named a task whose chain held
  a target, as the replay built the chains then. Bar: 85%.
- NEW TASKS: each of the conversation's fresh tasks whose line the brain sent to an implementer.
  Right when one of the line's parts said "new"; taken for a follow-up when none did. Bar: at most
  5% taken. Also counted: the parts on lines whose every task was fresh that went to a follow-up.
- Lines the conversation made no task of: how many parts went new and how many followed up.
- EDITS AND STOPS: every part the orchestrator answered `edit` or `stop` (a copy under RUNNING NOW
  changed or ended, no implementer), listed; not scored, since RUNNING NOW is unknown in this
  replay. One whose copy carries a task's name counts as naming that task above.
- ONE THING, ONE TASK: the follow-up scores above are read from the replay's own chains, so a
  model that puts many things into one task is scored against its own lumps. This is not: each
  task the replay made is set against the scripts its chain's conversation tasks wrote or edited
  (tasks.jsonl). Lumped: a task holding more than one script's work. Split: a script whose work is
  spread over more than one task. A line the brain gave one implementer part for two of the
  conversation's tasks lumps them whatever the orchestrator does.
- REPLIES: the reply the line's first part wrote, its length, and samples; the time of the first
  part's call, which the reply waits on.
- COST AND TIME: dollars in all and a call's median; a call's median and p90 time; the cached
  share, cached tokens over prompt tokens over every call.
- MISSES: every one, with the line, the answer and the truth.
"""

from __future__ import annotations

import json
import re
import statistics as st
import sys
from pathlib import Path

NEW, STOP, EDIT = "new", "stop", "edit"
RUNS = Path(__file__).resolve().parent / "tasks.jsonl"
WROTE = re.compile(r"\bNEW\s+([a-z][a-z0-9_]*)")
EDITED = re.compile(r"\b(?:edit|rerun)\s+([a-z][a-z0-9_]*(?:\s*\+\s*[a-z][a-z0-9_]*)*)")


def runs() -> tuple[list[str], dict[str, tuple[set[str], set[str]]]]:
    """The conversation's tasks in order, and each one's scripts (written, edited), from the run's
    results in tasks.jsonl."""
    order, out = [], {}
    for x in RUNS.read_text().splitlines():
        t = json.loads(x)
        result = str((t.get("run") or {}).get("result") or "")
        edited = {n.strip() for f in EDITED.findall(result) for n in f.split("+") if n.strip()}
        order.append(t["id"])
        out[t["id"]] = (set(WROTE.findall(result)), edited)
    return order, out


NOT_SCRIPTS = {"edit", "both"}  # words the results' shorthand puts where a script's name goes


def lumps(tasks: list[dict]) -> tuple[list[tuple[str, list[str], int]], dict[str, list[str]]]:
    """(lumped tasks: name, scripts, asks; split scripts: script -> names), by tasks.jsonl."""
    _, ran = runs()
    holders: dict[str, set[str]] = {}
    lumped = []
    for t in tasks:
        scripts = set()
        for x in t["chain"]:
            wrote, edited = ran.get(x, (set(), set()))
            scripts |= (wrote | edited) - NOT_SCRIPTS
        for sc in scripts:
            holders.setdefault(sc, set()).add(t["name"])
        if len(scripts) > 1:
            lumped.append((t["name"], sorted(scripts), len(t["history"])))
    return lumped, {sc: sorted(n) for sc, n in sorted(holders.items()) if len(n) > 1}


def by_script(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(follow-ups, follow-ups no task held, new) by what each task's implementer wrote or edited;
    each item {r, t, ok, right}, the chains rebuilt from the replay's own attribution, line by line."""
    order, ran = runs()
    pos = {tid: i for i, tid in enumerate(order)}
    owner: dict[str, str] = {}
    fu, none, new = [], [], []
    for r in rows:
        named = {p["name"] for p in r["parts"] if p.get("kind") and p["kind"] != NEW and p.get("name")}
        anew = any(p.get("kind") == NEW for p in r["parts"])
        for t in r["truth"]:
            if t["id"] not in ran:
                continue
            wrote, edited = ran[t["id"]]
            earlier = [x for x in order[:pos[t["id"]]]]
            prior = {sc for sc in edited if any(sc in ran[x][0] for x in earlier)}
            if prior:
                touched = [x for x in earlier if prior & (ran[x][0] | ran[x][1])]
                right = sorted({owner[x] for x in touched if x in owner})
                item = {"r": r, "t": dict(t, right=right, scripts=sorted(prior)), "ok": bool(named & set(right))}
                (fu if right else none).append(item)
            elif wrote and not edited and t["kind"] == "fresh":
                new.append({"r": r, "t": dict(t, scripts=sorted(wrote)), "ok": anew})
        for t in r["truth"]:
            if t.get("owner"):
                owner.setdefault(t["id"], t["owner"])
    return fu, none, new


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


def score(out: Path) -> tuple[str, dict]:
    rows = [json.loads(x) for x in (out / "taskcall.jsonl").read_text().splitlines() if x.strip()]
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    tasks = json.loads((out / "tasks.json").read_text()) if (out / "tasks.json").exists() else []
    parts = [p for r in rows for p in r["parts"]]
    calls = [p for p in parts if "ms" in p]
    errors = [p for p in parts if "error" in p]

    fu, fu_later, fu_no_task, fresh = [], [], [], []
    for r in rows:
        named = {p["name"] for p in r["parts"] if p.get("kind") and p["kind"] != NEW and p.get("name")}
        anew = any(p.get("kind") == NEW for p in r["parts"])
        for t in r["truth"]:
            item = {"r": r, "t": t}
            if t["kind"] == "fresh":
                item["ok"] = anew
                fresh.append(item)
            elif t["later"]:
                fu_later.append(item)
            elif not t["right"]:
                item["ok"] = not named
                fu_no_task.append(item)
            else:
                item["ok"] = bool(named & set(t["right"]))
                fu.append(item)
    all_fresh = [r for r in rows if r["truth"] and all(t["kind"] == "fresh" for t in r["truth"])]
    fresh_parts = [p for r in all_fresh for p in r["parts"] if p.get("kind")]
    fresh_parts_fu = [p for p in fresh_parts if p["kind"] != NEW]
    none_parts = [p for r in rows if not r["truth"] for p in r["parts"] if p.get("kind")]

    s_fu, s_none, s_new = by_script(rows)
    s_hit = sum(x["ok"] for x in s_fu)
    s_taken = sum(not x["ok"] for x in s_new)
    hit = sum(x["ok"] for x in fu)
    taken = sum(not x["ok"] for x in fresh)
    prompt = sum(p.get("prompt_tokens", 0) for p in calls)
    cached = sum(p.get("cached_tokens", 0) for p in calls)
    cost = [p.get("cost", 0) for p in calls]
    ms = [p["ms"] for p in calls]

    md: list[str] = []
    w = md.append
    w(f"# The orchestrator's replay: {meta.get('model', '?')} {meta.get('reasoning', '')}, prompt "
      f"{meta.get('version', '?')} ({meta.get('prompt_sha', '?')})")
    w(f"\n{len(rows)} lines, {len(parts)} implementer parts, {len(calls)} calls, {len(errors)} failed; "
      f"{len(tasks)} tasks made; head {meta.get('head', '?')}.")
    if errors:
        w("\nFailed: " + "; ".join(clip(p["error"], 120) for p in errors[:5]))
    first = [r["parts"][0] for r in rows if r["parts"] and "ms" in r["parts"][0]]
    fms = [p["ms"] for p in first]
    if calls:
        w(f"\n**Latency:** a call median {st.median(ms) / 1000:.1f} s, p90 {quantile(ms, 0.9) / 1000:.1f} s; the "
          f"line's first part, which the reply waits on: median {st.median(fms) / 1000:.1f} s, p90 "
          f"{quantile(fms, 0.9) / 1000:.1f} s.")
    w("\n**By script** (the run's record of what each task's implementer wrote or edited):\n")
    w("| | Right | Of | Share | Bar |\n|---|---|---|---|---|")
    w(f"| **Follow-ups** named the task holding the script it edited | {s_hit} | {len(s_fu)} | "
      f"{pct(s_hit, len(s_fu))} | at least 85%: {'met' if s_fu and s_hit / len(s_fu) >= 0.85 else 'NOT met'} |")
    w(f"| **New tasks** (a new script) called new | {len(s_new) - s_taken} | {len(s_new)} | "
      f"{pct(len(s_new) - s_taken, len(s_new))} | taken for follow-ups at most 5%: {pct(s_taken, len(s_new))}, "
      f"{'met' if s_new and s_taken / len(s_new) <= 0.05 else 'NOT met'} |")
    w(f"\n{len(s_none)} more edited a script no task here held (the brain ran its maker as an action).")
    w("\n**By the conversation's labels** (`Change to T<x>` is a follow-up, anything else fresh):\n")
    w("| | Right | Of | Share | Bar |\n|---|---|---|---|---|")
    w(f"| **Follow-ups** named the task whose chain holds the target | {hit} | {len(fu)} | {pct(hit, len(fu))} | "
      f"at least 85%: {'met' if fu and hit / len(fu) >= 0.85 else 'NOT met'} |")
    w(f"| **New tasks** called new | {len(fresh) - taken} | {len(fresh)} | {pct(len(fresh) - taken, len(fresh))} | "
      f"taken for follow-ups at most 5%: {pct(taken, len(fresh))}, "
      f"{'met' if fresh and taken / len(fresh) <= 0.05 else 'NOT met'} |")
    w(f"\n- Left out of the follow-ups: {len(fu_later)} whose target was written after the line reached the brain; "
      f"{len(fu_no_task)} whose target no task held (the brain ran actions for it), of which "
      f"{sum(x['ok'] for x in fu_no_task)} the orchestrator called new.")
    w(f"- On the {len(all_fresh)} lines whose every task was fresh, {len(fresh_parts_fu)} of {len(fresh_parts)} "
      f"parts went to a follow-up ({pct(len(fresh_parts_fu), len(fresh_parts))}).")
    w(f"- The lines the conversation made no task of: {len(none_parts)} parts, "
      f"{sum(p['kind'] == NEW for p in none_parts)} new, {sum(p['kind'] != NEW for p in none_parts)} follow-ups.")
    if calls:
        w(f"\n**Cost and time:** ${sum(cost):.4f} for {len(calls)} calls, a call median ${st.median(cost):.5f}, mean "
          f"${st.mean(cost):.5f}; a call median {st.median(ms) / 1000:.1f} s, p90 {quantile(ms, 0.9) / 1000:.1f} s, "
          f"slowest {max(ms) / 1000:.1f} s. **Cache:** {pct(cached, prompt)} of {prompt:,} prompt tokens cached; "
          f"a prompt grew from {calls[0].get('prompt_tokens', 0):,} to {calls[-1].get('prompt_tokens', 0):,} "
          f"tokens; completions {st.median([p.get('completion_tokens', 0) for p in calls]):.0f} tokens median.")

    def row_of(x: dict) -> str:
        r, t = x["r"], x["t"]
        answer = "; ".join(f"{p.get('kind', 'ERROR')} {p.get('name', '')}"
                           + (f" [stop {', '.join(p['answer']['stop'])}]" if p.get("kind") == STOP else "")
                           + f": {clip(p.get('shown', ''), 90)}" for p in r["parts"])
        truth = f"{t['id']}: {clip(t['task'], 80)}"
        if t["kind"] != "fresh":
            truth += f" → right: {', '.join(t['right']) or 'none'}"
        return f"| {r['n']} | {r['who']} | {clip(r['words'], 110)} | {answer} | {truth} |"

    head = "| # | Who | The line | The orchestrator: each part's answer and the brain's instruction | The truth |\n" \
           "|---|---|---|---|---|"
    def script_row(x: dict) -> str:
        r, t = x["r"], x["t"]
        answer = "; ".join(f"{p.get('kind', 'ERROR')} {p.get('name', '')}: {clip(p.get('shown', ''), 90)}"
                           for p in r["parts"])
        right = f" → right: {', '.join(t['right'])}" if t.get("right") else ""
        return (f"| {r['n']} | {r['who']} | {clip(r['words'], 110)} | {answer} | {t['id']} "
                f"({' '.join(t['scripts'])}): {clip(t['task'], 70)}{right} |")

    s_miss = [x for x in s_fu if not x["ok"]]
    w(f"\n## By script: follow-ups missed: {len(s_miss)}\n")
    if s_miss:
        w(head)
        w("\n".join(script_row(x) for x in s_miss))
    s_wrong = [x for x in s_new if not x["ok"]]
    w(f"\n## By script: new tasks taken for follow-ups: {len(s_wrong)}\n")
    if s_wrong:
        w(head)
        w("\n".join(script_row(x) for x in s_wrong))
    misses = [x for x in fu if not x["ok"]]
    w(f"\n## By the labels: follow-ups missed: {len(misses)}\n")
    if misses:
        w(head)
        w("\n".join(row_of(x) for x in misses))
    wrong_new = [x for x in fresh if not x["ok"]]
    w(f"\n## By the labels: new tasks taken for follow-ups: {len(wrong_new)}\n")
    if wrong_new:
        w(head)
        w("\n".join(row_of(x) for x in wrong_new))
    w(f"\n## By the labels: follow-ups to work no task held: {len(fu_no_task)}\n")
    if fu_no_task:
        w(head)
        w("\n".join(row_of(x) for x in fu_no_task))
    stops = [(r, p) for r in rows for p in r["parts"] if p.get("kind") in (STOP, EDIT)]
    w(f"\n## Edits and stops chosen: {len(stops)}\n")
    if stops:
        w("| # | The line | The brain's instruction | The answer | The conversation's tasks |\n|---|---|---|---|---|")
        for r, p in stops:
            a = p["answer"]
            what = (f"stop {', '.join(a['stop'])}" if p["kind"] == STOP else
                    f"edit {a.get('copy')}: " + ", ".join(f"{v['key']}={v['value']}" for v in a.get("values", [])))
            w(f"| {r['n']} | {clip(r['words'], 100)} | {clip(p['shown'], 100)} | {what} | "
              f"{'; '.join(t['id'] + ': ' + clip(t['task'], 60) for t in r['truth']) or 'none'} |")

    brain = {}
    if meta.get("lines") and Path(meta["lines"]).exists():
        brain = {b["n"]: b for b in (json.loads(x) for x in Path(meta["lines"]).read_text().splitlines() if x.strip())}

    def others(n: int) -> str:
        got = [q for q in brain.get(n, {}).get("parts", []) if q["to"] != "implementer"]
        return "; ".join(f"{q['to']}: {clip(q['instruction'] or q['part'], 60)}" for q in got) or "none"

    replies = [(r, r["parts"][0]["answer"].get("reply", "")) for r in rows
               if r["parts"] and "answer" in r["parts"][0] and "reply" in r["parts"][0]["answer"]]
    replies = [(r, x) for r, x in replies if x]
    if replies:
        lens = [len(x) for _, x in replies]
        w(f"\n## Replies: {len(replies)} lines\n")
        mixed = [(r, x) for r, x in replies if others(r["n"]) != "none"]
        plain = [(r, x) for r, x in replies if others(r["n"]) == "none"]
        w(f"Length: median {st.median(lens):.0f} characters, longest {max(lens)}; {sum(n > 80 for n in lens)} over 80, "
          f"{sum(n > 200 for n in lens)} over 200. {len(mixed)} of the lines also had action or talk parts. Five, "
          "spread over the session: three with other parts, two without:\n")
        w("| # | The line | Its action and talk parts | The brain's own reply then | The orchestrator's reply |\n"
          "|---|---|---|---|---|")
        picks = [mixed[int((i + 0.5) * len(mixed) / 3)] for i in range(3)] if len(mixed) >= 3 else mixed
        picks += [plain[int((i + 0.5) * len(plain) / 2)] for i in range(2)] if len(plain) >= 2 else plain
        for r, x in sorted(picks, key=lambda rx: rx[0]["fed"]):
            w(f"| {r['n']} | {clip(r['words'], 100)} | {others(r['n'])} | "
              f"{clip(brain.get(r['n'], {}).get('reply', '-'), 120)} | {clip(x, 220)} |")
    if fresh_parts_fu:
        w("\n## Parts on all-fresh lines that went to a follow-up\n")
        w("| # | The brain's instruction | Went to |\n|---|---|---|")
        for r in all_fresh:
            for p in r["parts"]:
                if p.get("kind") and p["kind"] != NEW:
                    w(f"| {r['n']} | {clip(p['shown'], 140)} | {p['name']} |")

    if tasks:
        lumped, split = lumps(tasks)
        w(f"\n## One thing, one task: {len(lumped)} tasks lumped, {len(split)} scripts split\n")
        w("Lumped (a task holding more than one script's work): " + ("; ".join(
            f"**{n}** ({k} asks): {', '.join(sc)}" for n, sc, k in lumped) or "none") + ".")
        w("\nSplit (a script spread over tasks): " + ("; ".join(
            f"{sc}: {', '.join(n)}" for sc, n in split.items()) or "none") + ".")

    busiest = sorted(tasks, key=lambda t: -len(t["history"]))[:5]
    w("\n## The busiest tasks: their names and descriptions\n")
    for t in busiest:
        w(f"- **{t['name']}** for {t['player']}, {len(t['history'])} asks, chain {', '.join(t['chain']) or '-'}: "
          f"first \"{clip(t['history'][0]['description'], 200)}\"; now \"{clip(t['description'], 260)}\"")
    w("\n## Every name\n")
    w(", ".join(f"`{t['name']}`" for t in tasks))

    text = "\n".join(md) + "\n"
    lumped, split = lumps(tasks) if tasks else ([], {})
    summary = {"lumped": len(lumped), "split": len(split), "by_script": {"follow_ups": {"right": s_hit, "of": len(s_fu), "no_task": len(s_none)},
                             "new": {"right": len(s_new) - s_taken, "of": len(s_new), "taken": s_taken}},
               "first_ms_median": st.median(fms) if fms else None, "first_ms_p90": quantile(fms, 0.9) if fms else None,
               "model": meta.get("model"), "reasoning": meta.get("reasoning"), "version": meta.get("version"),
               "prompt_sha": meta.get("prompt_sha"), "lines": len(rows), "parts": len(parts), "calls": len(calls),
               "errors": len(errors), "tasks": len(tasks),
               "follow_ups": {"right": hit, "of": len(fu), "later": len(fu_later), "no_task": len(fu_no_task),
                              "no_task_new": sum(x["ok"] for x in fu_no_task)},
               "new": {"right": len(fresh) - taken, "of": len(fresh), "taken": taken,
                       "fresh_line_parts": len(fresh_parts), "fresh_line_parts_followed": len(fresh_parts_fu)},
               "none_lines": {"parts": len(none_parts), "new": sum(p["kind"] == NEW for p in none_parts)},
               "stops": len(stops),
               "cost": sum(cost), "cost_median": st.median(cost) if cost else None,
               "ms_median": st.median(ms) if ms else None, "ms_p90": quantile(ms, 0.9) if ms else None,
               "cache": cached / prompt if prompt else None, "prompt_tokens": prompt}
    return text, summary


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__.split("\n\n")[1])
    for arg in sys.argv[1:]:
        out = Path(arg)
        text, summary = score(out)
        (out / "score.md").write_text(text, encoding="utf-8")
        (out / "score.json").write_text(json.dumps(summary, indent=1))
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
