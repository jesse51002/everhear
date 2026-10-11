#!/usr/bin/env python3
"""Scores gather_bench.py: D line by line, without the gather call and with it, on the clean labels
(labels_2026-10-08.jsonl, carried in the rows) and the whole asks (whole_asks_2026-10-08.jsonl).

    python3 integration/replay/gather_score.py --rows OUT/prod/rows.jsonl \
        --inputs integration/replay/whole_asks_2026-10-08.jsonl --gate-b GATE_OUT/B/rows.jsonl \
        [--alt g2=OUT/g2/rows.jsonl ...] --to OUT/score.md --json OUT/score.json

Standard library only.

WHERE A LINE GOES. Without the gather call, its class: `action_oriented` to the brain,
`conversational` to Respond, `context` kept, `not_relevant` dropped (a blank line is dropped, a line
Jev failed on five times is context, as the filter fails open). Each line to the brain or Respond is
one EVENT. With the gather call, the event is the ask and its group (the yes lines fed with it, after
fit_group), and a gathered line goes where its ask went: to the brain or Respond, inside the ask's
event. It is fed as its own class too, but that costs nothing.

REACHING THE BRAIN. An event reaches the brain when it goes there, or goes to Respond and Respond B
hands it up. B's hand-ups are the gate bench's recorded run (592 of the 843 lines), counted as
whole_asks_score.py counts a merged input: an event is handed up when B handed up any of its lines it
ran, answered in chat when B ran some of its lines and handed up none, and outside B's set when B ran
none of them (counted apart; in the cost, at B's hand-up rate for the event's label). A line reaches
the brain when its event does. An ask INPUT (whole_asks: one ask a player split across lines, its
label the strongest of its lines') reaches the brain when any of its lines does, and ARRIVES WHOLE
when one event that reaches the brain holds all of its lines.

A WRONG JOIN is a gathered line that is not in the same whole-ask input as its ask. A MISSED PIECE is
a line of an ask input left out of the reaching event that holds most of that input, with why.

COST a 2.5 h session (the 843 lines), as the other replays count it: brain calls x $0.0085 + Respond
calls x $0.0015 + Jev's input tokens x $42 per billion (every call and every failed try). A brain call
is an event to the brain or one Respond B hands up. LATENCY is a Jev call's ms; on a line to the AI
with the gather call, the routing call's and the gather call's added, as the filter makes them one
after the other.

THE HALVES: a line's is the trees benches' (the first 843 // 2 in time order); an input's, its last
line's; an event's, its ask's. The gather wording was not tuned on either half; a candidate wording
(--alt) is asked on the first half only, and the chosen one on the second.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics as st
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tree_score import clean, p90, pct, read  # noqa: E402

LABELS = ("request", "answer_to_ai", "talk_to_ai", "context", "noise")
ASKS = ("request", "answer_to_ai")
TALK = ("talk_to_ai", "noise")
OUTS = ("brain", "respond", "context", "dropped")
OUT_NAMES = {"brain": "brain", "respond": "Respond", "context": "context", "dropped": "dropped"}
WANT = {"request": "brain", "answer_to_ai": "brain", "talk_to_ai": "respond", "context": "context",
        "noise": "dropped"}
PLACE = {"action_oriented": "brain", "conversational": "respond", "context": "context", "not_relevant": "dropped"}
BRAIN_USD = 0.0085
RESPOND_USD = 0.0015
JEV_USD = 0.000000042
TIMEOUT_MS = 3000   # VFG_JEV_TIMEOUT's default: a call slower than this fails in production
BUCKETS = ((1, 1), (2, 3), (4, 6), (7, 10))


def strongest(labels) -> str:
    return min(labels, key=LABELS.index)


def call_tokens(call: dict | None) -> int:
    if not call:
        return 0
    return call.get("tokens", 0) + sum(t.get("tokens", 0) for t in call.get("tries", []) if isinstance(t, dict))


# ------------------------------------------------------------------ the data


class Data:
    def __init__(self, rows: list[dict], inputs: list[dict], gate: list[dict]):
        self.rows = sorted(rows, key=lambda r: (r["at"], r["id"]))
        self.by_id = {r["id"]: r for r in self.rows}
        self.inputs = [i for i in inputs if all(x in self.by_id for x in i["lines"])]   # all of them, on a full run
        self.input_of = {i: inp["id"] for inp in inputs for i in inp["lines"]}
        self.up = {r["id"]: bool(r["up"]) for r in gate}
        self.rate = {}
        for name in LABELS:
            ran = [i for i in self.up if i in self.by_id and self.by_id[i]["label"] == name]
            self.rate[name] = sum(self.up[i] for i in ran) / len(ran) if ran else 0.0

    def place(self, r: dict) -> str:
        if r.get("blank"):
            return "dropped"
        if r.get("failed"):
            return "context"
        return PLACE[r["class"]]

    def hand_up(self, ids: list[str]) -> bool | None:
        known = [self.up[i] for i in ids if i in self.up]
        return any(known) if known else None


class Routing:
    """Where every line and event goes, without the gather call (`groups` None) or with it (`groups`:
    {ask id: [gathered ids]}), and the Jev tokens and ms behind it."""

    def __init__(self, d: Data, name: str, groups: dict[str, list[str]] | None, gather_calls: dict[str, dict]):
        self.d, self.name = d, name
        self.events: dict[str, list[str]] = {}   # ask id -> its lines, in the order said, the ask last
        self.dest: dict[str, str] = {}
        self.joined: dict[str, str] = {}         # gathered line -> its ask
        self.gather_calls = gather_calls if groups is not None else {}
        for r in d.rows:
            p = d.place(r)
            if p in ("brain", "respond"):
                g = (groups or {}).get(r["id"], [])
                self.events[r["id"]] = list(g) + [r["id"]]
                self.dest[r["id"]] = p
                for i in g:
                    assert i not in self.joined, f"{i} joined twice"
                    self.joined[i] = r["id"]
        self.event_of = {i: a for a, ids in self.events.items() for i in ids}
        self.hu = {a: d.hand_up(ids) for a, ids in self.events.items()}

    def where(self, line_id: str) -> str:
        a = self.event_of.get(line_id)
        return self.dest[a] if a else self.d.place(self.d.by_id[line_id])

    def reaches(self, ask_id: str) -> bool:
        return self.dest[ask_id] == "brain" or (self.dest[ask_id] == "respond" and self.hu[ask_id] is True)

    def line_reaches(self, line_id: str) -> bool:
        a = self.event_of.get(line_id)
        return a is not None and self.reaches(a)

    def event_label(self, a: str) -> str:
        return strongest([self.d.by_id[i]["label"] for i in self.events[a]])


# ------------------------------------------------------------------ the tally


def tally(rt: Routing, half: str | None) -> dict:
    d = rt.d
    lines = [r for r in d.rows if half is None or r["half"] == half]
    inputs = [i for i in d.inputs if half is None or i["half"] == half]
    events = [a for a in rt.events if half is None or d.by_id[a]["half"] == half]
    lab = {r["id"]: r["label"] for r in lines}
    cm = Counter((lab[r["id"]], rt.where(r["id"])) for r in lines)

    def lines_where(ids: list[str]) -> dict:
        out = Counter()
        for i in ids:
            w = rt.where(i)
            out[w] += 1
            if w == "respond":
                hu = rt.hu[rt.event_of[i]]
                out["respond_up" if hu is True else "respond_kept" if hu is False else "respond_unknown"] += 1
        out["n"] = len(ids)
        out["reach"] = out["brain"] + out["respond_up"]
        return dict(out)

    asks = lines_where([r["id"] for r in lines if lab[r["id"]] in ASKS])
    talk = lines_where([r["id"] for r in lines if lab[r["id"]] in TALK])
    noise = lines_where([r["id"] for r in lines if lab[r["id"]] == "noise"])
    talk_joined = sum(1 for r in lines if lab[r["id"]] in TALK and r["id"] in rt.joined
                      and rt.where(r["id"]) == "brain")
    joined = Counter((lab[r["id"]], rt.where(r["id"])) for r in lines if r["id"] in rt.joined)

    ask_inputs = [i for i in inputs if i["label"] in ASKS]
    reach_any = [i for i in ask_inputs if any(rt.line_reaches(x) for x in i["lines"])]
    whole = [i for i in ask_inputs if any(set(i["lines"]) <= set(rt.events[a]) and rt.reaches(a)
                                          for a in {rt.event_of.get(x) for x in i["lines"]} if a)]
    multi = [i for i in ask_inputs if len(i["lines"]) > 1]
    talk_inputs = [i for i in inputs if i["label"] in TALK]

    brain_ev = [a for a in events if rt.dest[a] == "brain"]
    resp_ev = [a for a in events if rt.dest[a] == "respond"]
    up_known = sum(1 for a in resp_ev if rt.hu[a] is True)
    up_est = sum(d.rate[rt.event_label(a)] for a in resp_ev if rt.hu[a] is None)
    brain_calls = len(brain_ev) + up_known + up_est
    route_tok = sum(call_tokens(c) for r in lines for c in r.get("route", []))
    gather_tok = sum(call_tokens(rt.gather_calls.get(r["id"])) for r in lines)
    jev_usd = (route_tok + gather_tok) * JEV_USD
    route_ms = [c["ms"] for r in lines for c in r.get("route", []) if c.get("ms")]
    to_ai = [r for r in lines if rt.d.place(r) in ("brain", "respond")]
    ai_ms = [sum(c.get("ms", 0) for c in r.get("route", [])) + (rt.gather_calls.get(r["id"]) or {}).get("ms", 0)
             for r in to_ai]
    return {
        "lines": len(lines), "inputs": len(inputs), "cm": {f"{a}/{o}": cm[(a, o)] for a in LABELS for o in OUTS},
        "asks": asks, "talk": talk, "noise": noise, "talk_joined_brain": talk_joined,
        "joined": {f"{a}/{o}": joined[(a, o)] for a in LABELS for o in ("brain", "respond")},
        "ask_inputs": len(ask_inputs), "reach_any": len(reach_any), "whole": len(whole),
        "multi": len(multi), "multi_reach_any": sum(1 for i in multi if i in reach_any),
        "multi_whole": sum(1 for i in multi if i in whole), "whole_ids": [i["id"] for i in whole],
        "talk_inputs": len(talk_inputs),
        "talk_inputs_brain": sum(1 for i in talk_inputs if any(rt.where(x) == "brain" for x in i["lines"])),
        "right": sum(1 for r in lines if rt.where(r["id"]) == WANT[lab[r["id"]]]),
        "brain_events": len(brain_ev), "respond_events": len(resp_ev), "respond_up_known": up_known,
        "respond_up_est": up_est, "respond_kept": sum(1 for a in resp_ev if rt.hu[a] is False),
        "brain_calls": brain_calls, "route_tokens": route_tok, "gather_tokens": gather_tok, "jev_usd": jev_usd,
        "usd_brain": brain_calls * BRAIN_USD, "usd_respond": len(resp_ev) * RESPOND_USD,
        "usd": brain_calls * BRAIN_USD + len(resp_ev) * RESPOND_USD + jev_usd,
        "route_ms_median": st.median(route_ms) if route_ms else 0, "route_ms_p90": p90(route_ms),
        "route_calls": len(route_ms), "ai_ms_median": st.median(ai_ms) if ai_ms else 0, "ai_ms_p90": p90(ai_ms),
        "to_ai": len(to_ai), "failed": sum(1 for r in lines if r.get("failed")),
        "route_over_timeout": sum(1 for x in route_ms if x > TIMEOUT_MS),
    }


def gather_stats(rt: Routing, half: str | None) -> dict:
    """The gather call itself: calls, windows, answers, ms, tokens, failures."""
    d = rt.d
    tried = [r for r in d.rows if r.get("gather") is not None and (half is None or r["half"] == half)]
    calls = [(r, rt.gather_calls[r["id"]]) for r in tried if rt.gather_calls.get(r["id"])]
    sizes = Counter(len(r["gather"]["window"]) for r, _ in calls)
    asked = sum(c["questions"] for _, c in calls)
    answered = [(r, c) for r, c in calls if "answers" in c]
    yes = sum(1 for r, c in answered for k in range(1, c["questions"] + 1)
              if (c["answers"].get(f"line_{k}") or [None])[0] == "part_of_the_ask")
    missing = sum(len(c.get("missing", [])) for _, c in answered)
    ms = [c["ms"] for _, c in answered]
    by_size = {}
    for lo, hi in BUCKETS:
        xs = [c["ms"] for r, c in answered if lo <= c["questions"] <= hi]
        tk = [c["tokens"] for r, c in answered if lo <= c["questions"] <= hi]
        by_size[f"{lo}-{hi}" if lo != hi else str(lo)] = {
            "calls": len(xs), "ms_median": st.median(xs) if xs else 0, "ms_p90": p90(xs),
            "tokens_median": st.median(tk) if tk else 0}
    tok = sum(call_tokens(c) for _, c in calls)
    groups = [len(rt.events[r["id"]]) - 1 for r, _ in calls]
    return {
        "lines_to_ai": len(tried), "empty": sum(1 for r in tried if not r["gather"]["window"]),
        "calls": len(calls), "sizes": {str(k): sizes[k] for k in sorted(sizes)},
        "cap_bites": sum(1 for r, _ in calls if r["gather"]["window_all"] > len(r["gather"]["window"])),
        "cap_cut_lines": sum(r["gather"]["window_all"] - len(r["gather"]["window"]) for r, _ in calls),
        "asked": asked, "yes": yes, "missing": missing,
        "groups": sum(1 for g in groups if g), "grouped_lines": sum(groups),
        "fit_dropped": sum(len(r["gather"].get("yes", [])) - (len(rt.events[r["id"]]) - 1) for r, _ in calls),
        "ms_median": st.median(ms) if ms else 0, "ms_p90": p90(ms), "ms_max": max(ms) if ms else 0,
        "over_timeout": sum(1 for x in ms if x > TIMEOUT_MS), "by_size": by_size,
        "tokens": tok, "tokens_median": st.median([c["tokens"] for _, c in answered]) if answered else 0,
        "usd": tok * JEV_USD,
        "tries_down": sum(sum(1 for t in c.get("tries", []) if "down" in t) for _, c in calls),
        "refused": sum(sum(1 for t in c.get("tries", []) if "refused" in t) for _, c in calls),
        "failed": sum(1 for _, c in calls if "answers" not in c),
        "ten": sum(1 for _, c in calls if c["questions"] == 10),
        "ten_answered": sum(1 for _, c in answered if c["questions"] == 10 and not c.get("missing")),
    }


# ------------------------------------------------------------------ what the gather changed


def joins(rt: Routing) -> list[dict]:
    """Every gathered line, with whether it is in its ask's whole-ask input."""
    d = rt.d
    out = []
    for g, a in rt.joined.items():
        gr, ar = d.by_id[g], d.by_id[a]
        out.append({"line": g, "ask": a, "at": gr["at"], "ask_at": ar["at"], "who": gr["who"], "half": ar["half"],
                    "label": gr["label"], "ask_label": ar["label"], "dest": rt.dest[a], "reaches": rt.reaches(a),
                    "same_input": d.input_of[g] == d.input_of[a], "text": gr["text"], "ask_text": ar["text"],
                    "line_place": d.place(gr), "p_yes": p_yes(rt, a, g)})
    return sorted(out, key=lambda j: (j["ask_at"], j["at"]))


def p_yes(rt: Routing, ask: str, line: str) -> float | None:
    c = rt.gather_calls.get(ask)
    r = rt.d.by_id[ask]
    if not c or "answers" not in c:
        return None
    k = r["gather"]["window"].index(line) + 1
    a = c["answers"].get(f"line_{k}")
    return a[2].get("part_of_the_ask") if a else None


def missed(rt: Routing, half: str | None) -> list[dict]:
    """The lines of each multi-line ask input left out of the reaching event that holds most of it."""
    d = rt.d
    out = []
    for inp in d.inputs:
        if inp["label"] not in ASKS or len(inp["lines"]) < 2 or (half and inp["half"] != half):
            continue
        evs = [a for a in {rt.event_of.get(x) for x in inp["lines"]} if a and rt.reaches(a)]
        if not evs:
            out.append({"input": inp, "event": None, "pieces": [], "why": "no line of it reaches the brain"})
            continue
        main = max(evs, key=lambda a: (len(set(inp["lines"]) & set(rt.events[a])), d.by_id[a]["at"]))
        left = [x for x in inp["lines"] if x not in rt.events[main]]
        if not left:
            continue
        ar = d.by_id[main]
        win = ar["gather"]["window"] if ar.get("gather") else []
        call = rt.gather_calls.get(main)
        pieces = []
        for x in left:
            xr = d.by_id[x]
            if (xr["at"], x) > (ar["at"], main):
                why = "said after the ask, " + (f"sent on its own, to {OUT_NAMES[d.place(xr)]}"
                                                if d.place(xr) in ("brain", "respond") else f"D: {d.place(xr)}")
            elif d.place(xr) in ("brain", "respond"):
                why = f"sent on its own, to {OUT_NAMES[d.place(xr)]}"
            elif x in win:
                k = win.index(x) + 1
                got = (call or {}).get("answers", {}).get(f"line_{k}")
                why = ("in the window, the gather call failed" if not call or "answers" not in call else
                       "in the window, no answer" if got is None else
                       f"in the window, Jev said no ({got[2].get('part_of_the_ask', 0):.2f})"
                       if got[0] != "part_of_the_ask" else "yes, but dropped to fit 500 characters")
            else:
                why = "not in the window: " + outside(d, ar, xr)
            pieces.append({"line": x, "why": why, "label": xr["label"], "text": xr["text"], "at": xr["at"],
                           "place": d.place(xr)})
        out.append({"input": inp, "event": main, "pieces": pieces})
    return out


def secs(a: str, b: str) -> float:
    """Seconds from `a` to `b`, two ISO times."""
    return (datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))
            ).total_seconds()


def outside(d: Data, ar: dict, xr: dict) -> str:
    if secs(xr["at"], ar["at"]) > 180:
        return f"{secs(xr['at'], ar['at']):.0f} s before the ask, past 180 s"
    sent = [r for r in d.rows if r["who"] == ar["who"] and (xr["at"], xr["id"]) < (r["at"], r["id"]) < (ar["at"], ar["id"])
            and d.place(r) in ("brain", "respond")]
    if sent:
        return "before the speaker's last line sent to the AI"
    if not xr["text"].strip():
        return "blank"
    return "past the cap of 10"


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
    return (f"{a.get('respond_kept', 0)} / {a.get('context', 0)} / {a.get('dropped', 0)}"
            + (f", +{a['respond_unknown']}" if a.get("respond_unknown") else ""))


def summary_row(name: str, t: dict) -> str:
    a, k, nz = t["asks"], t["talk"], t["noise"]
    return (f"| {name} | **{a['reach']} of {a['n']} ({pct(a['reach'], a['n'])})** | {a.get('brain', 0)} + "
            f"{a.get('respond_up', 0)} | {lost_cells(a)} | **{t['reach_any']} of {t['ask_inputs']}** | "
            f"{t['whole']} of {t['ask_inputs']} ({t['multi_whole']} of {t['multi']} split) | {k.get('brain', 0)} of "
            f"{k['n']} ({pct(k.get('brain', 0), k['n'])}) | {nz.get('dropped', 0)} of {nz['n']} "
            f"({pct(nz.get('dropped', 0), nz['n'])}) | **${t['usd']:.2f}** | {t['route_ms_median']:.0f} / "
            f"{t['route_ms_p90']:.0f} ms | {t['ai_ms_median']:.0f} / {t['ai_ms_p90']:.0f} ms |")


SUMMARY_HEAD = ("| | Ask lines reach the brain | direct + via B | Lost: chat / context / dropped | Ask inputs reach "
                "(any line) | Ask inputs arrive whole | Talk and noise lines to the brain | Noise dropped | Cost | "
                "Jev ms, every routing call | Jev ms, a line to the AI |\n|---|---|---|---|---|---|---|---|---|---|---|")


# ------------------------------------------------------------------ the report


def report(d: Data, alts: dict[str, dict[str, dict]], w) -> dict:
    gather_calls = {r["id"]: r["gather"]["call"] for r in d.rows if r.get("gather") and r["gather"].get("call")}
    groups = {r["id"]: r["gather"]["group"] for r in d.rows if r.get("gather")}
    without = Routing(d, "without", None, {})
    with_ = Routing(d, "with", groups, gather_calls)
    halves = {"all": None, "first": "first", "second": "second"}
    s = {"rate": d.rate, "without": {h: tally(without, v) for h, v in halves.items()},
         "with": {h: tally(with_, v) for h, v in halves.items()},
         "gather": {h: gather_stats(with_, v) for h, v in halves.items()}}

    n_ask = sum(1 for r in d.rows if r["label"] in ASKS)
    c = Counter(r["label"] for r in d.rows)
    w(f"{len(d.rows)} lines: " + ", ".join(f"{c[x]} {x}" for x in LABELS) + f"; {n_ask} ask lines. "
      f"{len(d.inputs)} whole-ask inputs, {sum(1 for i in d.inputs if i['label'] in ASKS)} of them asks, "
      f"{sum(1 for i in d.inputs if i['label'] in ASKS and len(i['lines']) > 1)} of those split across lines. "
      "Second half: " f"{sum(1 for r in d.rows if r['half'] == 'second')} lines, "
      f"{sum(1 for r in d.rows if r['half'] == 'second' and r['label'] in ASKS)} ask lines, "
      f"{sum(1 for i in d.inputs if i['half'] == 'second' and i['label'] in ASKS)} ask inputs.\n")
    w("Respond B's hand-up rate by label, on the lines it ran: "
      + ", ".join(f"{x} {100 * d.rate[x]:.0f}%" for x in LABELS) + ".\n")

    w("## Summary\n")
    w("*Lost* is chat / context / dropped: in an event Respond B answers in chat, kept as context, dropped (+ to "
      "Respond outside B's set). *Ask inputs reach*: any of the input's lines reaches the brain; *arrive whole*: "
      "one event that reaches the brain holds all its lines (in brackets, of the inputs split across lines). "
      "*Talk and noise lines to the brain*: `talk_to_ai` and `noise` lines in an event to the brain (with the "
      "gather call, gathered ones too). Cost is a 2.5 h session. Latency is median / p90.\n")
    w(SUMMARY_HEAD)
    for h, name in (("all", "all"), ("second", "second half")):
        w(summary_row(f"D, without gather, {name}", s["without"][h]))
        w(summary_row(f"**D, with gather, {name}**", s["with"][h]))

    for h in ("all", "second"):
        tw, tg, g = s["without"][h], s["with"][h], s["gather"][h]
        w(f"\n## Each, {'all lines' if h == 'all' else 'the second half'}\n")
        for name, t in (("D, without the gather call", tw), ("D, with the gather call", tg)):
            a, k = t["asks"], t["talk"]
            w(f"\n### {name}{'' if h == 'all' else ', second half'}\n")
            w("\n".join(heat_html(Counter(t["cm"]), f"{name}: label (rows) by where the line went (columns)")))
            w("")
            w("\n".join(heat_md(Counter(t["cm"]))))
            w(f"\n- **Ask lines reaching the brain: {a['reach']} of {a['n']} ({pct(a['reach'], a['n'], 1)})**: "
              f"{a.get('brain', 0)} in events to the brain, {a.get('respond_up', 0)} in events Respond B hands up. Lost "
              f"{a['n'] - a['reach']}: {a.get('respond_kept', 0)} answered in chat, {a.get('context', 0)} kept as "
              f"context, {a.get('dropped', 0)} dropped"
              + (f", {a['respond_unknown']} to Respond outside B's set" if a.get("respond_unknown") else "") + ".")
            w(f"- **Ask inputs: {t['reach_any']} of {t['ask_inputs']} reach the brain** by any line; {t['whole']} "
              f"arrive whole in one event; of the {t['multi']} split across lines, {t['multi_reach_any']} reach and "
              f"{t['multi_whole']} arrive whole.")
            w(f"- **Talk and noise lines to the brain: {k.get('brain', 0)} of {k['n']} ({pct(k.get('brain', 0), k['n'], 1)})**"
              + (f", {t['talk_joined_brain']} of them gathered into an ask" if t["talk_joined_brain"] else "")
              + f"; {k.get('respond', 0)} to Respond. Talk and noise inputs with a line to the brain: "
                f"{t['talk_inputs_brain']} of {t['talk_inputs']}.")
            w(f"- **Noise dropped: {t['noise'].get('dropped', 0)} of {t['noise']['n']} "
              f"({pct(t['noise'].get('dropped', 0), t['noise']['n'], 1)})**; kept as context "
              f"{t['noise'].get('context', 0)}, to Respond {t['noise'].get('respond', 0)}, to the brain "
              f"{t['noise'].get('brain', 0)}.")
            w(f"- **Cost: ${t['usd']:.2f}**: {t['brain_events']} events to the brain, {t['respond_events']} to "
              f"Respond, of which B hands up {t['respond_up_known']}"
              + (f" and about {t['respond_up_est']:.1f} more outside B's set" if t["respond_up_est"] else "")
              + f" ({t['brain_calls']:.1f} brain calls ${t['usd_brain']:.2f}, Respond ${t['usd_respond']:.2f}); Jev "
                f"{t['route_tokens'] + t['gather_tokens']:,} tokens ${t['jev_usd']:.3f}"
              + (f", the gather calls {t['gather_tokens']:,} of them" if t["gather_tokens"] else "") + ".")
            w(f"- Latency: every routing call {t['route_ms_median']:.0f} ms median, {t['route_ms_p90']:.0f} ms p90 "
              f"({t['route_calls']} calls, {t['route_over_timeout']} over {TIMEOUT_MS} ms); a line to the AI "
              f"({t['to_ai']}) {t['ai_ms_median']:.0f} / {t['ai_ms_p90']:.0f} ms"
              + (", the routing and gather calls added" if t["gather_tokens"] else "") + "."
              + (f" {t['failed']} lines failed five times (context)." if t["failed"] else ""))
        w(f"\n### The gather call{'' if h == 'all' else ', second half'}\n")
        w(f"- **{g['calls']} calls** on {g['lines_to_ai']} lines to the AI; {g['empty']} had an empty window, so no "
          "call.")
        w("- **Window sizes** (lines asked about: calls): " + ", ".join(f"{k}: {v}" for k, v in g["sizes"].items())
          + f". The cap of 10 bit on {g['cap_bites']} calls, leaving out {g['cap_cut_lines']} older lines.")
        w(f"- **Answers: {g['yes']} yes of {g['asked']} lines asked ({pct(g['yes'], g['asked'], 1)})**; "
          f"{g['groups']} asks got a group, {g['grouped_lines']} lines in all; {g['fit_dropped']} yes lines dropped "
          f"to fit 500 characters; {g['missing']} questions with no well-formed answer.")
        w(f"- **Jev ms per call: {g['ms_median']:.0f} median, {g['ms_p90']:.0f} p90, {g['ms_max']} at most**; "
          f"{g['over_timeout']} over {TIMEOUT_MS} ms. By window size: "
          + "; ".join(f"{k}: {v['calls']} calls, {v['ms_median']:.0f} / {v['ms_p90']:.0f} ms, "
                      f"{v['tokens_median']:,.0f} tokens median" for k, v in g["by_size"].items() if v["calls"]) + ".")
        w(f"- **Cost: {g['tokens']:,} tokens, ${g['usd']:.4f}** ({g['tokens_median']:,.0f} tokens a call at the "
          f"median).")
        w(f"- **Failures**: {g['tries_down']} failed tries (tried again), {g['refused']} refusals, {g['failed']} "
          f"calls with no answer. 10-question calls: {g['ten']}, {g['ten_answered']} answered in full.")

    # what the gather changed
    js = joins(with_)
    s["joins"] = js
    w("\n## What the gather call adds\n")
    tw, tg = s["without"]["all"], s["with"]["all"]
    newly = [i for i in d.inputs if i["id"] in set(tg["whole_ids"]) - set(tw["whole_ids"])]
    w(f"**Ask inputs that now arrive whole: {len(newly)}** ({tw['whole']} to {tg['whole']}).\n")
    for i in newly:
        a = with_.event_of[i["lines"][-1]] if i["lines"][-1] in with_.event_of else None
        w(f"- `{i['at'][11:19]}` {i['who']} [{i['label']}, {len(i['lines'])} lines"
          f"{', second half' if i['half'] == 'second' else ''}] → {OUT_NAMES[with_.dest[a]] if a else '?'}: "
          f"{clean(i['text'], 220)}")
    pieces = [j for j in js if j["label"] in ASKS and j["line_place"] in ("context", "dropped")]
    w(f"\n**Ask pieces D kept as context or dropped that now go to the AI inside a group: {len(pieces)}** "
      f"({sum(1 for j in pieces if j['reaches'])} reach the brain).\n")
    for j in pieces:
        w(f"- `{j['at'][11:19]}` {j['who']} [{j['label']}, D: {j['line_place']}"
          f"{', same input' if j['same_input'] else ', another input'}] {clean(j['text'], 140)} → with "
          f"[{j['ask_label']}] {clean(j['ask_text'], 120)} → {OUT_NAMES[j['dest']]}")
    joined = Counter(j["label"] for j in js)
    w(f"\n**Every gathered line: {len(js)}**, by label: " + ", ".join(f"{joined[x]} {x}" for x in LABELS)
      + f". In the ask's own whole-ask input: {sum(1 for j in js if j['same_input'])}; not: "
        f"{sum(1 for j in js if not j['same_input'])}. Noise joined into an ask: {joined['noise']} "
        f"({sum(1 for j in js if j['label'] == 'noise' and j['dest'] == 'brain')} into one to the brain).\n")
    w("\n## Wrong joins: gathered lines not in their ask's whole-ask input\n")
    wrong = [j for j in js if not j["same_input"]]
    w(f"{len(wrong)} lines.\n")
    for j in wrong:
        w(f"- `{j['at'][11:19]}` {j['who']} [{j['label']}] “{clean(j['text'], 200)}” (P(part) {j['p_yes']:.2f}) "
          f"→ joined to `{j['ask_at'][11:19]}` [{j['ask_label']}] “{clean(j['ask_text'], 200)}” → "
          f"{OUT_NAMES[j['dest']]}{', second half' if j['half'] == 'second' else ''}")
    w("\n## Missed pieces: split ask inputs that do not arrive whole, with gather\n")
    ms_ = missed(with_, None)
    s["missed"] = [{"input": m["input"]["id"], "event": m["event"], "pieces": m["pieces"], "why": m.get("why")}
                   for m in ms_]
    why = Counter(p["why"].split(" (")[0].split(":")[0].split(", D")[0] for m in ms_ for p in m["pieces"])
    w(f"{len(ms_)} inputs; their pieces left out, by why: " + ", ".join(f"{v} {k}" for k, v in why.most_common())
      + ".\n")
    for m in ms_:
        i = m["input"]
        head = (f"- `{i['at'][11:19]}` {i['who']} [{i['label']}, {len(i['lines'])} lines"
                f"{', second half' if i['half'] == 'second' else ''}]")
        if m["event"] is None:
            w(f"{head}: {m['why']}: {clean(i['text'], 200)}")
            continue
        ask = d.by_id[m["event"]]
        w(f"{head}, the event: “{clean(ask['text'], 120)}” → {OUT_NAMES[with_.dest[m['event']]]}")
        for p in m["pieces"]:
            w(f"  - `{p['at'][11:19]}` [{p['label']}, D: {OUT_NAMES[p['place']]}] {p['why']}: {clean(p['text'], 140)}")

    # Respond B on events the gather changed
    changed = [a for a in with_.events if with_.dest[a] == "respond" and with_.hu[a] != without.hu[a]]
    w(f"\n**Respond events whose hand-up changed because a gathered line was handed up by B: {len(changed)}.**")
    for a in changed:
        r = d.by_id[a]
        w(f"- `{r['at'][11:19]}` [{r['label']}] {clean(r['text'], 120)}: {without.hu[a]} → {with_.hu[a]}")

    # candidate wordings
    if alts:
        w("\n## Candidate wordings of the gather call\n")
        w("Each asked again on the recorded windows of a half (the windows are D's verdicts and do not depend on "
          "the gather's answers), the state built as production's at the same moment. Production's answers on the "
          "same windows beside them.\n")
        w("| Wording | Half | Calls | Yes of asked | Gathered lines | In the ask's input | Wrong joins | Noise joined | "
          "Ask pieces from context or dropped | Ask inputs arrive whole | Split ask inputs arrive whole | Ask lines "
          "reach | Jev ms median / p90 | Tokens |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        s["alts"], lists = {}, []
        for name, rows in [("prod", None)] + list(alts.items()):
            for h in ("first", "second"):
                ids = {r["id"] for r in d.rows if r.get("gather") and r["gather"]["window"] and r["half"] == h}
                if rows is None:
                    rt = with_
                else:
                    have = {i for i in ids if i in rows}
                    if not have:
                        continue
                    if have != ids:
                        w(f"| {name} | {h} | incomplete: {len(have)} of {len(ids)} windows | | | | | | | | | | | |")
                        continue
                    g2, calls2 = dict(groups), dict(gather_calls)
                    for i in have:
                        g2[i] = rows[i]["group"]
                        calls2[i] = {**rows[i], "questions": len(rows[i]["window"])}
                    rt = Routing(d, name, g2, calls2)
                t, g = tally(rt, h), gather_stats(rt, h)
                jj = [j for j in joins(rt) if j["half"] == h]
                row = {"calls": g["calls"], "yes": g["yes"], "asked": g["asked"], "joined": len(jj),
                       "same": sum(1 for j in jj if j["same_input"]), "wrong": sum(1 for j in jj if not j["same_input"]),
                       "noise": sum(1 for j in jj if j["label"] == "noise"),
                       "pieces": sum(1 for j in jj if j["label"] in ASKS and j["line_place"] in ("context", "dropped")),
                       "whole": t["whole"], "inputs": t["ask_inputs"], "multi_whole": t["multi_whole"],
                       "multi": t["multi"], "reach": t["asks"]["reach"], "ask_lines": t["asks"]["n"],
                       "ms": g["ms_median"], "ms90": g["ms_p90"], "tokens": g["tokens"]}
                s["alts"][f"{name}/{h}"] = row
                w(f"| {name} | {h} | {row['calls']} | {row['yes']} of {row['asked']} | {row['joined']} | "
                  f"{row['same']} | {row['wrong']} | {row['noise']} | {row['pieces']} | {row['whole']} of "
                  f"{row['inputs']} | {row['multi_whole']} of {row['multi']} | {row['reach']} of "
                  f"{row['ask_lines']} | {row['ms']:.0f} / {row['ms90']:.0f} | {row['tokens']:,} |")
                lists.append((name, h, jj))
        for name, h, jj in lists:
            w(f"\n**{name}, {h} half**: its wrong joins, and the ask pieces from context or dropped it gathered.\n")
            for j in jj:
                if not j["same_input"] or (j["label"] in ASKS and j["line_place"] in ("context", "dropped")):
                    w(f"- {'**wrong** ' if not j['same_input'] else 'piece '}`{j['at'][11:19]}` [{j['label']}] "
                      f"“{clean(j['text'], 120)}” ({j['p_yes']:.2f}) → [{j['ask_label']}] “{clean(j['ask_text'], 100)}”")
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", required=True, help="gather_bench.py's OUT/prod/rows.jsonl")
    ap.add_argument("--inputs", required=True, help="whole_asks_2026-10-08.jsonl")
    ap.add_argument("--gate-b", required=True, help="the gate bench's B/rows.jsonl: Respond B's hand-ups")
    ap.add_argument("--alt", action="append", default=[], help="NAME=OUT/NAME/rows.jsonl, a candidate wording")
    ap.add_argument("--to", required=True, help="the report (.md)")
    ap.add_argument("--json", help="its numbers, as JSON")
    args = ap.parse_args()
    rows = read(Path(args.rows))
    d = Data(rows, read(Path(args.inputs)), read(Path(args.gate_b)))
    alts = {}
    for spec in args.alt:
        name, _, path = spec.partition("=")
        alts[name] = {r["id"]: r for r in read(Path(path))}
    md: list[str] = []
    w = md.append
    w("# D with and without the gather call, scored\n")
    w("Jev's production routing (D, consequence first, on the new state) on the 843 lines of 2026-10-08, line by "
      "line, through jev/filter.py itself (gather_bench.py), without and with the gather call.\n")
    if len(rows) != 843:
        w(f"**{len(rows)} of 843 lines in the rows.**\n")
    summary = report(d, alts, w)
    Path(args.to).write_text("\n".join(md) + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write("\n".join(md[:30]) + "\n")


if __name__ == "__main__":
    main()
