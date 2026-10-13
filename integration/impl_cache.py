#!/usr/bin/env python3
"""Test 6 of Prototype_Implementer_Harness, the cache: DeepSeek's cache hits over each task's
session, read from the runner's log of a replay (impl_replay.py's OUT/brain.log): each model
request's tokens as the provider counted them, `request <task> <model> in=… cached=… written=…`.

    python3 integration/impl_cache.py OUT_DIR [OUT_DIR …] [--follow-ups 10]

For each task: its runs (a start, then each resume), and the share of input tokens read from the
cache over the whole session and over its resumed runs alone. The bar: over a resumed session of
ten follow-ups, at least 90% of input tokens are cache hits. With messages, a follow-up that lands
in a live run adds no run of its own, so a task's follow-ups are counted from the replay's sends.
Prints a table; writes nothing."""
import argparse
import json
import os
import re
import sys

REQ = re.compile(r"^\S+ request (\S+) \S+ in=(\d+) cached=(\d+) written=(\d+) out=(\d+) size=(\d+) cost=([\d.]+)")
RUN = re.compile(r"^\S+ implementer (\S+) (started|resumed)\b")


def read(out: str) -> dict:
    tasks: dict = {}
    with open(os.path.join(out, "brain.log"), encoding="utf-8") as f:
        for line in f:
            m = RUN.match(line)
            if m:
                t = tasks.setdefault(m.group(1), {"runs": []})
                t["runs"].append({"kind": m.group(2), "in": 0, "cached": 0, "requests": 0, "cost": 0.0})
                continue
            m = REQ.match(line)
            if m and m.group(1) in tasks:
                r = tasks[m.group(1)]["runs"][-1]
                r["in"] += int(m.group(2))
                r["cached"] += int(m.group(3))
                r["requests"] += 1
                r["cost"] += float(m.group(7))
    try:
        with open(os.path.join(out, "runs.json"), encoding="utf-8") as f:
            sends = json.load(f).get("sends") or []
    except (OSError, ValueError):
        sends = []
    for name, t in tasks.items():
        t["follow_ups"] = sum(1 for s in sends if s["task"] == name and s["kind"] == "follow_up")
    return tasks


def share(runs: list) -> tuple[float, int]:
    total = sum(r["in"] + r["cached"] for r in runs)
    return (sum(r["cached"] for r in runs) / total if total else 0.0), total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outs", nargs="+")
    ap.add_argument("--follow-ups", type=int, default=10, help="the bar's session: at least this many follow-ups")
    a = ap.parse_args(argv)
    print("| Task | Follow-ups | Runs | Requests | Hit rate, whole session | Hit rate, resumed runs | Cost |")
    print("|---|---|---|---|---|---|---|")
    verdicts = []
    for out in a.outs:
        for name, t in read(out).items():
            whole, n = share(t["runs"])
            resumed, m = share([r for r in t["runs"] if r["kind"] == "resumed"])
            print("| %s | %d | %d | %d | %.1f%% of %s | %s | $%.4f |" % (
                name, t["follow_ups"], len(t["runs"]), sum(r["requests"] for r in t["runs"]), 100 * whole,
                "{:,}".format(n), ("%.1f%% of %s" % (100 * resumed, "{:,}".format(m))) if m else "-",
                sum(r["cost"] for r in t["runs"])))
            if t["follow_ups"] >= a.follow_ups:
                verdicts.append((name, whole))
    if verdicts:
        ok = all(w >= 0.9 for _, w in verdicts)
        print("\n%s: sessions of %d follow-ups or more: %s" % ("PASS" if ok else "FAIL", a.follow_ups, ", ".join(
            "%s %.1f%%" % (n, 100 * w) for n, w in verdicts)))
        return 0 if ok else 1
    print("\nno session of %d follow-ups in these logs" % a.follow_ups)
    return 1


if __name__ == "__main__":
    sys.exit(main())
