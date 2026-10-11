#!/usr/bin/env python3
"""The router's evaluation: the run's 116 implementer briefs against the library.

    python3 toolkit/tests/route_eval/run.py              live: `vfg route` on each brief, Jev on the box
    python3 toolkit/tests/route_eval/run.py --words      the fallback alone, no network: a baseline
    ... --briefs toolkit/tests/route_eval/value_asks.jsonl   20 short asks an example does with its values
    ... [--only E001,E007] [--limit N] [--misses] [--out results.jsonl]

briefs.jsonl holds each brief as the orchestrator wrote it on 2026-10-08, its
task and context (given to vfg route as a task in the brain's tasks.json), and its labels: every example script and
preset that is part of the job, from what the implementer actually did, with
players' names replaced (Steve, Alex, Sam); `values_only` marks the 19 jobs the
examples now do by their values alone, where the labelled script should come
back runs_it rather than close. It runs against whatever is in the
library now (VFG_REPO, default this repo), never this server's made/ folder.

A brief passes when every labelled script is among the loaded scripts and
every labelled preset is among the loaded presets or held by a loaded script.
The bars (spec, "Test"): 9 in 10 briefs pass, and 90% of calls answer within a
second, end to end. Live, each call writes a jev usage row with ref route-eval.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(os.path.dirname(HERE))
VFG = os.path.join(TOOLKIT, "vfg")
sys.path.insert(0, os.path.join(TOOLKIT, "lib"))
import route  # noqa: E402

NO_SERVER = "route-eval-none"   # a made/ folder that does not exist: the library only


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def pct(xs, p):
    """The p-th percentile, nearest rank."""
    xs = sorted(xs)
    if not xs:
        return 0
    k = max(0, min(len(xs) - 1, int(round(p / 100.0 * len(xs) + 0.5)) - 1))
    return xs[k]


def call_live(b, repo, home, ref):
    env = dict(os.environ, VFG_REPO=repo, VFG_HOME=home, VFG_SERVER=NO_SERVER)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, VFG, "route", "--task", b["task"], "--ref", ref],
                       input=b["brief"].encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, timeout=120)
    wall = int((time.monotonic() - t0) * 1000)
    if r.returncode != 0:
        raise SystemExit("%s: vfg route exited %d: %s" % (b["id"], r.returncode,
                                                         r.stderr.decode("utf-8", "replace").strip()))
    return json.loads(r.stdout.decode("utf-8")), wall


def call_words(b, cards):
    def down(state, questions, budget):
        raise route.JevFailed("words only")
    t0 = time.monotonic()
    got = route.route(cards, b["brief"], b["task"], b["task_text"], b["context"], down, 0.3, 3)
    return got, int((time.monotonic() - t0) * 1000)


def judge(b, got, holds):
    """(missed scripts, missed presets) for one brief."""
    scripts = [s["name"] for s in got["scripts"]]
    presets = set(p["name"] for p in got["presets"])
    for s in scripts:
        presets.update(holds.get(s, ()))
    return ([s for s in b["scripts"] if s not in scripts],
            [p for p in b["presets"] if p not in presets])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--words", action="store_true", help="the fallback alone, in process, no network")
    ap.add_argument("--repo", default=os.environ.get("VFG_REPO") or os.path.dirname(TOOLKIT),
                    help="the repo whose library is read (default: VFG_REPO, else this one)")
    ap.add_argument("--briefs", default=os.path.join(HERE, "briefs.jsonl"))
    ap.add_argument("--only", help="comma list of brief ids")
    ap.add_argument("--limit", type=int, help="the first N briefs")
    ap.add_argument("--misses", action="store_true", help="print each brief that fails, and why")
    ap.add_argument("--out", help="write each brief's picks and verdict here, as JSON lines")
    ap.add_argument("--ref", default="route-eval", help="the usage rows' ref, live (route-eval)")
    a = ap.parse_args()

    briefs = load(a.briefs)
    if a.only:
        want = set(a.only.split(","))
        briefs = [b for b in briefs if b["id"] in want]
    if a.limit:
        briefs = briefs[:a.limit]
    cards = route.load_cards(a.repo, NO_SERVER)
    have = {(c["kind"], c["name"]) for c in cards}
    holds = {c["name"]: c["presets"] for c in cards if c["kind"] == "script"}
    n_scripts = sum(1 for c in cards if c["kind"] == "script")
    print("library: %d example scripts, %d presets in %s" % (n_scripts, len(cards) - n_scripts, a.repo))

    # A VFG_HOME of its own, with this PC's config and known hosts, whose brain/tasks.json holds
    # each brief's task: vfg route --task reads its text there, and the context as the player's line.
    home = tempfile.mkdtemp(prefix="route-eval-")
    real = os.path.expanduser(os.environ.get("VFG_HOME") or "~/.vfg")
    for name in ("config", "known_hosts"):
        if os.path.exists(os.path.join(real, name)):
            os.symlink(os.path.join(real, name), os.path.join(home, name))
    os.makedirs(os.path.join(home, "brain"))
    tasks = {}
    for b in briefs:
        tasks.setdefault(b["task"], {"id": b["task"], "at": b["at"], "task": b["task_text"], "line": b["context"],
                                     "status": "open"})
    with open(os.path.join(home, "brain", "tasks.json"), "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, ensure_ascii=False)

    rows, out = [], open(a.out, "w", encoding="utf-8") if a.out else None
    for i, b in enumerate(briefs, 1):
        got, wall = call_words(b, cards) if a.words else call_live(b, a.repo, home, a.ref)
        ms, mp = judge(b, got, holds)
        absent = [s for s in b["scripts"] if ("script", s) not in have] + \
                 [p for p in b["presets"] if ("preset", p) not in have]
        verdicts = {s["name"]: s["verdict"] for s in got["scripts"]}
        row = {"id": b["id"], "task": b["task"], "pass": not ms and not mp, "missed_scripts": ms,
               "verdicts": {s: verdicts[s] for s in b["scripts"] if s in verdicts},
               "values_only": bool(b.get("values_only")),
               "missed_presets": mp, "absent": absent, "labelled": bool(b["scripts"] or b["presets"]),
               "loaded": len(got["scripts"]) + len(got["presets"]), "wall_ms": wall, "ms": got["ms"],
               "tokens": got["tokens"], "fallback": got["fallback"], "why": got.get("why"),
               "scripts": [s["name"] for s in got["scripts"]], "presets": [p["name"] for p in got["presets"]]}
        rows.append(row)
        if out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        if sys.stderr.isatty():
            sys.stderr.write("\r%d/%d" % (i, len(briefs)))
    if sys.stderr.isatty():
        sys.stderr.write("\r")
    if out:
        out.close()

    if a.misses:
        for r in rows:
            if not r["pass"]:
                print("MISS %s %s: scripts %s presets %s%s — loaded %s | %s" % (
                    r["id"], r["task"], ",".join(r["missed_scripts"]) or "-", ",".join(r["missed_presets"]) or "-",
                    " (not in the library: %s)" % ",".join(r["absent"]) if r["absent"] else "",
                    ",".join(r["scripts"]) or "-", ",".join(r["presets"]) or "-"))

    n = len(rows)
    ok = sum(r["pass"] for r in rows)
    labelled = [r for r in rows if r["labelled"]]
    present = [r for r in labelled if not r["absent"]]
    items = sum(len(b["scripts"]) + len(b["presets"]) for b in briefs)
    missed = sum(len(r["missed_scripts"]) + len(r["missed_presets"]) for r in rows)
    absent = sorted(set(x for r in rows for x in r["absent"]))
    walls = [r["wall_ms"] for r in rows]
    loaded = [r["loaded"] for r in rows]
    print("briefs: %d, %d with labels, %d whose labels are all in the library" % (n, len(labelled), len(present)))
    if absent:
        print("labelled but not in the library yet (%d): %s" % (len(absent), ", ".join(absent)))
    print("every labelled item loaded: %d/%d briefs (%.0f%%); %d/%d with labels (%.0f%%); "
          "%d/%d whose labels are all in the library (%.0f%%)" % (
              ok, n, 100.0 * ok / max(1, n),
              sum(r["pass"] for r in labelled), len(labelled), 100.0 * sum(r["pass"] for r in labelled) / max(1, len(labelled)),
              sum(r["pass"] for r in present), len(present), 100.0 * sum(r["pass"] for r in present) / max(1, len(present))))
    print("labelled items loaded: %d/%d (%.0f%%)" % (items - missed, items, 100.0 * (items - missed) / max(1, items)))
    for kind, key, miss in (("script", "scripts", "missed_scripts"), ("preset", "presets", "missed_presets")):
        # each kind alone, over the labels the library has: what a partial library can show
        want = [(b, r) for b, r in zip(briefs, rows) if any((kind, x) in have for x in b[key])]
        n_items = sum(sum(1 for x in b[key] if (kind, x) in have) for b, _ in want)
        n_miss = sum(sum(1 for x in r[miss] if (kind, x) in have) for _, r in want)
        n_ok = sum(1 for b, r in want if not [x for x in r[miss] if (kind, x) in have])
        print("%ss alone, labels in the library: every one loaded for %d/%d briefs (%.0f%%); %d/%d items (%.0f%%)" % (
            kind, n_ok, len(want), 100.0 * n_ok / max(1, len(want)), n_items - n_miss, n_items,
            100.0 * (n_items - n_miss) / max(1, n_items)))
    for title, group in (("all briefs", rows), ("jobs its values alone do", [r for r in rows if r["values_only"]])):
        vs = [v for r in group for v in r["verdicts"].values()]
        if vs and not a.words:
            print("labelled scripts loaded, %s: %d runs_it (%.0f%%), %d close" % (
                title, vs.count("runs_it"), 100.0 * vs.count("runs_it") / len(vs), vs.count("close")))
    print("loaded per brief: median %s, mean %.1f, max %d, of %d in the library" % (
        statistics.median(loaded) if loaded else 0, statistics.mean(loaded) if loaded else 0,
        max(loaded) if loaded else 0, len(cards)))
    print("time per call, end to end: median %d ms, p90 %d ms, max %d ms; %d%% under 1 s" % (
        statistics.median(walls) if walls else 0, pct(walls, 90), max(walls) if walls else 0,
        100.0 * sum(w < 1000 for w in walls) / max(1, n)))
    print("time inside vfg route: median %d ms, p90 %d ms" % (
        statistics.median([r["ms"] for r in rows]) if rows else 0, pct([r["ms"] for r in rows], 90)))
    fell = [r for r in rows if r["fallback"]]
    print("fell back to words: %d%s" % (len(fell), " (%s)" % fell[0]["why"] if fell and not a.words else ""))
    tokens = sum(r["tokens"] for r in rows)
    print("tokens: %d in all, %d a call on average, $%.4f at $42 a billion" % (
        tokens, tokens // max(1, n), tokens * 42e-9))
    bar1 = ok >= 0.9 * n
    bar2 = sum(w < 1000 for w in walls) >= 0.9 * n
    print("BAR every labelled item loaded for 9 in 10 briefs: %s" % ("PASS" if bar1 else "FAIL"))
    print("BAR 90%% of calls within a second: %s%s" % ("PASS" if bar2 else "FAIL",
                                                     " (words only: not a Jev call)" if a.words else ""))
    return 0 if bar1 and bar2 else 1


if __name__ == "__main__":
    sys.exit(main())
