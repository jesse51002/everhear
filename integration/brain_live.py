#!/usr/bin/env python3
"""The brain's loop live, on a real 26.3 server on this PC: Prototype_Brain_Loop's "Live, on
realmc" list, with the earlier specs' checks that still hold.

    REALMC_ROOT=… python3 integration/brain_live.py [--name live] [--keep-up] [--no-implementer]
                                                     [--only refused,read,…]

Real:  a Minecraft 26.3 server with Carpet's fake players Steve and Alex (integration/realmc,
       its world fresh from the template), the brain (`vfg brain start` through realmc's bin/vfg,
       which keeps this HOME so the implementers' Claude CLI finds its login), its model calls,
       the loop's steps on Gemini 3.8 Flash and Respond's on DeepSeek V4 Flash, through OpenRouter
       (OPEN_ROUTER_API: from the environment, else the first .env above this repo, or its
       Internal/.env, that holds it; given to the brain's process only, never printed or
       written), the implementers on the Claude subscription (Haiku 5.5), vfg, the actions and
       the scripts, the stand-in box's feed writer and vfg.db, with its tasks table.
Stand-ins: SSH (realmc's bin/ssh), and Jev: each player's line is put into the feed as a LINE
       row through the stand-in's vfg_feed, as Jev puts it there, several in one write when a
       check needs them to arrive together.

It starts from a clean slate: the stand-in's vfg.db (its -wal and -shm with it) and feed.log are
set aside in a timestamped folder beside them, never deleted, and made again by
`vfg_bridge --init-db`; what made/<name>/ held is saved with `vfg made save`, then moved into
made/.replaced/, so the server's tasks and what they made start from none.

Three brains, one after another, on the one server and feed:
1. On the defaults: a one-step ask whose actions all land; a step with actions together, and one
   in turn; a refused command the next step fixes (asks whose first command 26.3 may refuse, in
   turn, until one is: /enchant over Fire Aspect's maximum on the axe Steve holds, the scale
   attribute by its old name, a sword's enchantments in the component's old form); another
   player's items read with the tool; item asks by slot; the brain's own copy started, edited
   and stopped; Steve's second line waiting for his loop while Alex's runs at once; Steve's three
   lines waiting and a fourth turned away by a private message naming them; a conversational
   line for Respond; one hand-off on the real implementer, its brief in parts in the tasks table,
   its report starting a new loop, then a follow-up and a stop by the task's name.
2. VFG_LOOP_STEPS=2 and VFG_IMPLEMENTER_MINUTES=1: a loop that reaches its step limit and ends
   with a reply or a hand-off; and a report going ahead of Steve's waiting lines: a hand-off
   whose implementer reports within the 1-minute limit, while Steve's lines, fed from 15 s after
   it started, keep his turn busy with lines waiting.
3. VFG_LOOP_SECONDS=2 and VFG_IMPLEMENTER_MINUTES=1: a loop that reaches its time limit and ends
   with a reply or a hand-off.
The limits are lowered so a real ask reaches them in a step or two: a loop's 10 steps and 2
minutes are the same code with other numbers.

Every ask is checked in the world over RCON, in the feed's AI rows, in vfg.db's tasks and usage
tables, and in brain.log (each step's line, each loop's line, the turns'). Prints PASS/FAIL per
check, then each ask's time from line to reply with its loop's steps, cost and cache rate, the
implementer tasks' times, every call's cost from the `usage` rows and the cache rate of the steps
after a loop's first. The brain and Respond come to about $0.2; the implementers use the Claude
subscription. The server is left down unless --keep-up. Never `down --all`, `--purge-key`,
`--regen`, `key` or `carpet`: other builders' servers share REALMC_ROOT, and its env file holds a
real TypeSafe key, which nothing here reads or prints. Give a build's run a server of its own
(`--name live`): the clean slate sets aside whatever the server held before.
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
MODELS = ("brain", "respond")   # the usage components on OpenRouter
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")   # a task's name
HOME = (12, 12)    # where the saved place `home` is, on the arena (x, z)
# A hand-off's brief, as code renders it from its parts (brain/call.py HandOff.brief).
BRIEF_HEADS = ("THE PLAYER'S WORDS", "THE WHOLE PICTURE", "WHAT THE PLAYER TAKES FOR GRANTED",
               "HOW IT IS CAST, AND FOR WHOM", "DONE WHEN")
CHAT = ("haha this is fun", "you're the best AI ever", "lol Alex just fell in the lava again")


def load_e2e():
    """brain_e2e.py beside this file: the key's finder and brain.log's grammar."""
    spec = importlib.util.spec_from_file_location("brain_e2e", os.path.join(HERE, "brain_e2e.py"))
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    return e2e


E2E = load_e2e()
LOG_CALL, LOG_LOOP, LOG_STEP, ANY = E2E.LOG_CALL, E2E.LOG_LOOP, E2E.LOG_STEP, E2E.ANY
# A step or Respond call that failed for good, with what it spent: its usage row is written all the same (ok 0).
LOG_FAILED = re.compile(r"^(\S+) (brain|respond) (#\d+|[a-z][a-z0-9_]{1,55})(?: s(\d+))? FAILED \((\d+) ms \$([\d.]+)")
# One action a step ran: `action #5 s1 #2 command ok: …`.
LOG_ACTION = re.compile(r"^(\S+) action (#\d+|[a-z][a-z0-9_]{1,55}) s(\d+) #(\d+) (\w+) (\w+): (.*)$")


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
    return E2E.find_key()


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
            print("          " + short(detail, 2500), flush=True)
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

    def vfg(self, *args, timeout=120, with_key=False, extra=None):
        env = dict(self.env)
        env.update(extra or {})
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

    def start(self, extra=None):
        """`vfg brain start`, the key and `extra` settings (VFG_LOOP_STEPS, …) in its environment only."""
        r = self.vfg("brain", "start", with_key=True, timeout=60, extra=extra)
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

    def clean_slate(self, rep):
        """No brain left running from an earlier run; the stand-in's vfg.db (its -wal and -shm with
        it) and feed.log set aside in vfg/set-aside-<time>/, never deleted, and made again by
        `vfg_bridge --init-db`, as realmc's up makes them; made/<name>/ saved with `vfg made save`,
        then moved into made/.replaced/. False when a fresh vfg.db could not be made."""
        rep.head("a clean slate: realmc %s's vfg.db, feed and made/%s/" % (self.name, self.name))
        self.vfg("brain", "stop", timeout=60)     # exits 1 when none runs
        box = os.path.dirname(self.db)
        old = [n for n in ("vfg.db", "vfg.db-wal", "vfg.db-shm", "feed.log") if os.path.exists(os.path.join(box, n))]
        if old:
            aside = os.path.join(box, "set-aside-" + time.strftime("%Y%m%d-%H%M%S"))
            while os.path.lexists(aside):         # one set aside this second already
                aside += "-2"
            os.makedirs(aside)
            for n in old:
                os.rename(os.path.join(box, n), os.path.join(aside, n))
            rep.note("set aside %s in %s" % (", ".join(old), os.path.relpath(aside, self.dir)))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "VFG_DB": self.db,
               "PYTHONPATH": os.path.join(REPO, "bridge")}
        r = subprocess.run([os.path.join(box, "python", "bin", "python3"), "-m", "vfg_bridge", "--init-db"], env=env,
                           cwd=box, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        try:
            fresh = self.sql("SELECT (SELECT COUNT(*) FROM feed) AS feed, (SELECT COUNT(*) FROM tasks) AS tasks")
        except sqlite3.Error as e:
            fresh = "%s" % e
        ok = rep.check(r.returncode == 0 and fresh == [{"feed": 0, "tasks": 0}],
                       "vfg_bridge --init-db made a fresh vfg.db: no feed rows, and an empty tasks table",
                       "%s; %r" % (r.stdout[-800:], fresh))
        made = os.path.join(REPO, "made", self.name)
        if os.path.isdir(made) and os.listdir(made):
            s = self.vfg("made", "save", timeout=120)
            if rep.check(s.returncode == 0, "what made/%s/ held is saved: %s" % (self.name, s.stdout.strip()),
                         s.stdout + s.stderr):
                kept = os.path.join(REPO, "made", ".replaced", "%s-%s" % (self.name, time.strftime("%Y%m%d-%H%M%S")))
                while os.path.lexists(kept):
                    kept += "-2"
                os.makedirs(os.path.dirname(kept), exist_ok=True)
                os.rename(made, kept)
                rep.note("made/%s/ cleared: moved to %s" % (self.name, os.path.relpath(kept, REPO)))
            else:
                ok = False
        return ok

    def task_row(self, name):
        """The task's row in the stand-in's vfg.db, read-only, made and variables decoded; None
        when there is none."""
        rows = self.sql("SELECT * FROM tasks WHERE name = ?", (name,))
        if not rows:
            return None
        t = rows[0]
        for k in ("made", "variables"):
            try:
                t[k] = None if t[k] is None else json.loads(t[k])
            except ValueError:
                t[k] = None
        return t

    def copy_meta(self, copy):
        """A copy's meta file in the stand-in's run/, kept after the copy ends; None when there is none."""
        try:
            with open(os.path.join(self.dir, "vfg", "run", copy + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def copy_pid(self, copy):
        """A running copy's pid, from its pid file in the stand-in's run/ (vfg script list leaves
        the pid out); None when there is none."""
        try:
            with open(os.path.join(self.dir, "vfg", "run", copy + ".pid"), encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

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
            m = E2E.FEED_RE.match(r["line"])
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

    def day_ticks(self):
        m = re.search(r"is at (\d+) tick", self.cmd("time query day"))
        return int(m.group(1)) if m else None

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
        self.tasks = []    # the implementer tasks' timings
        self.names = []    # the tasks the run opened, by name
        self.claimed = set()   # the seqs of the AI say rows a line's reply took
        self.phase = "defaults"

    # -- lines and replies
    def batch(self, *said):
        """Lines into the feed in one write, (who, class, text) each, so the brain's watch gets them
        together and in order; their asks, not waited for."""
        out = []
        for (seq, at), (who, cls, text) in zip(self.live.lines(*said), said):
            a = {"seq": seq, "at": at, "who": who, "text": text, "cls": cls, "reply": None, "task": None,
                 "phase": self.phase}
            self.asks.append(a)
            self.rep.note("#%d %s (%s): %s" % (seq, who, cls, text))
            out.append(a)
        return out

    def ask(self, who, text, cls="action_oriented", timeout=60):
        """One line into the feed; waits for its turn to end and its reply."""
        a, = self.batch((who, cls, text))
        self.await_reply(a, timeout)
        return a

    def await_reply(self, a, timeout=60):
        """The ask's reply: the AI say row after its line with the words its turn logged (its loop's,
        or Respond's), so turns at once are told apart; `AI say <name>` when it opened or followed up
        a task (the name kept as the ask's task), else `AI say -`. None when the turn ended saying
        nothing (a["silent"]) or did not end in time."""
        live, ref = self.live, "#%d" % a["seq"]

        def got():
            want = E2E.logged_reply(live.log_since(live.mark), ref)
            if want == "":
                return "silent"
            for r in live.ai(a["seq"]) if want is not None else ():
                if r["seq"] not in self.claimed and (want is ANY or E2E.same_words(r["text"], want)):
                    return r
            return None
        r = wait_for(got, timeout)
        a["silent"] = r == "silent"
        if a["silent"]:
            self.rep.note("   -> (no chat line)")
        if not r or r == "silent":
            return None
        self.claimed.add(r["seq"])
        task = (r["fields"].split() + ["-"])[1]
        a["task"] = None if task == "-" else task
        a["reply"], a["reply_at"], a["reply_seq"] = r["text"], parse_iso(r["at"]), r["seq"]
        a["reply_fields"] = r["fields"]
        self.rep.note("   -> %s%s (%.1f s)" % (short(r["text"], 120), " [%s]" % a["task"] if a["task"] else "",
                                               a["reply_at"] - a["at"]))
        return r

    # -- what brain.log says of a turn
    def lines_of(self, ref):
        """brain.log's lines for `ref` this run: its steps, actions, loop, Respond's and the turn's."""
        pat = re.compile(r"^\S+ (brain|loop|action|respond|turn|exec) (report )?%s[ :]" % re.escape(ref))
        return [x for x in self.live.log_since(self.live.mark).splitlines() if pat.match(x)]

    def evidence(self, ref, n=2500):
        return short(" ¶ ".join(self.lines_of(ref)), n)

    def steps(self, ref):
        """The steps of `ref`'s loop: [{n, at (logged), start (its call began), ms, cost, did, came}]."""
        out = []
        for line in self.live.log_since(self.live.mark).splitlines():
            m = LOG_STEP.match(line)
            if m and m.group(2) == ref:
                at, ms = parse_iso(m.group(1)), int(m.group(4))
                out.append({"n": int(m.group(3)), "at": at, "start": at - ms / 1000.0, "ms": ms,
                            "cost": float(m.group(5)), "did": m.group(6), "came": m.group(7)})
        return out

    def actions_of(self, ref):
        """The actions `ref`'s steps ran: [{step, i, name, kind, said, at}]."""
        out = []
        for line in self.live.log_since(self.live.mark).splitlines():
            m = LOG_ACTION.match(line)
            if m and m.group(2) == ref:
                out.append({"at": parse_iso(m.group(1)), "step": int(m.group(3)), "i": int(m.group(4)),
                            "name": m.group(5), "kind": m.group(6), "said": m.group(7)})
        return out

    def loop(self, ref, timeout=0):
        """`ref`'s loop line: {at, who, steps, cost, cached, reply_s, s, ended}; None when there is none."""
        def find():
            for line in self.live.log_since(self.live.mark).splitlines():
                m = LOG_LOOP.match(line)
                if m and m.group(2) == ref:
                    return {"at": parse_iso(m.group(1)), "who": m.group(3), "steps": int(m.group(4)),
                            "cost": float(m.group(5)), "cached": m.group(6),
                            "reply_s": float(m.group(7)) if m.group(7) else None, "s": float(m.group(8)),
                            "ended": m.group(9)}
            return None
        return wait_for(find, timeout) if timeout else find()

    def turn_log(self, pattern):
        """The time of the first `turn` line matching `pattern` this run, or None."""
        for line in self.live.log_since(self.live.mark).splitlines():
            if re.match(r"^\S+ turn " + pattern, line):
                return parse_iso(line.split(" ", 1)[0])
        return None

    def calls(self, ref):
        """The model calls for `ref` in this run's brain.log, Respond's and each step's (`brain s2`):
        [(area, ms, cost, ended at)]. brain.log outlives a run, and the next run's line refs (#2,
        #5, …) are the same."""
        out = []
        for line in self.live.log_since(self.live.mark).splitlines():
            m = LOG_CALL.match(line)
            if m and m.group(3) == ref:
                area = m.group(2) + (" s%s" % m.group(4) if m.group(4) else "")
                out.append((area, int(m.group(5)), float(m.group(6)), parse_iso(m.group(1))))
        return out

    def records(self, a, timeout=5):
        """The ask's loop's AI action rows (its one record): (fields, text). A record is posted after
        the step that ended the loop, nobody waiting, so a row from before that step's line is an
        earlier loop's; and one with the words the step logged as its record, when it wrote one.
        Waited for up to `timeout`."""
        steps = self.steps("#%d" % a["seq"])
        if not steps:
            return []
        logged = re.search(r" \|\| record: (.*?)(?: \|\| status: .*)?$", steps[-1]["did"])

        def rows():
            return [(r["fields"], r["text"]) for r in self.live.ai(a["seq"], ("action",))
                    if parse_iso(r["at"]) >= steps[-1]["at"] - 0.5
                    and (not logged or E2E.same_words(r["text"], logged.group(1)))]
        return wait_for(rows, timeout) or []

    def table(self):
        print("\nLATENCY AND COST: a line fed -> its reply said (the feed rows' times); its loop from brain.log")
        print("  %-5s %-6s %-15s %-9s %7s  %-34s %s" % ("line", "who", "class", "phase", "reply", "loop", "calls"))
        for a in self.asks:
            took = "%.1f s" % (a["reply_at"] - a["at"]) if a.get("reply_at") else ("silent" if a.get("silent") else "-")
            lp = self.loop("#%d" % a["seq"])
            loop = ("%d step(s) $%.4f %s cached, %s" % (lp["steps"], lp["cost"], lp["cached"], lp["ended"])
                    if lp else "-")
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.calls("#%d" % a["seq"]))
            print("  #%-4d %-6s %-15s %-9s %7s  %-34s %s" % (a["seq"], a["who"], a["cls"], a.get("phase", ""), took,
                                                           loop, calls or "-"))
            print("        %s  ->  %s" % (short(a["text"], 64), short(a.get("reply") or "(no reply)", 90)))
        for name in self.names:
            lp = self.loop(name)
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.calls(name))
            print("  %s's report loop: %s; %s" % (name, "%d step(s) $%.4f, ended %s" % (lp["steps"], lp["cost"],
                                                                                       lp["ended"]) if lp else "-",
                                                calls or "-"))
        for t in self.tasks:
            print("  %s" % t)


def overlap(a, b):
    """Seconds two (start, end) spans share; negative when they do not touch."""
    return min(a[1], b[1]) - max(a[0], b[0])


def turn_end(run, ref):
    """When `ref`'s turn ended its model work: its loop's line, else its last call's end; None."""
    lp = run.loop(ref)
    if lp:
        return lp["at"]
    calls = run.calls(ref)
    return max(c[3] for c in calls) if calls else None


def first_call(run, ref):
    """When `ref`'s turn first called a model (Respond's or its loop's first step): (start, end), or None."""
    calls = run.calls(ref)
    if not calls:
        return None
    _, ms, _, end = min(calls, key=lambda c: c[3] - c[1] / 1000.0)
    return end - ms / 1000.0, end


# ======================================================================= the brains

def start_brain(run, rep, extra=None, label="defaults"):
    """`vfg brain start` with `extra` settings, waited for until it follows the feed and greets."""
    live = run.live
    run.phase = label
    mark = len(live.log())
    seq = live.last_seq()
    r = live.start(extra)
    up = wait_for(lambda: re.search(r"^\S+ start pid \d+;(.*)$", live.log_since(mark), re.M), 180)
    want = {"VFG_LOOP_STEPS": "%s steps", "VFG_LOOP_SECONDS": "%s s a loop"}
    said = [w % (extra or {})[k] for k, w in want.items() if k in (extra or {})]
    rep.check(r.returncode == 0 and up and all(s in up.group(1) for s in said),
              "vfg brain start: the brain is up on realmc %s (%s)" % (live.name, ", ".join(
                  "%s=%s" % kv for kv in (extra or {}).items()) or "the defaults"),
              "%s %s; log %s" % (r.stdout, r.stderr, short(live.log_since(mark)[-1500:])))
    if up:
        rep.note(short(up.group(1), 260))
    refs = wait_for(lambda: re.search(r"^\S+ refs server 26\.3: (\d+) commands", live.log_since(mark), re.M), 200)
    rep.check(refs, "the command references for 26.3 are loaded (%s commands)" % (refs.group(1) if refs else "?"))
    greet = wait_for(lambda: [r for r in live.ai(seq) if r["text"].startswith("AI is on")], 30)
    rep.check(greet, "it greeted the players: tellraw on the real server, and an AI say row")
    return bool(up)


def restart(run, rep, extra, label):
    rep.head("a new brain: %s" % ", ".join("%s=%s" % kv for kv in extra.items()))
    r = run.live.stop()
    rep.check(r is not None and r.returncode == 0, "vfg brain stop: the last brain stopped",
              "%s %s" % (r and r.stdout, r and r.stderr))
    return start_brain(run, rep, extra, label)


def setup(run):
    rep, live = run.rep, run.live
    rep.head("setup: Steve and Alex on a fresh world, their items, the place home, the brain started")
    p = live.p
    arena = p.arena()
    p.spawn("Steve")
    p.spawn("Alex", at=(arena["x"] + 4.5, arena["y"], arena["z"] + 0.5))
    for who in ("Steve", "Alex"):
        live.cmd("clear %s" % who)
        live.cmd("effect clear %s" % who)
    # Steve: a diamond axe held (hotbar.0), an iron sword in hotbar.3, iron boots carried in
    # inventory.5, diamond boots worn; no helmet. Alex: a trident held.
    for who, slot, item in (("Steve", "hotbar.0", "diamond_axe"), ("Steve", "hotbar.3", "iron_sword"),
                            ("Steve", "inventory.5", "iron_boots"), ("Steve", "armor.feet", "diamond_boots"),
                            ("Alex", "hotbar.0", "trident")):
        live.cmd("item replace entity %s %s with minecraft:%s" % (who, slot, item))
    p.hotbar("Steve", 1)
    p.hotbar("Alex", 1)
    ok = wait_for(lambda: all(live.data(who, "SelectedItemSlot") == "0" for who in ("Steve", "Alex")), 5)
    rep.check(ok and "diamond_axe" in (live.data("Steve", "Inventory[{Slot:0b}]") or "")
              and "diamond_boots" in (live.data("Steve", "equipment.feet") or "")
              and "iron_boots" in (live.data("Steve", "Inventory[{Slot:14b}]") or "")
              and live.data("Steve", "equipment.head") is None
              and "trident" in (live.data("Alex", "Inventory[{Slot:0b}]") or ""),
              "Steve holds a diamond axe, carries an iron sword and iron boots, wears diamond boots, no helmet; "
              "Alex holds a trident")
    r = live.vfg("place", "save", "home", "--at", str(HOME[0]), str(arena["y"]), str(HOME[1]))
    rep.check(r.returncode == 0, "the place home saved at %d %d %d" % (HOME[0], arena["y"], HOME[1]),
              r.stdout + r.stderr)
    seeded = live.seed_refs()
    rep.note("command references: %s" % (seeded or "none to copy: the brain fetches them (about 95 s)"))
    live.mark = len(live.log())
    return start_brain(run, rep)


# ------------------------------------------------------------- 1. on the defaults

def one_step(run):
    rep, live = run.rep, run.live
    rep.head("1. A one-step ask whose actions all land: the reply goes out with no second call")
    before = live.count_items("Steve", "cooked_beef")
    a = run.ask("Steve", "give me 64 steak")
    ref = "#%d" % a["seq"]
    got = live.count_items("Steve", "cooked_beef") - before
    rep.check(got == 64, "in the world: Steve has 64 more steak (%d)" % got, run.evidence(ref))
    lp = run.loop(ref, 10)
    rep.check(lp and lp["steps"] == 1 and lp["ended"] == "reply" and a["reply"],
              "one step, ended reply: %r" % a["reply"], run.evidence(ref))
    time.sleep(2)
    said = live.ai(a["seq"])
    rep.check(len(said) == 1 and said[0]["fields"] == "say -", "one chat line, with no task (AI say -)",
              "%r" % [(r["fields"], r["text"]) for r in said])
    recs = run.records(a)
    rep.check(len(recs) == 1 and recs[0][0] == "action -", "the loop's one record, AI action -: %r"
              % (recs and recs[0][1]), "%r" % recs)
    rep.check(not live.ai(a["seq"], ("task", "note")), "no AI task and no AI note row",
              "%r" % [r["line"] for r in live.ai(a["seq"], ("task", "note"))])
    meta = live.copy_meta("command_steve") or {}
    fresh = meta.get("started") and parse_iso(meta["started"]) >= a["at"] - 2
    rep.check(fresh and meta.get("action") == "command" and meta.get("task") is None,
              "its copy is command_steve, with no task", "%r" % meta)


def together(run):
    """Two parts no one action does both of (command packs several gives into one action): the
    command action's give, and summon_around's twenty chickens."""
    rep, live = run.rep, run.live
    rep.head("2a. A step with actions together")
    live.cmd("kill @e[type=minecraft:chicken]")
    before = live.count_items("Steve", "cobblestone")
    a = run.ask("Steve", "give me 64 cobblestone and summon twenty chickens around me")
    ref = "#%d" % a["seq"]
    got = live.count_items("Steve", "cobblestone") - before
    m = re.search(r"Killed (\d+)", live.cmd("kill @e[type=minecraft:chicken]"))
    chickens = int(m.group(1)) if m else 0
    rep.check(got == 64 and chickens >= 20, "in the world: 64 cobblestone to Steve and %d chickens round him (now "
              "killed again)" % chickens, run.evidence(ref))
    steps, acts = run.steps(ref), run.actions_of(ref)
    s1 = [x for x in acts if x["step"] == 1]
    rep.check(steps and steps[0]["did"].startswith("together: ") and len(s1) >= 2
              and all(x["kind"] == "ok" for x in s1),
              "step 1 ran %d actions together, every one landed: %s" % (
                  len(s1), ", ".join("#%d %s %s" % (x["i"], x["name"], x["kind"]) for x in s1)), run.evidence(ref))
    lp = run.loop(ref, 10)
    rep.check(lp and lp["steps"] == 1 and lp["ended"] in ("reply", "silent"),
              "one step, ended %s: %r" % (lp and lp["ended"], a["reply"]), run.evidence(ref))


def in_turn(run):
    rep, live = run.rep, run.live
    rep.head("2b. A step with actions in turn: home, then day")
    p, arena = live.p, live.p.arena()
    p.tp("Steve", arena["x"] - 11.5, arena["y"], arena["z"] - 11.5)
    live.cmd("time set midnight")
    rep.note("Steve at the arena's far corner, the time midnight (day at %s ticks)" % live.day_ticks())
    a = run.ask("Steve", "take me home, then make it day")
    ref = "#%d" % a["seq"]
    lp = run.loop(ref, 20)
    steps, acts = run.steps(ref), run.actions_of(ref)
    s1 = sorted((x for x in acts if x["step"] == 1), key=lambda x: x["i"])
    rep.check(steps and steps[0]["did"].startswith("in_turn: ") and len(s1) == 2
              and [x["name"] for x in s1] == ["teleport", "command"] and all(x["kind"] == "ok" for x in s1)
              and s1[0]["at"] <= s1[1]["at"],
              "step 1 ran teleport, then command, in turn, both landed: %s" % ", ".join(
                  "#%d %s %s" % (x["i"], x["name"], x["kind"]) for x in s1), run.evidence(ref))
    pos = p.pos("Steve") or (999, 0, 999)
    far = ((pos[0] - HOME[0]) ** 2 + (pos[2] - HOME[1]) ** 2) ** 0.5
    day = live.day_ticks()
    rep.check(far <= 12 and day is not None and day < 12000,
              "in the world: Steve %.0f blocks from home, the day at %s ticks" % (far, day), run.evidence(ref))
    rep.check(lp and lp["steps"] == 1 and lp["ended"] in ("reply", "silent"),
              "one step, ended %s%s" % (lp and lp["ended"], ": %r" % a["reply"] if a["reply"] else ""),
              run.evidence(ref))
    live.cmd("time set noon")
    p.tp("Steve", arena["x"] + 0.5, arena["y"], arena["z"] + 0.5)


# Asks whose first command 26.3 may refuse, each with its check in the world: /enchant over
# Fire Aspect's maximum (the spec's example; the prompt warns of it, so the brain may go straight to
# an item modifier); the `generic.` attribute names 26.3 dropped (`minecraft:scale`); and the
# enchantments component's older `{levels:{…}}` form, which 26.3 refuses as malformed.
REFUSABLE = (
    ("use /enchant to put fire aspect 5 on the axe I'm holding",
     lambda live: '"minecraft:fire_aspect": 5' in (live.data("Steve", "Inventory[{Slot:0b}]") or "")),
    ("make me twice as big",
     lambda live: re.search(r"is 2(\.0)?$", live.cmd("attribute Steve minecraft:scale base get").strip())),
    ("give me a diamond sword with sharpness 5",
     lambda live: re.search(r'\{components: \{"minecraft:enchantments": \{[^}]*"minecraft:sharpness": 5[^}]*\}\}, '
                            r'count: \d+, Slot: \d+b, id: "minecraft:diamond_sword"', live.data("Steve", "Inventory") or "")),
)


def refused(run):
    """A refused command the next step fixes: each ask of REFUSABLE in turn, until one's first
    command is refused; that loop is the check."""
    rep, live = run.rep, run.live
    rep.head("3. A refused command the next step fixes")
    for text, landed in REFUSABLE:
        a = run.ask("Steve", text, timeout=90)
        ref = "#%d" % a["seq"]
        lp = run.loop(ref, 20)
        acts = run.actions_of(ref)
        no = [x for x in acts if x["kind"] == "refused"]
        if no:
            break
        rep.note("nothing refused: %d step(s), %s; in the world %s" % (
            lp["steps"] if lp else 0, ", ".join("%s (%s)" % (x["name"], short(x["said"], 70)) for x in acts)
            or "no action", "as asked" if landed(live) else "NOT as asked"))
    live.cmd("attribute Steve minecraft:scale base reset")
    rep.check(no and no[0]["step"] == 1, "%r: step 1's command was refused by Minecraft: %s"
              % (text, no and short(no[0]["said"], 160)), run.evidence(ref))
    if not no:
        return
    fixed = [x for x in acts if x["step"] > no[0]["step"] and x["kind"] == "ok"]
    rep.check(lp and lp["steps"] >= 2 and fixed and lp["ended"] == "reply" and landed(live),
              "a later step fixed it, and it is so in the world: %s; %d step(s), ended %s: %r" % (
                  fixed and short(fixed[0]["said"], 120), lp and lp["steps"], lp and lp["ended"], a["reply"]),
              run.evidence(ref))
    recs = run.records(a)
    rep.check(len(recs) == 1, "the loop's one record: %r" % (recs and recs[0][1]), "%r" % recs)
    rep.note("its steps: %s" % " | ".join("s%d %s -> %s" % (x["n"], short(x["did"], 160), short(x["came"], 60))
                                          for x in run.steps(ref)))


def read_items(run):
    rep = run.rep
    rep.head("4. Another player's items, read with the tool")
    a = run.ask("Steve", "what is Alex holding right now?")
    ref = "#%d" % a["seq"]
    lp = run.loop(ref, 10)
    steps = run.steps(ref)
    read = [s for s in steps if re.search(r"(^| \| )read Alex\b", s["did"])]
    rep.check(read and "Alex" in read[0]["came"] and "refused" not in read[0]["came"],
              "step %s read Alex's items: %s" % (read and read[0]["n"], read and short(read[0]["came"], 100)),
              run.evidence(ref))
    rep.check(lp and lp["steps"] >= 2 and a["reply"] and re.search(r"trident", a["reply"], re.I),
              "the next step's reply names his trident: %r" % a["reply"], run.evidence(ref))


def item_asks(run):
    rep, live = run.rep, run.live
    rep.head("5. Item asks by slot: boots worn and boots carried; a helmet he does not have")
    a = run.ask("Steve", "put depth strider three on my boots")
    worn, carried = live.data("Steve", "equipment.feet") or "", live.data("Steve", "Inventory[{Slot:14b}]") or ""
    rep.check('"minecraft:depth_strider": 3' in worn and "enchantments" not in carried,
              "Depth Strider III on the diamond boots he wears; the iron boots he carries untouched",
              "worn %s; carried %s; %s" % (worn, carried, run.evidence("#%d" % a["seq"])))
    rep.check(a["reply"], "one reply: %r" % a["reply"])
    a = run.ask("Steve", "put protection four on my helmet")
    ref = "#%d" % a["seq"]
    acts = run.actions_of(ref)
    rep.check(live.data("Steve", "equipment.head") is None and not [x for x in acts if x["kind"] == "ok"],
              "no helmet: nothing changed (%s)" % (", ".join("%s %s" % (x["name"], x["kind"]) for x in acts)
                                                  or "no action run"), run.evidence(ref))
    rep.check(a["reply"] and re.search(r"helmet|head", a["reply"], re.I), "the reply says so: %r" % a["reply"])


def own_copy(run):
    rep, live = run.rep, run.live
    rep.head("6. The brain's own copy of keep_effects: started, edited and stopped by the loop's tools")
    name = "keep_effects_steve"
    a = run.ask("Steve", "keep speed two on me the whole time, even after I die, until I say stop")
    copies = {k: v for k, v in live.copies().items() if v.get("action") == "keep_effects"}
    lvl = live.effect_level("Steve", "speed")
    rep.check(list(copies) == [name] and not copies[name].get("task") and lvl == 1,
              "the start gives the copy %s, with no task, and Steve has Speed II" % name,
              "copies %r; speed amplifier %r; %s" % (copies, lvl, run.evidence("#%d" % a["seq"])))
    b = run.ask("Steve", "make it speed 5")
    ref = "#%d" % b["seq"]
    lvl = wait_for(lambda: live.effect_level("Steve", "speed") == 4, 10) and 4 or live.effect_level("Steve", "speed")
    edits = [x for x in run.actions_of(ref) if x["name"] == "edit"]
    rep.check(lvl == 4 and edits and edits[0]["kind"] == "ok" and name in live.copies(),
              "the edit action landed: Speed V, %s still running" % name,
              "speed amplifier %r; %s" % (lvl, run.evidence(ref)))
    rep.check(len(run.records(b)) == 1 and not live.ai(b["seq"], ("note",)),
              "one record for it (%r), no AI note row" % (run.records(b) and run.records(b)[0][1]),
              "%r %r" % (run.records(b), live.ai(b["seq"], ("note",))))
    c = run.ask("Steve", "stop my effects")
    ref = "#%d" % c["seq"]
    gone = wait_for(lambda: name not in live.copies(), 10)
    stops = [x for x in run.actions_of(ref) if x["name"] == "stop"]
    rep.check(gone and stops and stops[0]["kind"] == "ok", "the stop action landed: %s is gone" % name,
              "copies %r; %s" % (live.copies(), run.evidence(ref)))
    rep.note("Steve's speed after the stop: %r" % live.effect_level("Steve", "speed"))


def two_players(run):
    rep, live = run.rep, run.live
    rep.head("7. Steve's second line waits for his loop, while Alex's runs at once")
    bread, pick, shield = (live.count_items("Steve", "bread"), live.count_items("Steve", "iron_pickaxe"),
                           live.count_items("Alex", "shield"))
    s1, s2, x = run.batch(("Steve", "action_oriented", "give me 16 bread"),
                          ("Steve", "action_oriented", "and an iron pickaxe"),
                          ("Alex", "action_oriented", "can I get a shield"))
    for a in (s1, x, s2):
        run.await_reply(a, 90)
    r1, r2, rx = ("#%d" % a["seq"] for a in (s1, s2, x))
    rep.check(live.count_items("Steve", "bread") - bread == 16 and live.count_items("Steve", "iron_pickaxe") - pick >= 1
              and live.count_items("Alex", "shield") - shield >= 1,
              "in the world: 16 bread and an iron pickaxe to Steve, a shield to Alex",
              " ¶ ".join(run.evidence(r) for r in (r1, r2, rx)))
    waited = run.turn_log(r"%s Steve waits for their turn" % re.escape(r2))
    rep.check(waited, "brain.log: %s Steve waits for their turn" % r2, run.evidence(r2))
    l1, c2, c1, cx = run.loop(r1), first_call(run, r2), first_call(run, r1), first_call(run, rx)
    rep.check(l1 and c2 and c2[0] >= l1["at"] - 0.2,
              "Steve's second loop began %.1f s after his first ended" % ((c2[0] - l1["at"]) if l1 and c2 else -1),
              "%s ¶ %s" % (run.evidence(r1), run.evidence(r2)))
    rep.check(c1 and cx and overlap(c1, cx) > 0,
              "Alex's loop ran beside Steve's first: their first steps overlapped %.1f s"
              % (overlap(c1, cx) if c1 and cx else -1), "%s ¶ %s" % (run.evidence(r1), run.evidence(rx)))


def waiting(run):
    rep, live = run.rep, run.live
    rep.head("8. Three of Steve's lines wait, and a fourth is turned away by a private message naming them")
    time.sleep(4)  # Steve's last turn waits up to VFG_TURN_SETTLE_S for its rows to come back: his lane is free
    first, *chat, fourth = run.batch(("Steve", "action_oriented", "give me a stack of torches"),
                                     *(("Steve", "conversational", t) for t in CHAT),
                                     ("Steve", "conversational", "ok and one more thing"))
    gone = "#%d" % fourth["seq"]
    away = wait_for(lambda: run.turn_log(r"%s Steve turned away, 3 waiting" % re.escape(gone)), 30)
    rep.check(away, "brain.log: %s Steve turned away, 3 waiting" % gone, short(live.log_since(live.mark)[-2000:]))
    want = "(to Steve) Still on: %s." % ", ".join("“%s”" % t for t in CHAT)
    private = wait_for(lambda: [r for r in live.ai(first["seq"]) if r["text"].startswith("(to Steve) Still on:")], 30)
    rep.check(private and private[0]["fields"] == "say -" and all(t in private[0]["text"] for t in CHAT)
              and "can't take any more" in private[0]["text"] and fourth["text"] not in private[0]["text"],
              "the private message to Steve alone, naming the three waiting lines: %r"
              % (private and private[0]["text"]), "%r; wanted %r…" % (private, want))
    if private:
        run.claimed.add(private[0]["seq"])
    for a in [first] + chat:
        run.await_reply(a, 90)
    time.sleep(3)
    refs = ["#%d" % a["seq"] for a in [first] + chat]
    spans = [first_call(run, r) for r in refs]
    ends = [turn_end(run, r) for r in refs]
    ordered = all(spans) and all(ends) and all(spans[i + 1][0] >= ends[i] - 0.2 for i in range(len(spans) - 1))
    rep.check(ordered, "the three waiting lines took Steve's turn one after another, after his first: %s"
              % ", ".join("%s at +%.1f s" % (r, s[0] - first["at"]) for r, s in zip(refs, spans) if s),
              " ¶ ".join(run.evidence(r) for r in refs))
    row = [r for r in live.rows(fourth["seq"] - 1, "LINE") if r["seq"] == fourth["seq"]]
    use = live.sql("SELECT ref FROM usage WHERE ref = ? OR ref LIKE ?", (gone, gone + " s%"))
    rep.check(row and row[0]["processed_at"] and not use
              and not [x for x in run.lines_of(gone) if not re.match(r"^\S+ turn ", x)],
              "the turned-away line is marked read and never answered: no model call for it",
              "row %r; usage %r; %s" % (row, use, run.evidence(gone)))


def talk(run):
    rep, live = run.rep, run.live
    rep.head("9. A conversational line: Respond")
    a = run.ask("Alex", "haha thanks AI, you're a legend", cls="conversational", timeout=30)

    def use():  # the usage row goes out beside the reply, nobody waiting: it may come a moment after
        return sorted(u["component"] for u in live.sql("SELECT component FROM usage WHERE ref = ? OR ref LIKE ?",
                                                       ("#%d" % a["seq"], "#%d s%%" % a["seq"])))
    wait_for(use, 15)
    time.sleep(2)
    got = use()
    rep.check(a["reply"] and got == ["respond"], "Respond replied, and no step of the brain: %r" % a["reply"],
              "%r" % got)


def implementer(run):
    rep, live = run.rep, run.live
    rep.head("10. A hand-off and its brief; the report starting a new loop; a follow-up and a stop by its name")
    centre = live.p.pos("Steve")
    rep.note("Steve stands at %s" % " ".join("%.1f" % c for c in centre))
    mark = len(live.log())
    a = run.ask("Steve", "put a ring of eight torches on the ground around me, three blocks out", timeout=120)
    ref = "#%d" % a["seq"]
    # The AI task row and the reply are both written with nobody waiting: either may land first.
    opened = wait_for(lambda: [r for r in live.ai(a["seq"], ("task",)) if r["text"].startswith("new: ")], 15) or []
    tid = opened[0]["fields"].split()[1] if opened else None
    library = tid and os.path.exists(os.path.join(REPO, "actions", tid + ".py"))
    lp = run.loop(ref, 15)
    rep.check(tid and NAME_RE.match(tid) and not library and len(opened) == 1 and lp and lp["ended"] == "handoff",
              "the loop ended in a hand-off: the task %s, its AI task row %r"
              % (tid, opened and short(opened[0]["text"], 120)),
              run.evidence(ref))
    if not tid:
        return
    run.names.append(tid)
    rep.check(a["silent"] or a.get("reply_fields") == "say %s" % tid,
              "its line went out under its name (AI say %s): %r" % (tid, a["reply"]), "%r" % a.get("reply_fields"))
    row = live.task_row(tid) or {}
    brief = row.get("brief") or ""
    heads = [h for h in BRIEF_HEADS if h + "\n" in brief]
    done_when = brief.split("DONE WHEN\n", 1)[1].splitlines() if "DONE WHEN\n" in brief else []
    rep.check(len(heads) == len(BRIEF_HEADS) and done_when and all(x.startswith("- ") for x in done_when)
              and row.get("player") == "Steve" and row.get("description"),
              "its brief in vfg.db holds every part under its heading, and %d done-when items" % len(done_when),
              "%r" % row)
    rep.note("the brief: %s" % short(brief.replace("\n", " / "), 700))
    started = wait_for(lambda: re.search(r"^(\S+) task %s started for Steve" % tid, live.log_since(mark), re.M), 30)
    rep.check(started and lp and lp["at"] - parse_iso(started.group(1)) < 3,
              "the turn ended as the implementer started (%.1f s after)" % (
                  lp["at"] - parse_iso(started.group(1)) if started and lp else -1), run.evidence(ref))
    # While it works, a context line: marked read as it comes, and no model is shown it.
    live.lines(("Alex", "context", "lol Steve keeps walking into the lava over there"))
    t0 = opened[0]["at"]
    res = wait_for(lambda: live.ai(a["seq"], ("result",), tid), TASK_S, 1.0)
    rep.check(res, "%s's implementer reported, and the brain recorded it" % tid, short(live.log_since(mark)[-2000:]))
    if not res:
        return
    time.sleep(3)
    said = [r for r in live.ai(a["seq"], ("say",), tid) if r["seq"] > a.get("reply_seq", 0)]
    results = live.ai(a["seq"], ("result",), tid)
    rl = run.loop(tid, 10)
    rep.check(rl and rl["ended"] in ("reply", "handoff", "silent") and run.calls(tid),
              "the report started a new loop: %s" % ("%d step(s), $%.4f, ended %s" % (rl["steps"], rl["cost"],
                                                                                     rl["ended"]) if rl else "none"),
              run.evidence(tid))
    rep.check(len(said) == 1 and len(results) == 1 and results[0]["text"].startswith(tid),
              "the report became one chat line (%r) and one AI result row (%r)"
              % (said and said[0]["text"], results[0]["text"]), "%r %r" % (said, results))
    report = re.search(r"^\S+ implementer %s (done|failed): (.*)$" % tid, live.log_since(mark), re.M)
    tm = re.search(r"^\S+ implementer %s (?:ended|error) \((\w+), (\d+) turns, (\d+) s\)" % tid,
                   live.log_since(mark), re.M)
    run.tasks.append("%s: brief to report %.0f s (the implementer's own %s s, %s turns, %s); %s" % (
        tid, parse_iso(results[0]["at"]) - parse_iso(t0), tm and tm.group(3), tm and tm.group(2),
        tm and tm.group(1), report and report.group(1)))
    rep.note("in the world: %d torches on the ground round Steve (the implementer's work, not counted)"
             % live.count_blocks("torch", centre))
    rep.note("its report, as the implementer wrote it: %s" % short(report.group(2) if report else "?", 240))
    nothing_from_it(run, a["seq"], tid, report.group(2) if report else "", mark)
    row = live.task_row(tid) or {}
    made = {k: v for k, v in (row.get("made") or {}).items() if v}
    rep.check(row.get("player") == "Steve" and row.get("status") in ("done", "failed") and row.get("session"),
              "its row in vfg.db: for Steve, %s, with its implementer's session; made %s"
              % (row.get("status"), made or "nothing"), "%r" % row)
    if row.get("status") != "done":
        return

    # The follow-up, by its name, which no value covers: the follow-up tool, to the same task's
    # implementer, which resumes its session.
    b = run.ask("Steve", "make those soul torches, and keep the ring round me as I walk until I say stop", timeout=120)
    ref = "#%d" % b["seq"]
    fu = wait_for(lambda: [r for r in live.ai(b["seq"], ("task",), tid) if r["text"].startswith("follow-up: ")],
                  15) or []
    other = [r for r in live.ai(b["seq"], ("task",)) if r["fields"] != "task %s" % tid]
    steps = run.steps(ref)
    rep.check(fu and not other and any("follow up %s" % tid in s["did"] for s in steps),
              "the loop followed %s up: an AI task %s row, %r" % (tid, tid, fu and short(fu[0]["text"], 120)),
              "%r; %s" % ([r["line"] for r in other], run.evidence(ref)))
    resumed = wait_for(lambda: re.search(r"^\S+ implementer %s resumed ([^\s:]+)" % tid, live.log_since(mark),
                                         re.M), 30)
    rep.check(resumed, "the implementer resumed %s's session (%s; its row's %s)"
              % (tid, resumed and resumed.group(1), row.get("session")))
    t1 = fu[0]["at"] if fu else None
    res2 = wait_for(lambda: len(live.ai(b["seq"], ("result",), tid)) >= 1
                    and (live.task_row(tid) or {}).get("status") != "open", TASK_S, 1.0)
    rep.check(res2, "the follow-up reported, and a new loop recorded it", short(live.log_since(mark)[-2000:]))
    if res2:
        time.sleep(3)
        results = live.ai(b["seq"], ("result",), tid)
        reports = re.findall(r"^\S+ implementer %s (done|failed): (.*)$" % tid, live.log_since(mark), re.M)
        tms = re.findall(r"^\S+ implementer %s (?:ended|error) \((\w+), (\d+) turns, (\d+) s\)" % tid,
                         live.log_since(mark), re.M)
        if t1 and results:
            run.tasks.append("%s follow-up: brief to report %.0f s (the implementer's own %s s, %s turns, %s); %s" % (
                tid, parse_iso(results[0]["at"]) - parse_iso(t1), tms[-1][2] if len(tms) > 1 else None,
                tms[-1][1] if len(tms) > 1 else None, tms[-1][0] if len(tms) > 1 else None,
                reports[-1][0] if reports else "?"))
        rep.note("in the world: %d soul torches round Steve (the implementer's work, not counted)"
                 % live.count_blocks("soul_torch", live.p.pos("Steve") or centre))
        nothing_from_it(run, b["seq"], tid, reports[-1][1] if reports else "", mark)
    copy = live.copies().get(tid)
    if not copy:
        rep.note("%s runs no copy after its follow-up: the stop by its name is left out" % tid)
        return
    pid = live.copy_pid(tid)
    environ = b""
    try:
        with open("/proc/%d/environ" % (pid or 0), "rb") as f:
            environ = f.read()
    except OSError:
        pass
    rep.check(("VFG_TASK=%s" % tid).encode() in environ.split(b"\0"),
              "vfg script list --fresh shows %s running (pid %s), with VFG_TASK=%s" % (tid, pid, tid),
              "copy %r; environ has VFG_TASK: %r" % (copy, [e for e in environ.split(b"\0")
                                                         if e.startswith(b"VFG_TASK=")]))
    # A stop by its name: the stop action on the copy; the task stays done.
    c = run.ask("Steve", "ok stop the torch ring")
    ref = "#%d" % c["seq"]
    gone = wait_for(lambda: tid not in live.copies(), 10)
    stops = [x for x in run.actions_of(ref) if x["name"] == "stop" and x["kind"] == "ok"]
    row = live.task_row(tid) or {}
    rep.check(gone and stops and row.get("status") == "done",
              "a stop by name: the stop action landed, %s is gone, and its row is still done" % tid,
              "copies %r; status %r; %s" % (live.copies(), row.get("status"), run.evidence(ref)))


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


# ------------------------------------------------------------- 2. and 3. the limits, the report first

def at_limit(run, which):
    """A loop that reaches its limit (`which`: steps or seconds): an ask that reads first, then
    would act, whose second step is its last, so it runs nothing and ends with a reply or a hand-off."""
    rep, live = run.rep, run.live
    a = run.ask("Steve", "give me the same item Alex is holding, exactly the same", timeout=120)
    ref = "#%d" % a["seq"]
    lp = run.loop(ref, 20)
    steps, acts = run.steps(ref), run.actions_of(ref)
    last = steps[-1] if steps else None
    limit = (lp and lp["steps"] == 2) if which == "steps" else (lp and lp["steps"] < 10 and lp["s"] > 2)
    rep.check(limit and last and not re.match(r"^(together: |in_turn: |read )", last["did"])
              and not [x for x in acts if x["step"] == last["n"]] and lp["ended"] in ("reply", "handoff"),
              "the loop reached its %s limit: %d step(s) in %.1f s, its last ran nothing, ended %s: %r" % (
                  which, lp["steps"] if lp else 0, lp["s"] if lp else 0, lp and lp["ended"], a["reply"]),
              run.evidence(ref))
    rep.note("its steps: %s" % " | ".join("s%d %s" % (s["n"], short(s["did"], 120)) for s in steps))
    if lp and lp["ended"] == "handoff":
        names = [r["fields"].split()[1] for r in live.ai(a["seq"], ("task",))]
        rep.note("it handed off %s: waiting for the report (1 minute at most)" % ", ".join(names))
        wait_for(lambda: all(live.ai(a["seq"], ("result",), n) for n in names), 120, 1.0)


def step_limit(run):
    run.rep.head("11. A loop that reaches its step limit (VFG_LOOP_STEPS=2) and replies or hands off")
    at_limit(run, "steps")


def time_limit(run):
    run.rep.head("13. A loop that reaches its time limit (VFG_LOOP_SECONDS=2) and replies or hands off")
    at_limit(run, "seconds")


FILLERS = ("give me an apple", "and a cooked chicken", "and a loaf of bread", "and a carrot", "and a baked potato",
           "and a pumpkin pie", "and a cookie", "and a melon slice")   # Steve's lines that keep his turn busy


def report_first(run):
    """A hand-off whose implementer has a minute at most (VFG_IMPLEMENTER_MINUTES=1); 15 s after it
    starts, Steve's lines begin to keep his turn busy, two waiting at all times (a read-then-act
    ask, then small gives), until the report comes: it goes ahead of the lines still waiting."""
    rep, live = run.rep, run.live
    rep.head("12. A report goes ahead of Steve's waiting lines (VFG_IMPLEMENTER_MINUTES=1: a report within 60 s)")
    mark = len(live.log())
    a = run.ask("Steve", "make me a staff that shoots three homing fireballs that split into smaller ones when "
                         "they hit", timeout=120)
    opened = wait_for(lambda: [r for r in live.ai(a["seq"], ("task",)) if r["text"].startswith("new: ")], 15) or []
    tid = opened[0]["fields"].split()[1] if opened else None
    rep.check(tid, "the loop handed it off: %s" % tid, run.evidence("#%d" % a["seq"]))
    if not tid:
        return
    run.names.append(tid)
    started = wait_for(lambda: re.search(r"^(\S+) implementer %s started:" % tid, live.log_since(mark), re.M), 60)
    if not rep.check(started, "%s's implementer started" % tid, short(live.log_since(mark)[-1500:])):
        return
    at = parse_iso(started.group(1))

    def reported():
        return live.ai(a["seq"], ("result",), tid)
    wait_for(lambda: time.time() >= at + 15 or reported(), 30, 0.5)
    if reported():
        rep.check(False, "the report came after Steve's lines were fed", "%s reported within 15 s: nothing was "
                  "waiting" % tid)
        return
    lines = run.batch(("Steve", "action_oriented", "give me the same item Alex is holding, exactly the same"),
                      *(("Steve", "action_oriented", t) for t in FILLERS[:2]))
    more = list(FILLERS[2:])
    while not reported() and time.time() < at + 80:
        if more and sum(not run.calls("#%d" % x["seq"]) for x in lines) <= 2:   # one running, at most one waiting
            lines += run.batch(("Steve", "action_oriented", more.pop(0)))
        time.sleep(0.5)
    res = wait_for(reported, 30, 0.5)
    for x in lines:
        run.await_reply(x, 120)
    time.sleep(2)
    queued = run.turn_log(r"report %s waits for Steve's turn, first in line" % re.escape(tid))
    rl, rc = run.loop(tid), first_call(run, tid)
    behind = [x for x in lines if (first_call(run, "#%d" % x["seq"]) or (0, 0))[0] > (queued or 1e12)]
    rep.check(res and queued and rc and behind
              and all(first_call(run, "#%d" % x["seq"])[0] >= rc[0] for x in behind),
              "brain.log: report %s waits for Steve's turn, first in line, %.0f s after its implementer started; its "
              "loop ran before %s, which had waited since before it" % (
                  tid, (queued or at) - at, ", ".join("#%d" % x["seq"] for x in behind) or "no line"),
              "%s ¶ %s" % (run.evidence(tid), " ¶ ".join(run.evidence("#%d" % x["seq"]) for x in lines)))
    rep.check(rl and res, "the report's loop: %s; its AI result row %r" % (
        "%d step(s), ended %s" % (rl["steps"], rl["ended"]) if rl else "none", res and short(res[0]["text"], 120)),
        run.evidence(tid))


# ======================================================================= the end

def end(run):
    rep, live = run.rep, run.live
    rep.head("the end")
    unread = [r for r in live.rows(run.start_seq) if r["type"] == "LINE" and not r["processed_at"]]
    rep.check(not unread, "every line of the run was read and marked processed", "%r" % unread)
    # Before the stop, which fails whatever is still open; a task the last brain handed off has 1 minute.
    open_ = wait_for(lambda: not live.sql("SELECT name FROM tasks WHERE status = 'open'"), 90, 1.0)
    rep.check(open_, "no task open in vfg.db (%d in its tasks table)" % len(live.sql("SELECT name FROM tasks")),
              "%r" % live.sql("SELECT name, status FROM tasks WHERE status = 'open'"))
    r = live.stop()
    log = live.log_since(live.mark)
    rep.check(r is not None and r.returncode == 0 and re.search(r"^\S+ stop stopped$", log, re.M),
              "vfg brain stop: the brain stopped cleanly", "%s %s" % (r and r.stdout, r and r.stderr))
    rep.check("Traceback" not in log and not re.search(r"^\S+ error ", log, re.M), "no traceback or error in brain.log",
              short(log[max(0, log.find("Traceback")):][:1500]) + " ¶ " + " ¶ ".join(
                  x for x in log.splitlines() if re.match(r"^\S+ error ", x))[:1500])
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
    model = [u for u in use if u["component"] in MODELS]
    cost = sum(u["cost_usd"] or 0 for u in model)
    rep.check(cost < BUDGET, "the brain and Respond cost $%.4f of OpenRouter, under $%.2f" % (cost, BUDGET))
    steps = [m for m in (LOG_CALL.match(x) or LOG_FAILED.match(x) for x in log.splitlines())
             if m and m.group(2) == "brain"]
    brain = [u for u in model if u["component"] == "brain"]
    rep.check(brain and len(steps) == len(brain) and all(re.match(r"^(#\d+|[a-z][a-z0-9_]*) s\d+$", u["ref"] or "")
                                                          for u in brain),
              "every step, answered or failed, is a usage row `<ref> s<n>` (%d) and a line of brain.log (%d)"
              % (len(brain), len(steps)), "%r" % sorted({u["ref"] for u in brain}))
    old = [u for u in use if u["component"] in ("orchestrator", "planner")]
    rep.check(not old, "no orchestrator or planner call", "%r" % old)
    run.table()
    print("\nCOST, from the usage rows (%.0f min of play)" % ((time.time() - run.started) / 60.0))
    for u in use:
        print("  %-12s %-20s %-26s in %6d (%3.0f%% cached) out %5d  $%.5f" % (
            u["component"], (u["ref"] or "")[:20], (u["model"] or "")[:26], u["input_tokens"] or 0,
            100.0 * (u["cache_read_tokens"] or 0) / u["input_tokens"] if u["input_tokens"] else 0,
            u["output_tokens"] or 0, u["cost_usd"] or 0))
    impl = sum(u["cost_usd"] or 0 for u in use if u["component"] == "implementer")

    def line(name, us):
        if not us:
            return
        c = sorted(u["cost_usd"] or 0 for u in us)
        print("  %s: %d calls, $%.4f, median $%.4f a call, %.0f%% of the prompt cached" % (
            name, len(us), sum(c), c[len(c) // 2],
            100.0 * sum(u["cache_read_tokens"] or 0 for u in us) / max(1, sum(u["input_tokens"] or 0 for u in us))))
    line("the brain's steps", brain)
    line("  first steps", [u for u in brain if u["ref"].endswith(" s1")])
    line("  steps after a loop's first", [u for u in brain if not u["ref"].endswith(" s1")])
    line("respond", [u for u in model if u["component"] == "respond"])
    loops = [m for m in map(LOG_LOOP.match, log.splitlines()) if m]
    if loops:
        n = [int(m.group(4)) for m in loops]
        print("  loops: %d, %d of one step, %d of two or more; ended %s" % (
            len(n), sum(x == 1 for x in n), sum(x > 1 for x in n),
            ", ".join("%s %d" % (e, sum(m.group(9) == e for m in loops)) for e in sorted({m.group(9) for m in loops}))))
    prompt = sum(u["input_tokens"] or 0 for u in model)
    cached = sum(u["cache_read_tokens"] or 0 for u in model)
    print("  brain and Respond: $%.4f, %.0f%% of %d prompt tokens read from the cache; implementers $%.4f (the "
          "subscription's)" % (cost, 100.0 * cached / prompt if prompt else 0, prompt, impl))


STEPS = {   # the checks, in the order they run, each with the brain it needs
    "one_step": (one_step, "defaults"), "together": (together, "defaults"), "in_turn": (in_turn, "defaults"),
    "refused": (refused, "defaults"), "read": (read_items, "defaults"), "items": (item_asks, "defaults"),
    "own_copy": (own_copy, "defaults"), "two_players": (two_players, "defaults"), "waiting": (waiting, "defaults"),
    "talk": (talk, "defaults"), "implementer": (implementer, "defaults"),
    "step_limit": (step_limit, "steps"), "report_first": (report_first, "steps"),
    "time_limit": (time_limit, "seconds"),
}
BRAINS = {"steps": {"VFG_LOOP_STEPS": "2", "VFG_IMPLEMENTER_MINUTES": "1"},
          "seconds": {"VFG_LOOP_SECONDS": "2", "VFG_IMPLEMENTER_MINUTES": "1"}}
IMPLEMENTER = ("implementer", "report_first")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="live", help="the realmc server (default live)")
    ap.add_argument("--keep-up", action="store_true", help="leave the server running at the end")
    ap.add_argument("--no-implementer", action="store_true",
                    help="leave out the checks that start an implementer: no Claude subscription used")
    ap.add_argument("--only", default="", help="these checks only, comma-separated: " + ", ".join(STEPS))
    args = ap.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    if set(only) - set(STEPS):
        ap.error("no such check: %s" % ", ".join(sorted(set(only) - set(STEPS))))
    chosen = [s for s in STEPS if (not only or s in only) and not (args.no_implementer and s in IMPLEMENTER)]
    key, where = find_key()
    if not key:
        print("no %s in the environment or in a .env above %s" % (KEY, REPO), file=sys.stderr)
        return 2
    os.environ.setdefault("REALMC_ROOT", os.path.join(REALMC, ".server"))
    print("brain live: realmc %s in %s; key from %s; %s" % (args.name, os.environ["REALMC_ROOT"], where,
                                                            ", ".join(chosen)), flush=True)
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
        if live.clean_slate(rep):     # else nothing runs on what an earlier run left
            live.start_seq = live.last_seq()
            run = Run(live, rep)
            run.start_seq = live.start_seq
            run.started = time.time()
            if setup(run):
                brain = "defaults"
                for name in chosen:
                    step, needs = STEPS[name]
                    if needs != brain:
                        brain = needs
                        if not restart(run, rep, BRAINS[needs], needs):
                            break
                    try:
                        step(run)
                    except Exception:
                        rep.check(False, "%s ran to its end" % name, traceback.format_exc()[-1500:])
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
