#!/usr/bin/env python3
"""Scores integration/router_replay.py's runs against the router's bars (Prototype_Conversation_And_Router, _Test_).

    python3 integration/replay/router/score.py OUT [OUT …]        # each OUT/report.md and score.json
    python3 integration/replay/router/score.py --repo OUT …       # and integration/replay/router/report.md

Standard library only. Each OUT is one set (meta.json's `set`), its results.jsonl one row a call.

THE PLAYTEST (2026-10-08, rebuilt: router_replay.py's docstring):
- FOLLOW-UPS BY SCRIPT: each scored T<n> of kind `follow_up` is right only when the router answered
  `implementer` with one of `right`, the tasks holding an earlier T<n> that touched the script it
  edited, as they were when the line was fed. Bar: at least 36 of 41, 87.8%, as a share.
- NEW THINGS BY SCRIPT: each T<n> of kind `new` (a new script, nothing edited, not a `Change to`) is
  right when the router answered `new_loop` or `new_implementer`: it glued nothing onto a task.
  Those labelled an implementer's (the nearest to taskcall's 9) are counted apart.
- Left out, counted: follow-ups whose script no task held (the brain's actions made it), whose
  holder was written after the line was fed, and those the labels give to the brain's actions.
  A line with two scored T<n>s has one answer: it can be right for one of them at most.

THE NIGHT (2026-10-12/13): each row's `want`, computed by the replay at the moment of the call:
- `new`: right when `new_loop` or `new_implementer`.
- `ask`, `thing`: right when the router named a loop or task in `want.work` (the work the replay
  put the earlier lines of the same ask, or about the same thing, in; `loop` by its id, `implementer`
  by `task:<name>`); `nowhere` is counted apart as fine for a pure repeat (`nowhere_ok`), a miss
  otherwise. When that work did not exist (`fallback`: the earlier lines went nowhere), new or
  nowhere is right.
- `thing_or_new`: as `thing`, or new.
- `talk`: right when `nowhere`; a new loop is harmless (counted); a loop or a task is glued on.
- THE BARS: `repeat` (the 7 repeated asks: strict, and with `nowhere` for a pure repeat), `blizzard`
  (#114 to #113's work), and the T lines of kind `new` (different asks start their own). T lines
  and F/U lines are reported apart.

BOTH: cost (dollars in all, a call's median), latency (the call's ms as structured() measured it:
median and p90), the cached share (cached over prompt tokens, every call; and every call but the
first), completions, failed calls, and every miss with the line (short), what was expected, what
the router answered and its reason.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW = ("new_loop", "new_implementer")
BAR_PLAYTEST = 36 / 41
# Hand rulings on three follow-ups the script truth gets wrong, missed in every run so far. They are
# builder D1's (2026-10-13), NOT Jesse's: report.md gives the evidence. Each: the answers that are
# right (`to`: any answer with that choice; `tasks`: `implementer` naming one of them).
RULINGS = {
    "T92": {"line": 403, "to": ["new_loop", "new_implementer"], "tasks": ["keep_effects_krown"],
            "why": "a new standing rule (no damage), not a change to the speed effect keep_effects_krown kept; "
                   "its implementer only chose to build it in keep_effects"},
    "T135": {"line": 583, "to": [], "tasks": ["ice_beam_krown", "ice_walk_krown"],
             "why": "the line names the ice beam; tasks.jsonl's 'rerun ice_walk/ice_beam' lost ice_beam to "
                    "the scorer's parser, which splits on '+' only"},
    "T144": {"line": 619, "to": [], "tasks": ["spirit_bomb_eclipszr", "hollow_purple_eclipszr"],
             "why": "'make sure there's no cooldowns' names no power; the AI's last line to Eclipszr was about "
                    "Hollow Purple, and the conversation's own task also covered Hollow Purple's cooldown"},
}


def clip(text, n: int) -> str:
    text = " ".join(str(text or "").split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


def p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(0.9 * len(xs) + 0.5)) - 1))]


def targets(a: dict) -> list[str]:
    """Where the answer went: [`L3`], [`task:<name>`, …] (a line may follow several tasks up, one
    follow-up a task), [`new:<name>`], or []."""
    to = a.get("to", "")
    if to == "loop":
        return [a.get("loop", "")]
    if to == "implementer":
        return ["task:" + f.get("task", "") for f in a.get("follow_ups") or []]
    if to == "new_implementer":
        return ["new:" + ((a.get("hand_offs") or [{}])[0].get("name", ""))]
    return []


def target(a: dict) -> str:
    return ", ".join(targets(a))


def followed(a: dict) -> set[str]:
    """The tasks the answer follows up, by name."""
    return {t.removeprefix("task:") for t in targets(a) if t.startswith("task:")} if a.get("to") == "implementer" \
        else set()


def got_text(r: dict) -> str:
    if "error" in r:
        return f"FAILED: {clip(r['error'], 80)}"
    a = r.get("answer") or {}
    t = ", ".join(x.removeprefix("task:") for x in targets(a))
    more = " (cancel)" if a.get("to") == "implementer" and any(f.get("cancel") for f in a.get("follow_ups") or []) \
        else ""
    reply = f" “{clip(a.get('reply'), 60)}”" if a.get("to") == "nowhere" and a.get("reply") else ""
    return f"{a.get('to', '?')} {t}{more}{reply}".strip()


def measures(rows: list[dict]) -> dict:
    calls = [r for r in rows if "answer" in r and not r.get("skipped") and not r.get("stand_in")]
    failed = [r for r in rows if "error" in r]
    ms = [r["ms"] for r in calls if r.get("ms")]
    cost = [r.get("cost", 0) for r in calls]
    prompt = sum(r.get("prompt_tokens", 0) for r in calls)
    cached = sum(r.get("cached_tokens", 0) for r in calls)
    rest = calls[1:]
    prompt2, cached2 = sum(r.get("prompt_tokens", 0) for r in rest), sum(r.get("cached_tokens", 0) for r in rest)
    return {"calls": len(calls), "failed": len(failed), "cost": sum(cost),
            "cost_median": st.median(cost) if cost else None,
            "ms_median": st.median(ms) if ms else None, "ms_p90": p90(ms), "ms_max": max(ms) if ms else None,
            "prompt_tokens": prompt, "cache": cached / prompt if prompt else None,
            "cache_after_first": cached2 / prompt2 if prompt2 else None,
            "prompt_first": calls[0].get("prompt_tokens") if calls else None,
            "prompt_last": calls[-1].get("prompt_tokens") if calls else None,
            "completion_median": st.median([r.get("completion_tokens", 0) for r in calls]) if calls else None,
            "to": {k: sum((r.get("answer") or {}).get("to") == k for r in calls)
                   for k in ("new_loop", "loop", "new_implementer", "implementer", "nowhere")}}


def measures_md(m: dict) -> str:
    if not m["calls"]:
        return "No model calls (a dry run before router.py, or nothing ran)."
    s = (f"**{m['calls']} calls, {m['failed']} failed. Cost** ${m['cost']:.4f}, a call median "
         f"${m['cost_median']:.5f}. **Latency** median {m['ms_median'] / 1000:.2f} s, p90 {m['ms_p90'] / 1000:.2f} s, "
         f"slowest {m['ms_max'] / 1000:.2f} s. **Cache** {pct(round((m['cache'] or 0) * 1000), 1000)} of "
         f"{m['prompt_tokens']:,} prompt tokens cached")
    if m["cache_after_first"] is not None:
        s += f" ({pct(round(m['cache_after_first'] * 1000), 1000)} after the first call)"
    s += (f"; a prompt grew from {m['prompt_first']:,} to {m['prompt_last']:,} tokens; completions "
          f"{m['completion_median']:.0f} tokens median. **Choices:** "
          + ", ".join(f"{k} {v}" for k, v in m["to"].items()) + ".")
    return s


# ====================================================================== the playtest

def needs(x: dict) -> list[set[str]]:
    """What a follow-up needs followed up: for each script it edited that a task held, the tasks
    holding it then (any one of them will do); the same set once. A run scored before
    `right_by_script` was kept: its `right`, as one need."""
    by = x.get("right_by_script")
    if not by:
        return [set(x["right"])]
    out: list[set[str]] = []
    for holders in by.values():
        if set(holders) not in out:
            out.append(set(holders))
    return out


def covered(x: dict, done: set[str]) -> tuple[int, int]:
    """(needs met, needs) for a follow-up, given the tasks an answer followed up."""
    n = needs(x)
    return sum(bool(h & done) for h in n), len(n)


def score_playtest(rows: list[dict], left: dict | None = None) -> tuple[list[str], dict]:
    fu, new, lines2 = [], [], []
    left = dict(left or {"follow_up_no_task": 0, "follow_up_later": 0, "follow_up_actions": 0})
    counted = not any(left.values())   # the set's own counts (playtest_set.json) cover every line
    for r in rows:
        if r.get("skipped"):
            continue
        a = r.get("answer") or {}
        done = followed(a)
        items = [x for x in r["truth"] if x["kind"] == "follow_up"]
        line_needs: list[set[str]] = []
        for x in items:
            for h in needs(x):
                if h not in line_needs:
                    line_needs.append(h)
        if len(line_needs) > 1:   # a line about two tasks (or more): right when each is followed up
            met = sum(bool(h & done) for h in line_needs)
            lines2.append({"r": r, "needs": line_needs, "met": met,
                           "verdict": "right" if met == len(line_needs) else "partly" if met else "miss"})
        for x in r["truth"]:
            if x["kind"] == "follow_up":
                met, of = covered(x, done)
                ok = met == of
                rule = RULINGS.get(x["id"])
                ruled = ok if rule is None else (a.get("to") in rule["to"] or bool(done & set(rule["tasks"])))
                fu.append({"r": r, "x": x, "ok": ok, "partly": 0 < met < of, "ruled": ruled})
            elif x["kind"] == "new":
                new.append({"r": r, "x": x, "ok": a.get("to") in NEW})
            elif x["kind"] in left and counted:
                left[x["kind"]] += 1
    hit = sum(i["ok"] for i in fu)
    part = sum(i["partly"] for i in fu)
    ruled = sum(i["ruled"] for i in fu)
    nh = sum(i["ok"] for i in new)
    impl = [i for i in new if i["x"].get("implementer")]
    md = []
    w = md.append
    share = hit / len(fu) if fu else 0
    w("| | Right | Of | Share | Bar |\n|---|---|---|---|---|")
    w(f"| **Follow-ups by script**: `implementer`, following up the task holding each script it edited | {hit} | "
      f"{len(fu)} | {pct(hit, len(fu))} | at least 36 of 41 (87.8%): "
      f"**{'met' if fu and share >= BAR_PLAYTEST else 'NOT met'}** |")
    w(f"| … partly right: a follow-up on two tasks' scripts, one of them followed up | {part} | {len(fu)} | "
      f"{pct(part, len(fu))} | |")
    w(f"| … with D1's hand rulings on #403, #583 and #619 (not Jesse's: report.md) | {ruled} | {len(fu)} | "
      f"{pct(ruled, len(fu))} | at least 87.8%: **{'met' if fu and ruled / len(fu) >= BAR_PLAYTEST else 'NOT met'}** |")
    w(f"| **New things by script**: `new_loop` or `new_implementer` (nothing glued onto a task) | {nh} | {len(new)} | "
      f"{pct(nh, len(new))} | taskcall's: at most 5% taken for a follow-up: "
      f"{pct(len(new) - nh, len(new))}, **{'met' if new and (len(new) - nh) / len(new) <= 0.05 else 'NOT met'}** |")
    w(f"| … of them, the ones the labels give an implementer | {sum(i['ok'] for i in impl)} | {len(impl)} | "
      f"{pct(sum(i['ok'] for i in impl), len(impl))} | |")
    w(f"\nLeft out of the follow-ups: {left['follow_up_no_task']} whose script no task held (the brain's actions "
      f"made it), {left['follow_up_later']} whose holder was written after the line was fed, "
      f"{left['follow_up_actions']} the labels give to the brain's actions.")
    if lines2:
        w(f"\nLines about two tasks or more ({len(lines2)}): right when each is followed up, partly when some are: "
          + "; ".join(f"#{i['r']['n']} {i['verdict']} ({i['met']} of {len(i['needs'])}: "
                      + " + ".join("/".join(sorted(h)) for h in i["needs"]) + f"; got {got_text(i['r'])})"
                      for i in lines2) + ".")
    new_to = {}
    for i in new:
        k = (i["r"].get("answer") or {}).get("to", "FAILED")
        new_to[k] = new_to.get(k, 0) + 1
    w("New things went to: " + ", ".join(f"{k} {v}" for k, v in sorted(new_to.items())) + ".")
    miss = [i for i in fu if not i["ok"]]
    w(f"\n### Follow-ups missed: {len(miss)} ({sum(i['partly'] for i in miss)} of them partly right)\n")
    if miss:
        w("| # | Who | The line | Expected | Got | The router's reason |\n|---|---|---|---|---|---|")
        for i in miss:
            r, x = i["r"], i["x"]
            want = " + ".join("/".join(sorted(h)) for h in needs(x))
            w(f"| {r['n']} | {r['who']} | {clip(r['words'], 70)} | implementer {want} "
              f"({x['id']}: {clip(x['task'], 50)}) | {got_text(r)}{' (partly)' if i['partly'] else ''} | "
              f"{clip((r.get('answer') or {}).get('reason'), 220)} |")
    miss = [i for i in new if not i["ok"]]
    w(f"\n### New things glued onto a task (or sent nowhere): {len(miss)}\n")
    if miss:
        w("| # | Who | The line | Expected | Got | The router's reason |\n|---|---|---|---|---|---|")
        for i in miss:
            r, x = i["r"], i["x"]
            w(f"| {r['n']} | {r['who']} | {clip(r['words'], 70)} | new ({x['id']}: {clip(x['task'], 50)}) | "
              f"{got_text(r)} | {clip((r.get('answer') or {}).get('reason'), 220)} |")
    return md, {"follow_ups": {"right": hit, "partly": part, "ruled": ruled, "of": len(fu),
                               "met": bool(fu) and share >= BAR_PLAYTEST},
                "two_task_lines": [{"n": i["r"]["n"], "verdict": i["verdict"], "met": i["met"], "of": len(i["needs"])}
                                   for i in lines2],
                "new": {"right": nh, "of": len(new), "implementer_right": sum(i["ok"] for i in impl),
                        "implementer_of": len(impl)}, "left_out": left}


# ====================================================================== the night

def judge(r: dict) -> str:
    """right, nowhere (fine for a pure repeat), harmless (talk to a new loop), or miss."""
    if "error" in r:
        return "miss"
    w, a = r["want"], r.get("answer") or {}
    to, ts = a.get("to"), targets(a)
    kind = w["kind"]
    if kind == "talk":
        return "right" if to == "nowhere" else "harmless" if to == "new_loop" else "miss"
    if kind == "new":
        return "right" if to in NEW else "miss"
    if w.get("fallback"):
        return "right" if to in NEW or to == "nowhere" else "miss"
    if any(t in w["work"] for t in ts):
        return "right"
    if kind == "thing_or_new" and to in NEW:
        return "right"
    if to == "nowhere" and w.get("nowhere_ok"):
        return "nowhere"
    return "miss"


def expected_text(r: dict) -> str:
    w = r["want"]
    kind = w["kind"]
    if kind == "talk":
        return "nowhere (talk)"
    if kind == "new":
        return "new_loop or new_implementer"
    if w.get("fallback"):
        return "new or nowhere (no earlier work)"
    work = ", ".join(x.removeprefix("task:") for x in w["work"])
    return {"ask": f"the ask's work: {work}", "thing": f"the work on it: {work}",
            "thing_or_new": f"the work on it ({work}) or new"}[kind] + (" (or nowhere)" if w.get("nowhere_ok") else "")


def score_night(rows: list[dict]) -> tuple[list[str], dict]:
    rows = [r for r in rows if not r.get("skipped")]
    for r in rows:
        r["verdict"] = judge(r)
    bar_rep = [r for r in rows if r["want"].get("bar") == "repeat"]
    bar_bliz = [r for r in rows if r["want"].get("bar") == "blizzard"]
    bar_new = [r for r in rows if r["label"] == "T" and r["want"]["kind"] == "new"]
    t_other = [r for r in rows if r["label"] == "T" and r["want"]["kind"] in ("ask", "thing", "thing_or_new")
               and r["want"].get("bar") not in ("repeat", "blizzard")]
    t_talk = [r for r in rows if r["label"] == "T" and r["want"]["kind"] == "talk"]
    fu = [r for r in rows if r["label"] in ("F", "U") and r["want"].get("bar") not in ("repeat", "blizzard")]

    def count(rs, *ok):
        return sum(r["verdict"] in ok for r in rs)

    md = []
    w = md.append
    rep_strict, rep_soft = count(bar_rep, "right"), count(bar_rep, "right", "nowhere")
    w("| | Right | Of | Bar |\n|---|---|---|---|")
    w(f"| **The 7 repeated asks** (#{', #'.join(str(r['seq']) for r in bar_rep)}) to the loop or task already on "
      f"them | {rep_strict} | {len(bar_rep)} | all: **{'met' if bar_rep and rep_strict == len(bar_rep) else 'NOT met'}** |")
    w(f"| … counting `nowhere` (“still on it”) as fine for a pure repeat | {rep_soft} | {len(bar_rep)} | "
      f"{'met' if bar_rep and rep_soft == len(bar_rep) else 'not met'} |")
    w(f"| **#114**, KrownJM repeating the blizzard, to #113's work | {count(bar_bliz, 'right')} | {len(bar_bliz)} | "
      f"**{'met' if bar_bliz and count(bar_bliz, 'right') == len(bar_bliz) else 'NOT met'}**"
      + (" (nowhere: the soft reading meets it)" if bar_bliz and count(bar_bliz, 'nowhere') else "") + " |")
    w(f"| **Different asks start their own** (T lines that are asks of their own): `new_loop` or `new_implementer` | "
      f"{count(bar_new, 'right')} | {len(bar_new)} | all: "
      f"**{'met' if bar_new and count(bar_new, 'right') == len(bar_new) else 'NOT met'}** |")
    w(f"| T lines about work already under way (follow-ups, answers to the AI, #128 and #214) | "
      f"{count(t_other, 'right')} (+{count(t_other, 'nowhere')} nowhere) | {len(t_other)} | no bar |")
    w(f"| T lines that are talk (#5): nowhere | {count(t_talk, 'right')} (+{count(t_talk, 'harmless')} new loop) | "
      f"{len(t_talk)} | no bar |")
    w(f"| **F and U lines**, apart: as labelled | {count(fu, 'right')} | {len(fu)} | no bar |")
    talk = [r for r in fu if r["want"]["kind"] == "talk"]
    w(f"\nF and U lines that need nothing ({len(talk)}): {count(talk, 'right')} nowhere, {count(talk, 'harmless')} a new "
      f"loop (harmless: a loop that answers), {count(talk, 'miss')} glued onto a loop or a task. The other "
      f"{len(fu) - len(talk)} F/U lines are about work under way: {count([r for r in fu if r not in talk], 'right')} "
      "right.")
    miss = [r for r in rows if r["verdict"] == "miss" and r["label"] == "T" or r in bar_rep + bar_bliz and
            r["verdict"] != "right"]
    seen = set()
    miss = [r for r in miss if not (r["seq"] in seen or seen.add(r["seq"]))]
    w(f"\n### Misses on the T lines and the bars: {len(miss)}\n")
    if miss:
        w("| # | T/F/U | Who | The line | Expected | Got | The router's reason |\n|---|---|---|---|---|---|---|")
        for r in sorted(miss, key=lambda r: r["seq"]):
            w(f"| {r['seq']} | {r['label']} | {r['who']} | {clip(r['words'], 60)} | {expected_text(r)} | "
              f"{got_text(r)}{' (' + r['verdict'] + ')' if r['verdict'] != 'miss' else ''} | "
              f"{clip((r.get('answer') or {}).get('reason'), 220)} |")
    glued = [r for r in fu if r["verdict"] == "miss"]
    w(f"\n### F and U lines placed against their label: {len(glued)}\n")
    if glued:
        w("| # | T/F/U | Who | The line | Expected | Got | The router's reason |\n|---|---|---|---|---|---|---|")
        for r in glued:
            w(f"| {r['seq']} | {r['label']} | {r['who']} | {clip(r['words'], 60)} | {expected_text(r)} | "
              f"{got_text(r)} | {clip((r.get('answer') or {}).get('reason'), 180)} |")
    w("\n### Every line\n")
    w("| # | T/F/U | Want | Got | Verdict | Loops offered | ms |\n|---|---|---|---|---|---|---|")
    for r in rows:
        w(f"| {r['seq']} | {r['label']} | {r['want']['kind']}{' ' + r['want']['bar'] if r['want'].get('bar') else ''} | "
          f"{got_text(r)} | {r['verdict']} | {', '.join(r.get('loops') or []) or '-'} | {r.get('ms', '-')} |")
    summary = {"repeat": {"right": rep_strict, "with_nowhere": rep_soft, "of": len(bar_rep)},
               "blizzard": {"right": count(bar_bliz, "right"), "verdict": [r["verdict"] for r in bar_bliz]},
               "new": {"right": count(bar_new, "right"), "of": len(bar_new)},
               "t_other": {"right": count(t_other, "right"), "nowhere": count(t_other, "nowhere"), "of": len(t_other)},
               "fu": {"right": count(fu, "right"), "of": len(fu), "talk_nowhere": count(talk, "right"),
                      "talk_new_loop": count(talk, "harmless"), "talk_glued": count(talk, "miss"), "talk": len(talk)}}
    return md, summary


# ====================================================================== the files

def load(out: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    rows = [json.loads(x) for x in (out / "results.jsonl").read_text().splitlines() if x.strip()] \
        if (out / "results.jsonl").exists() else []
    rows.sort(key=lambda r: (r.get("at", ""), r.get("seq", 0)))   # a resumed run appends the calls asked again
    return meta, rows


def score(out: Path) -> tuple[str, dict]:
    meta, rows = load(out)
    s = meta.get("set") or (rows[0]["set"] if rows else "?")
    head = (f"# The router replay: {s}, {meta.get('model', '?')} {meta.get('reasoning', '')}"
            f"{' (DRY: a fake model)' if meta.get('dry') else ''}\n\n{len(rows)} lines; router "
            f"{meta.get('router', '?')}, prompt {meta.get('prompt_sha', '?')}, head {meta.get('head', '?')}; hosts "
            f"{meta.get('providers', '?')}; started {meta.get('started', '?')}.")
    m = measures(rows)
    left = None
    if (out / "playtest_set.json").exists():
        left = json.loads((out / "playtest_set.json").read_text()).get("left_out")
    body, summary = (score_playtest(rows, left) if s == "playtest" else score_night(rows))
    text = head + "\n\n" + measures_md(m) + "\n\n" + "\n".join(body) + "\n"
    summary.update(set=s, model=meta.get("model"), reasoning=meta.get("reasoning"), dry=meta.get("dry"),
                   prompt_sha=meta.get("prompt_sha"), measures=m, out=str(out))
    return text, summary


def main() -> None:
    args = sys.argv[1:]
    repo = "--repo" in args
    outs = [Path(a) for a in args if a != "--repo"]
    if not outs:
        sys.exit(__doc__.split("\n\n")[1])
    parts = []
    for out in outs:
        text, summary = score(out)
        (out / "report.md").write_text(text, encoding="utf-8")
        (out / "score.json").write_text(json.dumps(summary, indent=1))
        sys.stdout.write(text + "\n")
        parts.append((out, text, summary))
    if repo:
        body = ["# The router replay: the report\n",
                "Written by `integration/replay/router/score.py --repo` from the runs below; the diagnosis at "
                "the foot is written by hand. How each set is built and scored: the docstrings of "
                "`integration/router_replay.py` and this scorer. The data stays out of the repo.\n"]
        for out, text, summary in parts:
            body.append(text.replace("# The router replay:", "## The router replay:", 1)
                        .split("\n### Every line\n")[0])
        (HERE / "report.md").write_text("\n".join(body), encoding="utf-8")
        print(f"wrote {HERE / 'report.md'}")


if __name__ == "__main__":
    main()
