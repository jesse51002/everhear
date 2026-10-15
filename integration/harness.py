#!/usr/bin/env python3
"""End-to-end harness: the REAL bridge, Jev filter and vfg toolkit running together on
this PC, with a temp folder standing in for the EC2 box's /opt/vfg and /opt/mc, each path
driven the way the brain drives it.

    integration/run.sh [--keep]          (run.sh finds Python 3.12 and the JDK, then runs this)

Real:  bridge (vfg_bridge + vfg_feed, run on the box's Python 3.12), the Jev filter (3.12), the
       vfg toolkit (this repo's toolkit/vfg, under uv; its remote programs run on 3.12 through
       the fake ssh), the client mod's BridgeClient/LinePayload (path 8), and the brain's
       brain/context.py, which reads what the feed writer wrote (path 9).
Stand-ins: Minecraft (console.log lines appended here, shapes from the bridge's own
       fixture console-26.3.log; toolkit/tests/fake_rcon.py, in this process), SSH
       (toolkit/tests/fake_ssh, which runs the remote command here), systemd (toolkit/tests/bin,
       its four units active), Jev's API (jev/tests/fake_jev.py), the brain (its vfg calls made
       here, as it makes them). No game engine runs: the bridge's and Jev's engine socket and
       export point into the temp folder, so no player has an incantation.

Nothing touches ~/.vfg, ~/.ssh or any remote host: HOME and VFG_HOME point into the temp
folder, the fake repo links this repo's actions/ and engine/, and every port is on 127.0.0.1.
Prints PASS/FAIL per check and per path, and exits non-zero when anything failed.
"""
import argparse
import datetime as dt
import fcntl
import glob
import http.client
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import uuid

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BRIDGE = os.path.join(REPO, "bridge")
JEV = os.path.join(REPO, "jev")
TOOLKIT = os.path.join(REPO, "toolkit")
BRAIN = os.path.join(REPO, "brain")
MOD = os.path.join(REPO, "client-mod")
VFG = os.path.join(TOOLKIT, "vfg")
PC_PY = sys.executable
UTC = dt.timezone.utc

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
# §7: TYPE#seq HH:MM:SS fields | text  (fields never hold a |)
FEED_RE = re.compile(r"^(LINE|GAME|AI|SYS)#(\d+) (\d\d:\d\d:\d\d) (?:([^|]*?) )?\| (.*)$")
# §7's field grammar, written from the contract text (an old line may end with the retired re-ask's marker)
C_LINE = re.compile(r"^[A-Za-z0-9_]{1,16} (voice|chat) (to_ai|context) (\d\.\d\d|-) "
                    r"(-?\d+dB|-)( was=not_relevant)?$")
C_GAME = re.compile(r"^[a-z_]+ ([A-Za-z0-9_]{1,16}|-)$")
C_AI = re.compile(r"^(say|action|task|result|note) ([a-z][a-z0-9_]{1,31}|-)$")   # a task's name, or -
# vfg history's output (toolkit/remote/history.py)
H_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S+ (voice|chat|ai) "
                    r"(?:(?:to_ai|context|not_relevant) (?:\d\.\d\d|-)|-) \| .*$")
H_EVENT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (join|leave|death|advancement|server) \S+ \| .*$")
FED = ("to_ai", "context")   # the classes Jev feeds

SYS_OPEN = "jev unavailable — passing every line as context"
SYS_BACK = "jev back — filtering again"
WATCHERS = "join,leave,death,advancement,server"   # what the brain sets as it starts (brain/__main__.py)


def now():
    return dt.datetime.now(UTC)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def hms(d):
    return d.strftime("%H:%M:%S")


def wait_for(fn, timeout, step=0.05):
    end = time.monotonic() + timeout
    while True:
        v = fn()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def short(text, n=600):
    text = str(text)
    return text if len(text) <= n else text[:n] + " …"


def load_module(name, path):
    """A module from its file, registered first (dataclasses look their module up)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------- the report

class Report:
    def __init__(self):
        self.paths = []
        self.cur = None

    def path(self, key, title):
        self.cur = {"key": key, "title": title, "checks": [], "notes": []}
        self.paths.append(self.cur)
        print("\n== %s  %s" % (key, title), flush=True)

    def resume(self, key):
        self.cur = [p for p in self.paths if p["key"] == key][0]
        print("\n== %s  (continued) %s" % (key, self.cur["title"]), flush=True)

    def check(self, ok, name, detail=""):
        ok = bool(ok)
        self.cur["checks"].append((ok, name, detail))
        line = "  %s  %s" % ("PASS" if ok else "FAIL", name)
        if detail and not ok:
            line += "\n          " + short(detail, 1500).replace("\n", "\n          ")
        print(line, flush=True)
        return ok

    def note(self, text):
        self.cur["notes"].append(text)
        print("  note  " + text, flush=True)

    def crashed(self, exc):
        self.check(False, "the path ran to its end", "".join(traceback.format_exception(*exc))[-1500:])

    def failed(self):
        return any(not ok for p in self.paths for ok, _, _ in p["checks"])

    def summary(self):
        print("\n" + "=" * 72)
        print("RESULT per path")
        order = sorted(self.paths, key=lambda p: (len(p["key"]), p["key"]))
        for p in order:
            bad = [n for ok, n, _ in p["checks"] if not ok]
            n = len(p["checks"])
            print("  %-4s %s  %-58s %d/%d" % (p["key"], "FAIL" if bad else "PASS", p["title"][:58],
                                             n - len(bad), n))
            for name in bad:
                print("         x %s" % name)
        print("=" * 72)


# --------------------------------------------------------------------- the box

class Box:
    """The temp folder: /opt/vfg and /opt/mc stand-ins, the owner's VFG_HOME, the processes."""

    def __init__(self, root, ec2_py):
        self.root = root
        j = lambda *p: os.path.join(root, *p)  # noqa: E731
        self.vfg_dir, self.mc_dir = j("opt", "vfg"), j("opt", "mc")
        self.lib, self.jev_dir, self.bridge_dir = j("opt", "vfg", "lib"), j("opt", "vfg", "jev"), j("opt", "vfg", "bridge")
        self.db, self.feed = j("opt", "vfg", "vfg.db"), j("opt", "vfg", "feed.log")
        self.watch_json, self.lookout = j("opt", "vfg", "watch.json"), j("opt", "vfg", "lookout.md")
        self.menu = j("opt", "vfg", "menu.md")
        self.state, self.env_file = j("opt", "vfg", "state"), j("opt", "vfg", ".env")
        self.console_log, self.props = j("opt", "mc", "console.log"), j("opt", "mc", "server.properties")
        self.home = j("home")
        self.vhome = j("home", ".vfg")
        self.repo = j("repo")
        self.ec2bin, self.pcbin, self.units, self.logs = j("ec2bin"), j("pcbin"), j("units"), j("logs")
        self.fake_key = j("fake_ssh_key")
        self.watch_out = j("logs", "watch.out")
        self.crash_flag = j("crash.flag")
        self.ec2_py = ec2_py
        self.token = secrets.token_urlsafe(18)              # the bridge's client token, made up here
        self.jev_key = "fake-jev-key-" + secrets.token_hex(6)  # only the fake Jev ever sees it
        self.rcon_password = secrets.token_hex(8)
        self.procs = {}
        self.offsets = {}
        self.bridge_port = None
        self.fake_jev = None
        self.fake_jev_url = None
        self.fake_rcon = None
        self.watch_seen = []      # (wall time, line) as vfg watch printed them
        self._watch_pos = 0
        self._watch_buf = b""
        self._watch_lock = threading.RLock()
        self._watch_stop = threading.Event()
        self.watch_start_seq = 0
        self.started = now()

    # -- layout, as deploy.sh lays /opt/vfg out
    def deploy(self):
        for d in (self.lib, self.jev_dir, self.bridge_dir, self.state, self.mc_dir, self.vhome, self.repo,
                  self.ec2bin, self.pcbin, self.units, self.logs):
            os.makedirs(d, exist_ok=True)
        os.chmod(self.vhome, 0o700)
        shutil.copy2(os.path.join(BRIDGE, "vfg_feed.py"), os.path.join(self.lib, "vfg_feed.py"))
        shutil.copytree(os.path.join(BRIDGE, "vfg_bridge"), os.path.join(self.bridge_dir, "vfg_bridge"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for f in glob.glob(os.path.join(JEV, "*.py")):
            shutil.copy2(f, self.jev_dir)
        # The repo vfg reads from: the actions (vfg action list, the brain's menu for Jev) and the
        # engine's library (vfg task set refuses a new task named as one of its classes).
        for name in ("actions", "engine"):
            os.symlink(os.path.join(REPO, name), os.path.join(self.repo, name))
        fd = os.open(self.env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, ("VFG_CLIENT_TOKEN=%s\nTYPESAFE_API_KEY=%s\n" % (self.token, self.jev_key)).encode())
        os.close(fd)
        with open(self.props, "w") as f:
            f.write("#Minecraft server properties\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n"
                    "server-port=25565\n" % (self.fake_rcon.port, self.rcon_password))
        open(self.console_log, "a").close()
        os.symlink(self.ec2_py, os.path.join(self.ec2bin, "python3"))
        os.makedirs(os.path.join(self.vfg_dir, "python", "bin"), exist_ok=True)
        os.symlink(self.ec2_py, os.path.join(self.vfg_dir, "python", "bin", "python3"))  # as python.sh lays it out
        with open(os.path.join(self.pcbin, "vfg"), "w") as f:
            f.write("#!/bin/sh\nexec '%s' \"$@\"\n" % VFG)                      # under uv, as on the PC
        os.chmod(os.path.join(self.pcbin, "vfg"), 0o755)
        with open(self.fake_key, "w") as f:
            f.write("not a key: fake_ssh never reads it\n")
        cfg = os.path.join(self.vhome, "config")
        with open(cfg, "w") as f:
            f.write("# integration harness: everything points into %s\n" % self.root)
            f.write("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_SSH=%s\nVFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                    "VFG_REPO=%s\n" % (self.fake_key, os.path.join(TOOLKIT, "tests", "fake_ssh"),
                                        self.vfg_dir, self.mc_dir, self.repo))
        os.chmod(cfg, 0o600)
        for u in ("minecraft", "vfg-bridge", "vfg-jev", "vfg-engine"):
            with open(os.path.join(self.units, u), "w") as f:
                f.write("active\n")

    # -- environments
    def base_env(self):
        drop = ("VFG_", "FAKE_", "CLAUDE", "TYPESAFE_", "ANTHROPIC_", "OPEN_ROUTER")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(drop) and k not in ("PYTHONPATH", "PYTHONHOME")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "no_proxy": "127.0.0.1,localhost",
                    "NO_PROXY": "127.0.0.1,localhost"})
        return env

    def ec2_env(self, **extra):
        env = self.base_env()
        env.update({"VFG_CLIENT_TOKEN": self.token, "TYPESAFE_API_KEY": self.jev_key})  # EnvironmentFile
        env.update({
            "VFG_DB": self.db, "VFG_FEED": self.feed, "VFG_WATCH": self.watch_json, "VFG_STATE": self.state,
            "VFG_CONSOLE_LOG": self.console_log, "VFG_BRIDGE_BIND": "127.0.0.1", "VFG_BRIDGE_PORT": "0",
            "VFG_LOOKOUT": self.lookout, "VFG_MENU": self.menu, "VFG_LIB": self.lib, "VFG_ENV_FILE": self.env_file,
            # the game engine's socket and export, which the bridge and Jev read: in the temp box, where
            # no engine runs (no incantation, casts answer 404)
            "VFG_BASE": self.vfg_dir, "VFG_ENGINE_SOCKET": os.path.join(self.vfg_dir, "engine", "engine.sock"),
            "VFG_ENGINE_EXPORT": os.path.join(self.vfg_dir, "engine", "incantations.json"),
            "VFG_JEV_URL": self.fake_jev_url or "", "PYTHONUNBUFFERED": "1",
            "PATH": self.ec2bin + os.pathsep + os.environ.get("PATH", ""),
        })
        env.update(extra)
        return env

    def pc_env(self, **extra):
        env = self.base_env()
        env.update({
            "HOME": self.home, "VFG_HOME": self.vhome, "FAKE_UNITS": self.units,
            "PATH": os.pathsep.join([self.pcbin, self.ec2bin, os.path.join(TOOLKIT, "tests", "bin"),
                                     os.environ.get("PATH", "")]),
        })
        # uv, which runs vfg, keeps the owner's cache and Pythons under the temp HOME.
        for k, v in uv_dirs().items():
            env.setdefault(k, v)
        env.update(extra)
        return env

    # -- processes
    def logpath(self, name):
        return os.path.join(self.logs, name + ".log")

    def spawn(self, name, argv, env, cwd=None, stdout=None, stderr=None):
        out_path = stdout or self.logpath(name)
        self.offsets[name] = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        out = open(out_path, "ab")
        err = open(stderr, "ab") if stderr else None
        p = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=err or subprocess.STDOUT, start_new_session=True)
        out.close()
        if err:
            err.close()
        self.procs[name] = p
        return p

    def alive(self, name):
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop(self, name, sig=signal.SIGTERM, timeout=15):
        p = self.procs.get(name)
        if p is None:
            return None
        if p.poll() is None:
            try:
                os.kill(p.pid, sig)
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                return "timeout"
        return p.returncode

    def log_since(self, name):
        try:
            with open(self.logpath(name), "rb") as f:
                f.seek(self.offsets.get(name, 0))
                return f.read().decode("utf-8", "replace")
        except FileNotFoundError:
            return ""

    def wait_log(self, name, pattern, timeout=15):
        rx = re.compile(pattern)
        return wait_for(lambda: rx.search(self.log_since(name)), timeout)

    def start_fake_jev(self):
        self.fake_jev = subprocess.Popen([PC_PY, os.path.join(JEV, "tests", "fake_jev.py")],
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=self.base_env(), start_new_session=True)
        port = int(self.fake_jev.stdout.readline())
        self.fake_jev_base = "http://127.0.0.1:%d" % port
        self.fake_jev_url = self.fake_jev_base + "/v1/systemone"

    def start_fake_rcon(self):
        """toolkit/tests/fake_rcon.py's FakeRcon, in this process: Steve online, every command kept."""
        sys.path.insert(0, os.path.join(TOOLKIT, "tests"))
        try:
            from fake_rcon import FakeRcon
        finally:
            sys.path.pop(0)
        self.fake_rcon = FakeRcon(self.rcon_password).start()

    def rcon_commands(self):
        return list(self.fake_rcon.commands)

    def start_bridge(self):
        env = self.ec2_env(PYTHONPATH=os.pathsep.join([self.lib, self.bridge_dir]))
        self.spawn("bridge", [self.ec2_py, "-m", "vfg_bridge"], env, cwd=self.vfg_dir)
        m = self.wait_log("bridge", r"serving on 127\.0\.0\.1:(\d+)", 20)
        if not m:
            raise RuntimeError("the bridge did not start:\n" + self.log_since("bridge")[-2000:])
        self.bridge_port = int(m.group(1))

    def start_jev(self, lib=None, **extra):
        env = self.ec2_env(**extra)
        if lib:
            env["VFG_LIB"] = lib
        self.spawn("jev", [self.ec2_py, "-u", os.path.join(self.jev_dir, "jev_filter.py")], env, cwd=self.jev_dir)
        if not self.wait_log("jev", r"vfg-jev up", 20) or not self.wait_log("jev", r"database .* open", 20):
            raise RuntimeError("the Jev filter did not start:\n" + self.log_since("jev")[-2000:])

    def start_watch(self, after):
        """`vfg watch --after <seq>`, as the brain follows the feed: no cursor read or written."""
        self.spawn("watch", [PC_PY, VFG, "watch", "--after", str(after)], self.pc_env(), stdout=self.watch_out,
                   stderr=os.path.join(self.logs, "watch.err"))

    def watch_tail_thread(self):
        def run():
            while not self._watch_stop.is_set():
                self._read_watch()
                time.sleep(0.05)
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _read_watch(self):
        with self._watch_lock:
            try:
                with open(self.watch_out, "rb") as f:
                    f.seek(self._watch_pos)
                    data = f.read()
            except FileNotFoundError:
                return
            if not data:
                return
            self._watch_pos += len(data)
            lines = (self._watch_buf + data).split(b"\n")
            self._watch_buf = lines.pop()
            t = time.time()
            for raw in lines:
                self.watch_seen.append((t, raw.decode("utf-8", "replace")))

    def watch_lines(self):
        self._read_watch()
        with self._watch_lock:
            return [l for _, l in self.watch_seen]

    def watch_match(self, regex):
        rx = re.compile(regex)
        return [l for l in self.watch_lines() if rx.match(l)]

    def wait_watch(self, regex, timeout=10, count=1):
        return wait_for(lambda: len(self.watch_match(regex)) >= count, timeout) and self.watch_match(regex)

    def last_printed_seq(self):
        seqs = [int(m.group(2)) for m in (FEED_RE.match(l) for l in self.watch_lines()) if m]
        return max(seqs or [0])

    def arrival(self, line):
        self._read_watch()
        with self._watch_lock:
            for t, l in self.watch_seen:
                if l == line:
                    return t
        return None

    def close(self):
        self._watch_stop.set()
        for name in list(self.procs):
            self.stop(name, signal.SIGTERM, 5)
        if self.fake_jev and self.fake_jev.poll() is None:
            os.killpg(self.fake_jev.pid, signal.SIGKILL)
            self.fake_jev.wait()
        if self.fake_rcon:
            self.fake_rcon.stop()
        for name, p in self.procs.items():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    # -- talking to the parts
    def vfg(self, *args, timeout=90, input=None):
        return subprocess.run([PC_PY, VFG] + list(args), env=self.pc_env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout, input=input)

    def post_line(self, body, token=None, port=None):
        conn = http.client.HTTPConnection("127.0.0.1", port or self.bridge_port, timeout=10)
        data = body if isinstance(body, (bytes, str)) else json.dumps(body)
        conn.request("POST", "/v1/lines", body=data, headers={
            "Authorization": "Bearer " + (self.token if token is None else token),
            "Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def voice(self, player, text, peak, seconds=1.5, cid=None):
        """A §2 body as the mod sends it, ended now; returns (status, doc, body)."""
        end = now()
        body = {"client_line_id": cid or str(uuid.uuid4()), "player_uuid": str(uuid.uuid4()),
                "player_name": player, "text": text, "started_at": iso(end - dt.timedelta(seconds=seconds)),
                "ended_at": iso(end), "peak_dbfs": peak, "mean_dbfs": peak - 15}
        status, doc = self.post_line(body)
        return status, doc, body

    def fake_jev_api(self, path, body=None):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        data = None if body is None else (body if isinstance(body, bytes) else
                                          (body.encode() if isinstance(body, str) else json.dumps(body).encode()))
        req = urllib.request.Request(self.fake_jev_base + path, data=data, method="GET" if data is None else "POST")
        with opener.open(req, timeout=5) as r:
            return json.loads(r.read())

    def jev_call_about(self, text):
        """The body of the last Jev call that asked about `text`, or {}."""
        mine = [c["body"] for c in self.fake_jev_api("/calls")
                if any("“%s”" % text in q.get("instructions", "")
                       for q in ((c["body"] or {}).get("questions") or {}).values())]
        return mine[-1] if mine else {}

    def console(self, *messages):
        stamp = hms(now())
        with open(self.console_log, "a", encoding="utf-8") as f:
            f.write("".join("[%s] %s\n" % (stamp, m) for m in messages))
        return stamp

    def sql(self, query, params=()):
        conn = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def line_by_text(self, text):
        rows = self.sql("SELECT * FROM lines WHERE text = ?", (text,))
        return rows[0] if len(rows) == 1 else (rows or None)

    def wait_judged(self, text, timeout=8, fed=False):
        def ok():
            r = self.line_by_text(text)
            if isinstance(r, dict) and r["jev_class"] and (not fed or r["fed_at"]):
                return r
            return None
        return wait_for(ok, timeout)

    def feed_rows(self, after=0):
        return self.sql("SELECT seq, at, line, processed_at FROM feed WHERE seq > ? ORDER BY seq", (after,))

    def feed_file(self):
        try:
            with open(self.feed, encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def context_json(self, since=None):
        """`vfg context --json` as the brain reads it at start: [(obj, exit code, stderr)]."""
        r = self.vfg("context", "--json", *(["--since", since] if since else []))
        objs = []
        for raw in r.stdout.splitlines():
            try:
                objs.append(json.loads(raw))
            except ValueError:
                objs.append({"unparsed": raw})
        return objs, r


_UV = {}


def uv_dirs():
    """uv's cache and Pythons, the owner's, so vfg under uv needs no download in a temp HOME."""
    if not _UV:
        for k, cmd in (("UV_CACHE_DIR", ["uv", "cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["uv", "python", "dir"])):
            try:
                v = os.environ.get(k) or subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                        universal_newlines=True).stdout.strip()
            except OSError:
                v = ""
            if v:
                _UV[k] = v
    return _UV


def line_regex(hms_, fields, text):
    return r"^LINE#\d+ %s %s \| %s$" % (re.escape(hms_), re.escape(fields), re.escape(text))


def ai_regex(kind, task, text):
    return r"^AI#\d+ \d\d:\d\d:\d\d %s %s \| %s$" % (kind, task, re.escape(text))


def brain_lookout():
    with open(os.path.join(BRAIN, "prompts", "lookout.md"), encoding="utf-8") as f:
        return f.read().strip()


def feed_lines_with_text(box, text):
    """The feed rows that hold `text` as a line: alone, or as the ask that ends a group (§7)."""
    return [r for r in box.feed_rows() if r["line"].endswith("| " + text) or r["line"].endswith(" … " + text)]


# ======================================================================= paths

def path0(box, rep, ctx):
    rep.path("0", "setup as the brain does it (§12): up, watchers, lookout, Jev's menu, context, watch --after")
    r = box.vfg("up", timeout=150)
    units = [l.split()[0] for l in r.stdout.splitlines() if re.match(r"^\S+\s+active\b", l)]
    rep.check(r.returncode == 0 and "rcon ok" in r.stdout
              and set(units) >= {"minecraft", "vfg-bridge", "vfg-jev", "vfg-engine"},
              "vfg up: the four units active (minecraft, vfg-bridge, vfg-jev, vfg-engine; fake systemctl), RCON "
              "answers", r.stdout + r.stderr)
    r = box.vfg("watchers", "set", WATCHERS)
    want = {"events": WATCHERS.split(","), "players": [], "contains": []}
    with open(box.watch_json) as f:
        got = json.load(f)
    rep.check(r.returncode == 0 and got == want, "vfg watchers set %s writes §6 watch.json" % WATCHERS,
              "exit %s, file %r, stderr %s" % (r.returncode, got, r.stderr))
    rep.check(box.wait_log("bridge", r"watch\.json loaded: events=%s players=0 contains=0" % WATCHERS, 5),
              "the bridge rereads watch.json when it changes")
    lookout = brain_lookout()
    ctx["lookout"] = lookout
    r = box.vfg("lookout", "set", lookout)
    with open(box.lookout, encoding="utf-8") as f:
        got = f.read().strip()
    rep.check(r.returncode == 0 and got == lookout, "vfg lookout set writes brain/prompts/lookout.md to lookout.md",
              r.stderr)
    # The brain's menu for Jev is one `name — what it does` line per action: vfg action list's lines,
    # every action one of the game engine's ops (actions/engine_*.py).
    r = box.vfg("action", "list")
    menu = r.stdout.strip()
    lines = menu.splitlines()
    names = sorted(l.split(" — ", 1)[0] for l in lines)
    files = sorted(f[:-3] for f in os.listdir(os.path.join(REPO, "actions")) if re.match(r"^[a-z][a-z0-9_]*\.py$", f))
    rep.check(r.returncode == 0 and all(re.match(r"^engine_[a-z0-9_]+ — \S", l) for l in lines)
              and names == files and "engine_start" in names,
              "vfg action list: %d `name — what it does` lines, one per actions/engine_*.py, engine_start among "
              "them" % len(lines), "%s\nfiles %s" % (r.stdout[-800:] + r.stderr, files))
    ctx["menu"] = menu
    r = box.vfg("jev", "menu", "set", menu)
    shown = box.vfg("jev", "menu", "show")
    try:
        with open(box.menu, encoding="utf-8") as f:
            on_box = f.read().strip()
    except OSError:
        on_box = None
    rep.check(r.returncode == 0 and on_box == menu and shown.stdout.strip() == menu,
              "vfg jev menu set writes /opt/vfg/menu.md; vfg jev menu show prints it back",
              "set %s %s; show %r" % (r.stdout, r.stderr, shown.stdout[:200]))
    objs, r = box.context_json()
    rep.check(r.returncode == 0 and len(objs) >= 2 and objs[-2] == {"tasks": []} and set(objs[-1]) == {"last_seq"}
              and all(set(o) == {"seq", "at", "line", "processed_at"} for o in objs[:-2]),
              "vfg context --json reads the feed table (%d rows so far), then the tasks (none yet), then the "
              "feed's last seq" % (len(objs) - 2), r.stdout[-600:] + r.stderr)
    start = objs[-1].get("last_seq", 0) if objs else 0
    box.start_watch(start)
    r = box.vfg("feed", "post", "SYS", "", "the brain's watch is on")
    seq = r.stdout.strip()
    got = box.wait_watch(r"^SYS#%s \d\d:\d\d:\d\d \| the brain's watch is on$" % re.escape(seq), 15)
    rep.check(r.returncode == 0 and got and len(got) == 1,
              "vfg watch --after <the context's last seq> follows the feed: a line posted now arrives once",
              open(os.path.join(box.logs, "watch.err")).read()[-800:] + r.stderr)
    rep.check(not os.path.exists(os.path.join(box.vhome, "cursor")), "vfg watch --after reads and writes no cursor")
    box.watch_start_seq = start


def path1(box, rep, ctx):
    rep.path("1", "POST /v1/lines -> lines row -> Jev -> one LINE#seq in vfg watch (§2 §4 §5 §7)")
    end = now()
    start = end - dt.timedelta(milliseconds=2300)
    body = {"client_line_id": str(uuid.uuid4()), "player_uuid": "00000000-0000-4000-8000-000000000001",
            "player_name": "Steve", "text": "build me a castle", "started_at": iso(start), "ended_at": iso(end),
            "peak_dbfs": -8.2, "mean_dbfs": -23.5}
    s, d = box.post_line(body, token="not-the-token")
    rep.check(s == 401, "a wrong token gets 401", "%s %r" % (s, d))
    s, d = box.post_line({})
    rep.check(s == 400, "{} with the right token gets 400 (token checked before the body)", "%s %r" % (s, d))
    s, d = box.post_line(body)
    rep.check(s == 202 and isinstance(d, dict) and set(d) == {"id"} and ULID_RE.match(d.get("id", "")),
              "the §2 example body gets 202 {\"id\": <ULID>}", "%s %r" % (s, d))
    line_id = d.get("id") if isinstance(d, dict) else None
    s2, d2 = box.post_line(body)
    rows = box.sql("SELECT * FROM lines WHERE client_line_id = ?", (body["client_line_id"],))
    rep.check(s2 == 202 and d2 == d and len(rows) == 1, "a repeated client_line_id returns the first id, stores nothing",
              "%s %r, %d rows" % (s2, d2, len(rows)))
    row = rows[0] if rows else {}
    want = {"id": line_id, "at": body["started_at"], "ended_at": body["ended_at"], "player_uuid": body["player_uuid"],
            "player_name": "Steve", "source": "voice", "text": "build me a castle", "peak_dbfs": -8.2,
            "mean_dbfs": -23.5, "client_line_id": body["client_line_id"]}
    rep.check(all(row.get(k) == v for k, v in want.items()), "the lines row holds the §4 columns from the body",
              "%r" % row)
    judged = box.wait_judged("build me a castle", 6, fed=True)
    rep.check(judged and judged["jev_class"] == "to_ai" and judged["jev_confidence"] == 0.9
              and ISO_RE.match(judged["jev_judged_at"] or "") and ISO_RE.match(judged["fed_at"] or ""),
              "Jev judged it to_ai 0.90; jev_judged_at and fed_at set", "%r" % judged)
    rx = line_regex(hms(start), "Steve voice to_ai 0.90 -8dB", "build me a castle")
    got = box.wait_watch(rx, 8)
    time.sleep(0.5)
    got = box.watch_match(rx)
    rep.check(got and len(got) == 1,
              "vfg watch printed it once: LINE#n %s Steve voice to_ai 0.90 -8dB | build me a castle"
              % hms(start), "matching lines: %r\nlast watch lines: %r" % (got, box.watch_lines()[-5:]))
    if got:
        seq = int(FEED_RE.match(got[0]).group(2))
        frow = box.sql("SELECT * FROM feed WHERE seq = ?", (seq,))
        rep.check(frow and frow[0]["line"] == got[0] and frow[0]["at"] == body["started_at"]
                  and frow[0]["processed_at"] is None and got[0] in box.feed_file(),
                  "the feed row and feed.log hold the same line; feed.at is when it was said (started_at); "
                  "processed_at is empty until the brain reads it", "%r" % frow)
        t = box.arrival(got[0])
        if t:
            rep.note("reached vfg watch %.2f s after ended_at (%.2f s after started_at, the HH:MM:SS shown)"
                     % (t - end.timestamp(), t - start.timestamp()))
    ctx["castle_id"] = line_id


def path2(box, rep, ctx):
    rep.path("2", "typed chat, Jev's three classes and watched game events reach vfg watch (§3 §5 §6)")
    r = box.vfg("watchers", "set", "join,death,server", "--players", "Steve", "--contains", "Can't keep up")
    rep.check(r.returncode == 0 and box.wait_log(
        "bridge", r"watch\.json loaded: events=join,death,server players=1 contains=1", 5),
        "vfg watchers set join,death,server --players Steve --contains REGEX; the bridge reloads it",
        r.stdout + r.stderr)
    t0 = iso(now() - dt.timedelta(seconds=1))
    stamp = box.console(
        '[Server thread/INFO]: Done (3.412s)! For help, type "help"',
        '[Server thread/INFO]: System chat: Steve joined the game',
        '[User Authenticator #2/INFO]: UUID of player Alex is 00000000-0000-4000-8000-000000000002',
        '[Server thread/INFO]: Alex[/203.0.113.11:40022] logged in with entity id 313 at (0.5, 70.0, 0.5)',
        '[Server thread/INFO]: Alex joined the game',
        '[Server thread/INFO]: [Not Secure] <Alex> that cave by spawn',
        '[Server thread/INFO]: <Steve> make it taller',
        '[Server thread/INFO]: <Alex> lol',
        '[Server thread/INFO]: <Alex> thanks AI, love it',
        '[Server thread/INFO]: System chat: Steve has made the advancement [Stone Age]',
        '[Server thread/INFO]: System chat: Steve was slain by Zombie',
        '[Server thread/INFO]: System chat: Alex was shot by Skeleton',
        '[Server thread/INFO]: System chat: Alex left the game',
        "[Server thread/WARN]: Can't keep up! Is the server overloaded? Running 2043ms or 40 ticks behind",
        '[Server thread/INFO]: System chat: [Rcon: Summoned new Zombie]',
        '[Server thread/INFO]: [Not Secure] [Steve] hello from say',
        '[Server thread/INFO]: System chat: [Steve: Played sound minecraft:entity.ender_dragon.growl to Steve]',
    )
    fed_game = [
        ("server -", "server started"),
        ("join Steve", "Steve joined the game"),
        ("death Steve", "Steve was slain by Zombie"),
        ("raw -", "[Server thread/WARN]: Can't keep up! Is the server overloaded? Running 2043ms or 40 ticks behind"),
    ]
    for fields, text in fed_game:
        rx = r"^GAME#\d+ %s %s \| %s$" % (stamp, re.escape(fields), re.escape(text))
        got = box.wait_watch(rx, 6)
        rep.check(got and len(got) == 1, "watched -> once in vfg watch: GAME %s | %s" % (fields, text[:40]),
                  "last watch lines: %r" % box.watch_lines()[-8:])
    for who, fields, text in (("Alex", "chat context 0.70 -", "that cave by spawn"),
                              ("Steve", "chat to_ai 0.90 -", "make it taller"),
                              ("Alex", "chat to_ai 0.85 -", "thanks AI, love it")):
        got = box.wait_watch(line_regex(stamp, "%s %s" % (who, fields), text), 8)
        rep.check(got and len(got) == 1, "typed chat -> Jev -> vfg watch: LINE %s %s | %s" % (who, fields, text),
                  "last watch lines: %r" % box.watch_lines()[-8:])
    lol = box.wait_judged("lol", 6)
    time.sleep(1.0)
    rep.check(lol and lol["jev_class"] == "not_relevant" and not lol["fed_at"]
              and not box.watch_match(r"^LINE#\d+ .* \| lol$"),
              "typed chat Jev calls not_relevant (lol) is stored, not fed", "%r" % lol)
    watched_lines = "\n".join(box.watch_lines())
    for kind, text in (("join Alex", "Alex joined the game"), ("advancement", "Steve has made the advancement"),
                       ("death Alex", "Alex was shot by Skeleton"), ("leave", "Alex left the game")):
        rep.check(text not in watched_lines, "unwatched (%s) is not in vfg watch" % kind)
    rep.check("Summoned new Zombie" not in watched_lines and "hello from say" not in watched_lines
              and "Played sound" not in watched_lines, "RCON echoes and noise are ignored (§3)")
    ev = box.sql("SELECT kind, player_name, text FROM events WHERE at >= ? ORDER BY at, rowid", (t0,))
    want = [("server", None, "server started"), ("join", "Steve", "Steve joined the game"),
            ("join", "Alex", "Alex joined the game"),
            ("advancement", "Steve", "Steve has made the advancement [Stone Age]"),
            ("death", "Steve", "Steve was slain by Zombie"), ("death", "Alex", "Alex was shot by Skeleton"),
            ("leave", "Alex", "Alex left the game")]
    got = [(e["kind"], e["player_name"], e["text"]) for e in ev]
    rep.check(got == want, "every event, watched or not, is in events (§3)", "got %r" % got)
    chat = box.sql("SELECT player_name, text FROM lines WHERE source = 'chat' AND at >= ? ORDER BY at, rowid", (t0,))
    rep.check([(c["player_name"], c["text"]) for c in chat] ==
              [("Alex", "that cave by spawn"), ("Steve", "make it taller"), ("Alex", "lol"),
               ("Alex", "thanks AI, love it")],
              "chat lines stored with source=chat, nothing else", "%r" % chat)
    # A to_ai line flushes the batch at once, the context line waiting in it first (§5).
    time.sleep(6)   # the batch above has flushed: the next context line starts one of its own
    _, _, c_body = box.voice("Alex", "the base is past the river", -13.0)
    box.wait_judged("the base is past the river", 6)
    sent = time.time()
    _, _, v_body = box.voice("Steve", "thanks for that", -11.0)
    rx_c = line_regex(c_body["started_at"][11:19], "Alex voice context 0.70 -13dB", "the base is past the river")
    rx_v = line_regex(v_body["started_at"][11:19], "Steve voice to_ai 0.85 -11dB", "thanks for that")
    gc, gv = box.wait_watch(rx_c, 4), box.wait_watch(rx_v, 4)
    took = time.time() - sent
    order = gc and gv and int(FEED_RE.match(gc[0]).group(2)) < int(FEED_RE.match(gv[0]).group(2))
    rep.check(order and took < 3, "a to_ai line flushes the waiting context at once, the context "
              "first (%.1f s, not the 5 s batch)" % took, "%r" % box.watch_lines()[-4:])
    r = box.vfg("watchers", "set", WATCHERS)
    box.wait_log("bridge", r"watch\.json loaded: events=%s players=0 contains=0" % WATCHERS, 5)
    r2 = box.vfg("watchers", "show")
    rep.check(r.returncode == 0 and r2.returncode == 0 and json.loads(r2.stdout) ==
              {"events": WATCHERS.split(","), "players": [], "contains": []},
              "watchers put back to the brain's set; vfg watchers show agrees", r2.stdout + r2.stderr)


def path3(box, rep, ctx):
    rep.path("3", "vfg say: an ai line Jev never judges, and an AI say row in the feed; Jev's state (§4 §5 §7 §8)")
    t0 = iso(now() - dt.timedelta(seconds=1))
    r1 = box.vfg("say", "on it — castle coming up", "--task", "castle_steve")
    r2 = box.vfg("say", "your base is ready", "--to", "Steve")
    rep.check(r1.returncode == 0 and r2.returncode == 0, "vfg say --task castle_steve and vfg say --to exit 0",
              r1.stderr + r2.stderr)
    cmds = box.rcon_commands()
    want1 = 'tellraw @a [{"text":""},{"text":"[AI] ","color":"aqua"},{"text":"on it — castle coming up"}]'
    want2 = 'tellraw Steve [{"text":""},{"text":"[AI] ","color":"aqua"},{"text":"your base is ready"}]'
    rep.check(want1 in cmds and want2 in cmds, "RCON got tellraw @a / tellraw Steve with the aqua [AI] prefix",
              "%r" % cmds[-3:])
    ai = box.sql("SELECT * FROM lines WHERE source = 'ai' AND at >= ? ORDER BY at", (t0,))
    rep.check([(a["player_name"], a["player_uuid"], a["text"]) for a in ai] ==
              [("AI", None, "on it — castle coming up"), ("AI", None, "(to Steve) your base is ready")]
              and all(ISO_RE.match(a["at"]) for a in ai),
              "stored as source=ai, player AI, no uuid, text without prefix, whisper as (to Steve) …", "%r" % ai)
    ctx["ai_ids"] = [a["id"] for a in ai]
    g1 = box.wait_watch(ai_regex("say", "castle_steve", "on it — castle coming up"), 6)
    g2 = box.wait_watch(ai_regex("say", "-", "(to Steve) your base is ready"), 6)
    rep.check(g1 and g2 and len(g1) == len(g2) == 1,
              "each say is fed once, in the same SSH call: AI say castle_steve | … and AI say - | (to Steve) …",
              "%r" % box.watch_lines()[-4:])
    if g1 and ai:
        row = box.sql("SELECT at FROM feed WHERE seq = ?", (int(FEED_RE.match(g1[0]).group(2)),))
        rep.check(row and row[0]["at"] == ai[0]["at"], "the AI say row's at is the ai line's at",
                  "%r vs %r" % (row, ai[0]["at"]))
    # The brain's records of what it did: Jev's history takes action, task and result, not note. A
    # loop's one record is `AI action -`, with no task; a task's rows carry its name, which Jev's
    # history puts in front of the text unless the text starts with it, as a result's does
    # (jev/store.py named()). `AI note` rows are an older brain's: the loop writes none.
    records = [("action", "-", "gave Steve's axe Fire Aspect 5 by item modifier; /enchant refuses a level over "
                               "the maximum"),
               ("task", "castle_steve", "new: a castle at spawn for Steve, about 40x40, stone and oak"),
               ("result", "castle_steve", "castle_steve done: castle built at spawn, 40x40"),
               ("note", "-", "a note Jev never sees")]

    def named(task, text):
        return text if task == "-" or text.startswith(task) else "%s: %s" % (task, text)
    for kind, task, text in records:
        box.vfg("feed", "post", "AI", "%s %s" % (kind, task), text)
    box.wait_watch(ai_regex("note", "-", "a note Jev never sees"), 6)
    box.voice("Alex", "nice one", -18.0)
    judged = box.wait_judged("nice one", 6)
    time.sleep(1.5)
    ai = box.sql("SELECT * FROM lines WHERE source = 'ai' AND at >= ?", (t0,))
    rep.check(judged and all(a["jev_class"] is None and a["jev_confidence"] is None and a["jev_judged_at"] is None
                             and a["fed_at"] is None for a in ai),
              "after Jev's next call the ai lines are still unjudged and unfed by Jev", "%r" % ai)
    use = box.sql("SELECT * FROM usage WHERE ref IN (%s)" % ",".join("?" * len(ctx["ai_ids"])), ctx["ai_ids"])
    rep.check(not use, "no usage row for an ai line", "%r" % use)
    st = box.jev_call_about("nice one").get("state") or {}
    hist = st.get("history", "")
    rep.check("AI (ai): on it — castle coming up" in hist and "AI (ai): (to Steve) your base is ready" in hist,
              "the ai lines are in the history of Jev's next call (§5: the AI's included)", short(hist, 900))
    rep.check(all("AI (%s): %s" % (k, named(task, t)) in hist for k, task, t in records[:3])
              and records[3][2] not in hist,
              "and the brain's action, task and result rows are too, a task's named, its notes not (§5)",
              short(hist, 1500))
    rep.check(st.get("look_out_for") == ctx.get("lookout"), "Jev's state carries lookout.md as vfg lookout set wrote it",
              short(st.get("look_out_for"), 300))
    rep.check(st.get("actions") == ctx.get("menu"), "Jev's state carries menu.md, the brain's action menu, "
              "as vfg jev menu set wrote it", short(st.get("actions"), 300))
    calls = box.fake_jev_api("/calls")
    asked = [q.get("instructions", "") for c in calls for q in ((c["body"] or {}).get("questions") or {}).values()]
    rep.check(not any("castle coming up" in a or "your base is ready" in a for a in asked),
              "no Jev question ever asked about an ai line")
    r = box.vfg("say", "anyone there?", "--to", "Nobody")
    rep.check(r.returncode != 0 and "not online" in r.stderr and not box.watch_match(r"^AI#.* \| \(to Nobody\)"),
              "vfg say --to a player who is not on: non-zero, and nothing stored or fed", r.stdout + r.stderr)


def path4(box, rep, ctx):
    rep.path("4", "vfg history by date / source / player / class / events / feed (§8)")
    date = box.started.strftime("%Y-%m-%d")

    def run(*args):
        r = box.vfg("history", *args)
        return r, [l for l in r.stdout.splitlines() if l]

    def stamp(at):
        return at[:19].replace("T", " ")

    r, out = run("--since", date, "--limit", "1000")
    db = box.sql("SELECT * FROM lines WHERE at >= ? ORDER BY at, rowid", (date + "T00:00:00.000Z",))
    castle = box.line_by_text("build me a castle")
    want = "%s Steve voice to_ai 0.90 | build me a castle" % stamp(castle["at"])
    rep.check(r.returncode == 0 and len(out) == len(db) and want in out,
              "--since <date>: every line of the day, the castle as %r" % want[20:],
              "exit %s, %d vs %d rows, stderr %s" % (r.returncode, len(out), len(db), r.stderr))
    rep.check(all(H_LINE.match(l) for l in out), "every line has history's shape, with Jev's three classes",
              "%r" % [l for l in out if not H_LINE.match(l)])
    ctx["history_out"] = out
    r, out = run("--since", date, "--source", "ai")
    rep.check([l[20:] for l in out] == ["AI ai - | on it — castle coming up", "AI ai - | (to Steve) your base is ready"],
              "--source ai: the two vfg say lines", "%r" % out)
    r, out = run("--since", date, "--source", "chat")
    nchat = len([d for d in db if d["source"] == "chat"])
    rep.check(len(out) == nchat and any(l.endswith("Alex chat context 0.70 | that cave by spawn") for l in out)
              and any(l.endswith("Alex chat not_relevant 0.80 | lol") for l in out)
              and any(l.endswith("Alex chat to_ai 0.85 | thanks AI, love it") for l in out),
              "--source chat: only chat, with Jev's class", "%r" % out)
    r, out = run("--since", date, "--player", "steve", "--source", "voice")
    rep.check(out and all(" Steve voice " in l for l in out) and any(l.endswith("| build me a castle") for l in out),
              "--player steve (any case) --source voice", "%r" % out)
    for cls, texts in (("not_relevant", ("| lol",)), ("to_ai", ("| thanks AI, love it", "| make it taller")),
                       ("context", ("| that cave by spawn",))):
        r, out = run("--since", date, "--class", cls)
        rep.check(out and all(" %s " % cls in l for l in out) and all(any(l.endswith(t) for l in out) for t in texts),
                  "--class %s" % cls, "%r" % out)
    for old in ("direct_ask", "action_oriented", "conversational"):
        r, _ = run("--class", old)
        rep.check(r.returncode == 2 and "--class takes" in r.stderr, "--class %s, an old name, is refused" % old,
                  r.stderr)
    r, out = run("--events", "--since", date, "--limit", "1000")
    ev = box.sql("SELECT * FROM events WHERE at >= ?", (date + "T00:00:00.000Z",))
    rep.check(len(out) == len(ev) and any(l.endswith("death Steve | Steve was slain by Zombie") for l in out)
              and any(l.endswith("join Alex | Alex joined the game") for l in out)
              and any(l.endswith("server - | server started") for l in out) and all(H_EVENT.match(l) for l in out),
              "--events: every event, unwatched ones too", "%r" % out)
    r, out = run("--feed", "--limit", "1000")
    rows = [f["line"] for f in box.feed_rows()]
    rep.check(out == rows and rows == box.feed_file(), "--feed equals the feed table, which equals feed.log",
              "%d vs %d vs %d" % (len(out), len(rows), len(box.feed_file())))
    lo, hi = db[0]["at"], castle["at"]
    r, out = run("--since", lo, "--until", hi, "--limit", "1000")
    inside = [d for d in db if lo <= d["at"] <= hi]
    rep.check(len(out) == len(inside) and out[-1].endswith("| build me a castle"),
              "--since/--until ISO bound the window", "%r vs %d" % (out, len(inside)))
    r, out = run("--until", "2000-01-01")
    rep.check(r.returncode == 0 and out == [], "an empty window prints nothing and exits 0", r.stdout + r.stderr)


def path5(box, rep, ctx):
    rep.path("5", "vfg feed post AI / GAME / SYS round-trip; vfg context and vfg context mark (§7 §8 §9)")

    def post(*args):
        r = box.vfg("feed", "post", *args)
        seq = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
        return r, seq

    def seen(seq, typ, fields, text):
        rows = box.sql("SELECT * FROM feed WHERE seq = ?", (seq,))
        if not rows:
            return False, "no feed row %s" % seq
        h = rows[0]["at"][11:19]
        want = "%s#%d %s %s| %s" % (typ, seq, h, fields + " " if fields else "", text)
        got = box.wait_watch(r"^%s$" % re.escape(want), 6)
        ok = (got and len(got) == 1 and rows[0]["line"] == want and want in box.feed_file()
              and abs(dt.datetime.strptime(rows[0]["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()
                      - time.time()) < 10)
        return ok, "want %r; watch %r; row %r" % (want, got, rows)

    posted = []
    for kind, task, text in (("action", "-", "command: time set night — ok: Set the time to 13000"),
                             ("task", "ice_trail_steve", "new: an ice trail behind Steve as he walks"),
                             ("result", "ice_trail_steve", "ice_trail_steve done: an ice trail runs behind Steve"),
                             ("note", "ice_trail_steve", "stopped ice_trail_steve"),
                             ("note", "-", "Steve and Alex agreed on a truce")):
        r, seq = post("AI", "%s %s" % (kind, task), text)
        ok, why = seen(seq, "AI", "%s %s" % (kind, task), text) if seq else (False, r.stderr)
        rep.check(ok, "vfg feed post AI '%s %s' … prints the seq; once in vfg watch, feed table and feed.log"
                  % (kind, task), why)
        posted.append(seq)
    unread = []  # the rows no read has marked: the brain's AI rows are written processed (vfg_feed.py)
    r, seq = post("GAME", "cast Steve", "Steve cast fireball")
    ok, why = seen(seq, "GAME", "cast Steve", "Steve cast fireball") if seq else (False, r.stderr)
    rep.check(ok, "GAME takes any kind the bridge may feed: GAME cast Steve | …", why)
    unread.append(seq)
    r, seq = post("SYS", "", "smoke test")
    ok, why = seen(seq, "SYS", "", "smoke test") if seq else (False, r.stderr)
    rep.check(ok, "vfg feed post SYS '' 'smoke test' -> SYS#n HH:MM:SS | smoke test", why)
    unread.append(seq)
    r, seq = post("SYS", "", "two\nlines")
    ok, why = seen(seq, "SYS", "", "two lines") if seq else (False, r.stderr)
    rep.check(ok, "a newline in the text becomes a space", why)
    r, seq = post("AI", "note -", "long " + "y" * 700)
    cut = "long " + "y" * 494 + "…"
    ok, why = seen(seq, "AI", "note -", cut) if seq else (False, r.stderr)
    rep.check(ok and len(cut) == 500, "text over 500 characters is cut at 500 with …", why)
    n = len(box.feed_rows())
    for args, what in ((("AI", "done castle_steve", "x"), "AI fields of a kind not in §7"),
                       (("AI", "say 7", "x"), "AI fields without a task's name or -"),
                       (("AI", "task T7", "x"), "AI fields with a task number, retired for its name"),
                       (("AI", "say Galick", "x"), "AI fields with a name that is not a task's"),
                       (("AI", "task " + "a" * 33, "x"), "AI fields with a name past 32 characters"),
                       (("ORCH", "castle_steve done", "x"), "ORCH, which is retired"),
                       (("SYS", "x", "x"), "SYS with fields")):
        r, seq = post(*args)
        rep.check(r.returncode != 0 and len(r.stderr.strip().splitlines()) == 1 and len(box.feed_rows()) == n,
                  "%s: non-zero exit, one stderr line, nothing written" % what, r.stdout + r.stderr)

    # The context: what the brain reads at start, and the marks it sets on every read.
    objs, r = box.context_json()
    table = box.feed_rows()
    since = [t for t in table if t["at"] >= iso(now() - dt.timedelta(hours=6))]
    rep.check(r.returncode == 0 and [(o.get("seq"), o.get("at"), o.get("line"), o.get("processed_at"))
                                     for o in objs[:-2]] == [(t["seq"], t["at"], t["line"], t["processed_at"])
                                                             for t in since]
              and objs[-2:] == [{"tasks": []}, {"last_seq": max(t["seq"] for t in table)}],
              "vfg context --json: every feed row of the last VFG_BRAIN_HISTORY_HOURS, oldest first, "
              "{seq, at, line, processed_at}, then {tasks} (none yet: path 10 writes them), then {last_seq}",
              "%d objects vs %d rows; last two %r; %s" % (len(objs), len(since), objs[-2:], r.stderr))
    plain = box.vfg("context", "--since", "-1m")
    recent = [t["line"] for t in table if t["at"] >= iso(now() - dt.timedelta(seconds=55))]
    rep.check(plain.returncode == 0 and all(l in plain.stdout.splitlines() for l in recent),
              "vfg context --since -1m prints the lines themselves", plain.stdout[-600:] + plain.stderr)
    ai = box.sql("SELECT seq, processed_at FROM feed WHERE seq IN (%s)" % ",".join(str(s) for s in posted if s))
    rep.check(len(ai) == len(posted) and all(ISO_RE.match(m["processed_at"] or "") for m in ai),
              "the brain's AI rows are written processed: no read has to mark them", "%r" % ai)
    seqs = [str(s) for s in unread if s]
    r = box.vfg("context", "mark", *seqs)
    marked = box.sql("SELECT seq, processed_at FROM feed WHERE seq IN (%s)" % ",".join(seqs))
    rep.check(r.returncode == 0 and r.stdout.strip() == str(len(seqs))
              and all(ISO_RE.match(m["processed_at"] or "") for m in marked),
              "vfg context mark %s: prints %d; processed_at set on those rows, in one SSH call"
              % (" ".join(seqs), len(seqs)), r.stdout + r.stderr + "%r" % marked)
    again = box.vfg("context", "mark", *seqs)
    after = box.sql("SELECT seq, processed_at FROM feed WHERE seq IN (%s)" % ",".join(seqs))
    rep.check(again.returncode == 0 and again.stdout.strip() == "0" and after == marked,
              "marking them again prints 0 and keeps the first processed_at", again.stdout + again.stderr)
    objs, _ = box.context_json(since="-1m")
    shown = {o["seq"]: o["processed_at"] for o in objs if "seq" in o}
    rep.check(all(shown.get(int(s)) for s in seqs) and any(v is None for k, v in shown.items() if str(k) not in seqs),
              "vfg context --json shows the marks; the rows nobody read stay null", "%r" % shown)
    bad = box.vfg("context", "mark", "12x")
    rep.check(bad.returncode == 2 and len(bad.stderr.strip().splitlines()) == 1, "vfg context mark 12x is refused",
              bad.stderr)
    # Every processed mark so far, the AI rows' and these: path 7 checks the bridge's restarts keep them.
    ctx["marked"] = [m["seq"] for m in box.sql("SELECT seq FROM feed WHERE processed_at IS NOT NULL")]


def find_java():
    cands = [os.environ.get("VFG_JAVA", "")] + sorted(glob.glob(os.path.expanduser("~/.local/share/jdk/*/bin/java")))
    cands += [shutil.which("java") or ""]
    for c in cands:
        if c and os.access(c, os.X_OK) and os.path.exists(os.path.join(os.path.dirname(c), "javac")):
            return c
    return None


def mod_classpath():
    classes = os.path.join(MOD, "build", "classes", "java", "main")
    g = glob.glob(os.path.expanduser("~/.gradle/caches/modules-2/files-2.1/com.google.code.gson/gson/*/*/gson-*.jar"))
    s = glob.glob(os.path.expanduser("~/.gradle/caches/modules-2/files-2.1/org.slf4j/slf4j-api/*/*/slf4j-api-*.jar"))
    g = [x for x in g if "sources" not in x]
    s = [x for x in s if "sources" not in x]
    if not os.path.exists(os.path.join(classes, "com", "voiceforgaming", "vfg", "net", "BridgeClient.class")) \
            or not g or not s:
        return None, classes
    return os.pathsep.join([classes, sorted(g)[-1], sorted(s)[-1]]), classes


def path8(box, rep, ctx):
    rep.path("8", "the client mod's real BridgeClient/LinePayload against the real bridge (§2)")
    java = find_java()
    cp, classes = mod_classpath()
    if not java or not cp:
        rep.check(False, "a JDK and the mod's built classes are available",
                  "java %r, classpath %r — run client-mod's ./gradlew build first" % (java, cp))
        return
    for name in ("BridgeClient", "LinePayload"):
        src = os.path.join(MOD, "src", "main", "java", "com", "voiceforgaming", "vfg", "net", name + ".java")
        cls = os.path.join(classes, "com", "voiceforgaming", "vfg", "net", name + ".class")
        if os.path.getmtime(cls) < os.path.getmtime(src):
            rep.note("%s.class is older than %s.java: the mod's build is stale" % (name, name))
    url = "http://127.0.0.1:%d/v1/lines" % box.bridge_port
    env = box.base_env()
    env["VFG_MOD_TOKEN"] = box.token
    p = subprocess.run([java, "-cp", cp, os.path.join(HERE, "mod", "ModBridgeContract.java"), url], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
    rec = {}
    for line in p.stdout.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            rec.setdefault(k, []).append(v)
    if not rep.check(p.returncode == 0 and rec.get("done") == ["true"], "the mod's client ran (JDK source launcher, "
                     "mod classes from client-mod/build, nothing compiled to disk)", p.stdout[-800:] + p.stderr[-800:]):
        return
    rep.note("used the mod's real classes, not a re-serialized payload")
    payload = json.loads(rec["line.payload"][0])
    rep.check(list(payload) == ["client_line_id", "player_uuid", "player_name", "text", "started_at", "ended_at",
                                "peak_dbfs", "mean_dbfs"] and ISO_RE.match(payload["started_at"])
              and ISO_RE.match(payload["ended_at"]), "LinePayload serializes exactly §2's eight keys, ISO ms times",
              "%r" % payload)
    deliv = [d.split() for d in rec.get("good.delivered", [])]
    cid = payload["client_line_id"]
    first = [d for d in deliv if d[0] == cid]
    rows = box.sql("SELECT * FROM lines WHERE client_line_id = ?", (cid,))
    rep.check(len(first) == 2 and first[0][1] == first[1][1] and ULID_RE.match(first[0][1]) and len(rows) == 1
              and rows[0]["id"] == first[0][1], "202 with a ULID; the mod's retry (same client_line_id) gets the "
              "same id and stores nothing", "%r, %d rows" % (first, len(rows)))
    if rows:
        r = rows[0]
        rep.check((r["at"], r["ended_at"], r["player_uuid"], r["player_name"], r["text"], r["peak_dbfs"],
                   r["mean_dbfs"], r["source"]) == (payload["started_at"], payload["ended_at"], payload["player_uuid"],
                                                   "Steve", payload["text"], -8.2, -23.5, "voice"),
                  "the stored row is exactly what the mod sent", "%r" % r)
    bad = json.loads(rec["badtoken.payload"][0])
    rep.check(rec.get("bad.token_rejected") == ["true"] and any(d.endswith(" 401") for d in rec.get("bad.dropped", []))
              and not box.sql("SELECT id FROM lines WHERE client_line_id = ?", (bad["client_line_id"],)),
              "a wrong token: 401, the mod marks the token rejected and drops the line; nothing stored",
              "%r" % rec)
    rep.check(rec.get("conn.good", [""])[0].startswith("ConnectionResult[check=TOKEN_ACCEPTED, status=400")
              and rec.get("conn.bad", [""])[0].startswith("ConnectionResult[check=TOKEN_REJECTED, status=401"),
              "the settings screen's connection test: {} -> 400 accepted, wrong token -> 401 rejected",
              "%r %r" % (rec.get("conn.good"), rec.get("conn.bad")))
    edge_ok = []
    for tag in ("nouuid", "long", "quoted"):
        pl = json.loads(rec[tag + ".payload"][0])
        rr = box.sql("SELECT * FROM lines WHERE client_line_id = ?", (pl["client_line_id"],))
        want_uuid = None if pl["player_uuid"] == "" else pl["player_uuid"]
        edge_ok.append(len(rr) == 1 and rr[0]["text"] == pl["text"] and rr[0]["player_uuid"] == want_uuid)
    rep.check(all(edge_ok) and not rec.get("good.dropped"),
              "edge payloads accepted and stored as sent (2000 characters, quotes and unicode, 0 dB); the mod's "
              "blank player_uuid stored as NULL", "%r %r" % (edge_ok, rec.get("good.dropped")))
    start = payload["started_at"][11:19]
    got = box.wait_watch(line_regex(start, "Steve voice to_ai 0.90 -8dB", "build a tower by the lake"), 8)
    rep.check(got and len(got) == 1, "the mod's line goes on through Jev: once in vfg watch as "
              "LINE#n %s Steve voice to_ai 0.90 -8dB | build a tower by the lake" % start,
              "%r" % box.watch_lines()[-5:])
    long_pl = json.loads(rec["long.payload"][0])
    cut = long_pl["text"][:499] + "…"
    got = box.wait_watch(line_regex(long_pl["started_at"][11:19], "Steve voice to_ai 0.90 -12dB", cut), 8)
    rep.check(got and len(got) == 1, "its 2000-character line reaches vfg watch cut to 500 characters with …",
              "%r" % [short(l, 120) for l in box.watch_lines()[-4:]])


def path9(box, rep, ctx):
    rep.path("9", "the brain's context.parse vs what the real feed writer and its producers emit (§5 §7 §9)")
    # (a) a real group, through the real filter and the real feed writer: Steve's earlier line goes
    # with his ask; Alex's, said between them, never does. The fake Jev says yes to any line with
    # "big one" that a gather call asks about, so Alex's would join if it were ever asked.
    box.voice("Steve", "the big one", -10.4)
    box.voice("Alex", "is that the big one?", -12.0)
    a, b = box.wait_judged("the big one", 6), box.wait_judged("is that the big one?", 6)
    if not rep.check(a and b and a["jev_class"] == b["jev_class"] == "not_relevant",
                     "setup: Steve's 'the big one' and Alex's 'is that the big one?' judged not_relevant",
                     "%r %r" % (a, b)):
        return
    # the ask's routing call, canned in its own terms: the AI by name -> to_ai, to_the_ai's confidence
    box.fake_jev_api("/next", {"answers": {
        "to_the_ai": {"type": "choice", "choice": "says_its_name", "confidence": 0.87},
        "keep": {"type": "choice", "choice": "ignore_it", "confidence": 0.93}},
        "usage": {"input_tokens": 1500}})
    s, d, ask = box.voice("Steve", "make it bigger", -9.0)
    group = "the big one … make it bigger"
    gg = box.wait_watch(line_regex(ask["started_at"][11:19], "Steve voice to_ai 0.87 -9dB", group), 8)
    frow = box.sql("SELECT at FROM feed WHERE seq = ?", (int(FEED_RE.match(gg[0]).group(2)),)) if gg else []
    rep.check(gg and len(gg) == 1 and frow and frow[0]["at"] == ask["started_at"],
              "one LINE for the ask, under its fields and time, its text the group: "
              "'Steve voice to_ai 0.87 -9dB | %s'" % group, "%r" % box.watch_lines()[-4:])
    calls = [c["body"] for c in box.fake_jev_api("/calls")
             if any("part_of_the_ask" in (q.get("criteria") or {})
                    and "“make it bigger”" in q.get("instructions", "")
                    for q in ((c["body"] or {}).get("questions") or {}).values())]
    asked = [q["instructions"] for q in calls[-1]["questions"].values()] if calls else []
    use = box.sql("SELECT * FROM usage WHERE ref = ?", ("gather:%s" % (d or {}).get("id"),))
    rep.check(len(calls) == 1 and all(" said by Steve before it " in q for q in asked)
              and any("“the big one”" in q for q in asked) and not any("is that the big one?" in q for q in asked)
              and len(use) == 1 and use[0]["component"] == "jev" and use[0]["input_tokens"]
              and abs(use[0]["cost_usd"] - use[0]["input_tokens"] * 4.2e-8) < 1e-12,
              "one gather call, about Steve's own unsent lines only (%d, Alex's never), and its usage row "
              "ref gather:<ask id>" % len(asked), "calls %d, asked %r, usage %r" % (len(calls), asked, use))
    a, b = box.line_by_text("the big one"), box.line_by_text("is that the big one?")
    rep.check(a["jev_class"] == b["jev_class"] == "not_relevant" and not a["fed_at"] and not b["fed_at"]
              and not [r for r in box.feed_rows() if "is that the big one?" in r["line"]],
              "the gathered line keeps its class and is not fed on its own; Alex's line is in no feed row",
              "%r %r" % (a, b))
    # (b) real fail-open: the jev-down case's lines
    box.fake_jev_api("/mode", "fail")
    s, d, c_body = box.voice("Alex", "hello everyone", -21.4)
    g_open = box.wait_watch(r"^SYS#\d+ \d\d:\d\d:\d\d \| %s$" % re.escape(SYS_OPEN), 20)
    g_c = box.wait_watch(line_regex(c_body["started_at"][11:19], "Alex voice context - -21dB", "hello everyone"), 8)
    ok = g_open and g_c and int(FEED_RE.match(g_open[0]).group(2)) < int(FEED_RE.match(g_c[0]).group(2))
    rep.check(ok, "Jev down 10 s: one SYS 'jev unavailable…', then 'Alex voice context - -21dB | hello everyone'",
              "%r" % box.watch_lines()[-4:])
    box.fake_jev_api("/mode", "ok")
    g_back = box.wait_watch(r"^SYS#\d+ \d\d:\d\d:\d\d \| %s$" % re.escape(SYS_BACK), 12)
    stamp = box.console("[Server thread/INFO]: <Steve> make it wider")
    g_n = box.wait_watch(line_regex(stamp, "Steve chat to_ai 0.90 -", "make it wider"), 8)
    rep.check(g_back and g_n, "Jev back: one SYS 'jev back — filtering again', next line judged normally",
              "%r" % box.watch_lines()[-4:])

    # (c) every line the real vfg watch printed this run, read with §7's grammar and with the brain's parse
    context = load_module("brain_context", os.path.join(BRAIN, "brain", "context.py"))
    grammar_bad, parse_bad = [], []
    for t, line in box.watch_seen:
        m = FEED_RE.match(line)
        if not m:
            grammar_bad.append((line, "not TYPE#seq HH:MM:SS fields | text"))
            continue
        typ, seq, h, fields, text = m.group(1), int(m.group(2)), m.group(3), m.group(4) or "", m.group(5)
        if typ == "LINE" and not C_LINE.match(fields) or typ == "GAME" and not C_GAME.match(fields) \
                or typ == "AI" and not C_AI.match(fields) or typ == "SYS" and fields:
            grammar_bad.append((line, "fields outside §7's grammar"))
        row = context.parse(line, now=dt.datetime.fromtimestamp(t, UTC))
        why = parse_wrong(row, typ, seq, h, fields, text)
        if why:
            parse_bad.append((line, why))
    rep.check(not grammar_bad, "all %d lines the real vfg watch printed this run are in §7's grammar"
              % len(box.watch_seen), "\n".join("%s  (%s)" % x for x in grammar_bad))
    rep.check(not parse_bad, "and context.parse reads every one as the row it is: its seq, its time, its type, "
              "who, how and Jev's class (LINE), the kind and the player (GAME), the kind and the task (AI)",
              "\n".join("%s  (%s)" % x for x in parse_bad[:20]))
    types = sorted({FEED_RE.match(l).group(1) for _, l in box.watch_seen if FEED_RE.match(l)})
    rep.check(types == ["AI", "GAME", "LINE", "SYS"], "the run fed every type: %s" % ", ".join(types))
    grouped = [context.parse(l, now=now()) for l in (gg or [])]
    rep.check(grouped and (grouped[0].who, grouped[0].source, grouped[0].cls, grouped[0].text)
              == ("Steve", "voice", "to_ai", group)
              and grouped[0].show() == "%s Steve (to_ai): %s" % (ask["started_at"][11:19], group),
              "context.parse reads the group LINE as one row: Steve's, the ask's class, the whole group its "
              "text, shown to a model as one line", "%r" % grouped)
    opened = [context.parse(l, now=now()) for l in (g_c or [])]
    rep.check(opened and opened[0].cls == "context" and opened[0].who == "Alex" and opened[0].source == "voice",
              "a line passed open (`context - -21dB`) reads as context, for the batch call", "%r" % opened)

    # (d) the brain's start: `vfg context --json` into a Context, as load_context builds it
    objs, r = box.context_json()
    rows = [context.parse(o["line"], seq=o["seq"], at=o["at"], processed=o["processed_at"] is not None)
            for o in objs if "seq" in o]
    c = context.Context()
    added = [c.add(x) for x in rows if x is not None]
    table = {t["seq"]: t for t in box.feed_rows()}
    wrong = [(x.seq, x.type, x.at, x.processed) for x in rows
             if x is None or x.at != table[x.seq]["at"] or x.type not in ("LINE", "GAME", "AI", "SYS")
             or x.processed != (x.type == "AI" or table[x.seq]["processed_at"] is not None)]
    rep.check(r.returncode == 0 and rows and all(added) and not wrong and c.last_seq == max(table),
              "vfg context --json builds the brain's Context: %d rows, each at its own `at`, the read ones and the "
              "brain's AI rows processed, the last seq the feed's" % len(rows), "%r" % wrong[:10])
    unread = {x.seq for x in c.unprocessed()}
    marked = set(ctx.get("marked") or [])
    rep.check(unread and not (unread & marked) and all(table[s]["processed_at"] is None for s in unread),
              "Context.unprocessed() is exactly the rows no read has marked (%d of them)" % len(unread),
              "%r" % sorted(unread)[:20])
    blocks = c.blocks()
    cached = c.cached("OPENING", 5)
    rep.check(sum(len(b) for b in blocks) == sum(1 for x in c.rows if x.shown)
              and all(len({x.minute for x in b}) == 1 for b in blocks)
              and len(cached) == len(blocks) + 1 and sum(1 for _, mark in cached if mark) <= 4,
              "it shows them in %d minute-blocks for the prompt cache, at most four marks" % len(blocks))


def parse_wrong(row, typ, seq, h, fields, text):
    """Why the brain's Row is not the line it was parsed from, or ""."""
    if row is None:
        return "parsed to nothing"
    if (row.seq, row.type, row.hms) != (seq, typ, h):
        return "seq, type or time %r" % ((row.seq, row.type, row.hms),)
    if seq and row.text != text:
        return "text %r" % row.text
    f = fields.split()
    if typ == "LINE" and ((row.who, row.source, row.cls) != tuple(f[:3]) or row.processed):
        return "LINE fields %r" % ((row.who, row.source, row.cls, row.processed),)
    if typ == "GAME" and (row.kind, row.who or "-") != (f[0], f[1]):
        return "GAME fields %r" % ((row.kind, row.who),)
    if typ == "AI" and ((row.kind, row.task or "-") != (f[0], f[1]) or not row.processed):
        return "AI fields %r" % ((row.kind, row.task, row.processed),)
    return ""


def path7(box, rep, ctx):
    rep.path("7", "killing and restarting Jev (and the watch, and the bridge) loses and repeats nothing")
    # 7a: SIGTERM while a context line waits in its batch
    s, d, body = box.voice("Alex", "the cave is near our base", -12.0)
    r = box.wait_judged("the cave is near our base", 6)
    pending = r and r["jev_class"] == "context" and not r["fed_at"]
    t = time.monotonic()
    rc = box.stop("jev", signal.SIGTERM, 10)
    took = time.monotonic() - t
    r = box.line_by_text("the cave is near our base")
    got = box.wait_watch(line_regex(body["started_at"][11:19], "Alex voice context 0.70 -12dB",
                                    "the cave is near our base"), 4)
    rep.check(pending and rc == 0 and took < 5 and r["fed_at"] and got and len(got) == 1,
              "SIGTERM with a context line waiting in its batch: exit 0 in %.2f s, the line fed once on the way out"
              % took, "pending=%r rc=%r row=%r watch=%r" % (pending, rc, r, got))
    # 7b: lines that arrive while Jev is down
    e1 = box.voice("Steve", "build a wall here", -9.6)[2]
    e2 = box.voice("Alex", "our base is by the river", -14.2)[2]
    box.voice("Alex", "haha", -20.0)
    stamp = box.console("[Server thread/INFO]: <Steve> make it rain")
    time.sleep(1.0)
    rep.check(all(box.line_by_text(x)["jev_class"] is None for x in ("build a wall here", "haha", "make it rain")),
              "while Jev is down the lines wait unjudged")
    box.start_jev()
    oks = [box.wait_watch(line_regex(e1["started_at"][11:19], "Steve voice to_ai 0.90 -10dB",
                                     "build a wall here"), 8),
           box.wait_watch(line_regex(e2["started_at"][11:19], "Alex voice context 0.70 -14dB", "our base is by the river"), 8),
           box.wait_watch(line_regex(stamp, "Steve chat to_ai 0.90 -", "make it rain"), 8)]
    haha = box.wait_judged("haha", 4)
    rep.check(all(o and len(o) == 1 for o in oks) and haha and haha["jev_class"] == "not_relevant" and not haha["fed_at"],
              "restarted, it judges the backlog in order and feeds each kept line once", "%r" % oks)
    # 7c: SIGKILL with lines judged, lines waiting, and a Jev call in flight
    texts = ["build stairs to the roof", "the cave goes deep", "ok sure", "make some torches", "brb", "near the base"]
    for i, x in enumerate(texts[:3]):
        box.voice("Steve" if i % 2 else "Alex", x, -11.0 - i)
    wait_for(lambda: all((box.line_by_text(x) or {}).get("jev_class") for x in texts[:3]), 5)
    box.fake_jev_api("/mode", "hang")          # the next call hangs: it is in flight when Jev dies
    for i, x in enumerate(texts[3:], 3):
        box.voice("Steve" if i % 2 else "Alex", x, -11.0 - i)
    in_flight = wait_for(lambda: any("“make some torches”" in q.get("instructions", "")
                                     for c in box.fake_jev_api("/calls")
                                     for q in ((c["body"] or {}).get("questions") or {}).values()), 5)
    judged_at_kill = sum(1 for x in texts if (box.line_by_text(x) or {}).get("jev_class"))
    rc = box.stop("jev", signal.SIGKILL, 5)
    box.fake_jev_api("/mode", "ok")
    box.start_jev()
    wait_for(lambda: all((box.line_by_text(x) or {}).get("jev_class") for x in texts), 10)
    time.sleep(6.5)   # a context-only batch flushes VFG_BATCH_SECONDS (5) after its first verdict
    counts = {x: len(feed_lines_with_text(box, x)) for x in texts}
    kept = {x for x in texts if (box.line_by_text(x) or {}).get("jev_class") in FED}
    uses = {x: len(box.sql("SELECT id FROM usage WHERE ref = ?", (box.line_by_text(x)["id"],))) for x in texts}
    rep.check(in_flight and rc == -signal.SIGKILL and judged_at_kill == 3
              and all(counts[x] == (1 if x in kept else 0) for x in texts) and all(v == 1 for v in uses.values()),
              "SIGKILL with 3 lines judged, 3 waiting and a Jev call in flight, restart: each judged once (one "
              "usage row), each kept line fed once", "in flight %r, judged at kill %d, feed counts %r, usage %r"
              % (in_flight, judged_at_kill, counts, uses))
    # 7d: SIGKILL at the worst moment: after the feed write, before fed_at
    box.stop("jev", signal.SIGTERM, 10)
    open(box.crash_flag, "w").close()
    box.start_jev(lib=os.path.join(HERE, "shims", "crash_after_feed"),
                  VFG_HARNESS_REAL_FEED=os.path.join(box.lib, "vfg_feed.py"), VFG_HARNESS_CRASH_FLAG=box.crash_flag)
    s, d, body = box.voice("Steve", "make a bridge over the river", -7.8)
    died = wait_for(lambda: box.procs["jev"].poll() is not None, 8)
    r = box.line_by_text("make a bridge over the river")
    before = len(feed_lines_with_text(box, "make a bridge over the river"))
    box.start_jev()
    time.sleep(3)
    after = len(feed_lines_with_text(box, "make a bridge over the river"))
    in_watch = box.watch_match(line_regex(body["started_at"][11:19], "Steve voice to_ai 0.90 -8dB",
                                          "make a bridge over the river"))
    rep.check(died and box.procs["jev"].returncode is None and before == 1 and r and r["fed_at"] is None
              and after == 1 and len(in_watch) == 1,
              "SIGKILL right after append_batch, before fed_at is written: the restart does not feed it again",
              "Jev killed after writing LINE (fed_at still NULL: %r); after restart the feed holds it %d time(s) and "
              "vfg watch printed it %d time(s): %r" % (r and r["fed_at"], after, len(in_watch), in_watch))
    # 7e: the same, with a group: the restart knows the group's line as the ask's (it ends with it)
    box.stop("jev", signal.SIGTERM, 10)
    open(box.crash_flag, "w").close()
    box.start_jev(lib=os.path.join(HERE, "shims", "crash_after_feed"),
                  VFG_HARNESS_REAL_FEED=os.path.join(box.lib, "vfg_feed.py"), VFG_HARNESS_CRASH_FLAG=box.crash_flag)
    box.voice("Steve", "the big one by the hill", -11.0)
    box.wait_judged("the big one by the hill", 6)
    s, d, body = box.voice("Steve", "make that one taller", -8.0)
    group = "the big one by the hill … make that one taller"
    died = wait_for(lambda: box.procs["jev"].poll() is not None, 8)
    r = box.line_by_text("make that one taller")
    box.start_jev()
    known = box.wait_log("jev", r"%s was already fed as LINE#\d+ before a restart" % (d or {}).get("id"), 8)
    in_watch = box.wait_watch(line_regex(body["started_at"][11:19], "Steve voice to_ai 0.90 -8dB", group), 8)
    rep.check(died and r and r["fed_at"] is None and known and in_watch and len(in_watch) == 1
              and len(feed_lines_with_text(box, "make that one taller")) == 1,
              "SIGKILL right after a group's feed write, before fed_at: the restart finds the group as the ask's "
              "line and does not feed the ask again", "fed_at at the kill %r; the restart's look-up %r; vfg watch %r"
              % (r and r["fed_at"], bool(known), in_watch))
    # 7f: SIGKILL between an ask's verdict and its feed, its group waiting (the feed's lock held)
    lock = open(box.feed + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        box.voice("Steve", "that big one by the lake", -11.0)
        box.wait_judged("that big one by the lake", 6)
        s, d, body = box.voice("Steve", "make it twice as big", -9.0)
        held = box.wait_log("jev", r"gather %s Steve · \d+ asked, 1 yes" % (d or {}).get("id"), 8)
        r = box.line_by_text("make it twice as big")
        box.stop("jev", signal.SIGKILL, 5)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    box.start_jev()
    got = box.wait_watch(line_regex(body["started_at"][11:19], "Steve voice to_ai 0.90 -9dB",
                                    "make it twice as big"), 8)
    uses = [len(box.sql("SELECT id FROM usage WHERE ref = ?", (ref,))) for ref in (r["id"], "gather:" + r["id"])]
    rep.check(held and r["jev_class"] == "to_ai" and r["fed_at"] is None and got and len(got) == 1
              and len(feed_lines_with_text(box, "make it twice as big")) == 1 and uses == [1, 1],
              "SIGKILL while an ask's group waits for the feed: restarted, the ask is fed once, alone (a group is "
              "held in memory only), and neither judged nor gathered again",
              "gathered %r, row at the kill %r, vfg watch %r, usage rows %r" % (bool(held), r, got, uses))
    # 7g: the brain's watch killed and followed again from its own place: nothing lost, nothing twice
    rc = box.stop("watch", signal.SIGTERM, 10)
    last = box.last_printed_seq()
    r = box.vfg("feed", "post", "SYS", "", "posted while vfg watch was down")
    objs, _ = box.context_json(since="-1m")
    down = [o for o in objs if o.get("line", "").endswith("| posted while vfg watch was down")]
    rep.check(down and down[0]["processed_at"] is None and down[0]["seq"] > last,
              "the line posted while the watch was down is in vfg context --json, unread, after the watch's last "
              "seq (#%d): a brain started now reads it there" % last, "%r" % down)
    box.start_watch(last)
    got = box.wait_watch(r"^SYS#%s \d\d:\d\d:\d\d \| posted while vfg watch was down$" % r.stdout.strip(), 10)
    rep.check(rc == 0 and got and len(got) == 1, "vfg watch SIGTERMed (exit 0) and followed again with --after its "
              "last seq: the line written while it was down arrives once", "rc %r, %r" % (rc, got))
    # 7h: the bridge restarted: console lines and a retried POST are stored once
    rc = box.stop("bridge", signal.SIGTERM, 10)
    stamp = box.console("[Server thread/INFO]: <Steve> typed while the bridge was down",
                        "[Server thread/INFO]: System chat: Steve drowned")
    cid = str(uuid.uuid4())
    try:
        box.voice("Steve", "said while the bridge was down", -16.0, cid=cid)
        refused = False
    except OSError:
        refused = True
    box.start_bridge()
    s1, d1, _ = box.voice("Steve", "said while the bridge was down", -16.0, cid=cid)
    s2, d2, _ = box.voice("Steve", "said while the bridge was down", -16.0, cid=cid)
    g = box.wait_watch(r"^GAME#\d+ %s death Steve \| Steve drowned$" % stamp, 6)
    box.stop("bridge", signal.SIGTERM, 10)
    box.start_bridge()
    time.sleep(2)
    n_chat = len(box.sql("SELECT id FROM lines WHERE text = 'typed while the bridge was down'"))
    n_ev = len(box.sql("SELECT id FROM events WHERE text = 'Steve drowned'"))
    n_voice = len(box.sql("SELECT id FROM lines WHERE client_line_id = ?", (cid,)))
    rep.check(rc == 0 and refused and s1 == s2 == 202 and d1 == d2 and n_chat == 1 and n_ev == 1 and n_voice == 1
              and g and len(g) == 1 and len(box.watch_match(r"^GAME#\d+ .* \| Steve drowned$")) == 1,
              "bridge stopped, console written and a POST refused meanwhile, restarted twice: each stored once, "
              "the watched death fed once", "rc=%r refused=%r posts=%r/%r chat=%d events=%d voice=%d game=%r"
              % (rc, refused, s1, s2, n_chat, n_ev, n_voice, g))
    marks = box.sql("SELECT seq FROM feed WHERE processed_at IS NOT NULL")
    written = {t["seq"] for t in box.feed_rows() if t["line"].startswith("AI#")}  # written processed
    rep.check(sorted(m["seq"] for m in marks) == sorted(set(ctx.get("marked") or []) | written),
              "the bridge's restarts kept the processed marks, and set none of their own", "%r" % marks)


def path6(box, rep, ctx):
    rep.path("6", "usage rows: one per Jev call for component jev; the brain's steps, the conversation agent's, "
                  "the router's and an implementer's; an older brain's orchestrator and planner rows")
    judged = box.sql("SELECT id, text, jev_confidence FROM lines WHERE source != 'ai' AND jev_class IS NOT NULL")
    bad = []
    for r in judged:
        use = box.sql("SELECT * FROM usage WHERE ref = ?", (r["id"],))
        want = 0 if r["jev_confidence"] is None else 1
        if len(use) != want:
            bad.append((r["text"][:30], len(use), want))
            continue
        for u in use:
            if not (u["component"] == "jev" and u["model"] == "jev-latest" and u["input_tokens"] and u["input_tokens"] > 0
                    and u["output_tokens"] == 0 and abs(u["cost_usd"] - u["input_tokens"] * 4.2e-8) < 1e-12
                    and ISO_RE.match(u["at"])):
                bad.append((r["text"][:30], u))
    rep.check(judged and not bad, "each of %d judged lines has exactly one jev usage row (none when passed open), "
              "model jev-latest, tokens, output 0, cost = tokens x $42/1e9" % len(judged), "%r" % bad)
    probes = box.sql("SELECT * FROM usage WHERE ref = 'probe'")
    rep.check(probes and all(p["component"] == "jev" for p in probes), "the probe that found Jev back wrote its own "
              "usage row (ref=probe)", "%r" % probes)
    stray = box.sql("SELECT u.ref FROM usage u JOIN lines l ON l.id = u.ref WHERE l.source = 'ai'")
    rep.check(not stray, "no usage row points at an ai line")
    # Rows as `vfg usage add` takes them: brain/implementers/usage.py's usage_args() and the like. The brain's
    # own rows are structured()'s (below): a step of a brain loop is `#<seq> s<n>` for a line, `<task> s<n>` for
    # what a report left; the conversation agent's (`respond`) and the router's are the line's `#<seq>`, or a
    # report's task. A box that ran an older brain also holds `orchestrator` rows (and `planner` ones), which the
    # table and vfg still take.
    rows = [
        (["--component", "brain", "--model", "google/gemini-3.8-flash", "--in", "31512", "--out", "210", "--cost",
          "0.003834", "--cache-read", "29216", "--ref", "#812 s1"],
         ("brain", "google/gemini-3.8-flash", 31512, 210, 0.003834, 29216, None, "#812 s1")),
        (["--component", "brain", "--model", "google/gemini-3.8-flash", "--in", "33040", "--out", "120", "--cost",
          "0.002700", "--cache-read", "31800", "--ref", "#812 s2"],
         ("brain", "google/gemini-3.8-flash", 33040, 120, 0.0027, 31800, None, "#812 s2")),
        (["--component", "brain", "--model", "google/gemini-3.8-flash", "--in", "34400", "--out", "90", "--cost",
          "0.004100", "--cache-read", "30200", "--ref", "castle_steve s1"],
         ("brain", "google/gemini-3.8-flash", 34400, 90, 0.0041, 30200, None, "castle_steve s1")),
        (["--component", "respond", "--model", "~deepseek/deepseek-v4-flash-latest", "--in", "7100", "--out", "30",
          "--cost", "0.000410", "--cache-read", "6900", "--ref", "#813"],
         ("respond", "~deepseek/deepseek-v4-flash-latest", 7100, 30, 0.00041, 6900, None, "#813")),
        (["--component", "router", "--model", "deepseek/deepseek-v4.1-flash", "--in", "30500", "--out", "60",
          "--cost", "0.000420", "--cache-read", "29800", "--ref", "#814"],
         ("router", "deepseek/deepseek-v4.1-flash", 30500, 60, 0.00042, 29800, None, "#814")),
        (["--component", "orchestrator", "--model", "google/gemini-3.8-flash", "--in", "3120", "--out", "96",
          "--cost", "0.000690", "--cache-read", "2880", "--ref", "#790"],
         ("orchestrator", "google/gemini-3.8-flash", 3120, 96, 0.00069, 2880, None, "#790")),
        (["--component", "implementer", "--model", "claude-haiku-5-5", "--in", "48000", "--out", "2100", "--cost",
          "0.081000", "--cache-read", "41000", "--cache-write", "6000", "--ref", "castle_steve"],
         ("implementer", "claude-haiku-5-5", 48000, 2100, 0.081, 41000, 6000, "castle_steve")),
    ]
    for args, want in rows:
        r = box.vfg("usage", "add", *args)
        row = box.sql("SELECT * FROM usage WHERE id = ?", (int(r.stdout.strip()) if r.stdout.strip().isdigit() else -1,))
        got = row and (row[0]["component"], row[0]["model"], row[0]["input_tokens"], row[0]["output_tokens"],
                       row[0]["cost_usd"], row[0]["cache_read_tokens"], row[0]["cache_write_tokens"], row[0]["ref"])
        rep.check(r.returncode == 0 and got == want, "vfg usage add --component %s … --ref %r writes the §4 row, "
                  "its cached tokens too, and prints its id" % (want[0], want[-1]), r.stdout + r.stderr + "%r" % (got,))
    # structured()'s rows (lib/py/vfg_llm.py usage_row and record): queued on the PC, a failed call's with its cost
    # (NULL for one that timed out with an attempt in flight), sent by vfg usage flush into the columns the bridge
    # added on start: the brain's steps, a failed one among them, an older brain's planner row, an alias call's
    queued = [dict(at=iso(now()), component=c, model=m, input_tokens=i, output_tokens=o, cost_usd=cost, ref=ref,
                   cache_read_tokens=0, cache_write_tokens=None, ok=ok, error=err, ms=ms)
              for c, m, i, o, cost, ref, ok, err, ms in (
                  ("brain", "google/gemini-3.8-flash", 31200, 96, 0.0069, "#816 s1", 1, None, 2900),
                  ("brain", "google/gemini-3.8-flash", 33400, 0, None, "#816 s2", 0, "no answer from "
                   "google/gemini-3.8-flash within 20 s; $0.002310 spent before it, the attempt in flight not counted",
                   20012),
                  ("brain", "google/gemini-3.8-flash", 34100, 60, 0.0031, "galick_gun_styrk s1", 0,
                   "google/gemini-3.8-flash twice failed to match Step: - end: end reply needs the reply", 3100),
                  ("router", "deepseek/deepseek-v4.1-flash", 30100, 55, 0.0004, "#817", 1, None, 1300),
                  ("planner", "~deepseek/deepseek-v4-flash-latest", 2400, 60, 0.00062, "castle_steve #2", 1, None,
                   1800),
                  ("alias", "~deepseek/deepseek-v4-flash-latest", 300, 20, 0.00005, "fire ball", 1, None, 840))]
    with open(os.path.join(box.vhome, "usage.pending.jsonl"), "a") as f:
        f.write("".join(json.dumps(q) + "\n" for q in queued))
    r = box.vfg("usage", "flush")
    got = box.sql("SELECT component, cost_usd, ref, ok, error, ms FROM usage WHERE ref IN ('#816 s1', "
                  "'#816 s2', 'galick_gun_styrk s1', '#817', 'castle_steve #2', 'fire ball') ORDER BY id")
    rep.check(r.returncode == 0 and [tuple(g.values()) for g in got] == [
        (q["component"], q["cost_usd"], q["ref"], q["ok"], q["error"], q["ms"]) for q in queued],
        "vfg usage flush sends structured()'s queued rows, a failed call's with its cost (NULL when not known), ok, "
        "error and ms",
        r.stdout + r.stderr + "%r" % (got,))
    r = box.vfg("usage", "flush")
    rep.check(r.returncode == 0 and r.stdout == "", "nothing queued: vfg usage flush sends nothing", r.stdout + r.stderr)
    n = len(box.sql("SELECT id FROM usage"))
    r = box.vfg("usage", "add", "--component", "dispatcher", "--model", "x", "--in", "1", "--out", "1")
    rep.check(r.returncode == 2 and len(box.sql("SELECT id FROM usage")) == n,
              "a component that is not §4's (dispatcher) is refused, nothing written", r.stderr)


def path10(box, rep, ctx):
    rep.path("10", "vfg task set and vfg task list on the real vfg.db, as the brain writes its tasks; the tasks in "
                   "vfg context --json and vfg brain status (§4 §8)")
    columns = ("name", "description", "player", "status", "line", "brief", "made", "variables", "session", "result",
               "created_at", "updated_at", "closed_at")

    def task_set(row, stdin=False):
        text = row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)
        return box.vfg("task", "set", "-", input=text) if stdin else box.vfg("task", "set", text)

    def stored():
        """The tasks table as the brain reads it back: oldest first, made and variables decoded."""
        rows = box.sql("SELECT * FROM tasks ORDER BY created_at, name")
        for t in rows:
            for k in ("made", "variables"):
                t[k] = None if t[k] is None else json.loads(t[k])
        return rows

    have = box.sql("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tasks'")
    rep.check(have, "the bridge made the tasks table in vfg.db (its ADDED, run at every start)")
    if not have:
        return
    cols = [c["name"] for c in box.sql("PRAGMA table_info(tasks)")]
    rep.check(tuple(cols) == columns, "its columns are the spec's, in order", "%r" % cols)

    # The rows as the brain's Tasks sends them: the whole row on every write, made with its three kinds
    # (the engine classes it wrote, the bindings it made, its grants as player:ability), variables null.
    t0 = now()
    ago = lambda s: iso(t0 - dt.timedelta(seconds=s))  # noqa: E731
    empty = {"content": [], "bindings": [], "grants": []}
    castle = {"name": "castle_steve", "description": "A stone castle at spawn for Steve, about 40×40 — oak doors",
              "player": "Steve", "status": "open", "line": "build me a castle",
              "brief": "a stone castle at spawn, about 40x40, stone and oak", "made": empty, "variables": None,
              "session": None, "result": None, "created_at": ago(90), "updated_at": ago(90), "closed_at": None}
    r = task_set(castle)
    rep.check(r.returncode == 0 and r.stdout.strip() == "added castle_steve" and stored() == [castle],
              "vfg task set '<the whole row>' adds castle_steve: every column as given",
              r.stdout + r.stderr + "%r" % stored())
    raw = box.sql("SELECT made, variables FROM tasks WHERE name = 'castle_steve'")
    rep.check(raw == [{"made": '{"content":[],"bindings":[],"grants":[]}', "variables": None}],
              "made is stored as compact JSON text, variables null until they are read", "%r" % raw)

    done = dict(castle, status="done", description="A stone castle at spawn for Steve, 40x40, with a keep",
                made={"content": ["castle_steve", "castle_gate"], "bindings": ["steve.castle_up"],
                      "grants": ["Steve:castle_gate"]},
                session="5b0c6c1e-6f43-4a51-9d0e-2b8f1f0b9c11",
                result="castle_steve done: castle built at spawn, 40x40",
                updated_at=ago(30), closed_at=ago(30))
    r = task_set(done)
    rep.check(r.returncode == 0 and r.stdout.strip() == "updated castle_steve" and stored() == [done],
              "its report's write updates it: done, its description, made, session, result, closed_at; "
              "created_at kept", r.stdout + r.stderr + "%r" % stored())
    r = task_set(dict(done, status="failed", result="castle_steve failed: an older write", updated_at=ago(60)))
    rep.check(r.returncode == 0 and r.stdout.strip() == "kept castle_steve: a newer write is there"
              and stored() == [done], "a write with an older updated_at changes nothing, and exits 0",
              r.stdout + r.stderr + "%r" % stored())

    trail = {"name": "ice_trail_steve", "description": "An ice trail behind Steve as he walks", "player": "Steve",
             "status": "open", "line": "leave a trail of ice behind me", "brief": "ice under Steve's feet as he walks",
             "made": empty, "variables": None, "session": None, "result": None, "created_at": ago(20),
             "updated_at": ago(20), "closed_at": None}
    r = task_set(trail, stdin=True)
    rep.check(r.returncode == 0 and r.stdout.strip() == "added ice_trail_steve" and stored() == [done, trail],
              "vfg task set - reads the row from stdin", r.stdout + r.stderr + "%r" % stored())

    def listed(*args):
        r = box.vfg("task", "list", *args)
        return r, r.stdout.splitlines()

    r, out = listed()
    lines = ["castle_steve done Steve: A stone castle at spawn for Steve, 40x40, with a keep",
             "ice_trail_steve open Steve: An ice trail behind Steve as he walks"]
    rep.check(r.returncode == 0 and out == lines, "vfg task list: one line a task, oldest first, "
              "<name> <status> <player>: <description>", r.stdout + r.stderr)
    r, out = listed("--open")
    rep.check(r.returncode == 0 and out == lines[1:], "--open: the open one alone", r.stdout + r.stderr)
    r, out = listed("--name", "castle_steve")
    rep.check(r.returncode == 0 and out == lines[:1], "--name castle_steve: that one alone", r.stdout + r.stderr)
    r = box.vfg("task", "list", "--json")
    try:
        got = json.loads(r.stdout)
    except ValueError:
        got = None
    rep.check(r.returncode == 0 and got == [done, trail], "--json: the rows, made and variables decoded",
              r.stdout[-800:] + r.stderr)
    for bad in ("Galick", "cast"):
        r = box.vfg("task", "list", "--name", bad)
        rep.check(r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1,
                  "vfg task list --name %s is refused: exit 2, one stderr line" % bad, r.stdout + r.stderr)

    objs, r = box.context_json()
    rep.check(r.returncode == 0 and len(objs) >= 2 and objs[-2] == {"tasks": [done, trail]}
              and set(objs[-1]) == {"last_seq"},
              "vfg context --json: the rows, then {tasks: every task, oldest first}, then {last_seq}",
              "last two %r; %s" % (objs[-2:], r.stderr))
    r = box.vfg("brain", "status")
    out = r.stdout.splitlines()
    rep.check(r.returncode == 1 and "brain  stopped" in out
              and [l for l in out if l.startswith("open ")] == ["open   ice_trail_steve Steve: An ice trail behind "
                                                                 "Steve as he walks"],
              "vfg brain status: brain stopped (exit 1), and the box's open task, read from vfg.db",
              r.stdout + r.stderr)

    # A write of the keys it changes alone: the rest kept, closed_at following the status.
    r = task_set({"name": "castle_steve", "status": "open", "brief": "make the keep taller"})
    t = (stored() or [{}])[0]
    rep.check(r.returncode == 0 and r.stdout.strip() == "updated castle_steve"
              and t == dict(done, status="open", brief="make the keep taller", closed_at=None,
                            updated_at=t.get("updated_at"))
              and ISO_RE.match(t.get("updated_at") or "") and t["updated_at"] > done["updated_at"],
              "a write of status open and a brief alone: only those change, closed_at goes, updated_at is now",
              r.stdout + r.stderr + "%r" % t)
    r = task_set({"name": "castle_steve", "status": "done", "result": "castle_steve done: the keep is taller"})
    t2 = (stored() or [{}])[0]
    rep.check(r.returncode == 0 and t2.get("status") == "done" and ISO_RE.match(t2.get("closed_at") or "")
              and t2["closed_at"] >= t["updated_at"],
              "done again with no closed_at given: closed_at is now", r.stdout + r.stderr + "%r" % t2)

    before = box.sql("SELECT * FROM tasks ORDER BY name")
    library = sorted(f[:-3] for f in os.listdir(os.path.join(REPO, "engine", "library"))
                     if re.match(r"^[a-z][a-z0-9_]{1,31}\.py$", f))
    taken = [({"name": "engine_start", "description": "a start for Steve", "player": "Steve"}, 1,
              "engine_start is a library class's or an action's name", "a new task named as an action")]
    if library:
        taken.append(({"name": library[0], "description": "one for Steve", "player": "Steve"}, 1,
                      "%s is a library class's or an action's name" % library[0],
                      "a new task named as a library class (engine/library/%s.py)" % library[0]))
    else:
        rep.note("engine/library/ has no class yet: the refusal of a library class's name is not checked")
    for row, code, said, what in taken + [
            ({"name": "frost_bow_alex", "player": "Alex"}, 1, "a new task needs a description",
             "a new task with no description"),
            ({"name": "cast", "description": "x"}, 2, "cast is the bridge's task", "cast, the bridge's task"),
            ({"name": "Galick", "description": "x"}, 2, "", "a name that is not a task's"),
            ({"name": "castle_steve", "colour": "red"}, 2, "", "a key that is not a column"),
            ({"name": "castle_steve", "status": "running"}, 2, "", "a status not open, done or failed"),
            ({"name": "castle_steve", "made": {"scripts": []}}, 2, "", "made with a kind not its three"),
            ("[1, 2]", 2, "", "JSON that is not one object"),
            ("{not json", 2, "", "text that is not JSON")]:
        r = task_set(row)
        rep.check(r.returncode == code and len(r.stderr.strip().splitlines()) == 1 and said in r.stderr
                  and box.sql("SELECT * FROM tasks ORDER BY name") == before,
                  "%s: exit %d, one stderr line, nothing written" % (what, code), r.stdout + r.stderr)


def final_checks(box, rep, ctx):
    rep.resume("7")
    # Everything the feed holds since the watch began reached vfg watch exactly once, in order
    time.sleep(1)
    rows = box.feed_rows(box.watch_start_seq)
    printed = [l for l in box.watch_lines() if FEED_RE.match(l) and not l.startswith("SYS#0 ")]
    pseq = [int(FEED_RE.match(l).group(2)) for l in printed]
    missing = [r["line"] for r in rows if printed.count(r["line"]) != 1]
    rep.check(not missing and pseq == sorted(set(pseq)) and box.feed_file() == [r["line"] for r in box.feed_rows()],
              "all %d feed rows since vfg watch began were printed exactly once, in seq order; feed.log == feed table"
              % len(rows), "not exactly once: %r; order ok %r" % ([short(m, 120) for m in missing],
                                                                 pseq == sorted(set(pseq))))
    kept = box.sql("SELECT text FROM lines WHERE jev_class IN (%s) AND fed_at IS NOT NULL" % ",".join("?" * len(FED)),
                   FED)
    multi = {}
    for k in kept:
        t = k["text"] if len(k["text"]) <= 500 else k["text"][:499] + "…"
        n = len(feed_lines_with_text(box, t))
        if n != 1:
            multi[k["text"][:40]] = n
    rep.check(not multi, "every kept line in the whole run is in the feed exactly once", "%r" % multi)


# ======================================================================== main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ec2-python", default=os.environ.get("VFG_EC2_PYTHON") or shutil.which("python3"))
    ap.add_argument("--keep", action="store_true", help="keep the temp folder and its logs")
    args = ap.parse_args()
    for need in (VFG, os.path.join(BRIDGE, "vfg_feed.py"), os.path.join(JEV, "jev_filter.py"),
                 os.path.join(BRAIN, "brain", "context.py")):
        if not os.path.exists(need):
            print("missing: %s" % need, file=sys.stderr)
            return 2
    root = tempfile.mkdtemp(prefix="vfg-integration-")
    box = Box(root, args.ec2_python)
    rep = Report()
    ver = subprocess.run([args.ec2_python, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True).stdout.strip()
    print("vfg integration: box %s\n  EC2 side (bridge, Jev, remote programs) on %s; PC side on Python %s; vfg = %s"
          % (root, ver, sys.version.split()[0], VFG))
    ctx = {}
    t0 = time.monotonic()
    try:
        box.start_fake_rcon()
        box.deploy()
        box.start_fake_jev()
        box.start_bridge()
        box.start_jev()
        box.watch_tail_thread()
        for fn in (path0, path1, path2, path3, path4, path5, path8, path7, path9, path6, path10, final_checks):
            try:
                fn(box, rep, ctx)
            except Exception:
                rep.crashed(sys.exc_info())
    finally:
        box.close()
    rep.summary()
    print("ran in %.0f s" % (time.monotonic() - t0))
    failed = rep.failed()
    if args.keep:
        print("the box and its logs are kept in %s (logs/: bridge.log, jev.log, watch.out)" % root)
    else:
        shutil.rmtree(root, ignore_errors=True)
        if failed:
            print("run again with --keep to keep the box and its logs")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
