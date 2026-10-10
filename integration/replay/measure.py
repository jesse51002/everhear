#!/usr/bin/env python3
"""The replay, measured: what run.py left in its output folder, set against the run of 2026-10-08.

    python3 integration/replay/measure.py OUT [--json]

For each task sent: the time from the send to done; the rung it took; its cost by component; and,
from its implementers' transcripts, their time, the calls before their first change and the share
of their time on calls that only look. Then each bar of the spec's "Done when", with the run's
figure on the same tasks beside it, and the router's picks against what the implementers used.
It writes OUT/measure.md and OUT/measure.json, and prints the summary.

The run's own figures for each task are in tasks.jsonl beside this file. They were measured with
job_figures() below, on the run's 116 implementer transcripts, so both sides are measured alike.

**Definitions** (those of the analysis that produced the spec's figures, kept as they were):

- A job's time is its transcript's last timestamp minus its first.
- A job's calls are its tool calls, in order. A call's time is the stretch from the call before it
  (or the job's start) to it: "each stretch of time counted to the call it ended in". Time after
  the last call (writing the report) is in the job's time but in no call's.
- A call only looks when it is Read, Grep or Glob; a Bash call whose command has `vfg history`,
  `vfg script logs` or `vfg script list`; or `vfg cmd` with a query (data get, list, execute
  if/unless, locate, scoreboard players get, forceload query, time query, worldborder get, a bare
  gamerule). A Bash call with `sleep` or `py_compile` checks. Any other Bash call, Write and Edit
  act. These were the run's rules; the commands the run did not have are added: `vfg state` and
  `vfg place list` look, `vfg script check` checks. The rules are applied in that order, so a
  chained `vfg script run x && sleep 3 && vfg script logs x` counts as looking, in the run as here.
  A second reading takes a chained call by its strongest part (act, else check, else look); it is
  reported beside the first, for both sides, and is not the bar.
- A change is Write, Edit, `vfg script run|deploy`, or `vfg cmd` with a command that is not a
  query, and, added for the commands the run did not have, `vfg script new|set|save|stop` and
  `vfg preset save`. Calls before the first change are counted for jobs that made one.
- A job hit the turn cap when it made 40 or more model calls with a tool call in them.
- The rung a task took is the highest rung of any call of its implementers: 1 a plain command
  (`vfg cmd` that is not a query, or `vfg script stop`); 2 a script run (`vfg script run` of a
  script the task did not make itself); 3 a value changed (`vfg script set`, `vfg script save`);
  4 a new script (`vfg script new`, which copies presets in; or written whole with Write, or an
  earlier task's made script edited, both flagged and not counted as "from presets"); 5 a new
  preset (`vfg preset save`, or a file written into made/<server>/presets/).
  0 is none of these: the implementers only looked, or the orchestrator answered alone. A file
  was written when there was a Write or Edit, or `vfg script new|save`, or `vfg preset save`.
- A job's cost is its tokens priced at one unit per fresh input token, 1.25 per token written to
  the 5-minute cache and 2 to the 1-hour one, 0.1 per token read from the cache and 5 per output
  token (Anthropic's ratios; Haiku 5.5's usage rows fit them exactly), the unit set per model so
  that the jobs add up to that model's implementer usage rows. An orchestrator usage row is split
  evenly between the tasks open when it was written (its ref); a router row (jev) goes to its
  task. The replay has no conversation, so its cost is not counted on either side.
- A task's wait is from its send to the start of its first implementer: the orchestrator's turn,
  and any wait for running work it must not collide with.
- A call's prompt is its input tokens, fresh, read and written. By the SDK's cost figures a Haiku
  5.5 call whose prompt passes about 100K tokens costs five times as much a token; the report
  counts those calls.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks.jsonl")
MAX_TURNS = 40
# Past about this many prompt tokens a Haiku 5.5 call costs five times as much a token, by the
# SDK's own cost figures (fitted on the pilot's usage rows: the step lies between 97K and 110K).
LONG_PROMPT = 100000

# The run's queries: a vfg cmd whose command starts so only reads the game.
QUERY = re.compile(r"^\s*(data get|list\b|execute if|execute unless|locate|scoreboard players get|forceload query|"
                   r"time query|worldborder get|gamerule \S+\s*$)", re.I)
VFG_CMD = re.compile(r"vfg cmd\s+['\"]?(.*)")
CHANGE_CMD = re.compile(r"vfg (script (run|deploy|new|set|save|stop)|preset save)\b")
SEGMENT = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")
WEIGHTS = {"fresh": 1.0, "write_5m": 1.25, "write_1h": 2.0, "read": 0.1, "out": 5.0}


# ------------------------------------------------------------------ one job's transcript

def ts(rec):
    t = rec.get("timestamp")
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def is_query(command):
    m = VFG_CMD.search(command)
    return bool(m) and bool(QUERY.match(m.group(1).strip("'\" ")))


def kind(name, inp):
    """look, act, check or other: the run's rules, with the new commands added."""
    if name in ("Read", "Grep", "Glob"):
        return "look"
    if name == "Bash":
        c = inp.get("command", "")
        if re.search(r"vfg (history|script (logs|list)|state\b|place list)", c):
            return "look"
        if is_query(c):
            return "look"
        if re.search(r"sleep|py_compile|vfg script check", c):
            return "check"
        return "act"
    if name in ("Write", "Edit"):
        return "act"
    return "other"


def kind_by_part(name, inp):
    """look, act, check or other, a chained Bash call taken by its strongest part: act if any part
    changes something or is not a vfg look or check, else check if any part checks, else look."""
    if name != "Bash":
        return kind(name, inp)
    kinds = set()
    for part in SEGMENT.split(inp.get("command", "")):
        if not part.strip():
            continue
        if is_change("Bash", {"command": part}):
            kinds.add("act")
        else:
            kinds.add(kind("Bash", {"command": part}))
    for k in ("act", "check", "look"):
        if k in kinds:
            return k
    return "act"


def is_change(name, inp):
    if name in ("Write", "Edit"):
        return True
    if name == "Bash":
        c = inp.get("command", "")
        if CHANGE_CMD.search(c):
            return True
        m = VFG_CMD.search(c)
        if m and not QUERY.match(m.group(1).strip("'\" ")):
            return True
    return False


def job_figures(path):
    """One implementer transcript's figures: {"model", "s", "calls", "before", "to_change_s",
    "look_s", "act_s", "check_s", "other_s", "turns", "turn_cap", "tokens", "weighted", "tools"}."""
    recs = read_jsonl(path)
    times = [t for t in (ts(r) for r in recs) if t]
    if not times:
        return None
    start, end = min(times), max(times)
    calls = []
    usage = {}
    tool_msgs = set()
    model = None
    for r in recs:
        if r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        model = model or m.get("model")
        mid = m.get("id") or r.get("uuid")
        if m.get("usage"):
            usage[mid] = m["usage"]  # the last line of a message wins
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                calls.append((ts(r), b.get("name"), b.get("input") or {}))
                tool_msgs.add(mid)
    agg = {"look": 0.0, "act": 0.0, "check": 0.0, "other": 0.0}
    agg2 = dict(agg)
    prev = start
    for t, n, i in calls:
        gap = max(0.0, (t - prev).total_seconds())
        agg[kind(n, i)] += gap
        agg2[kind_by_part(n, i)] += gap
        prev = t
    first = next(((k, t) for k, (t, n, i) in enumerate(calls) if is_change(n, i)), None)
    tok = {"fresh": 0, "write_5m": 0, "write_1h": 0, "read": 0, "out": 0}
    for u in usage.values():
        tok["fresh"] += u.get("input_tokens") or 0
        tok["read"] += u.get("cache_read_input_tokens") or 0
        tok["out"] += u.get("output_tokens") or 0
        w = u.get("cache_creation_input_tokens") or 0
        cc = u.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens") or 0
        tok["write_1h"] += w1h
        tok["write_5m"] += w - w1h
    ctx = sorted((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                 + (u.get("cache_creation_input_tokens") or 0) for u in usage.values())
    return {
        "model": model or "?",
        "start": start.isoformat(),
        "s": round((end - start).total_seconds(), 1),
        "calls": len(calls),
        "before": first[0] if first else None,
        "to_change_s": round((first[1] - start).total_seconds(), 1) if first else None,
        "look_s": round(agg["look"], 1), "act_s": round(agg["act"], 1),
        "check_s": round(agg["check"], 1), "other_s": round(agg["other"], 1),
        "look_by_part_s": round(agg2["look"], 1),
        "ctx_max": ctx[-1] if ctx else 0, "ctx_median": ctx[len(ctx) // 2] if ctx else 0,
        "calls_over_100k": sum(1 for c in ctx if c > LONG_PROMPT), "model_calls": len(ctx),
        "turns": len(tool_msgs), "turn_cap": len(tool_msgs) >= MAX_TURNS,
        "tokens": tok, "weighted": sum(WEIGHTS[k] * v for k, v in tok.items()),
        "tools": [(n, _short(n, i)) for _, n, i in calls],
    }


def _short(name, inp):
    if name == "Bash":
        return inp.get("command", "")
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", "")
    if name in ("Grep", "Glob"):
        return inp.get("pattern", "")
    return ""


def price(jobs, rows_cost_by_model):
    """Sets each job's "cost": its weighted tokens at its model's unit, the unit making the jobs of a
    model add up to that model's implementer usage rows."""
    def row_model(m):  # a transcript's model id may carry a suffix the rows' does not, or the other way
        if m in rows_cost_by_model:
            return m
        return next((r for r in rows_cost_by_model if r and m and (r in m or m in r)), m)

    weighted = defaultdict(float)
    for j in jobs:
        weighted[row_model(j["model"])] += j["weighted"]
    for j in jobs:
        m = row_model(j["model"])
        total = rows_cost_by_model.get(m)
        j["cost"] = (j["weighted"] / weighted[m] * total) if total and weighted[m] else None


# ------------------------------------------------------------------ rungs

RUNG_WORDS = {0: "none", 1: "plain command", 2: "ran a script", 3: "changed a value",
              4: "new script from presets", 5: "new preset"}


def rung_words(rung):
    if rung["rung"] == 4 and rung["edited"] and not rung["made"]:
        return "edited a made script (%s)" % ", ".join(rung["edited"])
    if rung["rung"] == 4 and rung["hand_written"]:
        return "new script, written by hand"
    return RUNG_WORDS[rung["rung"]] + (" (and edited %s)" % ", ".join(rung["edited"]) if rung["edited"] else "")


def task_rung(tools, server):
    """The highest rung of a task's calls, whether a file was written, and what it used:
    {"rung", "file", "hand_written", "edited", "ran", "set", "made", "from", "presets", "read"}.
    hand_written: a new script written whole with Write, not copied by `vfg script new`; edited: made
    scripts the task changed with Edit that it did not make itself (an earlier task's)."""
    made = set()
    out = {"rung": 0, "file": False, "hand_written": False, "edited": [], "ran": [], "set": [], "made": [],
           "from": [], "presets": [], "saved_presets": [], "read": []}
    mine = "made/%s/" % server

    def up(n):
        out["rung"] = max(out["rung"], n)

    for name, arg in tools:
        if name in ("Write", "Edit"):
            out["file"] = True
            if "%spresets/" % mine in arg:
                up(5)
            elif "%sscripts/" % mine in arg:
                folder = arg.split("%sscripts/" % mine, 1)[1].split("/", 1)[0]
                if folder not in made and name == "Write":
                    out["hand_written"] = True
                    made.add(folder)
                    out["made"].append(folder)
                elif folder not in made and folder not in out["edited"]:
                    out["edited"].append(folder)
                up(4)
            continue
        if name == "Read":
            out["read"].append(arg)
            continue
        if name != "Bash":
            continue
        for part in SEGMENT.split(arg):
            w = part.split()
            if len(w) < 2 or w[0] != "vfg":
                continue
            if w[1] == "cmd" and not is_query(part):
                up(1)
            elif w[1] == "script" and len(w) > 2:
                op, rest = w[2], [x for x in w[3:] if not x.startswith("-") and "=" not in x]
                if op == "new" and rest:
                    made.add(rest[0])
                    out["made"].append(rest[0])
                    out["file"] = True
                    m = re.search(r"--from[ =](\S+)", part)
                    if m:
                        out["from"].append(m.group(1))
                    out["presets"] += re.findall(r"--preset[ =](\S+)", part)
                    up(4)
                elif op == "run" and rest:
                    name_ = [x for x in rest if not re.match(r"T\d+$", x)]
                    if name_:
                        out["ran"].append(name_[0])
                        up(4 if name_[0] in made else 2)
                elif op == "set":
                    out["set"].append(rest[0] if rest else "?")
                    up(3)
                elif op == "save":
                    out["file"] = True
                    up(3)
                elif op == "stop":
                    up(1)
            elif w[1] == "preset" and len(w) > 2 and w[2] == "save":
                out["file"] = True
                out["saved_presets"].append(w[-1])
                up(5)
    return out


# ------------------------------------------------------------------ the output folder

def load_tasks(path=TASKS):
    return {t["id"]: t for t in read_jsonl(path)}


def iso_s(a, b):
    if not a or not b:
        return None
    pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return round((pb - pa).total_seconds(), 1)


def split_cost(rows, component):
    """{task id: cost} from usage rows of one component, each row split evenly over its refs."""
    out = defaultdict(float)
    for r in rows:
        if r.get("component") != component or not r.get("cost_usd"):
            continue
        refs = [x for x in (r.get("ref") or "").split(",") if re.match(r"T\d+$", x)]
        for x in refs:
            out[x] += r["cost_usd"] / len(refs)
    return out


def replay_jobs(out_dir):
    """Every implementer of the replay: {"task", "description", "path", **job_figures}."""
    jobs = []
    for meta in sorted(glob.glob(os.path.join(out_dir, "transcripts", "*", "subagents", "agent-*.meta.json"))):
        try:
            with open(meta) as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if m.get("agentType") not in (None, "implementer"):
            continue
        path = meta[:-len(".meta.json")] + ".jsonl"
        fig = job_figures(path)
        if not fig:
            continue
        tid = (re.match(r"\s*(T\d+)", m.get("description") or "") or [None, None])[1]
        jobs.append(dict(fig, task=tid, description=m.get("description"), path=os.path.relpath(path, out_dir)))
    return jobs


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def measure(out_dir, tasks=None):
    tasks = tasks or load_tasks()
    sent = read_jsonl(os.path.join(out_dir, "sent.jsonl"))
    with open(os.path.join(out_dir, "orch", "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    rows = read_jsonl(os.path.join(out_dir, "usage.jsonl"))
    server = "replay"
    try:
        with open(os.path.join(out_dir, "run.json")) as f:
            server = json.load(f).get("server") or server
    except (OSError, ValueError):
        pass
    jobs = replay_jobs(out_dir)
    impl_rows = defaultdict(float)
    for r in rows:
        if r.get("component") == "implementer" and r.get("cost_usd"):
            impl_rows[r.get("model")] += r["cost_usd"]
    price(jobs, impl_rows)
    orch = split_cost(rows, "orchestrator")
    jev = split_cost(rows, "jev")
    by_task = defaultdict(list)
    for j in jobs:
        by_task[j["task"]].append(j)

    per = []
    for s in sent:
        rid, tid = s["replay_id"], s["run_id"]
        st = (state.get("tasks") or {}).get(rid) or {}
        run = tasks.get(tid, {})
        js = by_task.get(rid, [])
        tools = [t for j in js for t in j["tools"]]
        rung = task_rung(tools, server)
        replies = [r for r in st.get("replies") or [] if r.get("status") in ("done", "failed")]
        picks = []
        for e in st.get("implementers") or []:
            rt = e.get("route") or {}
            picks.append({"scripts": [(x.get("name"), x.get("verdict")) for x in rt.get("scripts") or []],
                          "presets": [x.get("name") for x in rt.get("presets") or []],
                          "fallback": rt.get("fallback"), "failed": rt.get("failed"), "ms": rt.get("ms")})
        picked = {n for p in picks for n, _ in p["scripts"]} | {n for p in picks for n in p["presets"]}
        used = set(rung["ran"]) | set(rung["from"]) | set(rung["presets"])
        used |= {os.path.basename(os.path.dirname(p)) for p in rung["read"] if "/scripts/" in p and p.endswith("main.py")}
        used |= {os.path.splitext(os.path.basename(p))[0] for p in rung["read"] if "/_presets/" in p or "/presets/" in p}
        used -= set(rung["made"])
        per.append({
            "id": tid, "replay_id": rid, "class": run.get("class"), "task": run.get("task"),
            "status": st.get("status") or "open", "send_to_done_s": iso_s(s.get("at"), st.get("closedAt")),
            "reply": replies[-1]["text"] if replies else None,
            "rung": rung["rung"], "rung_words": rung_words(rung), "file": rung["file"],
            "hand_written": rung["hand_written"], "made": rung["made"], "saved_presets": rung["saved_presets"],
            "edited": rung["edited"],
            "wait_s": iso_s(s.get("at"), min(j["start"] for j in js)) if js else None,
            "jobs": len(js), "impl_s": round(sum(j["s"] for j in js), 1),
            "job_s": [j["s"] for j in js], "before": [j["before"] for j in js],
            "look_s": round(sum(j["look_s"] for j in js), 1),
            "look_by_part_s": round(sum(j.get("look_by_part_s") or 0 for j in js), 1),
            "call_s": round(sum(j["look_s"] + j["act_s"] + j["check_s"] + j["other_s"] for j in js), 1),
            "turn_cap": sum(1 for j in js if j["turn_cap"]), "models": sorted({j["model"] for j in js}),
            "cost": {"implementer": round(sum(j["cost"] or 0 for j in js), 6), "orchestrator": round(orch.get(rid, 0), 6),
                     "jev": round(jev.get(rid, 0), 6)},
            "picks": picks, "picked_not_used": sorted(picked - used), "used_not_picked": sorted(used - picked),
            "picked_and_used": sorted(picked & used),
            "run": run.get("run") or {},
        })
    return {"tasks": per, "jobs": jobs, "summary": summarize(per, jobs, tasks, rows)}


# ------------------------------------------------------------------ the bars

def summarize(per, jobs, tasks, rows):
    ids = [p["id"] for p in per]
    run_jobs = [j for t in ids for j in (tasks.get(t, {}).get("run", {}).get("jobs") or [])]
    all_run_jobs = [j for t in tasks.values() for j in (t.get("run", {}).get("jobs") or [])]

    def job_bars(js):
        acted = [j for j in js if j.get("before") is not None]
        look = sum(j["look_s"] for j in js)
        calls = sum(j["look_s"] + j["act_s"] + j["check_s"] + j["other_s"] for j in js)
        haiku = [j for j in js if "haiku" in (j.get("model") or "")]
        look2 = sum(j.get("look_by_part_s") or 0 for j in js)
        mc = sum(j.get("model_calls") or 0 for j in js)
        return {"jobs": len(js), "median_job_s": median([j["s"] for j in js]),
                "look_share_by_part": round(look2 / calls, 3) if calls else None,
                "calls_over_100k": sum(j.get("calls_over_100k") or 0 for j in js), "model_calls": mc,
                "median_ctx": median([j.get("ctx_median") for j in js]),
                "median_before": median([j["before"] for j in acted]),
                "look_share": round(look / calls, 3) if calls else None,
                "turn_cap": sum(1 for j in js if j.get("turn_cap")), "haiku_jobs": len(haiku),
                "haiku_turn_cap": sum(1 for j in haiku if j.get("turn_cap")),
                "cost": round(sum(j.get("cost") or 0 for j in js), 4)}

    rp = job_bars(jobs)
    rn_same = job_bars(run_jobs)
    rn_all = job_bars(all_run_jobs)

    def cls(c):
        return [p for p in per if p["class"] == c]

    value = cls("value")
    value_ok = [p for p in value if p["status"] == "done" and 1 <= p["rung"] <= 3 and not p["file"]
                and p["send_to_done_s"] is not None and p["send_to_done_s"] <= 30]
    new = cls("new")
    new_ok = [p for p in new if p["status"] == "done" and 1 <= p["rung"] <= 4 and not p["saved_presets"]
              and not p["hand_written"] and not p["edited"]]
    run_value_30 = [t for t in (tasks.get(p["id"]) for p in value)
                    if t and (t["run"].get("send_to_done_s") or 1e9) <= 30]
    cost = {c: round(sum(p["cost"][c] for p in per), 4) for c in ("implementer", "orchestrator", "jev")}
    run_cost = {c: round(sum((tasks.get(i, {}).get("run", {}).get("cost") or {}).get(c) or 0 for i in ids), 4)
                for c in ("implementer", "orchestrator")}
    bars = [
        ("implementer's median job (s)", "<= half the run's 56", rp["median_job_s"], rn_same["median_job_s"],
         rp["median_job_s"] is not None and rp["median_job_s"] <= 28),
        ("median calls before the first change", "<= 1 (run: 4)", rp["median_before"], rn_same["median_before"],
         rp["median_before"] is not None and rp["median_before"] <= 1),
        ("look-only share of implementer call time", "<= 10% (run: 40%)", rp["look_share"], rn_same["look_share"],
         rp["look_share"] is not None and rp["look_share"] <= 0.10),
        ("the same, each chained Bash call taken by its strongest part", "(not a bar: a second reading)",
         rp["look_share_by_part"], rn_same["look_share_by_part"], None),
        ("value-only tasks by a run or a value, no file, closed <= 30 s", ">= 3 in 4",
         "%d of %d" % (len(value_ok), len(value)), "%d of %d closed <= 30 s" % (len(run_value_30), len(value)),
         bool(value) and len(value_ok) * 4 >= len(value) * 3),
        ("needed-something-new tasks by a script from presets, no new preset", ">= half",
         "%d of %d" % (len(new_ok), len(new)), "0 (no library)", bool(new) and len(new_ok) * 2 >= len(new)),
        ("implementer cost on these tasks ($)", "<= half the run's ($12.33 for all)", cost["implementer"],
         run_cost["implementer"], cost["implementer"] <= run_cost["implementer"] / 2),
        ("jobs hitting the turn cap", "<= 1 in 20 (run Haiku: 4 in 25)", "%d of %d" % (rp["turn_cap"], rp["jobs"]),
         "%d of %d" % (rn_same["turn_cap"], rn_same["jobs"]), rp["turn_cap"] * 20 <= max(rp["jobs"], 1)),
    ]
    rungs = defaultdict(lambda: defaultdict(int))
    for p in per:
        rungs[p["class"]][p["rung"]] += 1
    return {"replay": rp, "run_same_tasks": rn_same, "run_all": rn_all, "cost": cost, "run_cost": run_cost,
            "bars": [{"bar": b, "target": t, "replay": r, "run": n, "pass": ok} for b, t, r, n, ok in bars],
            "rungs_by_class": {c: dict(v) for c, v in rungs.items()},
            "value_by_rung_no_file": sum(1 for p in value if 1 <= p["rung"] <= 3 and not p["file"]),
            "value_closed_30": sum(1 for p in value if (p["send_to_done_s"] or 1e9) <= 30),
            "value_median_wait_s": median([p["wait_s"] for p in value]),
            "new_hand_written": sum(1 for p in new if p["hand_written"]),
            "new_edited_made": sum(1 for p in new if p["edited"]),
            "median_wait_s": median([p["wait_s"] for p in per]),
            "tasks": len(per), "closed": sum(1 for p in per if p["status"] in ("done", "failed")),
            "done": sum(1 for p in per if p["status"] == "done"),
            "median_send_to_done_s": median([p["send_to_done_s"] for p in per]),
            "run_median_send_to_done_s": median([tasks.get(i, {}).get("run", {}).get("send_to_done_s") for i in ids]),
            "usage_rows": len(rows)}


# ------------------------------------------------------------------ the report

def fmt(x, digits=1):
    if x is None:
        return "-"
    if isinstance(x, float):
        return ("%." + str(digits) + "f") % x
    return str(x)


def report(m):
    s = m["summary"]
    out = ["# Replay measured", "",
           "%d tasks sent, %d closed, %d done. Median send to done %s s (the run, same tasks: %s s)." % (
               s["tasks"], s["closed"], s["done"], fmt(s["median_send_to_done_s"]), fmt(s["run_median_send_to_done_s"])),
           "", "## The bars", "", "| Bar | Target | Replay | Run, same tasks | Pass |", "|---|---|---|---|---|"]
    for b in s["bars"]:
        r = b["replay"]
        n = b["run"]
        if "share" in b["bar"] or "strongest part" in b["bar"]:
            r = "%d%%" % round(r * 100) if r is not None else "-"
            n = "%d%%" % round(n * 100) if n is not None else "-"
        out.append("| %s | %s | %s | %s | %s |" % (b["bar"], b["target"], fmt(r, 3) if isinstance(r, float) else fmt(r),
                                                fmt(n, 3) if isinstance(n, float) else fmt(n), "-" if b["pass"] is None else "yes" if b["pass"] else "NO"))
    out += ["", "The run on all its 116 jobs: median job %s s, median calls before a change %s, look share %s, "
            "turn cap %d of %d (Haiku %d of %d)." % (
                fmt(s["run_all"]["median_job_s"]), fmt(s["run_all"]["median_before"]),
                "%d%%" % round((s["run_all"]["look_share"] or 0) * 100), s["run_all"]["turn_cap"], s["run_all"]["jobs"],
                s["run_all"]["haiku_turn_cap"], s["run_all"]["haiku_jobs"]),
            "", "Value-only tasks: %d of %d by a run or a value with no file written; %d of %d closed within 30 s; median "
            "wait from the send to their implementer's start %s s. Needed something new: %d written whole by hand, %d by "
            "editing an earlier task's made script. Median wait from a send to its implementer's start, all tasks: %s s." % (
                s["value_by_rung_no_file"], sum(s["rungs_by_class"].get("value", {}).values()),
                s["value_closed_30"], sum(s["rungs_by_class"].get("value", {}).values()), fmt(s["value_median_wait_s"]),
                s["new_hand_written"], s["new_edited_made"], fmt(s["median_wait_s"])),
            "", "Implementer prompts: median %s tokens a call; %d of %d model calls over %dK tokens, where Haiku 5.5 costs "
            "five times as much a token (the run: median %s, %d of %d over)." % (
                fmt(s["replay"]["median_ctx"], 0), s["replay"]["calls_over_100k"], s["replay"]["model_calls"],
                LONG_PROMPT // 1000, fmt(s["run_same_tasks"]["median_ctx"], 0), s["run_same_tasks"]["calls_over_100k"],
                s["run_same_tasks"]["model_calls"]),
            "", "Cost, replay: implementer $%.4f, orchestrator $%.4f, router (jev) $%.4f. The run on the same tasks: "
            "implementer $%.4f, orchestrator $%.4f." % (s["cost"]["implementer"], s["cost"]["orchestrator"], s["cost"]["jev"],
                                                    s["run_cost"]["implementer"], s["run_cost"]["orchestrator"]),
            "", "Rungs by class: " + "; ".join("%s %s" % (c, ", ".join("%s x%d" % (RUNG_WORDS[r], n) for r, n in sorted(v.items())))
                                               for c, v in sorted(s["rungs_by_class"].items(), key=lambda x: str(x[0]))),
            "", "## Per task", "",
            "| Task | Class | Status | Send→done s (run) | Wait s | Rung (run) | File | Jobs | Impl s (run) | Calls before change (run) | "
            "Look s / call s | Impl $ (run) | Orch $ (run) | Jev $ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in m["tasks"]:
        r = p["run"]
        flags = ""
        out.append("| %s (%s) %s | %s | %s | %s (%s) | %s | %s (%s) | %s | %d | %s (%s) | %s (%s) | %s / %s | %.4f (%.4f) | %.4f (%.4f) | %.4f |" % (
            p["id"], p["replay_id"], (p["task"] or "")[:60].replace("|", "/"), p["class"], p["status"],
            fmt(p["send_to_done_s"], 0), fmt(r.get("send_to_done_s"), 0), fmt(p["wait_s"], 0), p["rung_words"] + flags,
            r.get("kind") or "-", "yes" if p["file"] else "no", p["jobs"], fmt(p["impl_s"], 0), fmt(r.get("impl_s"), 0),
            ",".join(fmt(b) for b in p["before"]) or "-", ",".join(fmt(j.get("before")) for j in r.get("jobs") or []) or "-",
            fmt(p["look_s"], 0), fmt(p["call_s"], 0), p["cost"]["implementer"], (r.get("cost") or {}).get("implementer") or 0,
            p["cost"]["orchestrator"], (r.get("cost") or {}).get("orchestrator") or 0, p["cost"]["jev"]))
    out += ["", "## The router's picks against what the implementers used", "",
            "| Task | Picked (scripts: verdict; presets) | Used | Picked and used | Used, not picked | Picked, not used |",
            "|---|---|---|---|---|---|"]
    for p in m["tasks"]:
        picks = "; ".join(("fallback " if k["fallback"] else "") + ("failed: %s" % k["failed"] if k.get("failed") else
                          ", ".join("%s %s" % (n, v) for n, v in k["scripts"]) + (" + " + ", ".join(k["presets"]) if k["presets"] else ""))
                          for k in p["picks"]) or "-"
        used = sorted(set(p["picked_and_used"]) | set(p["used_not_picked"]))
        out.append("| %s | %s | %s | %s | %s | %s |" % (p["id"], picks, ", ".join(used) or "-", ", ".join(p["picked_and_used"]) or "-",
                                                        ", ".join(p["used_not_picked"]) or "-", ", ".join(p["picked_not_used"]) or "-"))
    out += ["", "## Replies", ""]
    for p in m["tasks"]:
        out.append("- **%s** %s: %s" % (p["id"], p["status"], (p["reply"] or "-").replace("\n", " ")))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", help="the output folder run.py left")
    ap.add_argument("--json", action="store_true", help="print the JSON instead of the report")
    a = ap.parse_args(argv)
    m = measure(a.out)
    md = report(m)
    with open(os.path.join(a.out, "measure.md"), "w", encoding="utf-8") as f:
        f.write(md)
    slim = dict(m, jobs=[{k: v for k, v in j.items() if k != "tools"} for j in m["jobs"]])
    with open(os.path.join(a.out, "measure.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=1, default=str)
    print(json.dumps(slim["summary"], indent=1, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
