#!/usr/bin/env python3
"""The router's evaluation: the run's 116 implementer briefs against the engine's library.

    python3 toolkit/tests/route_eval/run.py              live: `vfg route` on each brief, Jev on the box
    python3 toolkit/tests/route_eval/run.py --words      the fallback alone, no network: a baseline
    ... --briefs toolkit/tests/route_eval/value_asks.jsonl   20 short asks a class does with its args
    ... [--only E001,E007] [--limit N] [--misses] [--out results.jsonl]

briefs.jsonl holds each brief as the orchestrator wrote it on 2026-10-08, its task (its id of that
day, a label here) and the task's text and context, and its labels: the example scripts and presets
of the old library that were part of the job, from what the implementer actually did, with
players' names replaced (Steve, Alex, Sam); `values_only` marks the 19 jobs an example did by its
values alone, where the labelled class should come back runs_it rather than close.

The old library is gone (the game engine spec, part 2): every script that was kept is a class in
engine/library/, and `vfg route` picks classes. A script's label is read as the class that took it
over: the library's class of the same name, else the one SCRIPT_CLASS names (a script that was no
action's), else the one lib/migrate_map.py's row for its action names (zoo and summon_around are
horde, meteor_shower is meteor, ...); a script none knows is counted "absent". Preset labels are
dropped: a preset is a kit helper now, in every implementer's prefix (engine/KIT.md), never routed.
It runs against whatever is in the library now (VFG_REPO, default this repo), never this server's
made/ folder.

--words gives route.route, in process, the brief with its task's text and context. Live, `vfg
route` reads a task from the box's tasks table, which only the brain writes, so the eval runs `vfg
route --ref <ref>` with no --task: Jev gets the brief alone.

A brief passes when every labelled class is among the loaded classes (at most 3: Jesse's cap). The
bars (spec, "Test"): 9 in 10 briefs pass, and 90% of calls answer within a second, end to end.
Live, each call writes a jev usage row with ref route-eval.
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
import migrate_map  # noqa: E402
import route  # noqa: E402

NO_SERVER = "route-eval-none"   # a made/ folder that does not exist: the library only
# Old scripts whose name was no action's (a script under several actions, or one a boss ran): the
# class that took each over, by the library's own module docstrings.
SCRIPT_CLASS = {"dragon_boss": "boss_fight", "gravity_staff": "gravity_lift_staff", "keep_effects": "potion_effect",
                "mountain_trees": "mountains", "sniper_rifle": "super_sniper"}


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


def class_of(script, have):
    """The class an old script's label is now: the library's of the same name, else SCRIPT_CLASS's,
    else the one its action's migration row names; the script's own name when none knows it
    (absent)."""
    if script in have:
        return script
    if script in SCRIPT_CLASS:
        return SCRIPT_CLASS[script]
    row = migrate_map.MAP.get(script) or {}
    return row.get("scenario") or row.get("ability") or script


def labels(b, have):
    """A brief's labelled classes, each once, in order."""
    return list(dict.fromkeys(class_of(s, have) for s in b.get("scripts") or []))


def call_live(b, repo, home, ref):
    """`vfg route --ref <ref>` on the brief alone: no --task, as the eval may not write the box's
    tasks table, where vfg route reads one."""
    env = dict(os.environ, VFG_REPO=repo, VFG_HOME=home, VFG_SERVER=NO_SERVER)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, VFG, "route", "--ref", ref],
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
    have = {c["name"] for c in cards}
    print("library: %d classes in %s" % (len(cards), a.repo))

    # A VFG_HOME of its own, with this PC's config and known hosts and no brain/ folder: live, each
    # brief goes to vfg route alone.
    home = tempfile.mkdtemp(prefix="route-eval-")
    real = os.path.expanduser(os.environ.get("VFG_HOME") or "~/.vfg")
    for name in ("config", "known_hosts"):
        if os.path.exists(os.path.join(real, name)):
            os.symlink(os.path.join(real, name), os.path.join(home, name))

    rows, out = [], open(a.out, "w", encoding="utf-8") if a.out else None
    for i, b in enumerate(briefs, 1):
        got, wall = call_words(b, cards) if a.words else call_live(b, a.repo, home, a.ref)
        want = labels(b, have)
        picked = [c["name"] for c in got["classes"]]
        verdicts = {c["name"]: c["verdict"] for c in got["classes"]}
        missed = [c for c in want if c not in picked]
        row = {"id": b["id"], "task": b["task"], "pass": not missed, "labels": want, "missed": missed,
               "absent": [c for c in want if c not in have], "verdicts": {c: verdicts[c] for c in want if c in verdicts},
               "values_only": bool(b.get("values_only")), "labelled": bool(want), "loaded": len(picked),
               "wall_ms": wall, "ms": got["ms"], "tokens": got["tokens"], "fallback": got["fallback"],
               "why": got.get("why"), "classes": picked}
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
                print("MISS %s %s: %s%s — loaded %s" % (
                    r["id"], r["task"], ",".join(r["missed"]),
                    " (not in the library: %s)" % ",".join(r["absent"]) if r["absent"] else "",
                    ",".join(r["classes"]) or "-"))

    n = len(rows)
    ok = sum(r["pass"] for r in rows)
    labelled = [r for r in rows if r["labelled"]]
    present = [r for r in labelled if not r["absent"]]
    items = sum(len(r["labels"]) for r in rows)
    missed = sum(len(r["missed"]) for r in rows)
    absent = sorted(set(x for r in rows for x in r["absent"]))
    walls = [r["wall_ms"] for r in rows]
    loaded = [r["loaded"] for r in rows]
    print("briefs: %d, %d with labels, %d whose labels are all in the library" % (n, len(labelled), len(present)))
    print("preset labels dropped (a kit helper now, never routed): %d" % sum(len(b.get("presets") or [])
                                                                           for b in briefs))
    if absent:
        print("labelled but not in the library (%d): %s" % (len(absent), ", ".join(absent)))
    print("every labelled class loaded: %d/%d briefs (%.0f%%); %d/%d with labels (%.0f%%); "
          "%d/%d whose labels are all in the library (%.0f%%)" % (
              ok, n, 100.0 * ok / max(1, n),
              sum(r["pass"] for r in labelled), len(labelled), 100.0 * sum(r["pass"] for r in labelled) / max(1, len(labelled)),
              sum(r["pass"] for r in present), len(present), 100.0 * sum(r["pass"] for r in present) / max(1, len(present))))
    print("labelled classes loaded: %d/%d (%.0f%%)" % (items - missed, items, 100.0 * (items - missed) / max(1, items)))
    for title, group in (("all briefs", rows), ("jobs its args alone do", [r for r in rows if r["values_only"]])):
        vs = [v for r in group for v in r["verdicts"].values()]
        if vs and not a.words:
            print("labelled classes loaded, %s: %d runs_it (%.0f%%), %d close" % (
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
    print("BAR every labelled class loaded for 9 in 10 briefs: %s" % ("PASS" if bar1 else "FAIL"))
    print("BAR 90%% of calls within a second: %s%s" % ("PASS" if bar2 else "FAIL",
                                                     " (words only: not a Jev call)" if a.words else ""))
    return 0 if bar1 and bar2 else 1


if __name__ == "__main__":
    sys.exit(main())
