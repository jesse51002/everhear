#!/usr/bin/env python3
"""Jev's gather call live, on a real 26.3 server on this PC: the Jev routing spec's "Live, on realmc".

    REALMC_ROOT=… python3 integration/jev_live.py [--name jr] [--keep-up] [--no-brain]

Real:  a Minecraft 26.3 server with Carpet's fake players Steve and Alex (integration/realmc, its
       world fresh from the template), the bridge (as incantations_e2e.py starts it), the Jev
       filter (jev/jev_filter.py on the box's Python 3.12) calling the real TypeSafe API with the
       key in the stand-in's .env (REALMC_ROOT's shared/env, which only the filter reads), and the
       brain (`vfg brain start`, as brain_live.py starts it), its model calls through OpenRouter
       (OPEN_ROUTER_API, found as brain_e2e.py finds it, given to the brain's process only).
Posted as the mod posts them: each line said (POST /v1/lines), a second or two apart.

Each scenario is an ask said in two pieces, with the other player saying something between them
that is about the same thing on purpose. Per scenario: the feed has ONE LINE for the ask, its
text the first piece … the ask; the other player's line is never asked about and in no group;
the brain's turn is for that line (its usage rows, Respond's `#seq` and its loop's steps'
`#seq s<n>`; brain.log's Respond line with the line's words and the loop's line for the asker;
the task it opened), not for the first piece; and, a bonus, what the brain did is in the world
(over RCON). Jev is not deterministic: every line's verdict is printed, and a scenario whose first
piece Jev sent to the AI on its own, or whose ask it did not, is reported as Jev's routing, not
the gather's, and skipped. PASS/FAIL per check; exit 1 when one failed or no scenario could be
checked. Each run starts a new transcript:
the last run's vfg.db, feed, lookout, menu and logs move to vfg/runs/. --no-brain checks Jev alone
(no OpenRouter). The server is left down unless --keep-up. Never `down --all`, `--purge-key`,
`--regen`, `key` or `carpet`: other builders share REALMC_ROOT.
"""
import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
sys.path.insert(0, HERE)

import brain_live  # noqa: E402
import incantations_e2e  # noqa: E402  (imports realmc and players, from REALMC_ROOT)
from brain_live import KEY, parse_iso, short, wait_for  # noqa: E402

JEV_BUDGET, MODEL_BUDGET = 0.10, 0.50     # dollars of TypeSafe and of OpenRouter a run may spend
TO_THE_AI, HELD_BACK = ("action_oriented", "conversational"), ("context", "not_relevant")
JOIN = " … "

# (name, asker, other, [the first piece, the other's line, the ask], what the brain may do in the world)
SCENARIOS = [
    ("the dark cave", "Steve", "Alex",
     ["the cave over there is pitch black", "yeah it's really dark", "AI, give me something so I can see in there"],
     {"effects": ["night_vision"], "items": ["torch"]}),
    ("the broken pickaxe", "Alex", "Steve",
     ["my diamond pickaxe just broke", "mine's about to break too", "AI, can you give me a new one"],
     {"effects": [], "items": ["diamond_pickaxe"]}),
    ("the ocean monument", "Steve", "Alex",
     ["I'm going down into that ocean monument", "the guardians in there hit really hard",
      "AI, I need to be able to breathe down there"],
     {"effects": ["water_breathing", "conduit_power"], "items": []}),
]

JUDGE = re.compile(r"^judge (\S+) \S+ \S+ (\S+) (\S+) · (.*) · (\d+)ms")
GATHER = re.compile(r"^gather (\S+) \S+ · (\d+) asked, (\d+) yes: (.*) · no: (.*) · (\d+)ms (\S+)tok")
# What brain.log says of a line's turn: Respond's answer, with the line's words (`respond #5 Steve: '…' -> …`),
# and the loop's last line (`loop #5 Steve: 1 step(s), …, ended reply`); a step's line holds no words of the line.
SAID = re.compile(r"^\S+ respond #(\d+) (\S+): (.*?) -> ")
LOOP = re.compile(r"^\S+ loop #(\d+) (\S+): \d+ step\(s\), .*, ended (\S+)$")


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def answers(listed):
    """A gather line's `<id> <conf>, …` list (`-` is none) as {id: conf}."""
    return dict(part.split() for part in listed.split(", ") if len(part.split()) == 2)


class Report(brain_live.Report):
    def __init__(self):
        super().__init__()
        self.skipped, self.bonus, self.scenarios = [], [], 0

    def skip(self, name, why):
        self.skipped.append(name)
        print("  SKIP  %s\n          %s" % (name, why), flush=True)

    def extra(self, ok, name, detail=""):
        """A bonus check: printed, and not counted."""
        self.bonus.append(bool(ok))
        print("  %s  (bonus) %s%s" % ("PASS" if ok else "FAIL", name, "" if ok or not detail else
                                     "\n          " + short(detail, 600)), flush=True)


class Filter:
    """The real Jev filter, on the box's Python, against the stand-in's vfg.db, feed and .env."""

    def __init__(self, srv):
        self.path = os.path.join(srv.dir, "jev.log")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", "TYPESAFE_", KEY))}
        env.update({"VFG_DB": os.path.join(srv.vfg, "vfg.db"), "VFG_FEED": os.path.join(srv.vfg, "feed.log"),
                    "VFG_LIB": os.path.join(srv.vfg, "lib"), "VFG_ENV_FILE": os.path.join(srv.vfg, ".env"),
                    "VFG_LOOKOUT": os.path.join(srv.vfg, "lookout.md"), "VFG_MENU": os.path.join(srv.vfg, "menu.md"),
                    "PYTHONUNBUFFERED": "1"})
        self.log = open(self.path, "w")
        self.proc = subprocess.Popen([os.path.join(srv.vfg, "python", "bin", "python3"), "-u",
                                      os.path.join(REPO, "jev", "jev_filter.py")], env=env, cwd=srv.vfg,
                                     stdout=self.log, stderr=subprocess.STDOUT)

    def journal(self):
        with open(self.path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


class Run:
    def __init__(self, live, bridge, jev, rep, brain):
        self.live, self.bridge, self.jev, self.rep, self.brain = live, bridge, jev, rep, brain
        self.replies = brain_live.Run(live, rep)
        self.said = []      # every line posted: {"id", "who", "text", "end"}
        self.asks = []      # each checked ask: its LINE, its gather line, its times
        self.started = time.time()

    def say(self, who, text, gap=1.2):
        """`text` said `gap` s after the last line ended, about 0.3 s a word, posted when it ends."""
        took = 0.3 * len(text.split()) + 0.3
        time.sleep(gap + took)
        end = time.time()
        code, doc = self.bridge.line(who, text, end - took, end)
        line = {"id": (doc or {}).get("id"), "who": who, "text": text, "start": iso(end - took), "end": end}
        self.said.append(line)
        return line

    def row(self, line):
        return self.live.sql("SELECT * FROM lines WHERE id = ?", (line["id"],))[0]

    def verdicts(self):
        """`judge` journal lines by line id: (class, confidence, D2's two answers, ms)."""
        return {m.group(1): m.groups()[1:] for m in map(JUDGE.match, self.jev.journal()) if m}

    def gather(self, ask_id):
        for m in map(GATHER.match, self.jev.journal()):
            if m and m.group(1) == ask_id:
                return {"asked": int(m.group(2)), "yes": answers(m.group(4)), "no": answers(m.group(5)),
                        "ms": int(m.group(6)), "tokens": m.group(7)}
        return None


def scenario(run, name, asker, other, said, world):
    rep, live = run.rep, run.live
    rep.head("%s: %s says it in two pieces, %s talks between them" % (name, asker, other))
    before = {item: live.count_items(asker, item) for item in world["items"]}
    first, between, ask = (run.say(asker, said[0]), run.say(other, said[1]), run.say(asker, said[2]))
    for line in (first, between, ask):
        rep.note("%s: %s" % (line["who"], line["text"]))
    row = wait_for(lambda: (lambda r: r if r["jev_class"] and (r["fed_at"] or r["jev_class"] not in TO_THE_AI)
                            else None)(run.row(ask)), 30)
    rows = [run.row(x) for x in (first, between, ask)]
    if not rep.check(row, "Jev judged the ask, and fed it if it goes to the AI, within 30 s", "%r" % rows[2]):
        return
    if rows[2]["jev_class"] not in TO_THE_AI or rows[0]["jev_class"] not in HELD_BACK:
        rep.skip(name, "Jev's routing, not the gather: the first piece %s, the ask %s"
                 % (rows[0]["jev_class"], rows[2]["jev_class"]))
        return
    rep.scenarios += 1
    group = said[0] + JOIN + said[2]
    lines = [r for r in live.rows(run.start_seq, "LINE") if r["text"] == said[2] or r["text"].endswith(JOIN + said[2])]
    line = lines[0] if len(lines) == 1 else None
    want = r"^%s voice %s %.2f -?\d+dB$" % (asker, rows[2]["jev_class"], rows[2]["jev_confidence"])
    rep.check(line and line["text"] == group and re.match(want, line["fields"]) and line["at"] == ask["start"],
              "one LINE for the ask, under its fields and time, its text both pieces in order: %r" % group,
              "feed lines for the ask: %r" % [(r["seq"], r["fields"], r["text"]) for r in lines])
    g = run.gather(ask["id"]) or {"asked": 0, "yes": {}, "no": {}, "ms": None, "tokens": "?"}
    rep.check(g["asked"] and between["id"] not in {**g["yes"], **g["no"]}
              and not any(said[1] in r["text"] for r in lines),
              "%s's line was never asked about and is in no group (the gather call asked about %d of %s's lines, "
              "%d yes)" % (other, g["asked"], asker, len(g["yes"])), "gather %r; between %s" % (g, between["id"]))
    a = {"seq": line["seq"] if line else 0, "at": parse_iso(rows[2]["fed_at"]), "who": asker, "text": group,
         "reply": None, "task": None, "said": ask, "row": rows[2], "gather": g, "line": line}
    run.asks.append(a)
    if not run.brain or not line:
        return
    run.replies.await_reply(a, 90)

    def calls(seq):  # a line's model calls: Respond's (`#seq`) and its loop's steps (`#seq s<n>`)
        return live.sql("SELECT component, ref FROM usage WHERE ref = ? OR ref LIKE ? ORDER BY id",
                        ("#%d" % seq, "#%d s%%" % seq))
    use = wait_for(lambda: calls(a["seq"]), 15) or []
    log = live.log_since(live.mark).splitlines()
    worded = [m.group(3) for m in map(SAID.match, log) if m and m.group(1) == str(a["seq"])]
    looped = [(m.group(2), m.group(3)) for m in map(LOOP.match, log) if m and m.group(1) == str(a["seq"])]
    opened = [t for t in live.sql("SELECT * FROM tasks WHERE player = ? ORDER BY created_at", (asker,))
              if parse_iso(t["created_at"]) >= a["at"] - 2]   # the brain's tasks, in vfg.db by name
    task = opened[0] if opened else {}
    first_fed = rows[0]["fed_at"] and [r["seq"] for r in live.rows(run.start_seq, "LINE") if r["text"] == said[0]]
    alone = first_fed and calls(first_fed[0])
    rep.check((a["reply"] or a.get("silent")) and use and (worded or looped)
              and all(t == repr(group[:80]) for t in worded) and all(who == asker for who, _ in looped)
              and (not task or task.get("line") == group) and not alone,
              "the brain's turn is for that LINE (#%d: %s%s%s), and none for the first piece alone" % (
                  a["seq"], ", ".join("%s %s" % (u["component"], u["ref"]) for u in use) or "no call",
                  "; its loop ended %s" % looped[0][1] if looped else "",
                  "; task %s's line the group" % task["name"] if task else ""),
              "usage %r; Respond's words %r; loop %r; task %r; first piece's calls %r"
              % (use, worded, looped, task, alone))
    effects = [e for e in world["effects"] if live.effect_level(asker, e) is not None]
    items = {i: live.count_items(asker, i) - before[i] for i in world["items"]}
    rep.extra(effects or any(n > 0 for n in items.values()),
              "in the world: %s %s" % (asker, ", ".join(effects + ["%+d %s" % (n, i) for i, n in items.items() if n])
                                       or "has nothing new"), "reply %r" % a["reply"])


def report(run):
    """Every line's verdict, the groups, the times and the money."""
    live, verdicts = run.live, run.verdicts()
    text = {line["id"]: line["text"] for line in run.said}
    print("\nVERDICTS, every line said (from vfg.db and the filter's journal)")
    for line in run.said:
        r = run.row(line)
        v = verdicts.get(line["id"])
        print("  %-5s %-15s %4s  %-28s %s" % (line["who"], r["jev_class"], "%.2f" % r["jev_confidence"]
                                               if r["jev_confidence"] is not None else "-",
                                               v[2] if v else "(no judge line)", short(line["text"], 60)))
    print("\nTHE ASKS: said (its end) -> Jev's verdict -> fed -> the brain's reply")
    for a in run.asks:
        said, row, g = a["said"]["end"], a["row"], a["gather"]
        judged, fed = parse_iso(row["jev_judged_at"]) - said, parse_iso(row["fed_at"]) - said
        calls = ", ".join("%s %d ms" % (area, ms) for area, ms, _, end in run.replies.calls("#%d" % a["seq"])
                          if end >= run.started)   # brain.log runs on across runs, and so do the seqs
        print("  #%-4s %s: %s" % (a["seq"], a["who"], short(a["text"], 100)))
        for verdict in ("yes", "no"):
            for i, conf in g[verdict].items():
                print("        gather %-3s %s  %s" % (verdict, conf, short(text.get(i, i), 80)))
        print("        verdict %.1f s, gather call %s ms (%s tok), fed %.1f s, reply %s%s" % (
            judged, g["ms"], g["tokens"], fed, "%.1f s" % (a["reply_at"] - said) if a.get("reply_at") else "-",
            " (%s)" % calls if calls else ""))
        if a.get("reply"):
            print("        -> %s" % short(a["reply"], 120))
    use = [u for u in live.sql("SELECT * FROM usage ORDER BY id") if parse_iso(u["at"]) >= run.started]
    jev = [u for u in use if u["component"] == "jev"]
    gathers = [u for u in jev if (u["ref"] or "").startswith("gather:")]
    models = [u for u in use if u["component"] in ("brain", "respond")]
    cost = lambda us: sum(u["cost_usd"] or 0 for u in us)  # noqa: E731
    calls = ", ".join("%s %s $%.4f" % (u["component"], u["ref"], u["cost_usd"] or 0) for u in models)
    print("\nMONEY, from the usage rows: TypeSafe $%.4f (%d routing calls, %d gather calls); OpenRouter $%.4f (%s)"
          % (cost(jev), len(jev) - len(gathers), len(gathers), cost(models), calls or "none"))
    run.rep.check(cost(jev) < JEV_BUDGET and cost(models) < MODEL_BUDGET, "within the budget: TypeSafe $%.4f < $%.2f, "
                  "OpenRouter $%.4f < $%.2f" % (cost(jev), JEV_BUDGET, cost(models), MODEL_BUDGET))


def new_transcript(srv):
    """The last run's vfg.db, feed, lookout, menu and logs, moved to vfg/runs/<time>/: each run starts as a
    new box, and the brain sets Jev's lookout and menu again as it starts."""
    names = [p for p in [os.path.join(srv.vfg, n) for n in ("vfg.db", "vfg.db-wal", "vfg.db-shm", "feed.log",
                                                             "feed.log.lock", "lookout.md", "menu.md")]
             + [os.path.join(srv.dir, n) for n in ("jev.log", "bridge.log")] if os.path.exists(p)]
    if names:
        old = os.path.join(srv.vfg, "runs", time.strftime("%Y%m%dT%H%M%S"))
        os.makedirs(old)
        for p in names:
            shutil.move(p, os.path.join(old, os.path.basename(p)))


def setup(run, no_brain):
    rep, live = run.rep, run.live
    rep.head("setup: Steve and Alex on a fresh world, the bridge, the Jev filter%s"
             % ("" if no_brain else ", the brain"))
    for who in ("Steve", "Alex"):
        live.p.spawn(who)
        live.cmd("clear %s" % who)
        live.cmd("effect clear %s" % who)
    up = wait_for(lambda: any(x.startswith("vfg-jev up") for x in run.jev.journal()), 20)
    keyed = any(x.startswith("key from ") for x in run.jev.journal())
    if not rep.check(up and keyed, "the Jev filter is up, its TypeSafe key from the stand-in's .env",
                     "\n".join(run.jev.journal()[-5:])):
        return False
    menu = os.path.join(live.dir, "vfg", "menu.md")
    if no_brain:  # what the brain sets as it starts
        with open(os.path.join(REPO, "brain", "prompts", "lookout.md"), encoding="utf-8") as f:
            live.vfg("lookout", "set", f.read().strip())
        live.vfg("jev", "menu", "set", live.vfg("action", "list").stdout.strip())
    else:
        rep.note("command references: %s" % (live.seed_refs() or "none to copy: the brain fetches them (about 95 s)"))
        live.mark = len(live.log())
        r = live.start()
        started = wait_for(lambda: re.search(r"^\S+ start pid \d+;", live.log_since(live.mark), re.M), 180)
        if not rep.check(r.returncode == 0 and started, "vfg brain start: the brain is up on realmc %s" % live.name,
                         "%s %s %s" % (r.stdout, r.stderr, live.log_since(live.mark)[-1500:])):
            return False
    return rep.check(wait_for(lambda: os.path.exists(menu) and os.path.getsize(menu) > 0, 30),
                     "Jev's lookout and menu are set, as on the box")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="jr", help="the realmc server (default jr)")
    ap.add_argument("--keep-up", action="store_true", help="leave the server running at the end")
    ap.add_argument("--no-brain", action="store_true", help="Jev only: the groups on the feed, no OpenRouter")
    args = ap.parse_args()
    key, where = brain_live.find_key()
    os.environ.pop(KEY, None)   # the brain's process gets it from Live.start(), nothing else does
    if not key and not args.no_brain:
        print("no %s in the environment or in a .env above %s" % (KEY, REPO), file=sys.stderr)
        return 2
    srv = incantations_e2e.realmc.Server(args.name)
    print("jev live: realmc %s in %s%s" % (args.name, os.path.dirname(srv.dir),
                                           "" if args.no_brain else "; OpenRouter key from %s" % where), flush=True)
    new_transcript(srv)
    r = subprocess.run([os.path.join(REALMC, "up"), args.name, "--fresh"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    print(short(r.stdout[-600:], 600), flush=True)
    if r.returncode != 0:
        print("realmc up %s failed" % args.name, file=sys.stderr)
        return 1
    rep, live, bridge, jev = Report(), None, None, None
    try:
        live = brain_live.Live(args.name, key or "")
        bridge = incantations_e2e.Bridge(srv, os.path.join(srv.dir, "bridge.log"))
        jev = Filter(srv)
        run = Run(live, bridge, jev, rep, not args.no_brain)
        run.start_seq = live.last_seq()
        if setup(run, args.no_brain):
            if not args.no_brain:
                wait_for(lambda: live.ai(run.start_seq), 30)   # the greeting, said before the first line
            for step, more in [(scenario, sc) for sc in SCENARIOS] + [(report, ())]:
                try:
                    step(run, *more)
                except Exception:
                    rep.check(False, "the step ran to its end", traceback.format_exc()[-1500:])
    finally:
        for part in (live and live.stop, jev and jev.stop, bridge and bridge.stop):
            if part:
                try:
                    part()
                except Exception as e:  # noqa: BLE001 — stop the rest anyway
                    print("stopping: %s" % e, file=sys.stderr)
        if not args.keep_up:
            d = subprocess.run([os.path.join(REALMC, "down"), args.name], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            print("\nrealmc down %s: exit %d" % (args.name, d.returncode))
    failed, checked = rep.failed(), rep.scenarios
    print("\n" + "=" * 72)
    print("%d/%d checks passed; %d of %d scenarios checked (%d skipped: Jev's routing)%s" % (
        len(rep.checks) - len(failed), len(rep.checks), checked, len(SCENARIOS), len(rep.skipped),
        "; the world (bonus) %d/%d" % (sum(rep.bonus), len(rep.bonus)) if rep.bonus else ""))
    for name in failed:
        print("  x %s" % name)
    print("RESULT: %s" % ("PASS" if not failed and checked else "FAIL"))
    print("=" * 72)
    return 1 if failed or not checked else 0


if __name__ == "__main__":
    sys.exit(main())
