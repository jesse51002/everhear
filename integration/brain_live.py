#!/usr/bin/env python3
"""The brain live, on a real 26.3 server on this PC: the spec's "Live, on realmc" list.

    REALMC_ROOT=… python3 integration/brain_live.py [--name brain] [--keep-up] [--no-implementer]

Real:  a Minecraft 26.3 server with Carpet's fake players Steve and Alex (integration/realmc,
       its world fresh from the template), the brain (`vfg brain start` through realmc's bin/vfg,
       which keeps this HOME so the implementers' Claude CLI finds its login), its model calls
       through OpenRouter (OPEN_ROUTER_API: from the environment, else the first .env above
       this repo, or its Internal/.env, that holds it; given to the brain's process only, never
       printed or written), the implementers on the Claude subscription (Haiku 5.5), vfg, the
       actions and the scripts, the stand-in box's feed writer and vfg.db.
Stand-ins: SSH (realmc's bin/ssh), and Jev: each player's line is put into the feed as a LINE
       row through the stand-in's vfg_feed, as Jev puts it there.

Every ask is checked in the world over RCON. Prints PASS/FAIL per check, then each ask's time
from line to reply, the implementer task's time, every call's cost from the `usage` rows and the
cache hit rate. One implementer task and one follow-up; the brain and Respond come to well
under $1. The server is left down unless --keep-up. Never `down --all`, `--purge-key`, `--regen`,
`key` or `carpet`: other builders' servers share REALMC_ROOT, and its env file holds a real
TypeSafe key, which nothing here reads or prints.
"""
import argparse
import datetime as dt
import glob
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
KEY = "OPEN_ROUTER_API"
BUDGET = 1.00      # dollars of OpenRouter the brain and Respond may spend in a run
TASK_S = 600       # an implementer task's time limit, its report included

FEED_RE = re.compile(r"^([A-Z]+)#(\d+) (\d\d:\d\d:\d\d) (?:([^|]*?) )?\| (.*)$")
LOG_CALL = re.compile(r"^(\S+) (brain|respond) (#\d+|T\d+|batch #\d+-#\d+) (\d+) ms \$([\d.]+), (\d+) in")


def parse_iso(s):
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


def wait_for(fn, timeout, step=0.2):
    end = time.monotonic() + timeout
    while True:
        v = fn()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def short(text, n=300):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + " …"


def find_key():
    """The OpenRouter key and where it came from; never printed (as brain_e2e.py finds it)."""
    spec = importlib.util.spec_from_file_location("brain_e2e", os.path.join(HERE, "brain_e2e.py"))
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    return e2e.find_key()


# ----------------------------------------------------------------- the report

class Report:
    def __init__(self):
        self.checks = []

    def head(self, title):
        print("\n== %s" % title, flush=True)

    def check(self, ok, name, detail=""):
        ok = bool(ok)
        self.checks.append((ok, name))
        print("  %s  %s" % ("PASS" if ok else "FAIL", name), flush=True)
        if detail and not ok:
            print("          " + short(detail, 1500), flush=True)
        return ok

    def note(self, text):
        print("  note  " + text, flush=True)

    def failed(self):
        return [n for ok, n in self.checks if not ok]


# --------------------------------------------------------------- the server

class Live:
    """One realmc server, its stand-in box, the brain on it, and the fake players."""

    def __init__(self, name, key):
        self.name, self.key = name, key
        self.env = self.realmc_env()
        self.dir = self.env["REALMC_DIR"]
        self.vhome = self.env["VFG_HOME"]
        self.brain_dir = os.path.join(self.vhome, "brain")
        self.db = os.path.join(self.dir, "vfg", "vfg.db")
        self.feed = os.path.join(self.dir, "vfg", "feed.log")
        os.environ["VFG_DB"], os.environ["VFG_FEED"] = self.db, self.feed
        spec = importlib.util.spec_from_file_location("vfg_feed", os.path.join(self.dir, "vfg", "lib", "vfg_feed.py"))
        self.writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.writer)
        sys.path.insert(0, REALMC)
        import players
        self.p = players.on(name)
        self.started = False
        self.mark = len(self.log())

    def realmc_env(self):
        """This environment, with `realmc/env <name>`'s exports applied, VFG_SERVER the server's
        name (its implementers make things in made/<name>/), and no other VFG_ setting."""
        r = subprocess.run([os.path.join(REALMC, "env"), self.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", KEY))}
        for line in r.stdout.splitlines():
            m = re.match(r"^export (\w+)=(.*)$", line)
            if not m:
                continue
            value = m.group(2)
            if m.group(1) == "PATH":
                value = value.split(":", 1)[0].strip("'") + os.pathsep + env.get("PATH", "")
            else:
                value = value.strip("'")
            env[m.group(1)] = value
        env["VFG_SERVER"] = self.name
        return env

    def vfg(self, *args, timeout=120, with_key=False):
        env = dict(self.env)
        if with_key:
            env[KEY] = self.key
        return subprocess.run(["vfg"] + list(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)

    def cmd(self, command):
        return self.p.cmd(command)

    # -- the brain
    def seed_refs(self, version="26.3"):
        """The command references from another server's stand-in, when one has them complete,
        so the brain's first start here does not fetch them for 95 s."""
        d = os.path.join(self.brain_dir, "refs", version)
        if os.path.exists(os.path.join(d, "complete.json")):
            return "already here"
        root = os.path.dirname(self.dir)
        for src in sorted(glob.glob(os.path.join(root, "*", "home", ".vfg", "*", "refs", version))):
            if os.path.exists(os.path.join(src, "complete.json")) and os.path.exists(os.path.join(src, "index.json")):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copytree(src, d)
                return os.path.relpath(src, root)
        return None

    def start(self):
        r = self.vfg("brain", "start", with_key=True, timeout=60)
        self.started = True
        return r

    def stop(self):
        if not self.started:
            return None
        self.started = False
        return self.vfg("brain", "stop", timeout=60)

    def log(self):
        try:
            with open(os.path.join(self.brain_dir, "brain.log"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def log_since(self, mark):
        return self.log()[mark:]

    def tasks(self):
        try:
            with open(os.path.join(self.brain_dir, "tasks.json")) as f:
                return json.load(f).get("tasks") or {}
        except (OSError, ValueError):
            return {}

    # -- the feed and the database
    def sql(self, query, params=()):
        con = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(query, params).fetchall()]
        finally:
            con.close()

    def rows(self, after=0, kind=None):
        out = []
        for r in self.sql("SELECT seq, at, line, processed_at FROM feed WHERE seq > ? ORDER BY seq", (after,)):
            m = FEED_RE.match(r["line"])
            r.update(type=m.group(1) if m else "", fields=(m.group(4) or "") if m else "", text=m.group(5) if m else "")
            if kind is None or r["type"] == kind:
                out.append(r)
        return out

    def ai(self, after=0, kinds=("say",), task=None):
        return [r for r in self.rows(after, "AI") if r["fields"].split()[0] in kinds
                and (task is None or r["fields"].split()[1] == task)]

    def last_seq(self):
        return self.sql("SELECT MAX(seq) AS s FROM feed")[0]["s"] or 0

    def lines(self, *said):
        """Players' lines into the feed in one write, as the Jev filter feeds a batch."""
        entries = [("LINE", "%s voice %s %s -9dB" % (who, cls, "0.93" if cls == "action_oriented" else "0.88"),
                    text, None) for who, cls, text in said]
        seqs = self.writer.append_batch(entries)
        at = time.time()
        return [(s, at) for s in seqs]

    # -- the world
    def data(self, who, path):
        """`data get entity <who> <path>`'s value, or None when there is none."""
        out = self.cmd("data get entity %s %s" % (who, path))
        m = re.search(r"has the following entity data: (.*)$", out, re.S)
        return m.group(1) if m else None

    def effect_level(self, who, effect):
        """The amplifier of `effect` on `who`, or None."""
        for part in re.split(r"\}, \{", self.data(who, "active_effects") or ""):
            if '"minecraft:%s"' % effect in part:
                m = re.search(r"amplifier: (\d+)b", part)
                return int(m.group(1)) if m else 0
        return None

    def count_items(self, who, item):
        m = re.search(r"Found (\d+) matching item", self.cmd("clear %s minecraft:%s 0" % (who, item)))
        return int(m.group(1)) if m else 0

    def count_blocks(self, block, centre, r=7):
        x0, y0, z0 = (int(c // 1) for c in centre)
        n = 0
        for x in range(x0 - r, x0 + r + 1):
            for z in range(z0 - r, z0 + r + 1):
                for y in range(y0 - 1, y0 + 3):
                    if self.cmd("execute if block %d %d %d minecraft:%s" % (x, y, z, block)).startswith("Test passed"):
                        n += 1
        return n

    def copies(self):
        r = self.vfg("script", "list", "--json", "--fresh", timeout=60)
        try:
            return json.loads(r.stdout).get("copies") or {}
        except ValueError:
            return {}


# ======================================================================= the run

class Run:
    def __init__(self, live, rep):
        self.live, self.rep = live, rep
        self.asks = []
        self.tasks = []    # the implementer task's timings

    def ask(self, who, text, cls="action_oriented", timeout=60, extra=()):
        """A line into the feed; waits for its reply: under the task the brain opens for it, or
        the first chat line after it. Returns the ask."""
        live = self.live
        said = live.lines((who, cls, text), *extra)
        (seq, at) = said[0]
        a = {"seq": seq, "at": at, "who": who, "text": text, "cls": cls, "reply": None, "task": None}
        self.asks.append(a)
        self.rep.note("#%d %s (%s): %s" % (seq, who, cls, text))
        self.await_reply(a, timeout)
        return a

    def await_reply(self, a, timeout=60):
        live = self.live

        def got():
            for tid, t in live.tasks().items():  # this run's: an earlier run's T1 may have had the same words
                if t.get("line") == a["text"] and t.get("player") == a["who"] and parse_iso(t["at"]) >= a["at"] - 2:
                    a["task"] = tid
            for r in live.ai(a["seq"]):
                if a["task"] is None or r["fields"] == "say %s" % a["task"]:
                    return r
            return None
        r = wait_for(got, timeout)
        if r:
            a["reply"], a["reply_at"], a["reply_seq"] = r["text"], parse_iso(r["at"]), r["seq"]
            self.rep.note("   -> %s%s (%.1f s)" % (short(r["text"], 120), " [%s]" % a["task"] if a["task"] else "",
                                                   a["reply_at"] - a["at"]))
        return r

    def actions(self, a):
        """The AI action rows of the ask's task: (state, text)."""
        out = []
        for r in self.live.ai(a["seq"], ("action",), a["task"]):
            m = re.search(r" — (ok|failed|dropped): ", r["text"])
            out.append((m.group(1) if m else "?", r["text"]))
        return out

    def calls(self, ref):
        out = []
        for line in self.live.log().splitlines():
            m = LOG_CALL.match(line)
            if m and m.group(3) == ref:
                out.append((m.group(2), int(m.group(4)), float(m.group(5)), parse_iso(m.group(1))))
        return out

    def table(self):
        print("\nLATENCY: a line fed -> its reply said (the feed rows' times); the model calls from brain.log")
        print("  %-5s %-6s %-16s %8s  %s" % ("line", "who", "class", "reply", "calls"))
        for a in self.asks:
            took = "%.1f s" % (a["reply_at"] - a["at"]) if a.get("reply_at") else "-"
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.calls("#%d" % a["seq"]))
            print("  #%-4d %-6s %-16s %8s  %s" % (a["seq"], a["who"], a["cls"], took, calls or "-"))
            print("        %s  ->  %s" % (short(a["text"], 64), short(a.get("reply") or "(no reply)", 90)))
        for t in self.tasks:
            print("  %s" % t)


def setup(live, rep):
    rep.head("setup: Steve and Alex on a fresh world, their items, the brain started")
    p = live.p
    for who in ("Steve", "Alex"):
        p.spawn(who)
        live.cmd("clear %s" % who)
        live.cmd("effect clear %s" % who)
    # Steve: a diamond sword held (hotbar.0), an iron sword in hotbar.3, iron boots carried in
    # inventory.5, diamond boots worn; no helmet.
    for slot, item in (("hotbar.0", "diamond_sword"), ("hotbar.3", "iron_sword"), ("inventory.5", "iron_boots"),
                       ("armor.feet", "diamond_boots")):
        live.cmd("item replace entity Steve %s with minecraft:%s" % (slot, item))
    p.hotbar("Steve", 1)
    ok = wait_for(lambda: live.data("Steve", "SelectedItemSlot") == "0", 5)
    rep.check(ok and "diamond_boots" in (live.data("Steve", "equipment.feet") or "")
              and "iron_boots" in (live.data("Steve", "Inventory[{Slot:14b}]") or "")
              and live.data("Steve", "equipment.head") is None,
              "Steve holds a diamond sword, carries an iron sword and iron boots, wears diamond boots, no helmet")
    seeded = live.seed_refs()
    rep.note("command references: %s" % (seeded or "none to copy: the brain fetches them (about 95 s)"))
    live.mark = len(live.log())
    r = live.start()
    up = wait_for(lambda: re.search(r"^\S+ start pid \d+;", live.log_since(live.mark), re.M), 180)
    rep.check(r.returncode == 0 and up, "vfg brain start: the brain is up on realmc %s" % live.name,
              "%s %s; log %s" % (r.stdout, r.stderr, short(live.log_since(live.mark)[-1500:])))
    refs = wait_for(lambda: re.search(r"^\S+ refs server 26\.3: (\d+) commands", live.log_since(live.mark), re.M), 200)
    rep.check(refs, "the command references for 26.3 are loaded (%s commands)" % (refs.group(1) if refs else "?"))
    greet = wait_for(lambda: [r for r in live.ai(live.start_seq) if r["text"].startswith("AI is on")], 30)
    rep.check(greet, "it greeted the players: tellraw on the real server, and an AI say row")
    return bool(up)


def action_ask(run):
    rep, live = run.rep, run.live
    rep.head("1. An action ask")
    before = live.count_items("Steve", "torch")
    a = run.ask("Steve", "give me 64 torches and night vision")
    got = live.count_items("Steve", "torch") - before
    nv = live.effect_level("Steve", "night_vision")
    rep.check(got == 64 and nv is not None, "in the world: Steve has 64 more torches (%d) and night vision (%r)"
              % (got, nv), "%r" % run.actions(a))
    rep.check(a["reply"] and a["task"], "one reply under %s: %r" % (a["task"], a["reply"]))


def item_asks(run):
    rep, live = run.rep, run.live
    rep.head("2. Item asks: boots worn and boots carried; a sword held and one carried; an item he does not have")
    a = run.ask("Steve", "put depth strider three on my boots")
    worn, carried = live.data("Steve", "equipment.feet") or "", live.data("Steve", "Inventory[{Slot:14b}]") or ""
    rep.check('"minecraft:depth_strider": 3' in worn and "enchantments" not in carried,
              "Depth Strider III on the diamond boots he wears; the iron boots he carries untouched",
              "worn %s; carried %s; %r" % (worn, carried, run.actions(a)))
    rep.check(a["reply"], "one reply: %r" % a["reply"])
    a = run.ask("Steve", "add sharpness five to my sword")
    held, other = live.data("Steve", "Inventory[{Slot:0b}]") or "", live.data("Steve", "Inventory[{Slot:3b}]") or ""
    rep.check('"minecraft:sharpness": 5' in held and "enchantments" not in other,
              "Sharpness V on the diamond sword he holds; the iron sword in hotbar.3 untouched",
              "held %s; other %s; %r" % (held, other, run.actions(a)))
    rep.check(a["reply"], "one reply: %r" % a["reply"])
    a = run.ask("Steve", "put protection four on my helmet")
    acts = run.actions(a)
    rep.check(live.data("Steve", "equipment.head") is None and (not acts or all(s == "dropped" for s, _ in acts)),
              "no helmet: nothing changed, the part dropped (%s)" % ("; ".join(t for _, t in acts) or "no action row"),
              "%r" % acts)
    rep.check(a["reply"] and re.search(r"helmet|head", a["reply"], re.I),
              "the reply says so: %r" % a["reply"])


def edit_and_stop(run):
    rep, live = run.rep, run.live
    rep.head("3. A running copy: started, edited, stopped")
    a = run.ask("Steve", "keep speed two on me the whole time, even after I die, until I say stop")
    copies = {k: v for k, v in live.copies().items() if v.get("action") == "keep_effects"}
    lvl = live.effect_level("Steve", "speed")
    rep.check(copies and lvl == 1, "a keep_effects copy runs (%s) and Steve has Speed II" % ", ".join(copies),
              "copies %r; speed amplifier %r; %r" % (copies, lvl, run.actions(a)))
    name = next(iter(copies), None)
    b = run.ask("Steve", "make that speed five")
    acts = run.actions(b)
    lvl = wait_for(lambda: live.effect_level("Steve", "speed") == 4, 5) and 4 or live.effect_level("Steve", "speed")
    rep.check(lvl == 4 and any(t.startswith("edit:") and s == "ok" for s, t in acts),
              "an edit of %s: Steve has Speed V within seconds, the copy still running" % name,
              "speed amplifier %r; %r" % (lvl, acts))
    c = run.ask("Steve", "ok stop the speed thing now")
    acts = run.actions(c)
    left = {k: v for k, v in live.copies().items() if v.get("action") == "keep_effects"}
    rep.check(not left and any(t.startswith("stop:") and s == "ok" for s, t in acts),
              "a stop of %s: the copy is gone" % name, "copies %r; %r" % (left, acts))
    rep.note("Steve's speed after the stop: %r" % live.effect_level("Steve", "speed"))


def two_at_once(run):
    rep, live = run.rep, run.live
    rep.head("4. Two players asking in the same second")
    carrots, arrows = live.count_items("Steve", "golden_carrot"), live.count_items("Alex", "arrow")
    a = run.ask("Steve", "give me five golden carrots", extra=[("Alex", "action_oriented", "can I get ten arrows")])
    b = {"seq": a["seq"] + 1, "at": a["at"], "who": "Alex", "text": "can I get ten arrows", "cls": "action_oriented",
         "reply": None, "task": None}
    run.asks.append(b)
    run.await_reply(b)
    rep.check(live.count_items("Steve", "golden_carrot") - carrots == 5 and live.count_items("Alex", "arrow") - arrows == 10,
              "in the world: 5 golden carrots to Steve, 10 arrows to Alex")
    rep.check(a["reply"] and b["reply"] and a["task"] != b["task"],
              "both answered, each under its own task (%s, %s)" % (a["task"], b["task"]))
    c1, c2 = run.calls("#%d" % a["seq"]), run.calls("#%d" % b["seq"])
    if c1 and c2:
        (_, ms1, _, end1), (_, ms2, _, end2) = c1[0], c2[0]
        s1, s2 = end1 - ms1 / 1000.0, end2 - ms2 / 1000.0
        rep.check(min(end1, end2) - max(s1, s2) > 0, "neither waited: their brain calls overlapped %.1f s, started "
                  "%.2f s apart" % (min(end1, end2) - max(s1, s2), abs(s1 - s2)))
    else:
        rep.check(False, "both brain calls are in brain.log", "%r %r" % (c1, c2))


def talk(run):
    rep, live = run.rep, run.live
    rep.head("5. A conversational line: Respond")
    a = run.ask("Alex", "haha thanks AI, you're a legend", cls="conversational", timeout=30)
    def use():  # the usage row goes out beside the reply, nobody waiting: it may come a moment after
        return sorted(u["component"] for u in live.sql("SELECT component FROM usage WHERE ref = ?", ("#%d" % a["seq"],)))
    wait_for(use, 15)
    time.sleep(2)
    use = use()
    rep.check(a["reply"] and use == ["respond"], "Respond replied, and no brain call: %r" % a["reply"], "%r" % use)


def implementer(run):
    rep, live = run.rep, run.live
    rep.head("6. An implementer task end to end, with no orchestrator, and a follow-up")
    centre = live.p.pos("Steve")
    rep.note("Steve stands at %s" % " ".join("%.1f" % c for c in centre))
    mark = len(live.log())
    a = run.ask("Steve", "put a ring of eight torches on the ground around me, three blocks out", timeout=90)
    tid = a["task"]
    opened = live.ai(a["seq"], ("task",), tid)
    rep.check(tid and opened and opened[0]["text"].startswith("%s: " % tid),
              "the brain wrote a brief and code started %s: %r" % (tid, opened and short(opened[0]["text"], 120)),
              short(live.log_since(mark)[-1500:]))
    if not tid:
        return
    # While it works, a context line nobody answers: the batch timer's to take in, after 60 s.
    live.lines(("Alex", "context", "lol Steve keeps walking into the lava over there"))
    t0 = opened[0]["at"]
    res = wait_for(lambda: live.ai(a["seq"], ("result",), tid), TASK_S, 1.0)
    rep.check(res, "%s's implementer reported, and the brain recorded it" % tid, short(live.log_since(mark)[-2000:]))
    if not res:
        return
    time.sleep(3)
    said = [r for r in live.ai(a["seq"], ("say",), tid) if r["seq"] > a.get("reply_seq", 0)]
    results = live.ai(a["seq"], ("result",), tid)
    rep.check(len(said) == 1 and len(results) == 1 and results[0]["text"].startswith(tid),
              "the report became one chat line (%r) and one AI result row (%r)"
              % (said and said[0]["text"], results[0]["text"]), "%r %r" % (said, results))
    report = re.search(r"^\S+ implementer %s (done|failed): (.*)$" % tid, live.log_since(mark), re.M)
    tm = re.search(r"^\S+ implementer %s (?:ended|error) \((\w+), (\d+) turns, (\d+) s\)" % tid, live.log_since(mark), re.M)
    run.tasks.append("%s: brief to report %.0f s (the implementer's own %s s, %s turns, %s); %s" % (
        tid, parse_iso(results[0]["at"]) - parse_iso(t0), tm and tm.group(3), tm and tm.group(2),
        tm and tm.group(1), report and report.group(1)))
    torches = live.count_blocks("torch", centre)
    rep.check(torches >= 6, "in the world: %d torches on the ground round Steve" % torches)
    rep.note("its report, as the implementer wrote it: %s" % short(report.group(2) if report else "?", 240))
    nothing_from_it(run, a["seq"], tid, report.group(2) if report else "", mark)

    # The follow-up: to the same task's implementer, which resumes its session.
    b = run.ask("Steve", "actually make those soul torches instead", timeout=90)
    fu = [r for r in live.ai(b["seq"], ("task",)) if r["text"].startswith("%s follow-up" % tid)]
    rep.check(fu and b["task"] in (None, tid), "the brain sent it to %s's implementer: %r"
              % (tid, fu and short(fu[0]["text"], 120)), short(live.log_since(mark)[-1500:]))
    resumed = wait_for(lambda: re.search(r"^\S+ implementer %s resumed " % tid, live.log_since(mark), re.M), 30)
    rep.check(resumed, "the implementer resumed %s's session" % tid)
    t1 = fu[0]["at"] if fu else None
    res2 = wait_for(lambda: len(live.ai(a["seq"], ("result",), tid)) >= 2, TASK_S, 1.0)
    rep.check(res2, "the follow-up reported, and the brain recorded it", short(live.log_since(mark)[-2000:]))
    if res2:
        time.sleep(3)
        results = live.ai(b["seq"], ("result",), tid)
        said = [r for r in live.ai(b["seq"], ("say",), tid) if r["seq"] > (b.get("reply_seq") or 0)]
        rep.check(len(said) == 1 and len(results) == 1, "again one chat line (%r) and one AI result row (%r)"
                  % (said and said[0]["text"], results and results[0]["text"]))
        reports = re.findall(r"^\S+ implementer %s (done|failed): (.*)$" % tid, live.log_since(mark), re.M)
        tms = re.findall(r"^\S+ implementer %s (?:ended|error) \((\w+), (\d+) turns, (\d+) s\)" % tid,
                         live.log_since(mark), re.M)
        if t1 and results:
            run.tasks.append("%s follow-up: brief to report %.0f s (the implementer's own %s s, %s turns, %s); %s" % (
                tid, parse_iso(results[0]["at"]) - parse_iso(t1), tms[-1][2] if len(tms) > 1 else None,
                tms[-1][1] if len(tms) > 1 else None, tms[-1][0] if len(tms) > 1 else None,
                reports[-1][0] if reports else "?"))
        soul = live.count_blocks("soul_torch", centre)
        rep.check(soul >= 6, "in the world: %d soul torches round Steve" % soul)
        nothing_from_it(run, b["seq"], tid, reports[-1][1] if reports else "", mark)


def nothing_from_it(run, after, tid, report, mark):
    """Nothing the implementer wrote is in the context: no row holds its report or its words."""
    rep, live = run.rep, run.live
    said = [m.group(1) for m in re.finditer(r"^\S+ implementer %s says: (.*)$" % tid, live.log_since(mark), re.M)]
    words = [w for w in [report] + said if len(w) >= 40]
    rows = live.rows(after)
    hits = [(r["seq"], r["line"][:120]) for r in rows for w in words if w[:60] in r["line"]]
    kinds = sorted({r["type"] + (" " + r["fields"].split()[0] if r["type"] == "AI" else "") for r in rows})
    rep.check(words and not hits, "none of the implementer's %d messages and report is in the feed; its rows since "
              "the line: %s" % (len(words), ", ".join(kinds)), "%r" % hits)


def end(run):
    rep, live = run.rep, run.live
    rep.head("the end")
    batch = [u for u in live.sql("SELECT * FROM usage WHERE ref LIKE 'batch #%' ORDER BY id")
             if parse_iso(u["at"]) > run.started]
    rep.note("batch calls: %d (%s)" % (len(batch), ", ".join("%s $%.4f" % (u["ref"], u["cost_usd"]) for u in batch)))
    unread = [r for r in live.rows(live.start_seq) if r["type"] == "LINE" and not r["processed_at"]]
    rep.check(not unread, "every line of the run was read and marked processed", "%r" % unread)
    r = live.stop()
    log = live.log_since(live.mark)
    rep.check(r is not None and r.returncode == 0 and re.search(r"^\S+ stop stopped$", log, re.M),
              "vfg brain stop: the brain stopped cleanly", "%s %s" % (r and r.stdout, r and r.stderr))
    open_ = [t for t, v in live.tasks().items() if v.get("status") == "open"]
    rep.check(not open_, "no task left open", "%r" % open_)
    rep.check("Traceback" not in log, "no traceback in brain.log", short(log[max(0, log.find("Traceback")):][:1500]))
    leaks = []
    for d in (os.path.join(live.dir, "vfg"), os.path.join(live.dir, "home"), os.path.join(REPO, "made", live.name)):
        for root, _, files in os.walk(d):
            for fn in files:
                try:
                    with open(os.path.join(root, fn), "rb") as f:
                        if live.key.encode() in f.read():
                            leaks.append(os.path.join(root, fn))
                except OSError:
                    pass
    rep.check(not leaks, "the OpenRouter key is in no file of the stand-in or of made/%s" % live.name, "%r" % leaks)
    use = [u for u in live.sql("SELECT * FROM usage ORDER BY id") if parse_iso(u["at"]) > run.started]
    model = [u for u in use if u["component"] in ("brain", "respond")]
    cost = sum(u["cost_usd"] or 0 for u in model)
    rep.check(cost < BUDGET, "the brain and Respond cost $%.4f of OpenRouter, under $%.2f" % (cost, BUDGET))
    run.table()
    hours = (time.time() - run.started) / 3600.0
    prompt = sum(u["input_tokens"] or 0 for u in model)
    cached = sum(u["cache_read_tokens"] or 0 for u in model)
    print("\nCOST, from the usage rows (%.0f min of play)" % (hours * 60))
    for u in use:
        print("  %-11s %-14s %-24s in %6d (%3.0f%% cached) out %5d  $%.5f" % (
            u["component"], u["ref"], u["model"][:24], u["input_tokens"] or 0,
            100.0 * (u["cache_read_tokens"] or 0) / u["input_tokens"] if u["input_tokens"] else 0,
            u["output_tokens"] or 0, u["cost_usd"] or 0))
    impl = sum(u["cost_usd"] or 0 for u in use if u["component"] == "implementer")
    for comp in ("brain", "respond"):
        us = [u for u in model if u["component"] == comp]
        if us:
            c = sorted(u["cost_usd"] or 0 for u in us)
            print("  %s: %d calls, $%.4f, median $%.4f a call, %.0f%% of the prompt cached" % (
                comp, len(us), sum(c), c[len(c) // 2],
                100.0 * sum(u["cache_read_tokens"] or 0 for u in us) / max(1, sum(u["input_tokens"] or 0 for u in us))))
    print("  brain and Respond: $%.4f, %.0f%% of %d prompt tokens read from the cache; implementers $%.4f "
          "(the subscription's)" % (cost, 100.0 * cached / prompt if prompt else 0, prompt, impl))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="brain", help="the realmc server (default brain)")
    ap.add_argument("--keep-up", action="store_true", help="leave the server running at the end")
    ap.add_argument("--no-implementer", action="store_true",
                    help="leave out the implementer task and its follow-up: no Claude subscription used")
    args = ap.parse_args()
    key, where = find_key()
    if not key:
        print("no %s in the environment or in a .env above %s" % (KEY, REPO), file=sys.stderr)
        return 2
    os.environ.setdefault("REALMC_ROOT", os.path.join(REALMC, ".server"))
    print("brain live: realmc %s in %s; key from %s" % (args.name, os.environ["REALMC_ROOT"], where), flush=True)
    r = subprocess.run([os.path.join(REALMC, "up"), args.name, "--fresh"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    print(short(r.stdout[-600:], 600), flush=True)
    if r.returncode != 0:
        print("realmc up %s failed" % args.name, file=sys.stderr)
        return 1
    rep = Report()
    live = None
    try:
        live = Live(args.name, key)
        live.start_seq = live.last_seq()
        run = Run(live, rep)
        run.started = time.time()
        if setup(live, rep):
            steps = [action_ask, item_asks, edit_and_stop, two_at_once, talk]
            for step in steps if args.no_implementer else steps + [implementer]:
                try:
                    step(run)
                except Exception:
                    rep.check(False, "the step ran to its end", traceback.format_exc()[-1500:])
        end(run)
    finally:
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass
        if not args.keep_up:
            d = subprocess.run([os.path.join(REALMC, "down"), args.name], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            print("\nrealmc down %s: exit %d" % (args.name, d.returncode))
    failed = rep.failed()
    print("\n" + "=" * 72)
    print("%d/%d checks passed" % (len(rep.checks) - len(failed), len(rep.checks)))
    for name in failed:
        print("  x %s" % name)
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
