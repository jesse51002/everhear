#!/usr/bin/env python3
"""The dispatcher end to end on this PC: the spec's smoke test with no EC2.

    uv run --project dispatcher python integration/dispatcher_e2e.py [--keep] [--model <id>]

Real:  the dispatcher (`vfg orch start` runs dispatcher/run), its model calls through
       OpenRouter (OPEN_ROUTER_API: from the environment, else the first .env above this
       repo that holds it; passed to the dispatcher's process only, never printed or
       written), the vfg toolkit (toolkit/vfg; its remote programs, remote/action.py and
       the action handlers run on Python 3.9 through the fake ssh), actions/, and
       scripts/_lib/mc.py on the box.
Stand-ins: SSH (toolkit/tests/fake_ssh, which runs the remote command here), Minecraft
       (scripts/_test/fake_places_world.py over scripts/_test/fake_rcon.py, in this process: 26.3's
       recorded replies, and a world that keeps its players, effects and places), the
       bridge's feed writer (toolkit/tests/fake_vfg_feed.py, so `vfg orch reply` works),
       the orchestrator runner (a shell loop: handed tasks stay in ~orch/inbox.jsonl), the
       command references (refs/26.3/ seeded from dispatcher/tests/fixtures/refs: the
       server's real command tree and five wiki pages; the other commands have their
       syntax alone; nothing is fetched).

Nothing touches ~/.vfg, ~/.ssh or any remote host but OpenRouter: HOME, VFG_HOME, VFG_REPO,
VFG_ORCH_DIR and VFG_DISPATCH_DIR point into a temp folder. Every ask goes in through
`vfg orch send`; the table at the end times each reply from the task line's `at` to its
`vfg orch reply`. Prints PASS/FAIL per check; exits non-zero when anything failed. A full
run makes about 15 model calls, well under $0.01. Not part of any pytest run.
"""
import argparse
import datetime as dt
import gzip
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLKIT = os.path.join(REPO, "toolkit")
VFG = os.path.join(TOOLKIT, "vfg")
DISPATCHER = os.path.join(REPO, "dispatcher")
SCRIPTS_TEST = os.path.join(REPO, "scripts", "_test")
FIXTURES = os.path.join(DISPATCHER, "tests", "fixtures", "refs")
UTC = dt.timezone.utc
KEY = "OPEN_ROUTER_API"
WINDOW_S = 10.0  # VFG_DISPATCH_WINDOW_S, the spec's default
WIKI = "https://minecraft.wiki/w/"

ORCH_RE = re.compile(r"^ORCH#(\d+) \d\d:\d\d:\d\d (T\d+) (progress|done|failed) \| (.*)$")
XYZ_RE = re.compile(r"-?\d+ -?\d+ -?\d+")

RUNNER = """#!/usr/bin/env bash
# A stand-in for orchestrator/run: it reads nothing, so handed tasks stay in the inbox.
trap 'exit 0' TERM INT
echo "stand-in runner $$ up"
while true; do sleep 0.2; done
"""


def parse_iso(s):
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def wait_for(fn, timeout, step=0.1):
    end = time.monotonic() + timeout
    while True:
        v = fn()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def short(text, n=300):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + " …"


def read_jsonl(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def scripts_test(name):
    """scripts/_test/<name>.py; fake_places_world imports mc_26_3 beside it."""
    if SCRIPTS_TEST not in sys.path:
        sys.path.insert(0, SCRIPTS_TEST)
    return __import__(name)


def find_key():
    """The OpenRouter key and where it came from (a path or "the environment"); never printed."""
    if os.environ.get(KEY, "").strip():
        return os.environ[KEY].strip(), "the environment"
    d = REPO
    while True:
        path = os.path.join(d, ".env")
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith("export "):
                        line = line[7:].lstrip()
                    k, sep, v = line.partition("=")
                    if sep and k.strip() == KEY:
                        v = v.strip()
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                            v = v[1:-1]
                        if v:
                            return v, path
        except OSError:
            pass
        if os.path.dirname(d) == d:
            return None, None
        d = os.path.dirname(d)


def seed_refs(dispatch_dir, version="26.3"):
    """refs/<version>/ as refs.load() takes a finished cache (complete.json, commands.json and
    index.json present): the fixture's command tree and its five wiki pages (raw/<name>.txt is the
    page's URL, then the API's plain text). Every other command is marked not fetched, so it has its
    syntax alone. complete.json carries format 0, so the dispatcher rebuilds usage.json, menu.txt
    and wiki/ from these with its own parser on its first load. Returns the folder."""
    d = os.path.join(dispatch_dir, "refs", version)
    os.makedirs(os.path.join(d, "raw"), exist_ok=True)
    with gzip.open(os.path.join(FIXTURES, "commands.json.gz")) as f:
        tree = json.load(f)
    with open(os.path.join(d, "commands.json"), "w") as f:
        json.dump(tree, f)
    index = {}
    for name in sorted(tree["children"]):
        index[name] = {"status": "failed", "why": "not in the e2e fixtures", "title": "Commands/" + name,
                       "url": WIKI + "Commands/" + name}
    for fn in sorted(os.listdir(os.path.join(FIXTURES, "wiki"))):
        if not fn.endswith(".json"):  # the folder's README.md (the pages' licence)
            continue
        name = fn[:-len(".json")]
        with open(os.path.join(FIXTURES, "wiki", fn)) as f:
            answer = json.load(f)
        page = answer["query"]["pages"][0]
        url = WIKI + page["title"].replace(" ", "_")
        with open(os.path.join(d, "raw", name + ".txt"), "w") as f:
            f.write(url + "\n" + page["extract"])
        index[name] = {"status": "ok", "title": page["title"], "url": url,
                       "redirected": bool(answer["query"].get("redirects")), "why": ""}
    with open(os.path.join(d, "index.json"), "w") as f:
        json.dump(index, f, indent=1, sort_keys=True)
    with open(os.path.join(d, "complete.json"), "w") as f:
        json.dump({"format": 0, "version": version, "seeded_by": "integration/dispatcher_e2e.py"}, f)
    return d


def server_jar(mc_dir, version="26.3"):
    """A server.jar holding only version.json: all `vfg action refs --version` reads."""
    with zipfile.ZipFile(os.path.join(mc_dir, "server.jar"), "w") as z:
        z.writestr("version.json", json.dumps({"id": version, "name": version}))


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


# --------------------------------------------------------------------- the box

class Box:
    """The temp folder: HOME, VFG_HOME and its orch/ and dispatch/, the repo, the box's /opt/vfg and
    /opt/mc, and the fake world on the box's RCON port."""

    def __init__(self, root, py39, key=None, model=None):
        j = lambda *p: os.path.join(root, *p)  # noqa: E731
        self.root, self.py39, self.key, self.model = root, py39, key, model
        self.home, self.vhome = j("home"), j("home", ".vfg")
        self.orch, self.dispatch = j("home", ".vfg", "orch"), j("home", ".vfg", "dispatch")
        self.repo, self.rvfg, self.rmc = j("repo"), j("box", "vfg"), j("box", "mc")
        self.bin, self.units, self.ssh_key = j("bin"), j("units"), j("fake_ssh_key")
        self.feed = j("box", "vfg", "feed.log")
        self.password = secrets.token_hex(8)
        self.port = free_port()
        self.world = self.rcon = None
        self.started = False
        # uv for dispatcher/run keeps using the owner's cache and Pythons under the temp HOME.
        self.uv_env = {}
        for k, cmd in (("UV_CACHE_DIR", ["uv", "cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["uv", "python", "dir"])):
            try:
                self.uv_env[k] = os.environ.get(k) or subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
            except OSError:
                pass

    def deploy(self, players):
        for d in (self.vhome, self.orch, self.dispatch, self.repo, os.path.join(self.rvfg, "lib"),
                  os.path.join(self.rvfg, "scripts", "_lib"), self.rmc, self.bin, self.units):
            os.makedirs(d, exist_ok=True)
        for d in (self.vhome, self.orch, self.dispatch):
            os.chmod(d, 0o700)
        # The repo vfg orch start and vfg action run read: the real actions, scripts and dispatcher.
        for name in ("actions", "scripts", "dispatcher"):
            os.symlink(os.path.join(REPO, name), os.path.join(self.repo, name))
        os.makedirs(os.path.join(self.repo, "orchestrator"))
        runner = os.path.join(self.repo, "orchestrator", "run")
        with open(runner, "w") as f:
            f.write(RUNNER)
        os.chmod(runner, 0o755)
        # The box: the feed writer, mc.py (as vfg script deploy leaves it), RCON's settings, the jar.
        shutil.copy(os.path.join(TOOLKIT, "tests", "fake_vfg_feed.py"), os.path.join(self.rvfg, "lib", "vfg_feed.py"))
        shutil.copy(os.path.join(REPO, "scripts", "_lib", "mc.py"), os.path.join(self.rvfg, "scripts", "_lib", "mc.py"))
        with open(os.path.join(self.rmc, "server.properties"), "w") as f:
            f.write("#Minecraft server properties\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n"
                    "server-port=25565\n" % (self.port, self.password))
        server_jar(self.rmc)
        for u in ("minecraft", "vfg-bridge", "vfg-jev"):
            with open(os.path.join(self.units, u), "w") as f:
                f.write("active\n")
        # python3 is the box's 3.9, for fake_ssh's remote side; vfg is this repo's, on the same
        # Python, with that PATH again in front (uv run puts the dispatcher's venv first).
        os.symlink(self.py39, os.path.join(self.bin, "python3"))
        os.makedirs(os.path.join(self.rvfg, "python", "bin"), exist_ok=True)
        os.symlink(self.py39, os.path.join(self.rvfg, "python", "bin", "python3"))  # the box's Python, at the box's path
        with open(os.path.join(self.bin, "vfg"), "w") as f:
            f.write("#!/bin/sh\nPATH='%s':\"$PATH\"; export PATH\nexec '%s' '%s' \"$@\"\n"
                    % (self.bin, self.py39, VFG))
        os.chmod(os.path.join(self.bin, "vfg"), 0o755)
        with open(self.ssh_key, "w") as f:
            f.write("not a key: fake_ssh never reads it\n")
        cfg = os.path.join(self.vhome, "config")
        with open(cfg, "w") as f:
            f.write("# dispatcher_e2e: everything points into %s\n" % self.root)
            f.write("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_SSH=%s\nVFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                    "VFG_REPO=%s\nVFG_DISPATCH_DIR=%s\n" % (self.ssh_key, os.path.join(TOOLKIT, "tests", "fake_ssh"),
                                                          self.rvfg, self.rmc, self.repo, self.dispatch))
        os.chmod(cfg, 0o600)
        self.refs = seed_refs(self.dispatch)
        fake_places_world, fake_rcon = scripts_test("fake_places_world"), scripts_test("fake_rcon")
        self.world = fake_places_world.FakeWorld(players=players)
        self.rcon = fake_rcon.FakeRcon(self.password, port=self.port, responder=self.world.respond).start()

    # -- environments
    def env(self, with_key=False):
        drop = ("VFG_", "FAKE_", "CLAUDE", "TYPESAFE_", "ANTHROPIC_", "UV_", KEY)
        venv_bin = os.path.join(os.environ.get("VIRTUAL_ENV", "\0"), "bin")
        path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != venv_bin]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(drop) and k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
        env.update(self.uv_env)
        env.update({
            "HOME": self.home, "VFG_HOME": self.vhome, "VFG_REPO": self.repo, "VFG_ORCH_DIR": self.orch,
            "VFG_DISPATCH_DIR": self.dispatch, "FAKE_UNITS": self.units, "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.pathsep.join([self.bin, os.path.join(TOOLKIT, "tests", "bin")] + path),
        })
        if self.model:
            env["VFG_DISPATCH_MODEL"] = self.model
        if with_key:
            env[KEY] = self.key
        return env

    def vfg(self, *args, timeout=60, with_key=False):
        return subprocess.run([os.path.join(self.bin, "vfg")] + list(args), env=self.env(with_key),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)

    # -- the dispatcher
    def start(self):
        """`vfg orch start`, the key in its environment only: the dispatcher inherits it."""
        r = self.vfg("orch", "start", with_key=True, timeout=60)
        self.started = True
        return r

    def stop(self):
        if not self.started:
            return None
        self.started = False
        return self.vfg("orch", "stop", timeout=60)

    def dlog(self):
        try:
            with open(os.path.join(self.dispatch, "dispatcher.log"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def state(self):
        try:
            with open(os.path.join(self.dispatch, "state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # -- tasks and answers
    def send(self, task):
        r = self.vfg("orch", "send", task)
        tid = r.stdout.strip()
        line = next((x for x in read_jsonl(os.path.join(self.dispatch, "inbox.jsonl")) if x.get("id") == tid), None)
        return tid, (parse_iso(line["at"]) if line else dt.datetime.now(UTC)), r

    def replies(self, tid):
        """[(at, status, text)] in order: replies.jsonl (vfg orch reply's own record, with the
        time it returned) joined to the box's feed.log by seq."""
        texts = {}
        try:
            with open(self.feed, encoding="utf-8") as f:
                for line in f:
                    m = ORCH_RE.match(line.rstrip("\n"))
                    if m:
                        texts[int(m.group(1))] = m.group(4)
        except OSError:
            pass
        return [(parse_iso(r["at"]), r["status"], texts.get(r.get("seq"), "?"))
                for r in read_jsonl(os.path.join(self.orch, "replies.jsonl")) if r.get("id") == tid]

    def closes(self, tid):
        return [x for x in self.replies(tid) if x[1] in ("done", "failed")]

    def ended(self, tid):
        """Closed by the dispatcher, or handed on (then the orchestrator closes it)."""
        return self.closes(tid) or self.handed(tid)

    def handed(self, tid):
        return [x for x in read_jsonl(os.path.join(self.orch, "inbox.jsonl")) if x.get("id") == tid]

    def close(self):
        try:
            if self.started:
                self.stop()
        except Exception:
            pass
        for pidfile in (os.path.join(self.dispatch, "dispatcher.pid"), os.path.join(self.orch, "runner.pid")):
            try:
                with open(pidfile) as f:
                    os.killpg(int(f.read().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        if self.rcon:
            self.rcon.stop()


# ======================================================================= the run

class Run:
    def __init__(self, box, rep):
        self.box, self.rep = box, rep
        self.asks = []  # (tid, task, sent_at, extra rows [(seconds, label, text)])

    def ask(self, task, until, timeout=60):
        """Send `task` with vfg orch send and wait until `until(tid)` is true."""
        box = self.box
        mark = len(box.world.sent)
        tid, at, r = box.send(task)
        ask = {"tid": tid, "task": task, "at": at, "extra": [], "mark": mark}
        self.asks.append(ask)
        if not re.match(r"^T\d+$", tid):
            self.rep.check(False, "vfg orch send %r prints T<n>" % task, "%s %s" % (r.stdout, r.stderr))
            return ask
        self.rep.note("%s sent: %s%s" % (tid, task, ("  (vfg: %s)" % r.stderr.strip()) if r.stderr.strip() else ""))
        wait_for(lambda: until(tid), timeout, 0.05)
        return ask

    def sent(self, ask):
        """The commands the fake world got since the ask was sent."""
        return self.box.world.sent[ask["mark"]:]

    def secs(self, ask, when):
        return (when - ask["at"]).total_seconds()

    def table(self):
        print("\nTIMING: send -> each reply (task line's `at` to `vfg orch reply`'s record)")
        for a in self.asks:
            print("  %-4s %s" % (a["tid"], a["task"]))
            rows = [(self.secs(a, at), st, text) for at, st, text in self.box.replies(a["tid"])] + a["extra"]
            for secs, label, text in sorted(rows, key=lambda r: r[0]):
                print("        %+6.1f s  %-8s  %s" % (secs, label, short(text, 150)))
            m = re.search(r"^\S+ task %s finished in ([\d.]+) s.*?(\d+) model call\(s\), (\d+) ms, \$([\d.]+)"
                          % a["tid"], self.box.dlog(), re.M)
            if m:
                print("        dispatcher: %s s in the task, %s model call(s), %s ms of them, $%s"
                      % m.group(1, 2, 3, 4))


def ask1(run):
    rep, box = run.rep, run.box
    rep.head("1. Give Steve 64 firework rockets and Speed II")
    a = run.ask("Give Steve 64 firework rockets and Speed II", box.ended)
    c = box.closes(a["tid"])
    sent = run.sent(a)
    gives = [x for x in sent if re.match(r"^(execute .* run )?give (Steve|@a|@p|@s) (minecraft:)?firework_rocket\S* 64$", x)]
    speed = [x for x in sent if re.match(r"^(execute .* run )?effect give (Steve|@a|@p|@s) (minecraft:)?speed "
                                         r"(\d+|infinite) 1( (true|false))?$", x)]
    rep.check(len(c) == 1 and c[0][1] == "done", "%s ends done" % a["tid"], "replies %r" % box.replies(a["tid"]))
    rep.check(gives, "64 firework rockets given to Steve in the world's command log", "sent %r" % sent)
    rep.check(speed, "Speed II (amplifier 1) given to Steve in the world's command log", "sent %r" % sent)
    lasting = [x for x in speed if re.search(r"\bspeed (infinite|\d{2,}) ", x)]
    rep.check(lasting, "and it lasts 10 s or more", "sent %r" % speed)
    if c:
        rep.check(run.secs(a, c[0][0]) <= 6, "done within 6 s of vfg orch send (%.1f s)" % run.secs(a, c[0][0]))


def ask2(run, grove):
    rep, box = run.rep, run.box
    rep.head("2. Teleport Steve to a cherry grove")
    a = run.ask("Teleport Steve to a cherry grove", box.ended)
    c = box.closes(a["tid"])
    steve = box.world.players["Steve"]
    rep.check(len(c) == 1 and c[0][1] == "done", "%s ends done" % a["tid"], "replies %r" % box.replies(a["tid"]))
    rep.check(any(XYZ_RE.search(text) for _, _, text in box.replies(a["tid"])),
              "its answers carry the coordinates (x y z)", "replies %r" % box.replies(a["tid"]))
    near = math.hypot(steve.pos[0] - grove[0], steve.pos[2] - grove[2])
    rep.check(near <= 64, "Steve stands in the world within 64 blocks of the cherry grove (%.0f)" % near,
              "Steve at %r; sent %r" % (steve.pos, run.sent(a)))
    rep.check(any("locate biome minecraft:cherry_grove" in x for x in run.sent(a)),
              "the handler located minecraft:cherry_grove", "sent %r" % run.sent(a))
    if c:
        rep.check(run.secs(a, c[0][0]) <= 10, "done within 10 s of vfg orch send (%.1f s)" % run.secs(a, c[0][0]))


def ask3(run):
    rep, box = run.rep, run.box
    rep.head("3. Give Steve speed and build a small stone tower at his position")
    stood = list(box.world.players["Steve"].pos)
    a = run.ask("Give Steve speed and build a small stone tower at his position", lambda t: box.handed(t),
                timeout=60 + WINDOW_S)
    tid = a["tid"]
    moved = [x for x in run.sent(a) if re.match(r"^(execute .* run )?(tp|teleport|spreadplayers) ", x)]
    rep.check(not moved and box.world.players["Steve"].pos == stood, "nothing else happened to Steve: he was not moved",
              "moved by %r; at %r, was at %r" % (moved, box.world.players["Steve"].pos, stood))
    seen = dt.datetime.now(UTC)
    handed = box.handed(tid)
    replies = box.replies(tid)
    speed = [x for x in replies if x[1] == "progress" and "speed" in x[2].lower() and "handed on" not in x[2]]
    rep.check(speed, "a progress line for the speed", "replies %r" % replies)
    rep.check(any(re.match(r"^(execute .* run )?effect give (Steve|@a|@p|@s) (minecraft:)?speed\b", x)
                  for x in run.sent(a)), "Speed given to Steve in the world's command log", "sent %r" % run.sent(a))
    rep.check(len(handed) == 1 and "tower" in handed[0]["task"].lower(),
              "the tower is in ~orch/inbox.jsonl, once", "orch inbox lines for %s: %r" % (tid, handed))
    if handed:
        a["extra"].append((run.secs(a, seen), "handed", "~orch/inbox.jsonl: " + handed[0]["task"]))
        rep.check("Dispatcher:" in handed[0]["context"] and "speed" in handed[0]["context"].lower(),
                  "its context tells the orchestrator the speed was done", "context %r" % handed[0]["context"])
        if speed:
            after = (seen - speed[0][0]).total_seconds()
            rep.check(after <= WINDOW_S + 5, "handed %.1f s after the speed's progress (window %g s)" % (after, WINDOW_S))
    rep.check(wait_for(lambda: re.search(r"batch handed 1 in one write: %s\b" % tid, box.dlog()), 5),
              "through vfg orch hand (the dispatcher's batch log)", short(box.dlog()[-1500:]))
    rep.check(not box.closes(tid), "the dispatcher leaves the close to the orchestrator", "replies %r" % replies)


def ask4(run):
    rep, box = run.rep, run.box
    rep.head("4. Teleport Bob to a village (Bob is not online)")
    a = run.ask("Teleport Bob to a village", box.ended)
    c = box.closes(a["tid"])
    rep.check(len(c) == 1 and c[0][1] == "failed" and "bob" in c[0][2].lower()
              and re.search(r"online|offline|not on|isn't on|is not here", c[0][2], re.I),
              "%s is dropped, the reason told: Bob is not online" % a["tid"], "replies %r" % box.replies(a["tid"]))
    rep.check(not any(re.search(r"\bBob\b", x) and re.match(r"^(execute .* run )?(tp|teleport|spreadplayers) ", x)
                      for x in box.world.sent), "nothing moved Bob", "sent %r" % run.sent(a))
    return a


def ask4_nothing_handed(run, a):
    """Checked late, so the batch window has long passed since the drop."""
    rep, box = run.rep, run.box
    c = box.closes(a["tid"])
    if c:
        left = WINDOW_S + 3 - (dt.datetime.now(UTC) - c[0][0]).total_seconds()
        if left > 0:
            time.sleep(left)
    rep.check(not box.handed(a["tid"]) and not re.search(r"batch %s joins" % a["tid"], box.dlog()),
              "4. nothing of %s reached the orchestrator, %g s after the drop" % (a["tid"], WINDOW_S + 3),
              "orch inbox %r" % box.handed(a["tid"]))


def ask5(run):
    rep, box = run.rep, run.box
    rep.head("5. Save where Steve stands as home, then teleport Steve home")
    steve = box.world.players["Steve"]
    a = run.ask("Save where Steve stands as home", box.ended)
    stood = [int(math.floor(v)) for v in steve.pos]
    c = box.closes(a["tid"])
    home = box.world.storage.get("home")
    rep.check(len(c) == 1 and c[0][1] == "done", "%s ends done" % a["tid"], "replies %r" % box.replies(a["tid"]))
    rep.check(home and home["pos"] == stood and home["by"] == a["tid"],
              "home is saved in everhear:places where Steve stood, by %s" % a["tid"],
              "storage %r; Steve stood at %r" % (box.world.storage, stood))
    # Steve walks off before asking to go back.
    steve.pos = [stood[0] + 300.5, 70.0, stood[2] - 200.5]
    b = run.ask("Teleport Steve home", box.ended)
    c = box.closes(b["tid"])
    m = re.search(r"^\S+ places %s (\d+) saved" % b["tid"], box.dlog(), re.M)
    rep.check(m and int(m.group(1)) >= 1, "the next task read the saved places (vfg place list: %s saved)"
              % (m.group(1) if m else "?"), short(box.dlog()[-1500:]))
    rep.check(len(c) == 1 and c[0][1] == "done", "%s ends done" % b["tid"], "replies %r" % box.replies(b["tid"]))
    rep.check(any("data get storage everhear:places" in x for x in run.sent(b)),
              "the teleport read home from everhear:places", "sent %r" % run.sent(b))
    if home:
        off = math.hypot(steve.pos[0] - home["pos"][0], steve.pos[2] - home["pos"][2])
        rep.check(off <= 8, "Steve is back home (%.1f blocks off)" % off, "Steve at %r, home %r" % (steve.pos, home))


def teardown_checks(run):
    rep, box = run.rep, run.box
    rep.head("the end")
    for a in run.asks:
        n = len(box.closes(a["tid"]))
        want = 0 if "tower" in a["task"] else 1  # the orchestrator closes the handed task
        rep.check(n == want, "%s: %d closing answer(s) from the dispatcher" % (a["tid"], n), "%r" % box.replies(a["tid"]))
    r = box.stop()
    log = box.dlog()
    st = box.state()
    rep.check(r is not None and r.returncode == 0 and re.search(r"^\S+ stop stopped$", log, re.M),
              "vfg orch stop: the dispatcher stopped cleanly",
              "%s %s; log tail %s" % (r and r.stdout, r and r.stderr, short(log[-800:])))
    rep.check(st and not st.get("tasks") and not st.get("batch") and not st.get("outbox"),
              "state.json holds no task, batch line or answer", "%r" % {k: st.get(k) for k in ("tasks", "batch", "outbox")})
    rep.check("Traceback" not in log, "no traceback in dispatcher.log", short(log[log.find("Traceback"):][:1500]))
    leaks = []
    for d, _, files in os.walk(box.root):
        for fn in files:
            try:
                with open(os.path.join(d, fn), "rb") as f:
                    if box.key.encode() in f.read():
                        leaks.append(os.path.relpath(os.path.join(d, fn), box.root))
            except OSError:
                pass
    rep.check(not leaks, "the OpenRouter key is in no file of the run", "found in %d file(s): %s" % (len(leaks), leaks))
    costs = [float(x) for x in re.findall(r"^\S+ task T\d+ finished .*\$([\d.]+)$", log, re.M)]
    rep.note("model cost of the run: $%.6f over %d task(s)" % (sum(costs), len(costs)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the temp folder and its logs")
    ap.add_argument("--model", help="VFG_DISPATCH_MODEL for the run (default: the dispatcher's)")
    args = ap.parse_args()
    key, where = find_key()
    if not key:
        print("no %s in the environment or in a .env above %s" % (KEY, REPO), file=sys.stderr)
        return 2
    try:
        py39 = subprocess.run(["uv", "python", "find", "3.9"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True).stdout.strip()
    except OSError:
        py39 = ""
    if not py39:
        py39 = shutil.which("python3")
        print("no Python 3.9 found: the box's side runs on %s" % py39)
    root = tempfile.mkdtemp(prefix="vfg-dispatch-e2e-")
    box = Box(root, py39, key, args.model)
    rep = Report()
    run = Run(box, rep)
    print("dispatcher e2e: temp folder %s\n  key from %s; the box's side and vfg on %s; refs/26.3/ seeded "
          "from dispatcher/tests/fixtures/refs" % (root, where, py39))
    grove = (1412, 92, -2140)
    try:
        box.deploy({"Steve": (197.5, 68.0, 251.5), "Alex": (180.5, 70.0, 240.5)})
        box.world.located[("biome", "minecraft:cherry_grove")] = grove
        box.world.located[("structure", "#minecraft:village")] = (-544, 0, 320, "minecraft:village_plains")
        box.world.located[("structure", "minecraft:village_plains")] = (-544, 0, 320)
        rep.head("start")
        r = box.start()
        up = wait_for(lambda: re.search(r"^\S+ start pid \d+", box.dlog(), re.M), 120, 0.2)
        rep.check(r.returncode == 0 and up, "vfg orch start: the dispatcher is up and following its inbox",
                  "%s %s; log %s" % (r.stdout, r.stderr, short(box.dlog()[-1500:])))
        m = re.search(r"^\S+ refs server (\S+): (\d+) commands", box.dlog(), re.M)
        rep.check(m and m.group(1) == "26.3", "the references load from the seeded refs/26.3/ (%s commands)"
                  % (m.group(2) if m else "?"), short(box.dlog()[-1500:]))
        m = re.search(r"^\S+ actions loaded (\d+): (.*)$", box.dlog(), re.M)
        rep.note("actions: %s" % (m.group(2) if m else "? (no 'actions loaded' line)"))
        if up:
            for step in (ask1, lambda run_: ask2(run_, grove), ask3):
                try:
                    step(run)
                except Exception:
                    rep.check(False, "the step ran to its end", traceback.format_exc()[-1500:])
            a4 = None
            try:
                a4 = ask4(run)
                ask5(run)
            except Exception:
                rep.check(False, "the step ran to its end", traceback.format_exc()[-1500:])
            if a4:
                ask4_nothing_handed(run, a4)
            run.table()
            teardown_checks(run)
    finally:
        box.close()
    failed = rep.failed()
    print("\n" + "=" * 72)
    print("%d/%d checks passed" % (len(rep.checks) - len(failed), len(rep.checks)))
    for name in failed:
        print("  x %s" % name)
    print("=" * 72)
    if args.keep or failed:
        print("kept: %s (home/.vfg/dispatch/dispatcher.log, home/.vfg/orch/, box/vfg/feed.log)" % root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
