#!/usr/bin/env python3
"""The brain live, on a real 26.3 server on this PC: Prototype_Conversation_And_Router's "Live, on
realmc" list, with the checks of Prototype_Brain_Loop's that still mean something.

    REALMC_ROOT=… python3 integration/brain_live.py [--name live] [--keep-up] [--only refused,read,…]

Real:  a Minecraft 26.3 server with Carpet's fake players Steve and Alex (integration/realmc,
       its world fresh from the template), the brain (`vfg brain start` through realmc's bin/vfg,
       which keeps this HOME), its model calls through OpenRouter on the brain's defaults (the
       conversation agent and the router on DeepSeek V4.1 Flash with no reasoning, the steps at low;
       OPEN_ROUTER_API: from the environment, else the first .env above this repo, or its
       Internal/.env, that holds it; given to the brain's process only, never printed or
       written), vfg, the actions and the scripts, the stand-in box's feed writer and vfg.db,
       with its tasks table, and the implementers' real runner, relay and Claude Code CLI.
Stand-ins: SSH (realmc's bin/ssh); Jev: each player's line is put into the feed as a LINE row
       through the stand-in's vfg_feed, as Jev puts it there, several in one write when a check
       needs them to arrive together; the implementers' MODEL (integration/stand_in_model.py, on
       127.0.0.1, the brain told so with VFG_IMPL_PROVIDER=deepseek, DEEPSEEK_API_KEY=stand-in and
       VFG_IMPL_BASE_URL): it records what each implementer is sent and answers with a report after
       the delay a check sets, building nothing and costing nothing; and a slow provider, on cue
       (integration/shims/slow_step, on the brain's PYTHONPATH with VFG_TEST_SLOW): it holds a
       chosen step, or a conversation agent's call, back for so many seconds before it goes out,
       inside the call's own timeout, where a check needs a loop still thinking, a step past 45 s
       or a player's turn busy.

It starts from a clean slate: the stand-in's vfg.db (its -wal and -shm with it) and feed.log are
set aside in a timestamped folder beside them, never deleted, and made again by
`vfg_bridge --init-db`; what made/<name>/ held is saved with `vfg made save`, then moved into
made/.replaced/, so the server's tasks and what they made start from none.

What it checks. Every line to the AI takes its player's turn for two quick calls: the conversation
agent answers (brain.log's `respond #<seq>` line), and a hand-off goes to the router, which places
the work (`router #<seq> -> …`); the turn's `front #<seq>` line says both and the lines said. A
brain loop runs behind the turn (`loop <ref> …: …; L3 saved, 1 of 10 steps, lines #5`). Three brains,
one after another, on the one server and feed:
1. On the defaults (the router without reasoning: the hold). Kept from Prototype_Brain_Loop, each
   now through the front turn: a one-step ask whose actions all land (the agent's held
   acknowledgement, then the loop's reply and its one record); actions together and in turn; a
   refused command the next step fixes; another player's items read; item asks by slot; the brain's
   own copy started, edited and stopped; three of Steve's lines waiting for his turn and a fourth
   turned away by a private message naming them; a line the agent answers itself. New:
   - Steve's second line waits only for his first line's front turn, not its loop: his two loops
     run side by side, and Alex's line is answered beside them;
   - a step held past 45 s (a slow stand-in: 65 s at low, 47 s with no reasoning) lands inside its
     timeout (120 s at low, the default; 60 s with none), and while
     that loop thinks, Steve's "is it done yet?" is answered at once, from what is running;
   - the router adds a line to a thinking loop (its step held), which reads it in an extra step,
     and a line to the same loop once it has ended, inside its 2 minutes: taken up again, its next
     step read from the cache; after the 2 minutes, a line on that thing starts a new loop;
   - the router starts an implementer with its brief (the stand-in records it), follows it up while
     it runs, and answers the same ask said again with a line of its own in place of the agent's
     (nowhere, one line); the implementer's report reaches the conversation agent while Steve's
     turn is busy (his agent's call held), and is told at once;
   - one line per ask, all through this brain: a hand-off placed somewhere says the agent's line,
     held until the router answered; one placed nowhere says the router's in its place.
2. VFG_ROUTER_REASONING=low: nothing is held: the agent's line goes out before the router answers,
   and the router's own after it.
3. VFG_LOOP_STEPS=1, VFG_LOOP_CAP=2: a line's loop gets one step and each line added one more, never
   past two: the line added to an ended loop takes it to the cap, its last step replies or hands off
   and it is deleted; the next line on that thing starts a new loop.

At the end: every line read, no task open, a clean stop, no traceback or error in brain.log, the key
in no file, every step a usage row `<ref> s<n>` and a line of brain.log, every front turn's calls
`#<seq>` rows of respond and router, no report waiting for a turn, and the cost under $1. It prints
each line's front turn and its work, with the time to the reply, every call's cost and the cache
rate of the steps after a loop's first. `--only` runs the checks it names (the brain each needs is
started for it). The server is left down unless --keep-up. Never `down --all`, `--purge-key`,
`--regen`, `key` or `carpet`: other builders' servers share REALMC_ROOT, and its env file holds a
real TypeSafe key, which nothing here reads or prints. Give a build's run a server of its own
(`--name cr-live`): the clean slate sets aside whatever the server held before.
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
SHIM = os.path.join(HERE, "shims", "slow_step")
KEY = "OPEN_ROUTER_API"
BUDGET = 1.00      # dollars of OpenRouter the brain's three agents may spend in a run
MODELS = ("brain", "respond", "router")   # the usage components on OpenRouter
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")   # a task's name
HOME = (12, 12)    # where the saved place `home` is, on the arena (x, z)
TAKE_UP_S = 120    # VFG_LOOP_TAKE_UP_S's default: an ended loop takes a line this long after its creation
# A hand-off's brief, as code renders it from its parts (brain/call.py HandOff.brief).
BRIEF_HEADS = ("THE PLAYER'S WORDS", "THE WHOLE PICTURE", "WHAT THE PLAYER TAKES FOR GRANTED",
               "HOW IT IS CAST, AND FOR WHOM", "DONE WHEN")
CHAT = ("haha this is fun", "you're the best AI ever", "lol Alex just fell in the lava again")
# A line that claims the thing is done: "is it done yet?" must never be answered so while its loop thinks;
# a line that hedges ("not done yet", "still on it", "almost ready") claims nothing.
DONE = re.compile(r"\b(all set|done|here you go|there you go|ready|you've got|you now have|enjoy)\b", re.I)
HEDGE = re.compile(r"\b(not|yet|still|almost|nearly|soon|working|coming|being|on it|hang tight|moment|sec)\b|n't\b",
                   re.I)


def claims_done(text):
    return bool(DONE.search(text or "")) and not HEDGE.search(text or "")

# The slow stand-in's rules for the default brain (integration/shims/slow_step): the loop that the
# "is it done yet?" line finds thinking, held past 45 s; the loop whose step is in flight when a line
# is added to it; Steve's first loop, still thinking when his second line's loop starts; and Steve's
# agent's call while an implementer's report comes in.
SLOW = {"defaults": "sixteen golden carrots|1|{hold};stack of oak logs|1|15;give me 16 bread|1|12;"
                    "joke about creepers|0|17|Response"}


def slow_hold(run):
    """How long the carrots' step is held: past 45 s, and past 60 s when the step's timeout is 120 s
    (low, the default), so the check shows which timeout applies; inside it either way."""
    return 65 if run.step_timeout >= 120 else 47
CARROTS, OAK, BREAD, JOKE = "give me sixteen golden carrots", "give me a stack of oak logs", "give me 16 bread", \
    "tell me a joke about creepers"
BOOMERANG = ("make me a boomerang axe: when I throw my axe it flies ten blocks out, hits whatever is there, and comes "
             "back to my hand")


def load_e2e():
    """brain_e2e.py beside this file: the key's finder and brain.log's grammar."""
    spec = importlib.util.spec_from_file_location("brain_e2e", os.path.join(HERE, "brain_e2e.py"))
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    return e2e


def load_stand_in():
    spec = importlib.util.spec_from_file_location("stand_in_model", os.path.join(HERE, "stand_in_model.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


E2E = load_e2e()
LOG_CALL, LOG_LOOP, LOG_STEP, ANY = E2E.LOG_CALL, E2E.LOG_LOOP, E2E.LOG_STEP, E2E.ANY
# A step or a call that failed for good, with what it spent: its usage row is written all the same (ok 0).
LOG_FAILED = re.compile(r"^(\S+) (brain|respond|router) (#\d+|[a-z][a-z0-9_]{1,55})(?: s(\d+))? FAILED "
                        r"\((\d+) ms \$([\d.]+)")
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
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", KEY, "DEEPSEEK_"))}
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
        """`vfg brain start`, the key and `extra` settings in its environment only."""
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
    def __init__(self, live, rep, model=None):
        self.live, self.rep = live, rep
        self.model = model  # the implementers' stand-in model
        self.asks = []
        self.names = []    # the tasks the run opened, by name
        self.claimed = set()   # the seqs of the AI say rows a line's reply took
        self.phase = "defaults"
        self.reasoning = ""    # --brain-reasoning: VFG_BRAIN_REASONING for every brain ("" its default, low)
        self.step_timeout = 120  # a brain step's timeout, as config.py derives it: 120 s thinking, 60 s off

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
        """One line into the feed; waits for its reply (its work's, else its front turn's)."""
        a, = self.batch((who, cls, text))
        self.await_reply(a, timeout)
        return a

    def log(self):
        return self.live.log_since(self.live.mark)

    def await_reply(self, a, timeout=60):
        """The ask's reply: the AI say row after its line with the words brain.log gives it (the
        reply of the loop that took it, else the front turn's own line; E2E.logged_reply), so turns
        at once are told apart; `AI say <name>` when it names a task (kept as the ask's task), else
        `AI say -`. None when nothing was said (a["silent"]) or nothing came in time."""
        live, ref = self.live, "#%d" % a["seq"]

        def got():
            log = self.log()
            want = E2E.logged_reply(log, ref)
            if want == "":
                return "silent"
            ack = E2E.acked(log, ref)   # the front turn's own line, never a loop's reply
            for r in live.ai(a["seq"]) if want is not None else ():
                if r["seq"] not in self.claimed and (E2E.same_words(r["text"], want) if want is not ANY
                                                     else not (ack and E2E.same_words(r["text"], ack))):
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

    def await_front(self, a, timeout=40):
        """The line's front turn, once logged (E2E.front_of): {agent, to, where, said, reply, …}."""
        return wait_for(lambda: E2E.front_of(self.log(), "#%d" % a["seq"]), timeout) or None

    def front(self, a):
        return E2E.front_of(self.log(), "#%d" % a["seq"])

    def work(self, a, timeout=0):
        """The run of the loop that took the line (E2E.work_of): {ref, loop, steps, ended, state,
        used, limit, lines, …}; None when no loop took it or its run has not ended."""
        def find():
            return E2E.work_of(self.log(), "#%d" % a["seq"])
        return wait_for(find, timeout) if timeout else find()

    def ack_lines(self, a):
        """The chat lines the line's front turn said (0 or 1 under the hold)."""
        return len((self.front(a) or {}).get("said") or [])

    def front_said_rows(self, a):
        """The AI say row the front turn said first: the agent's, by its logged words; else the
        router's or code's own, the line's reply when no loop took it, or the first after the line."""
        f = self.front(a) or {}
        if not f.get("said"):
            return []
        rows = self.live.ai(a["seq"])
        if "agent" in f["said"] and f.get("reply"):
            # The front line is logged as soon as its say returns: of the rows with the agent's words (another
            # line's "On it!" may have the same), the last said by then is this turn's.
            c = self.call_span("#%d" % a["seq"], "respond")
            same = [r for r in rows if E2E.same_words(r["text"], f["reply"]) and parse_iso(r["at"]) <= f["at"] + 0.3
                    and (c is None or parse_iso(r["at"]) >= c[1] - 0.05)]
            return same[-1:]
        if f.get("to") not in ("new_loop", "loop") and a.get("reply_seq"):
            return [r for r in rows if r["seq"] == a["reply_seq"]]
        return rows[:1]

    # -- what brain.log says
    def lines_of(self, ref):
        """brain.log's lines for `ref` this run: its steps, actions, loop, calls and turn."""
        pat = re.compile(r"^\S+ (brain|loop|action|respond|router|front|turn|exec|task) (report )?%s[ :,]"
                         % re.escape(ref))
        return [x for x in self.log().splitlines() if pat.match(x)]

    def evidence(self, ref, n=2500):
        return short(" ¶ ".join(self.lines_of(ref)), n)

    def evidence_of(self, a, n=2500):
        """The line's front turn's lines, and its work's (the loop's ref, when another line's loop took it)."""
        refs = ["#%d" % a["seq"]]
        w = self.work(a)
        if w and w["ref"] not in refs:
            refs.append(w["ref"])
        return short(" ¶ ".join(x for r in refs for x in self.lines_of(r)), n)

    def steps(self, ref, after=None, until=None):
        """The steps of the loop whose ref is `ref` (its first line's #seq, or a report's task),
        logged after `after` and up to `until`: [{n, at, start, ms, cost, did, came, failed}]; a step
        whose call failed for good is one too (`did` "FAILED", `failed` True)."""
        out = []
        for line in self.log().splitlines():
            m, failed = LOG_STEP.match(line), False
            if not m:
                m, failed = LOG_FAILED.match(line), True
                if not m or m.group(2) != "brain":
                    continue
            if m.group(3 if failed else 2) != ref:
                continue
            at = parse_iso(m.group(1))
            ms = int(m.group(5 if failed else 4))
            if (after is not None and at < after) or (until is not None and at > until + 0.001):
                continue
            out.append({"n": int(m.group(4 if failed else 3)), "at": at, "start": at - ms / 1000.0, "ms": ms,
                        "cost": float(m.group(6 if failed else 5)), "did": "FAILED" if failed else m.group(6),
                        "came": "" if failed else m.group(7), "failed": failed})
        return out

    def actions_of(self, ref, after=None, until=None):
        """The actions the steps of `ref` ran: [{step, i, name, kind, said, at}]."""
        out = []
        for line in self.log().splitlines():
            m = LOG_ACTION.match(line)
            if m and m.group(2) == ref:
                at = parse_iso(m.group(1))
                if (after is not None and at < after) or (until is not None and at > until + 0.001):
                    continue
                out.append({"at": at, "step": int(m.group(3)), "i": int(m.group(4)),
                            "name": m.group(5), "kind": m.group(6), "said": m.group(7)})
        return out

    def work_steps(self, a):
        """The steps the line's work took: its loop's, from its front turn to the run's end."""
        w, f = self.work(a), self.front(a)
        return self.steps(w["ref"], after=f["at"] - 0.001, until=w["at"]) if w and f else []

    def work_actions(self, a):
        w, f = self.work(a), self.front(a)
        return self.actions_of(w["ref"], after=f["at"] - 0.001, until=w["at"]) if w and f else []

    def turn_log(self, pattern):
        """The time of the first `turn` line matching `pattern` this run, or None."""
        for line in self.log().splitlines():
            if re.match(r"^\S+ turn " + pattern, line):
                return parse_iso(line.split(" ", 1)[0])
        return None

    def calls(self, ref):
        """The model calls for `ref` this run, the agent's and the router's (`#seq`) and each step's
        (`brain s2`): [(area, ms, cost, ended at)]. brain.log outlives a run, and the next run's
        refs (#2, #5, …) are the same."""
        out = []
        for line in self.log().splitlines():
            m = LOG_CALL.match(line)
            if m and m.group(3) == ref:
                area = m.group(2) + (" s%s" % m.group(4) if m.group(4) else "")
                out.append((area, int(m.group(5)), float(m.group(6)), parse_iso(m.group(1))))
        return out

    def call_span(self, ref, area):
        """(start, end) of `ref`'s first call of `area` (respond, router, "brain s1"), or None."""
        for got, ms, _, end in self.calls(ref):
            if got == area:
                return end - ms / 1000.0, end
        return None

    def records(self, a, timeout=5):
        """The AI action rows of the line's loop run (its one record): (fields, text). A record is
        posted after the step that ended the run, nobody waiting, so a row from before that step's
        line is an earlier run's; and one with the words the step logged as its record."""
        steps = self.work_steps(a)
        if not steps:
            return []
        logged = re.search(r" \|\| record: (.*?)$", steps[-1]["did"])

        def rows():
            return [(r["fields"], r["text"]) for r in self.live.ai(a["seq"], ("action",))
                    if parse_iso(r["at"]) >= steps[-1]["at"] - 0.5
                    and (not logged or E2E.same_words(r["text"], logged.group(1)))]
        return wait_for(rows, timeout) or []

    def table(self):
        print("\nLATENCY AND COST: a line fed -> its reply said (the feed rows' times); its front turn and work "
              "from brain.log")
        print("  %-5s %-6s %-9s %7s  %-40s %-40s %s" % ("line", "who", "phase", "reply", "front: agent; router",
                                                       "work", "calls"))
        for a in self.asks:
            took = "%.1f s" % (a["reply_at"] - a["at"]) if a.get("reply_at") else ("silent" if a.get("silent") else "-")
            f = self.front(a)
            front = ("%s %d ms; %s%s; said %s" % (f["agent"], f["agent_ms"], f["to"] or "-",
                                                  " " + f["where"] if f["where"] and f["where"] != "-" else "",
                                                  ", ".join(f["said"]) or "nothing") if f else "-")
            w = self.work(a)
            work = ("%s %d step(s) $%.4f %s, %s, %s %d/%d" % (w["loop"], w["steps"], w["cost"], w["cached"], w["ended"],
                                                              w["state"], w["used"], w["limit"]) if w else "-")
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.calls("#%d" % a["seq"]))
            print("  #%-4d %-6s %-9s %7s  %-40s %-40s %s" % (a["seq"], a["who"], a.get("phase", ""), took,
                                                            short(front, 40), short(work, 40), calls or "-"))
            print("        %s  ->  %s" % (short(a["text"], 64), short(a.get("reply") or "(no reply)", 90)))
        for name in self.names:
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.calls(name))
            print("  %s's reports: %s" % (name, calls or "-"))


def overlap(a, b):
    """Seconds two (start, end) spans share; negative when they do not touch."""
    return min(a[1], b[1]) - max(a[0], b[0])


def front_span(run, a):
    """(start, end) of the line's front turn: its agent's call's start to its front line."""
    f, c = run.front(a), run.call_span("#%d" % a["seq"], "respond")
    return (c[0], f["at"]) if f and c else None


# ======================================================================= the brains

BRAINS = {
    "defaults": {},
    "low": {"VFG_ROUTER_REASONING": "low"},
    "budget": {"VFG_LOOP_STEPS": "1", "VFG_LOOP_CAP": "2"},
}


def brain_env(run, label):
    """The settings a brain starts with: its own (BRAINS), the brain's reasoning when --brain-reasoning
    says one, the implementers' stand-in model, and the slow stand-in's rules (SLOW), the shim on its
    PYTHONPATH."""
    extra = dict(BRAINS[label])
    if run.reasoning:
        extra["VFG_BRAIN_REASONING"] = run.reasoning
    if run.model is not None:
        extra.update(VFG_IMPL_PROVIDER="deepseek", DEEPSEEK_API_KEY="stand-in", VFG_IMPL_BASE_URL=run.model.url)
    if SLOW.get(label):
        extra["VFG_TEST_SLOW"] = SLOW[label].replace("{hold}", str(slow_hold(run)))
        extra["PYTHONPATH"] = SHIM + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    return extra


def start_brain(run, rep, label="defaults"):
    """`vfg brain start` with the brain's settings, waited for until it follows the feed and greets;
    its start line says the settings it took."""
    live = run.live
    run.phase = label
    extra = brain_env(run, label)
    mark = len(live.log())
    seq = live.last_seq()
    r = live.start(extra)
    up = wait_for(lambda: re.search(r"^\S+ start pid \d+;(.*)$", live.log_since(mark), re.M), 180)
    want = {"defaults": ["router deepseek/deepseek-v4.1-flash off 30 s, the hold on",
                         "brain deepseek/deepseek-v4.1-flash %s %d s a step, 10 steps a line up to 20, "
                         "taken up within 120 s" % (run.reasoning or "low", run.step_timeout)],
            "low": ["router deepseek/deepseek-v4.1-flash low 60 s, no hold"],
            "budget": ["1 steps a line up to 2"]}[label]
    rep.check(r.returncode == 0 and up and all(w in up.group(1) for w in want),
              "vfg brain start: the brain is up on realmc %s, %s: %s" % (
                  live.name, ", ".join("%s=%s" % kv for kv in BRAINS[label].items()) or "the defaults", "; ".join(want)),
              "%s %s; log %s" % (r.stdout, r.stderr, short(live.log_since(mark)[-1500:])))
    if up:
        rep.note(short(up.group(1), 400))
    refs = wait_for(lambda: re.search(r"^\S+ refs server 26\.3: (\d+) commands", live.log_since(mark), re.M), 200)
    rep.check(refs, "the command references for 26.3 are loaded (%s commands)" % (refs.group(1) if refs else "?"))
    greet = wait_for(lambda: [r for r in live.ai(seq) if r["text"].startswith("AI is on")], 30)
    rep.check(greet, "it greeted the players: tellraw on the real server, and an AI say row")
    return bool(up)


def restart(run, rep, label):
    rep.head("a new brain: %s" % (", ".join("%s=%s" % kv for kv in BRAINS[label].items()) or "the defaults"))
    r = run.live.stop()
    rep.check(r is not None and r.returncode == 0, "vfg brain stop: the last brain stopped",
              "%s %s" % (r and r.stdout, r and r.stderr))
    time.sleep(1)
    return start_brain(run, rep, label)


def setup(run):
    rep, live = run.rep, run.live
    rep.head("setup: Steve and Alex on a fresh world, their items, the place home, the brain started")
    p = live.p
    arena = p.arena()
    # No hostile mobs: a night a check sets (2b) once let zombies kill both fake players, and a dead fake player
    # leaves the server, so every line after it found nobody online.
    live.cmd("difficulty peaceful")
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


def held_ack(run, a):
    """The agent's acknowledgement to a hand-off the router placed somewhere, under the hold: one
    front line, the agent's, said after the router answered. (ok, detail)."""
    f = run.front(a) or {}
    rows = run.front_said_rows(a)
    rc = run.call_span("#%d" % a["seq"], "router")
    if f.get("to") in (None, "nowhere"):
        return False, "not a hand-off placed somewhere: %r" % f
    if not f.get("said"):
        return True, "the agent wrote no acknowledgement: nothing said by the front turn"
    held = rows and rc and parse_iso(rows[0]["at"]) >= rc[1] - 0.3
    return (f["said"] == ["agent"] and held,
            "said %s; its row %s; the router answered at %s" % (
                f.get("said"), rows and rows[0]["at"],
                rc and dt.datetime.fromtimestamp(rc[1], dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3]))


# ------------------------------------------------------------- 1. on the defaults

def one_step(run):
    rep, live = run.rep, run.live
    rep.head("1. A one-step ask: the agent hands it off, the router starts a loop, its actions land, the reply goes "
             "out after the agent's held line")
    before = live.count_items("Steve", "cooked_beef")
    a = run.ask("Steve", "give me 64 steak")
    ref = "#%d" % a["seq"]
    got = live.count_items("Steve", "cooked_beef") - before
    rep.check(got == 64, "in the world: Steve has 64 more steak (%d)" % got, run.evidence_of(a))
    f = run.front(a) or {}
    rep.check(f.get("agent") == "handoff" and f.get("to") == "new_loop",
              "its front turn: the agent handed it off (%r), the router started a new loop %s"
              % (f.get("reply"), f.get("where")), run.evidence(ref))
    ok, why = held_ack(run, a)
    rep.check(ok, "one line from the front turn, the agent's, held until the router answered: %s" % why, why)
    w = run.work(a, 10)
    rep.check(w and w["steps"] == 1 and w["ended"] == "reply" and a["reply"] and w["state"] == "saved",
              "its loop: one step, ended reply, saved (%s, %s of %s steps): %r" % (
                  w and w["loop"], w and w["used"], w and w["limit"], a["reply"]), run.evidence_of(a))
    time.sleep(2)
    said = live.ai(a["seq"])
    rep.check(len(said) == 1 + run.ack_lines(a) and all(r["fields"] == "say -" for r in said),
              "its chat lines, each AI say -: the front turn's %d and the loop's reply" % run.ack_lines(a),
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
    """Two parts no one action does both of: the command action's give, and summon_around's chickens."""
    rep, live = run.rep, run.live
    rep.head("2a. A step with actions together")
    live.cmd("kill @e[type=minecraft:chicken]")
    before = live.count_items("Steve", "cobblestone")
    a = run.ask("Steve", "give me 64 cobblestone and summon twenty chickens around me")
    got = live.count_items("Steve", "cobblestone") - before
    m = re.search(r"Killed (\d+)", live.cmd("kill @e[type=minecraft:chicken]"))
    chickens = int(m.group(1)) if m else 0
    rep.check(got == 64 and chickens >= 20, "in the world: 64 cobblestone to Steve and %d chickens round him (now "
              "killed again)" % chickens, run.evidence_of(a))
    steps, acts = run.work_steps(a), run.work_actions(a)
    s1 = [x for x in acts if steps and x["step"] == steps[0]["n"]]
    rep.check(steps and steps[0]["did"].startswith("together: ") and len(s1) >= 2
              and all(x["kind"] == "ok" for x in s1),
              "its first step ran %d actions together, every one landed: %s" % (
                  len(s1), ", ".join("#%d %s %s" % (x["i"], x["name"], x["kind"]) for x in s1)), run.evidence_of(a))
    w = run.work(a, 10)
    rep.check(w and w["steps"] == 1 and w["ended"] in ("reply", "silent"),
              "one step, ended %s: %r" % (w and w["ended"], a["reply"]), run.evidence_of(a))


def in_turn(run):
    rep, live = run.rep, run.live
    rep.head("2b. A step with actions in turn: home, then day")
    p, arena = live.p, live.p.arena()
    p.tp("Steve", arena["x"] - 11.5, arena["y"], arena["z"] - 11.5)
    live.cmd("time set midnight")
    rep.note("Steve at the arena's far corner, the time midnight (day at %s ticks)" % live.day_ticks())
    a = run.ask("Steve", "take me home, then make it day")
    w = run.work(a, 20)
    steps, acts = run.work_steps(a), run.work_actions(a)
    s1 = sorted((x for x in acts if steps and x["step"] == steps[0]["n"]), key=lambda x: x["i"])
    rep.check(steps and steps[0]["did"].startswith("in_turn: ") and len(s1) == 2
              and [x["name"] for x in s1] == ["teleport", "command"] and all(x["kind"] == "ok" for x in s1)
              and s1[0]["at"] <= s1[1]["at"],
              "its first step ran teleport, then command, in turn, both landed: %s" % ", ".join(
                  "#%d %s %s" % (x["i"], x["name"], x["kind"]) for x in s1), run.evidence_of(a))
    pos = p.pos("Steve") or (999, 0, 999)
    far = ((pos[0] - HOME[0]) ** 2 + (pos[2] - HOME[1]) ** 2) ** 0.5
    day = live.day_ticks()
    rep.check(far <= 12 and day is not None and day < 12000,
              "in the world: Steve %.0f blocks from home, the day at %s ticks" % (far, day), run.evidence_of(a))
    rep.check(w and w["steps"] == 1 and w["ended"] in ("reply", "silent"),
              "one step, ended %s%s" % (w and w["ended"], ": %r" % a["reply"] if a["reply"] else ""),
              run.evidence_of(a))
    live.cmd("time set noon")
    p.tp("Steve", arena["x"] + 0.5, arena["y"], arena["z"] + 0.5)


# Asks whose first command 26.3 may refuse, each with its check in the world: /enchant over
# Fire Aspect's maximum; the `generic.` attribute names 26.3 dropped (`minecraft:scale`); and the
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
    no, acts, w, a, text, done = [], [], None, None, "", False
    for text, landed in REFUSABLE:
        a = run.ask("Steve", text, timeout=90)
        w = run.work(a, 20)
        acts = run.work_actions(a)
        no = [x for x in acts if x["kind"] == "refused"]
        done = bool(landed(live))   # before the scale is reset below
        if no:
            break
        rep.note("nothing refused: %d step(s), %s; in the world %s" % (
            w["steps"] if w else 0, ", ".join("%s (%s)" % (x["name"], short(x["said"], 70)) for x in acts)
            or "no action", "as asked" if done else "NOT as asked"))
    live.cmd("attribute Steve minecraft:scale base reset")
    if not no:   # the brain wrote each command right the first time: the path cannot be made to run
        rep.note("NOT EXERCISED: none of the %d asks had a command refused, so no later step had one to fix"
                 % len(REFUSABLE))
        return
    first = run.work_steps(a)[:1]
    rep.check(first and no[0]["step"] == first[0]["n"], "%r: its first step's command was refused by "
              "Minecraft: %s" % (text, short(no[0]["said"], 160)), run.evidence_of(a))
    fixed = [x for x in acts if x["step"] > no[0]["step"] and x["kind"] == "ok"]
    rep.check(w and w["steps"] >= 2 and fixed and w["ended"] == "reply" and done,
              "a later step fixed it, and it is so in the world: %s; %d step(s), ended %s: %r" % (
                  fixed and short(fixed[0]["said"], 120), w and w["steps"], w and w["ended"], a["reply"]),
              run.evidence_of(a))
    recs = run.records(a)
    rep.check(len(recs) == 1, "the loop's one record: %r" % (recs and recs[0][1]), "%r" % recs)


def read_items(run):
    rep = run.rep
    rep.head("4. Another player's items, read with the tool")
    a = run.ask("Steve", "what is Alex holding right now?")
    w = run.work(a, 10)
    steps = run.work_steps(a)
    read = [s for s in steps if re.search(r"(^| \| )read Alex\b", s["did"])]
    rep.check(read and "Alex" in read[0]["came"] and "refused" not in read[0]["came"],
              "step %s read Alex's items: %s" % (read and read[0]["n"], read and short(read[0]["came"], 100)),
              run.evidence_of(a))
    rep.check(w and w["steps"] >= 2 and a["reply"] and re.search(r"trident", a["reply"], re.I),
              "the next step's reply names his trident: %r" % a["reply"], run.evidence_of(a))


def item_asks(run):
    rep, live = run.rep, run.live
    rep.head("5. Item asks by slot: boots worn and boots carried; a helmet he does not have")
    a = run.ask("Steve", "put depth strider three on my boots")
    worn, carried = live.data("Steve", "equipment.feet") or "", live.data("Steve", "Inventory[{Slot:14b}]") or ""
    rep.check('"minecraft:depth_strider": 3' in worn and "enchantments" not in carried,
              "Depth Strider III on the diamond boots he wears; the iron boots he carries untouched",
              "worn %s; carried %s; %s" % (worn, carried, run.evidence_of(a)))
    rep.check(a["reply"], "a reply: %r" % a["reply"])
    a = run.ask("Steve", "put protection four on my helmet")
    acts = run.work_actions(a)
    rep.check(live.data("Steve", "equipment.head") is None and not [x for x in acts if x["kind"] == "ok"],
              "no helmet: nothing changed (%s)" % (", ".join("%s %s" % (x["name"], x["kind"]) for x in acts)
                                                  or "no action run"), run.evidence_of(a))
    rep.check(a["reply"] and re.search(r"helmet|head", a["reply"], re.I), "the reply says so: %r" % a["reply"])


def own_copy(run):
    rep, live = run.rep, run.live
    rep.head("6. The brain's own copy of keep_effects: started, edited and stopped by a loop's tools")
    name = "keep_effects_steve"
    a = run.ask("Steve", "keep speed two on me the whole time, even after I die, until I say stop")
    copies = {k: v for k, v in live.copies().items() if v.get("action") == "keep_effects"}
    lvl = live.effect_level("Steve", "speed")
    rep.check(list(copies) == [name] and not copies[name].get("task") and lvl == 1,
              "the start gives the copy %s, with no task, and Steve has Speed II" % name,
              "copies %r; speed amplifier %r; %s" % (copies, lvl, run.evidence_of(a)))
    b = run.ask("Steve", "make it speed 5")
    f = run.front(b) or {}
    rep.note("the router put it: %s %s" % (f.get("to"), f.get("where")))
    lvl = wait_for(lambda: live.effect_level("Steve", "speed") == 4, 10) and 4 or live.effect_level("Steve", "speed")
    edits = [x for x in run.work_actions(b) if x["name"] == "edit"]
    rep.check(lvl == 4 and edits and edits[0]["kind"] == "ok" and name in live.copies(),
              "the edit action landed: Speed V, %s still running" % name,
              "speed amplifier %r; %s" % (lvl, run.evidence_of(b)))
    recs = run.records(b)
    rep.check(len(recs) == 1 and not live.ai(b["seq"], ("note",)),
              "one record for it (%r), no AI note row" % (recs and recs[0][1]),
              "%r %r" % (recs, live.ai(b["seq"], ("note",))))
    c = run.ask("Steve", "stop my effects")
    gone = wait_for(lambda: name not in live.copies(), 10)
    stops = [x for x in run.work_actions(c) if x["name"] == "stop"]
    rep.check(gone and stops and stops[0]["kind"] == "ok", "the stop action landed: %s is gone" % name,
              "copies %r; %s" % (live.copies(), run.evidence_of(c)))


def side_by_side(run):
    """Steve's first loop is held 12 s (the slow stand-in) so it is still thinking when his second
    line's loop starts; Alex's line comes in the same write."""
    rep, live = run.rep, run.live
    rep.head("7. Steve's second line waits only for his first line's front turn: his two loops run side by side, "
             "and Alex's line is answered beside them")
    time.sleep(4)  # Steve's last turn waits up to VFG_TURN_SETTLE_S for its rows to come back: his lane is free
    bread, shield = live.count_items("Steve", "bread"), live.count_items("Alex", "shield")
    live.cmd("kill @e[type=minecraft:chicken]")
    s1, s2, x = run.batch(("Steve", "action_oriented", BREAD),
                          ("Steve", "action_oriented", "summon three chickens next to me"),
                          ("Alex", "action_oriented", "can I get a shield"))
    for a in (s1, s2, x):
        run.await_reply(a, 90)
    m = re.search(r"Killed (\d+)", live.cmd("kill @e[type=minecraft:chicken]"))
    chickens = int(m.group(1)) if m else 0
    rep.check(live.count_items("Steve", "bread") - bread == 16 and chickens >= 3
              and live.count_items("Alex", "shield") - shield >= 1,
              "in the world: 16 bread to Steve, %d chickens by him (killed again), a shield to Alex" % chickens,
              " ¶ ".join(run.evidence_of(a) for a in (s1, s2, x)))
    r2 = "#%d" % s2["seq"]
    waited = run.turn_log(r"%s Steve waits for their turn" % re.escape(r2))
    f1, f2 = run.front(s1) or {}, run.front(s2) or {}
    w1, w2 = run.work(s1), run.work(s2)
    c2 = run.call_span(r2, "respond")
    rep.check(waited and f1 and c2 and w1 and c2[0] >= f1["at"] - 0.3 and c2[0] < w1["at"],
              "brain.log: %s Steve waits for their turn; his second line's agent began %.1f s after his first line's "
              "front turn ended, %.1f s before its loop ended" % (
                  r2, (c2[0] - f1["at"]) if c2 and f1 else -1, (w1["at"] - c2[0]) if c2 and w1 else -1),
              "%s ¶ %s" % (run.evidence_of(s1), run.evidence_of(s2)))
    st1 = run.work_steps(s1)
    st2 = run.work_steps(s2)
    span1 = (st1[0]["start"], st1[-1]["at"]) if st1 else None
    span2 = (st2[0]["start"], st2[-1]["at"]) if st2 else None
    rep.check(f1.get("to") == "new_loop" and f2.get("to") == "new_loop" and w1 and w2 and w1["loop"] != w2["loop"]
              and span1 and span2 and overlap(span1, span2) > 0,
              "two loops of Steve's side by side (%s and %s): their steps overlapped %.1f s"
              % (w1 and w1["loop"], w2 and w2["loop"], overlap(span1, span2) if span1 and span2 else -1),
              "%s ¶ %s" % (run.evidence_of(s1), run.evidence_of(s2)))
    cx = run.call_span("#%d" % x["seq"], "respond")
    rep.check(cx and w1 and cx[0] < w1["at"],
              "Alex's line was answered beside them: its agent's call began %.1f s before Steve's first loop ended"
              % ((w1["at"] - cx[0]) if cx and w1 else -1), run.evidence_of(x))


def waiting(run):
    rep, live = run.rep, run.live
    rep.head("8. Three of Steve's lines wait for his turn, and a fourth is turned away by a private message naming them")
    time.sleep(4)
    first, *chat, fourth = run.batch(("Steve", "action_oriented", "give me a stack of torches"),
                                     *(("Steve", "conversational", t) for t in CHAT),
                                     ("Steve", "conversational", "ok and one more thing"))
    gone = "#%d" % fourth["seq"]
    away = wait_for(lambda: run.turn_log(r"%s Steve turned away, 3 waiting" % re.escape(gone)), 30)
    rep.check(away, "brain.log: %s Steve turned away, 3 waiting" % gone, short(run.log()[-2000:]))
    private = wait_for(lambda: [r for r in live.ai(first["seq"]) if r["text"].startswith("(to Steve) Still on:")], 30)
    rep.check(private and private[0]["fields"] == "say -" and all(t in private[0]["text"] for t in CHAT)
              and "can't take any more" in private[0]["text"] and fourth["text"] not in private[0]["text"],
              "the private message to Steve alone, naming the three waiting lines: %r"
              % (private and private[0]["text"]), "%r" % private)
    if private:
        run.claimed.add(private[0]["seq"])
    for a in [first] + chat:
        run.await_reply(a, 90)
    time.sleep(3)
    spans = [front_span(run, a) for a in [first] + chat]
    ordered = all(spans) and all(spans[i + 1][0] >= spans[i][1] - 0.2 for i in range(len(spans) - 1))
    rep.check(ordered, "the three waiting lines took Steve's turn one after another, after his first: %s" % ", ".join(
        "#%d at +%.1f s" % (a["seq"], s[0] - first["at"]) for a, s in zip([first] + chat, spans) if s),
        " ¶ ".join(run.evidence_of(a) for a in [first] + chat))
    longest = max((s[1] - s[0] for s in spans if s), default=None)
    rep.note("a front turn here took at most %s" % ("%.1f s" % longest if longest is not None else "?"))
    row = [r for r in live.rows(fourth["seq"] - 1, "LINE") if r["seq"] == fourth["seq"]]
    use = live.sql("SELECT ref FROM usage WHERE ref = ? OR ref LIKE ?", (gone, gone + " s%"))
    rep.check(row and row[0]["processed_at"] and not use
              and not [x for x in run.lines_of(gone) if not re.match(r"^\S+ turn ", x)],
              "the turned-away line is marked read and never answered: no model call for it",
              "row %r; usage %r; %s" % (row, use, run.evidence(gone)))


def talk(run):
    rep, live = run.rep, run.live
    rep.head("9. A line the agent answers itself, with a reply or silence: no router, no loop")
    a = run.ask("Alex", "haha thanks AI, you're a legend", cls="conversational", timeout=30)

    def use():  # the usage row goes out beside the reply, nobody waiting: it may come a moment after
        return sorted(u["component"] for u in live.sql("SELECT component FROM usage WHERE ref = ? OR ref LIKE ?",
                                                       ("#%d" % a["seq"], "#%d s%%" % a["seq"])))
    wait_for(use, 15)
    time.sleep(2)
    got, f = use(), run.front(a) or {}
    rep.check(got == ["respond"] and (f.get("agent") == "reply" and f.get("said") == ["agent"] and a["reply"]
                                      or f.get("agent") == "silent" and not f.get("said") and a.get("silent")),
              "the agent answered it itself (%s%s), and nothing else was called" % (
                  f.get("agent"), ": %r" % a["reply"] if a["reply"] else ", saying nothing"), "%r; %r" % (got, f))


def slow_step(run):
    """A step held past 45 s by the slow stand-in (slow_hold): it lands inside its timeout. While it thinks,
    Steve asks whether it is done: the agent answers at once, from WORK UNDER WAY."""
    rep, live = run.rep, run.live
    rep.head("10. A step past 45 s lands inside its timeout; meanwhile \"is it done yet?\" is answered at once, "
             "from what is running")
    time.sleep(4)
    before = live.count_items("Steve", "golden_carrot")
    a, = run.batch(("Steve", "action_oriented", CARROTS))
    hold = slow_hold(run)
    held = wait_for(lambda: re.search(r"^(\S+) test slow call: held Step \"sixteen golden carrots\" s1 %d s$" % hold,
                                      run.log(), re.M), 40)
    rep.check(held, "the slow stand-in holds its loop's first step %d s" % hold, short(run.log()[-1500:]))
    time.sleep(5)
    b = run.ask("Steve", "is it done yet?", cls="conversational", timeout=40)
    fb, wa = run.front(b) or {}, run.work(a)
    rep.check(b.get("reply") and wa is None and fb.get("to") in (None, "nowhere") and not claims_done(b["reply"]),
              "answered at once (%.1f s) while the loop thinks, with no new work and never as done: %r (agent %s%s)"
              % ((b["reply_at"] - b["at"]) if b.get("reply_at") else -1, b.get("reply"), fb.get("agent"),
                 ", router " + fb["to"] if fb.get("to") else ""), "%r; %s" % (fb, run.evidence_of(b)))
    run.await_reply(a, run.step_timeout + 30)
    w = run.work(a, 10)
    st = run.work_steps(a)
    ms = st[0]["ms"] if st else 0
    got = live.count_items("Steve", "golden_carrot") - before
    timed_out = re.search(r"^\S+ brain #%d s1 FAILED \([^)]*\): no answer from .* within" % a["seq"], run.log(), re.M)
    past = 60 if hold > 60 else 45   # the old timeout, and the no-reasoning one, it outlasted
    rep.check(st and past * 1000 < ms < run.step_timeout * 1000 and not timed_out,
              "its first step's call took %.1f s, past %d s, and came back inside its %d s timeout, not cut at %d s"
              % (ms / 1000.0, past, run.step_timeout, past), run.evidence_of(a))
    rep.check(st and not st[0].get("failed") and w and w["ended"] == "reply" and got == 16,
              "and its answer landed: %d golden carrots, ended %s" % (got, w and w["ended"]), run.evidence_of(a))


def added_lines(run):
    """A line added to a thinking loop (its step held 15 s), read in an extra step; one added to the
    same loop once it ended, inside its 2 minutes: taken up, from the cache; and a line on that
    thing after the 2 minutes: a new loop."""
    rep, live = run.rep, run.live
    rep.head("11. The router adds a line to a thinking loop (an extra step) and to an ended one inside its 2 minutes "
             "(taken up, from the cache); after them, a new loop")
    time.sleep(4)
    counts = {k: live.count_items("Steve", k) for k in ("oak_log", "birch_log", "spruce_log")}
    a, = run.batch(("Steve", "action_oriented", OAK))
    fa = run.await_front(a)
    if not rep.check(fa and fa.get("to") == "new_loop", "its front turn started a loop: %s" % (fa and fa.get("where")),
                     run.evidence_of(a)):
        return
    loop = fa["where"]
    time.sleep(2)   # its step is held 15 s: the loop is thinking
    b = run.ask("Steve", "actually make that birch logs instead", timeout=90)
    fb = run.front(b) or {}
    w = run.work(a, 60)
    sa = run.work_steps(a)
    after_b = [s for s in sa if s["start"] >= (fb.get("at") or 1e12) - 0.5]
    rep.check(fb.get("to") == "loop" and fb.get("where") == loop and w and w["loop"] == loop
              and "#%d" % b["seq"] in w["lines"] and len(sa) >= 2 and after_b and w["limit"] == 20,
              "the router added #%d to %s while it thought; the loop took %d step(s), %d after the line came, read in "
              "an extra step; its limit now %s (10 + 10), lines %s" % (
                  b["seq"], loop, len(sa), len(after_b), w and w["limit"], w and ", ".join(w["lines"])),
              "%r; %s" % (fb, run.evidence_of(a)))
    rep.note("its steps: %s" % " | ".join("s%d %s" % (s["n"], short(s["did"], 120)) for s in sa))
    got = {k: live.count_items("Steve", k) - v for k, v in counts.items()}
    rep.note("in the world: %s" % ", ".join("%+d %s" % (n, k) for k, n in got.items()))
    rep.check(got["birch_log"] >= 64, "in the world: Steve has the birch logs (%+d)" % got["birch_log"],
              run.evidence_of(a))
    # The loop has ended and is saved: a line inside its 2 minutes takes it up again.
    created = fa["at"]   # the loop was made as its front turn placed the line, a moment before this line
    c = run.ask("Steve", "actually make those spruce logs instead", timeout=90)
    fc = run.front(c) or {}
    wc = run.work(c, 30)
    runs = E2E.loop_runs(run.log(), loop=loop)
    first_after = [s for s in run.steps(runs[-1]["ref"], after=fc.get("at", 1e12)) if runs] if runs else []
    use = live.sql("SELECT ref, input_tokens, cache_read_tokens FROM usage WHERE ref = ?",
                   ("%s s%d" % (runs[-1]["ref"], first_after[0]["n"]),)) if first_after else []
    share = (use[0]["cache_read_tokens"] or 0) / use[0]["input_tokens"] if use and use[0]["input_tokens"] else 0
    rep.check(fc.get("to") == "loop" and fc.get("where") == loop and wc and wc["loop"] == loop and len(runs) >= 2
              and first_after and share >= 0.8,
              "the router added #%d to %s, ended %.0f s after its start: taken up again, its step s%s read %.0f%% of "
              "its prompt from the cache" % (c["seq"], loop, fc.get("at", 0) - created,
                                             first_after and first_after[0]["n"], 100 * share),
              "%r; runs %r; %s" % (fc, runs, run.evidence_of(c)))
    rep.check(live.count_items("Steve", "spruce_log") - counts["spruce_log"] >= 1,
              "in the world: Steve has spruce logs", run.evidence_of(c))
    # After its 2 minutes from its start, the loop is gone: a line on that thing starts a new loop.
    left = created + TAKE_UP_S + 5 - time.time()
    if left > 0:
        rep.note("waiting %.0f s for %s's 2 minutes to pass" % (left, loop))
        time.sleep(left)
    d = run.ask("Steve", "one more stack of oak logs please", timeout=90)
    fd = run.front(d) or {}
    rep.check(fd.get("to") == "new_loop" and fd.get("where") not in (None, loop),
              "past its 2 minutes, %s is gone: the router started a new loop for #%d (%s)"
              % (loop, d["seq"], fd.get("where")), "%r; %s" % (fd, run.evidence_of(d)))


def implementer(run):
    """The router starts an implementer (the stand-in model: its first answer after 32 s, then 3 s),
    follows it up while it runs, and answers the same ask said again with its own line; the
    report reaches the conversation agent while Steve's agent's call is held (his turn busy)."""
    rep, live = run.rep, run.live
    rep.head("12. The router starts an implementer with its brief and follows it up; the same ask again gets the "
             "router's line alone; the report reaches the conversation agent while Steve's turn is busy")
    if run.model is None:
        rep.check(False, "the stand-in model runs")
        return
    mark = len(live.log())
    time.sleep(4)
    a = run.ask("Steve", BOOMERANG, timeout=120)
    f = run.front(a) or {}
    tid = f.get("where") if f.get("to") == "new_implementer" else None
    rep.check(tid and NAME_RE.match(tid) and not os.path.exists(os.path.join(REPO, "actions", tid + ".py")),
              "the router started a new implementer: the task %s (the agent said %r)" % (tid, f.get("reply")),
              "%r; %s" % (f, run.evidence_of(a)))
    if not tid:
        return
    run.names.append(tid)
    ok, why = held_ack(run, a)
    rep.check(ok, "one line from the front turn, the agent's, held until the router answered: %s" % why, why)
    opened = wait_for(lambda: [r for r in live.ai(a["seq"], ("task",), tid) if r["text"].startswith("new: ")], 15) or []
    row = live.task_row(tid) or {}
    brief = row.get("brief") or ""
    heads = [h for h in BRIEF_HEADS if h + "\n" in brief]
    done_when = brief.split("DONE WHEN\n", 1)[1].splitlines() if "DONE WHEN\n" in brief else []
    rep.check(opened and len(heads) == len(BRIEF_HEADS) and done_when and all(x.startswith("- ") for x in done_when)
              and row.get("player") == "Steve" and row.get("description"),
              "its AI task row and its brief in vfg.db: every part under its heading, %d done-when items"
              % len(done_when), "%r; %r" % (opened, row))
    rep.note("the brief: %s" % short(brief.replace("\n", " / "), 600))
    sent = wait_for(lambda: run.model.requests(tid), 60)
    t1 = sent[0]["at"] if sent else None
    rep.check(sent and "YOUR TASK: %s" % tid in sent[0]["text"] and "DONE WHEN" in sent[0]["text"],
              "the implementer started: its model (the stand-in) got the brief under YOUR TASK: %s" % tid,
              "%r" % [(r["task"], short(r["text"], 200)) for r in run.model.requests()])
    if not t1:
        return
    # A follow-up while it runs: the router sends it to the same task's implementer.
    b = run.ask("Steve", "and make the boomerang axe set mobs on fire when it hits them", timeout=60)
    fb = run.front(b) or {}
    fu = wait_for(lambda: [r for r in live.ai(b["seq"], ("task",), tid) if r["text"].startswith("follow-up: ")], 10)
    # The same ask again while it is built: nowhere, one line: the router's in place of the agent's when it wrote
    # one, else the agent's held one (router.md: it leaves its reply empty when the agent's covers it).
    c = run.ask("Steve", "hey can you make me that boomerang axe", timeout=40)
    fc = run.front(c) or {}
    time.sleep(2)
    said_c = [r for r in live.ai(c["seq"]) if parse_iso(r["at"]) <= (c.get("reply_at") or 0) + 1.0]
    rep.check(fc.get("to") == "nowhere" and fc.get("said") in (["router"], ["agent"]) and c.get("reply")
              and len(said_c) == 1,
              "the same ask again while it is built: the router sent it nowhere, one line went out, %s: %r (the "
              "agent's was %r)" % ("the router's in place of the agent's" if fc.get("said") == ["router"]
                                   else "the agent's held one, the router writing none", c.get("reply"),
                                   fc.get("reply")), "%r; %s" % (fc, run.evidence_of(c)))
    # Steve's turn, busy when the report comes (about 36 s into the run): his agent's call held 17 s (under
    # its 20 s timeout) from about 26 s in.
    wait_for(lambda: time.time() >= t1 + 25, 45, 0.5)
    d, = run.batch(("Steve", "conversational", JOKE))
    reached = wait_for(lambda: [r for r in run.model.requests(tid) if re.search(r"fire", r["text"], re.I)
                                and r["at"] > t1], 60)
    rep.check(fb.get("to") == "implementer" and fb.get("where") == tid and fu and reached,
              "the router followed %s up (%s), its AI task follow-up row, and the follow-up reached its implementer "
              "(its model's request %s)" % (tid, fb.get("to"), reached and reached[0]["n"]),
              "%r; %s" % (fb, run.evidence_of(b)))
    res = wait_for(lambda: live.ai(a["seq"], ("result",), tid), 120, 0.5)
    run.await_reply(d, 60)
    report = re.search(r"^(\S+) respond %s (\d+) ms " % re.escape(tid), live.log_since(mark), re.M)
    rend = parse_iso(report.group(1)) if report else None
    told = [r for r in live.ai(a["seq"], ("say",), tid) if rend and parse_iso(r["at"]) >= rend - 0.1]
    fd, cd = run.front(d), run.call_span("#%d" % d["seq"], "respond")
    span = (cd[0], fd["at"]) if cd and fd else None
    rstart = rend - int(report.group(2)) / 1000.0 if report else None
    rep.check(res and report and span and span[0] <= rstart and rend <= span[1]
              and told and parse_iso(told[0]["at"]) <= span[1]
              and not re.search(r"^\S+ turn report ", run.log(), re.M),
              "the report reached the conversation agent while Steve's turn was busy (his agent's call held): its call "
              "%.1f-%.1f s into the %.1f s turn, its line %r said before the turn ended" % (
                  (rstart - span[0]) if span and rstart else -1, (rend - span[0]) if span and rend else -1,
                  (span[1] - span[0]) if span else -1, told and told[0]["text"]),
              "result %r; report %r; turn %r; told %r; %s" % (res, report and report.group(0), span, told,
                                                               run.evidence(tid)))
    row = live.task_row(tid) or {}
    rep.check(row.get("status") in ("done", "failed") and res and res[0]["text"].startswith(tid),
              "its record (%r) and its row: %s" % (res and short(res[0]["text"], 120), row.get("status")), "%r" % row)


def one_line_per_ask(run):
    """Under the hold, all through the default brain: every hand-off placed somewhere said at most
    the agent's line, after the router answered; every one placed nowhere at most one line."""
    rep = run.rep
    rep.head("13. One line per ask while the router does not reason, all through the default brain")
    placed = [a for a in run.asks if a.get("phase") == "defaults" and (run.front(a) or {}).get("to")
              not in (None, "nowhere")]
    nowhere = [a for a in run.asks if a.get("phase") == "defaults" and (run.front(a) or {}).get("to") == "nowhere"]
    bad = []
    for a in placed:
        ok, why = held_ack(run, a)
        if not ok:
            bad.append("#%d %s" % (a["seq"], why))
    rep.check(placed and not bad, "%d hand-offs placed somewhere: each said the agent's line or none, held until the "
              "router answered (%d said one)" % (len(placed), sum(1 for a in placed if run.ack_lines(a))),
              "; ".join(bad))
    many = [a for a in nowhere if len((run.front(a) or {}).get("said") or []) > 1]
    rep.check(not many, "%d placed nowhere: each said one line at most (%s)" % (
        len(nowhere), ", ".join("#%d %s" % (a["seq"], "+".join((run.front(a) or {}).get("said") or ["nothing"]))
                                for a in nowhere) or "none"), "%r" % [a["seq"] for a in many])


# ------------------------------------------------------------- 2. the router at low: no hold

def no_hold(run):
    """The router at low: a new thing (the router's to start an implementer for), then the same ask
    again while it is built (nowhere, with the router's own line): the agent's line goes out before
    the router answers, every time, and the router's own after it."""
    rep = run.rep
    rep.head("14. The router at low reasoning: nothing held, the agent's line out at once, the router's own after it")
    time.sleep(4)
    a = run.ask("Steve", "make me a frost bow: its arrows freeze any mob they hit solid for five seconds", timeout=90)
    time.sleep(3)
    b = run.ask("Steve", "hey can you make me that frost bow", timeout=90)
    time.sleep(2)
    c = run.ask("Steve", "hurry up with my frost bow!", timeout=90)
    early, both = [], []
    for x in (a, b, c):
        f = run.front(x) or {}
        rows = run.front_said_rows(x)
        rc = run.call_span("#%d" % x["seq"], "router")
        if f.get("agent") != "handoff":
            rep.note("#%d: the agent answered it itself (%r): nothing for the router" % (x["seq"], f.get("reply")))
            continue
        first = rows and rc and parse_iso(rows[0]["at"]) < rc[1]
        if "agent" in f.get("said", []):
            early.append(x)
        if f.get("said") == ["agent", "router"]:
            both.append(x)
        rep.check(rc and ("agent" not in f.get("said", []) or first)
                  and f.get("said", []) in ([], ["agent"], ["router"], ["agent", "router"]),
                  "#%d: the router took %.1f s and placed it %s %s; said %s%s" % (
                      x["seq"], (rc[1] - rc[0]) if rc else -1, f.get("to"), f.get("where") or "",
                      " then ".join(f.get("said") or ["nothing"]),
                      ", the agent's line %.1f s before the router answered" % (rc[1] - parse_iso(rows[0]["at"]))
                      if "agent" in f.get("said", []) and rows and rc else ""), "%r; %s" % (f, run.evidence_of(x)))
    rep.check(early, "the agent's line went out at once, before the router answered: %s"
              % (", ".join("#%d" % x["seq"] for x in early) or "no hand-off had one"))
    if both:   # only when the router writes a line of its own: it leaves none when the agent's covers it
        rep.check(True, "both lines for one ask, the agent's then the router's own: %s"
                  % ", ".join("#%d" % x["seq"] for x in both))
    else:
        rep.note("not seen: the router wrote no line of its own for these (%s)" % "; ".join(
            "#%d %s, said %s" % (x["seq"], (run.front(x) or {}).get("to"),
                                 "+".join((run.front(x) or {}).get("said") or ["nothing"])) for x in (a, b, c)))


# ------------------------------------------------------------- 3. the budget: VFG_LOOP_STEPS=1, VFG_LOOP_CAP=2

def budget(run):
    rep = run.rep
    rep.head("15. One step a line, never past two (VFG_LOOP_STEPS=1, VFG_LOOP_CAP=2): a line added takes the loop to "
             "its cap, its last step replies or hands off, it is deleted, and the next line starts a new loop")
    a = run.ask("Steve", "give me a diamond", timeout=90)
    fa, wa = run.front(a) or {}, run.work(a, 20)
    rep.check(fa.get("to") == "new_loop" and wa and wa["used"] == 1 and wa["limit"] == 1 and wa["state"] == "saved",
              "its loop %s: 1 of 1 steps (its first is its last), ended %s, saved" % (
                  fa.get("where"), wa and wa["ended"]), "%r; %s" % (wa, run.evidence_of(a)))
    b = run.ask("Steve", "actually make that two diamonds", timeout=90)
    fb, wb = run.front(b) or {}, run.work(b, 20)
    rep.check(fb.get("to") == "loop" and fb.get("where") == fa.get("where") and wb and wb["used"] == 2
              and wb["limit"] == 2 and wb["state"] == "deleted",
              "the router added #%d to %s: one more step, 2 of 2, the cap, and the loop deleted (%s)" % (
                  b["seq"], fa.get("where"), wb and wb["state"]), "%r; %r; %s" % (fb, wb, run.evidence_of(b)))
    rep.check(wb and wb["ended"] in ("reply", "handoff"),
              "its last step, at the cap, replied or handed off: ended %s%s" % (
                  wb and wb["ended"], ": %r" % b.get("reply") if b.get("reply") else ""), run.evidence_of(b))
    c = run.ask("Steve", "make it three diamonds", timeout=90)
    fc = run.front(c) or {}
    rep.check(fc.get("to") == "new_loop" and fc.get("where") not in (None, fa.get("where")),
              "%s is deleted: the next line on that thing started a new loop (%s)" % (fa.get("where"), fc.get("where")),
              "%r; %s" % (fc, run.evidence_of(c)))


# ======================================================================= the end

def end(run):
    rep, live = run.rep, run.live
    rep.head("the end")
    unread = [r for r in live.rows(run.start_seq) if r["type"] == "LINE" and not r["processed_at"]]
    rep.check(not unread, "every line of the run was read and marked processed", "%r" % unread)
    open_ = wait_for(lambda: not live.sql("SELECT name FROM tasks WHERE status = 'open'"), 90, 1.0)
    rep.check(open_, "no task open in vfg.db (%d in its tasks table)" % len(live.sql("SELECT name FROM tasks")),
              "%r" % live.sql("SELECT name, status FROM tasks WHERE status = 'open'"))
    r = live.stop()
    log = run.log()
    rep.check(r is not None and r.returncode == 0 and re.search(r"^\S+ stop stopped$", log, re.M),
              "vfg brain stop: the brain stopped cleanly", "%s %s" % (r and r.stdout, r and r.stderr))
    rep.check("Traceback" not in log and not re.search(r"^\S+ error ", log, re.M), "no traceback or error in brain.log",
              short(log[max(0, log.find("Traceback")):][:1500]) + " ¶ " + " ¶ ".join(
                  x for x in log.splitlines() if re.match(r"^\S+ error ", x))[:1500])
    rep.check(not re.search(r"^\S+ turn report ", log, re.M), "no report waited for a turn: reports never queue")
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
    rep.check(cost < BUDGET, "the brain's agents cost $%.4f of OpenRouter, under $%.2f" % (cost, BUDGET))
    steps = [m for m in (LOG_CALL.match(x) or LOG_FAILED.match(x) for x in log.splitlines())
             if m and m.group(2) == "brain"]
    brain = [u for u in model if u["component"] == "brain"]
    rep.check(brain and len(steps) == len(brain) and all(re.match(r"^(#\d+|[a-z][a-z0-9_]*) s\d+$", u["ref"] or "")
                                                          for u in brain),
              "every step, answered or failed, is a usage row `<ref> s<n>` (%d) and a line of brain.log (%d)"
              % (len(brain), len(steps)), "%r" % sorted({u["ref"] for u in brain}))
    fronts = [u for u in model if u["component"] in ("respond", "router")]
    rep.check(fronts and all(re.match(r"^(#\d+|[a-z][a-z0-9_]*)$", u["ref"] or "") for u in fronts),
              "every agent's and router's call is a usage row of its line (`#<seq>`) or its report's task (%d)"
              % len(fronts), "%r" % sorted({(u["component"], u["ref"]) for u in fronts}))
    old = [u for u in use if u["component"] in ("orchestrator", "planner")]
    rep.check(not old, "no orchestrator or planner call", "%r" % old)
    run.table()
    print("\nCOST, from the usage rows (%.0f min of play)" % ((time.time() - run.started) / 60.0))
    for u in use:
        print("  %-12s %-20s %-30s in %6d (%3.0f%% cached) out %5d  $%.5f" % (
            u["component"], (u["ref"] or "")[:20], (u["model"] or "")[:30], u["input_tokens"] or 0,
            100.0 * (u["cache_read_tokens"] or 0) / u["input_tokens"] if u["input_tokens"] else 0,
            u["output_tokens"] or 0, u["cost_usd"] or 0))
    impl = sum(u["cost_usd"] or 0 for u in use if u["component"] == "implementer")

    def line(name, us):
        if not us:
            return
        c = sorted(u["cost_usd"] or 0 for u in us)
        ms = sorted(u["ms"] or 0 for u in us if u.get("ms"))
        print("  %s: %d calls, $%.4f, median $%.4f a call, %.0f%% of the prompt cached%s" % (
            name, len(us), sum(c), c[len(c) // 2],
            100.0 * sum(u["cache_read_tokens"] or 0 for u in us) / max(1, sum(u["input_tokens"] or 0 for u in us)),
            ", median %d ms" % ms[len(ms) // 2] if ms else ""))
    line("the brain's steps", brain)
    line("  first steps", [u for u in brain if u["ref"].endswith(" s1")])
    line("  steps after a loop's first", [u for u in brain if not u["ref"].endswith(" s1")])
    line("the conversation agent", [u for u in model if u["component"] == "respond"])
    line("the router", [u for u in model if u["component"] == "router"])
    loops = E2E.loop_runs(log)
    if loops:
        n = [x["steps"] for x in loops]
        print("  loop runs: %d, %d of one step, %d of two or more; ended %s" % (
            len(n), sum(x == 1 for x in n), sum(x > 1 for x in n),
            ", ".join("%s %d" % (e, sum(x["ended"] == e for x in loops)) for e in sorted({x["ended"] for x in loops}))))
    prompt = sum(u["input_tokens"] or 0 for u in model)
    cached = sum(u["cache_read_tokens"] or 0 for u in model)
    print("  the three agents: $%.4f, %.0f%% of %d prompt tokens read from the cache; implementers $%.4f (the "
          "stand-in's)" % (cost, 100.0 * cached / prompt if prompt else 0, prompt, impl))


STEPS = {   # the checks, in the order they run, each with the brain it needs
    "one_step": (one_step, "defaults"), "together": (together, "defaults"), "in_turn": (in_turn, "defaults"),
    "refused": (refused, "defaults"), "read": (read_items, "defaults"), "items": (item_asks, "defaults"),
    "own_copy": (own_copy, "defaults"), "side_by_side": (side_by_side, "defaults"),
    "waiting": (waiting, "defaults"), "talk": (talk, "defaults"), "slow_step": (slow_step, "defaults"),
    "added_lines": (added_lines, "defaults"), "implementer": (implementer, "defaults"),
    "one_line": (one_line_per_ask, "defaults"),
    "no_hold": (no_hold, "low"),
    "budget": (budget, "budget"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="live", help="the realmc server (default live)")
    ap.add_argument("--keep-up", action="store_true", help="leave the server running at the end")
    ap.add_argument("--only", default="", help="these checks only, comma-separated: " + ", ".join(STEPS))
    ap.add_argument("--brain-reasoning", default="", help="VFG_BRAIN_REASONING for every brain (default: its own, "
                                                          "low, 120 s a step); off makes a step's timeout 60 s")
    args = ap.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    if set(only) - set(STEPS):
        ap.error("no such check: %s" % ", ".join(sorted(set(only) - set(STEPS))))
    chosen = [s for s in STEPS if not only or s in only]
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
    live = model = None
    try:
        live = Live(args.name, key)
        # Every implementer's first answer after 32 s, the rest after 3: a run stays live long enough for a
        # follow-up and the same ask again to come in, and its report lands while a held turn is busy.
        model = load_stand_in().StandIn(record=os.path.join(live.dir, "stand-in-model.jsonl"),
                                        delay=[32, 3]).start()
        print("the implementers' stand-in model on %s" % model.url, flush=True)
        if live.clean_slate(rep):     # else nothing runs on what an earlier run left
            live.start_seq = live.last_seq()
            run = Run(live, rep, model)
            run.reasoning = args.brain_reasoning.strip()
            run.step_timeout = 60 if run.reasoning == "off" else 120
            run.start_seq = live.start_seq
            run.started = time.time()
            if setup(run):
                brain = "defaults"
                for name in chosen:
                    step, needs = STEPS[name]
                    if needs != brain:
                        brain = needs
                        if not restart(run, rep, needs):
                            break
                    gone = [who for who in ("Steve", "Alex") if who not in live.p.online()]
                    if gone:   # a fake player that died left the server: back, as he was set up
                        rep.check(False, "%s still online before %s" % (" and ".join(gone), name),
                                  "they left the server; spawned again")
                        for who in gone:
                            live.p.spawn(who)
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
        if model is not None:
            model.stop()
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
