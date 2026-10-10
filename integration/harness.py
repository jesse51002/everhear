#!/usr/bin/env python3
"""End-to-end harness: the REAL bridge, Jev filter and vfg toolkit running together on
this PC, with a temp folder standing in for the EC2 box's /opt/vfg and /opt/mc.

    integration/run.sh [--keep]          (run.sh finds Python 3.12 and the JDK, then runs this)

Real:  bridge (vfg_bridge + vfg_feed, run on the box's Python 3.12), the Jev filter (3.12), the
       vfg toolkit (this repo's toolkit/vfg, under uv; its remote programs run on 3.12 through
       the fake ssh), the client mod's BridgeClient/LinePayload (path 8), the
       conversation's hooks (after-compact.sh, usage.py).
Stand-ins: Minecraft (console.log lines appended here, shapes from the bridge's own
       fixture console-26.3.log; toolkit/tests/fake_rcon.py), SSH (toolkit/tests/fake_ssh,
       which runs the remote command here), systemd (toolkit/tests/bin), Jev's API
       (jev/tests/fake_jev.py), the orchestrator (ORCH lines posted by hand).

Nothing touches ~/.vfg, ~/.ssh or any remote host: HOME and VFG_HOME point into the temp
folder, and every port is on 127.0.0.1. Prints PASS/FAIL per check and per path, and exits
non-zero when anything failed.
"""
import argparse
import datetime as dt
import glob
import http.client
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import urllib.request
import uuid

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BRIDGE = os.path.join(REPO, "bridge")
JEV = os.path.join(REPO, "jev")
TOOLKIT = os.path.join(REPO, "toolkit")
CONV = os.path.join(REPO, "conversation")
MOD = os.path.join(REPO, "client-mod")
VFG = os.path.join(TOOLKIT, "vfg")
PC_PY = sys.executable
UTC = dt.timezone.utc

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
# §7: TYPE#seq HH:MM:SS fields | text  (fields never hold a |)
FEED_RE = re.compile(r"^(LINE|GAME|ORCH|SYS)#(\d+) (\d\d:\d\d:\d\d) (?:([^|]*?) )?\| (.*)$")
# §7's field grammar, written from the contract text
C_LINE = re.compile(r"^[A-Za-z0-9_]{1,16} (voice|chat) (direct_ask|context) (\d\.\d\d|-) (-?\d+dB|-)( was=not_useful)?$")
C_GAME = re.compile(r"^(join|leave|death|advancement|server|raw) ([A-Za-z0-9_]{1,16}|-)$")
C_ORCH = re.compile(r"^T\d+ (progress|done|failed)$")
# The same grammar as conversation/CLAUDE.md states it ("Reading the feed")
M_LINE = re.compile(r"^\S+ (voice|chat) (direct_ask|context) (\d\.\d\d|-) (-?\d+dB|-)( was=not_useful)?$")
M_GAME = re.compile(r"^(join|leave|death|server|raw) (\S+|-)$")
M_ORCH = re.compile(r"^T\d+ (progress|done|failed)$")
# vfg history's output, as conversation/test/fixtures/history.txt shows it
H_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S+ (voice|chat|ai) "
                    r"(?:(?:direct_ask|context|not_useful) (?:\d\.\d\d|-)|-) \| .*$")
H_EVENT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (join|leave|death|advancement|server) \S+ \| .*$")

SYS_OPEN = "jev unavailable — passing every line as context"
SYS_BACK = "jev back — filtering again"


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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def short(text, n=600):
    text = str(text)
    return text if len(text) <= n else text[:n] + " …"


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
        self.state, self.env_file = j("opt", "vfg", "state"), j("opt", "vfg", ".env")
        self.console_log, self.props = j("opt", "mc", "console.log"), j("opt", "mc", "server.properties")
        self.home = j("home")
        self.vhome = j("home", ".vfg")
        self.repo = j("repo")
        self.ec2bin, self.pcbin, self.units, self.logs = j("ec2bin"), j("pcbin"), j("units"), j("logs")
        self.fake_key = j("fake_ssh_key")
        self.watch_out = j("logs", "watch.out")
        self.rcon_log = j("logs", "rcon.jsonl")
        self.crash_flag = j("crash.flag")
        self.ec2_py = ec2_py
        self.token = secrets.token_urlsafe(18)              # the bridge's client token, made up here
        self.jev_key = "fake-jev-key-" + secrets.token_hex(6)  # only the fake Jev ever sees it
        self.rcon_password = secrets.token_hex(8)
        self.rcon_port = free_port()
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
        fd = os.open(self.env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, ("VFG_CLIENT_TOKEN=%s\nTYPESAFE_API_KEY=%s\n" % (self.token, self.jev_key)).encode())
        os.close(fd)
        with open(self.props, "w") as f:
            f.write("#Minecraft server properties\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n"
                    "server-port=25565\n" % (self.rcon_port, self.rcon_password))
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
        for u in ("minecraft", "vfg-bridge", "vfg-jev"):
            with open(os.path.join(self.units, u), "w") as f:
                f.write("active\n")

    # -- environments
    def base_env(self):
        drop = ("VFG_", "FAKE_", "CLAUDE", "TYPESAFE_", "ANTHROPIC_")
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
            "VFG_LOOKOUT": self.lookout, "VFG_LIB": self.lib, "VFG_ENV_FILE": self.env_file,
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
        self.fake_rcon = subprocess.Popen([PC_PY, os.path.join(TOOLKIT, "tests", "fake_rcon.py"), self.props,
                                           self.rcon_log], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          env=self.base_env(), start_new_session=True)
        assert self.fake_rcon.stdout.readline().strip() == b"ready"

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

    def start_watch(self):
        self.spawn("watch", [PC_PY, VFG, "watch"], self.pc_env(), stdout=self.watch_out,
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
        for p in (self.fake_jev, self.fake_rcon):
            if p and p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
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
        return self.sql("SELECT seq, at, line FROM feed WHERE seq > ? ORDER BY seq", (after,))

    def feed_file(self):
        try:
            with open(self.feed, encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


def line_regex(hms_, fields, text):
    return r"^LINE#\d+ %s %s \| %s$" % (re.escape(hms_), re.escape(fields), re.escape(text))


def conversation_lookout():
    try:
        with open(os.path.join(CONV, "CLAUDE.md"), encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"The text for `vfg lookout set` at start:\s*\n(.+)\Z", text, re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except OSError:
        pass
    return "Players ask an AI to build things; requests to it are direct asks."


def feed_lines_with_text(box, text):
    return [r for r in box.feed_rows() if r["line"].endswith("| " + text)]


# ======================================================================= paths

def path0(box, rep, ctx):
    rep.path("0", "setup as the conversation does it (§12): up, watchers, lookout, watch")
    r = box.vfg("up", timeout=150)
    rep.check(r.returncode == 0 and "rcon ok" in r.stdout, "vfg up: three units active (fake systemctl), RCON answers",
              r.stdout + r.stderr)
    r = box.vfg("watchers", "set", "join,leave,death,server")
    want = {"events": ["join", "leave", "death", "server"], "players": [], "contains": []}
    with open(box.watch_json) as f:
        got = json.load(f)
    rep.check(r.returncode == 0 and got == want, "vfg watchers set join,leave,death,server writes §6 watch.json",
              "exit %s, file %r, stderr %s" % (r.returncode, got, r.stderr))
    rep.check(box.wait_log("bridge", r"watch\.json loaded: events=join,leave,death,server players=0 contains=0", 5),
              "the bridge rereads watch.json when it changes")
    lookout = conversation_lookout()
    ctx["lookout"] = lookout
    r = box.vfg("lookout", "set", lookout)
    with open(box.lookout, encoding="utf-8") as f:
        got = f.read().strip()
    rep.check(r.returncode == 0 and got == lookout, "vfg lookout set writes CLAUDE.md's lookout to lookout.md",
              r.stderr)
    box.start_watch()
    cursor = os.path.join(box.vhome, "cursor")
    ok = wait_for(lambda: os.path.exists(cursor), 15)
    rep.check(ok, "vfg watch connected through fake_ssh and saved its cursor",
              open(os.path.join(box.logs, "watch.err")).read())
    rows = box.feed_rows()
    box.watch_start_seq = rows[-1]["seq"] if rows else 0


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
    rep.check(judged and judged["jev_class"] == "direct_ask" and judged["jev_confidence"] == 0.9
              and ISO_RE.match(judged["jev_judged_at"] or "") and ISO_RE.match(judged["fed_at"] or ""),
              "Jev judged it direct_ask 0.90; jev_judged_at and fed_at set", "%r" % judged)
    rx = line_regex(hms(start), "Steve voice direct_ask 0.90 -8dB", "build me a castle")
    got = box.wait_watch(rx, 8)
    time.sleep(0.5)
    got = box.watch_match(rx)
    rep.check(got and len(got) == 1,
              "vfg watch printed it once: LINE#n %s Steve voice direct_ask 0.90 -8dB | build me a castle" % hms(start),
              "matching lines: %r\nlast watch lines: %r" % (got, box.watch_lines()[-5:]))
    if got:
        seq = int(FEED_RE.match(got[0]).group(2))
        frow = box.sql("SELECT * FROM feed WHERE seq = ?", (seq,))
        rep.check(frow and frow[0]["line"] == got[0] and frow[0]["at"] == body["started_at"]
                  and got[0] in box.feed_file(),
                  "the feed row and feed.log hold the same line; feed.at is when it was said (started_at)",
                  "%r" % frow)
        t = box.arrival(got[0])
        if t:
            rep.note("reached vfg watch %.2f s after ended_at (%.2f s after started_at, the HH:MM:SS shown)"
                     % (t - end.timestamp(), t - start.timestamp()))
    ctx["castle_id"] = line_id


def path2(box, rep, ctx):
    rep.path("2", "typed chat and watched game events reach vfg watch per watch.json (§3 §6)")
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
    for fields, text in (("chat context 0.70 -", "that cave by spawn"), ("chat direct_ask 0.90 -", "make it taller")):
        who = "Alex" if "cave" in text else "Steve"
        got = box.wait_watch(line_regex(stamp, "%s %s" % (who, fields), text), 8)
        rep.check(got and len(got) == 1, "typed chat -> Jev -> vfg watch: LINE %s %s | %s" % (who, fields, text),
                  "last watch lines: %r" % box.watch_lines()[-8:])
    lol = box.wait_judged("lol", 6)
    time.sleep(1.0)
    rep.check(lol and lol["jev_class"] == "not_useful" and not lol["fed_at"]
              and not box.watch_match(r"^LINE#\d+ .* \| lol$"),
              "typed chat Jev calls not_useful (lol) is stored, not fed", "%r" % lol)
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
              [("Alex", "that cave by spawn"), ("Steve", "make it taller"), ("Alex", "lol")],
              "chat lines stored with source=chat, nothing else", "%r" % chat)
    r = box.vfg("watchers", "set", "join,leave,death,server")
    box.wait_log("bridge", r"watch\.json loaded: events=join,leave,death,server players=0 contains=0", 5)
    r2 = box.vfg("watchers", "show")
    rep.check(r.returncode == 0 and r2.returncode == 0 and json.loads(r2.stdout) ==
              {"events": ["join", "leave", "death", "server"], "players": [], "contains": []},
              "watchers put back to the conversation's set; vfg watchers show agrees", r2.stdout + r2.stderr)


def path3(box, rep, ctx):
    rep.path("3", "vfg say writes an ai line that Jev never judges or feeds (§4 §8)")
    t0 = iso(now() - dt.timedelta(seconds=1))
    r1 = box.vfg("say", "on it — castle coming up")
    r2 = box.vfg("say", "your base is ready", "--to", "Steve")
    rep.check(r1.returncode == 0 and r2.returncode == 0, "vfg say and vfg say --to exit 0", r1.stderr + r2.stderr)
    with open(box.rcon_log) as f:
        cmds = [json.loads(l) for l in f if l.strip()]
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
    box.voice("Alex", "nice one", -18.0)
    judged = box.wait_judged("nice one", 6)
    time.sleep(1.5)
    ai = box.sql("SELECT * FROM lines WHERE source = 'ai' AND at >= ?", (t0,))
    rep.check(judged and all(a["jev_class"] is None and a["jev_confidence"] is None and a["jev_judged_at"] is None
                             and a["fed_at"] is None for a in ai),
              "after Jev's next call the ai lines are still unjudged and unfed", "%r" % ai)
    use = box.sql("SELECT * FROM usage WHERE ref IN (%s)" % ",".join("?" * len(ctx["ai_ids"])), ctx["ai_ids"])
    rep.check(not use, "no usage row for an ai line", "%r" % use)
    w = "\n".join(box.watch_lines())
    rep.check("castle coming up" not in w and "your base is ready" not in w, "no ai line in vfg watch")
    calls = box.fake_jev_api("/calls")
    mine = [c for c in calls if any("“nice one”" in q.get("instructions", "")
                                    for q in (c["body"] or {}).get("questions", {}).values())]
    st = mine[-1]["body"]["state"] if mine else {}
    hist = st.get("history", "")
    rep.check("AI (ai): on it — castle coming up" in hist and "AI (ai): (to Steve) your base is ready" in hist,
              "the ai lines are in the history of Jev's next call (§5: the AI's included)", short(hist, 900))
    rep.check(st.get("look_out_for") == ctx.get("lookout"), "Jev's state carries lookout.md as vfg lookout set wrote it",
              short(st.get("look_out_for"), 300))
    asked = [q.get("instructions", "") for c in calls for q in ((c["body"] or {}).get("questions") or {}).values()]
    rep.check(not any("castle coming up" in a or "your base is ready" in a for a in asked),
              "no Jev question ever asked about an ai line")


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
    want = "%s Steve voice direct_ask 0.90 | build me a castle" % stamp(castle["at"])
    rep.check(r.returncode == 0 and len(out) == len(db) and want in out,
              "--since <date>: every line of the day, the castle as %r" % want[20:],
              "exit %s, %d vs %d rows, stderr %s" % (r.returncode, len(out), len(db), r.stderr))
    rep.check(all(H_LINE.match(l) for l in out), "every line matches the shape of the conversation's history fixture",
              "%r" % [l for l in out if not H_LINE.match(l)])
    ctx["history_out"] = out
    r, out = run("--since", date, "--source", "ai")
    rep.check([l[20:] for l in out] == ["AI ai - | on it — castle coming up", "AI ai - | (to Steve) your base is ready"],
              "--source ai: the two vfg say lines", "%r" % out)
    r, out = run("--since", date, "--source", "chat")
    nchat = len([d for d in db if d["source"] == "chat"])
    rep.check(len(out) == nchat and any(l.endswith("Alex chat context 0.70 | that cave by spawn") for l in out)
              and any(l.endswith("Alex chat not_useful 0.80 | lol") for l in out),
              "--source chat: only chat, with Jev's class", "%r" % out)
    r, out = run("--since", date, "--player", "steve", "--source", "voice")
    rep.check(out and all(" Steve voice " in l for l in out) and any(l.endswith("| build me a castle") for l in out),
              "--player steve (any case) --source voice", "%r" % out)
    r, out = run("--since", date, "--class", "not_useful")
    rep.check(out and all(" not_useful " in l for l in out) and any(l.endswith("| lol") for l in out),
              "--class not_useful", "%r" % out)
    r, out = run("--events", "--since", date, "--limit", "1000")
    ev = box.sql("SELECT * FROM events WHERE at >= ?", (date + "T00:00:00.000Z",))
    rep.check(len(out) == len(ev) and any(l.endswith("death Steve | Steve was slain by Zombie") for l in out)
              and any(l.endswith("join Alex | Alex joined the game") for l in out)
              and any(l.endswith("server - | server started") for l in out) and all(H_EVENT.match(l) for l in out),
              "--events: every event, unwatched ones too", "%r" % out)
    ctx["history_events_out"] = out
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
    hook = subprocess.run(["bash", os.path.join(CONV, "hooks", "after-compact.sh")], env=box.pc_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
    rep.check(hook.returncode == 0 and "| build me a castle" in hook.stdout and "death Steve" in hook.stdout
              and "failed)" not in hook.stdout,
              "the conversation's after-compact hook gets real lines and events from vfg history",
              hook.stdout[-1200:])


def path5(box, rep, ctx):
    rep.path("5", "vfg feed post ORCH / SYS round-trip, and vfg orch send/reply (§7 §8 §9)")

    def post(*args):
        r = box.vfg("feed", "post", *args)
        seq = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
        return r, seq

    def seen(seq, fields, text):
        rows = box.sql("SELECT * FROM feed WHERE seq = ?", (seq,))
        if not rows:
            return False, "no feed row %s" % seq
        h = rows[0]["at"][11:19]
        want = "%s#%d %s %s| %s" % (fields[0], seq, h, fields[1] + " " if fields[1] else "", text)
        got = box.wait_watch(r"^%s$" % re.escape(want), 6)
        ok = (got and len(got) == 1 and rows[0]["line"] == want and want in box.feed_file()
              and abs(dt.datetime.strptime(rows[0]["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()
                      - time.time()) < 10)
        return ok, "want %r; watch %r; row %r" % (want, got, rows)

    r, seq = post("ORCH", "T1 done", "castle built at spawn, 40x40")
    ok, why = seen(seq, ("ORCH", "T1 done"), "castle built at spawn, 40x40") if seq else (False, r.stderr)
    rep.check(ok, "vfg feed post ORCH 'T1 done' … prints the seq; once in vfg watch, feed table and feed.log", why)
    r, seq = post("SYS", "", "smoke test")
    ok, why = seen(seq, ("SYS", ""), "smoke test") if seq else (False, r.stderr)
    rep.check(ok, "vfg feed post SYS '' 'smoke test' -> SYS#n HH:MM:SS | smoke test", why)
    r, seq = post("SYS", "", "two\nlines")
    ok, why = seen(seq, ("SYS", ""), "two lines") if seq else (False, r.stderr)
    rep.check(ok, "a newline in the text becomes a space", why)
    r, seq = post("SYS", "", "long " + "y" * 700)
    cut = "long " + "y" * 494 + "…"
    ok, why = seen(seq, ("SYS", ""), cut) if seq else (False, r.stderr)
    rep.check(ok and len(cut) == 500, "text over 500 characters is cut at 500 with …", why)
    n = len(box.feed_rows())
    r, seq = post("ORCH", "T1 finished", "x")
    rep.check(r.returncode != 0 and len(r.stderr.strip().splitlines()) == 1 and len(box.feed_rows()) == n,
              "bad ORCH fields: non-zero exit, one stderr line, nothing written", r.stderr)
    r, seq = post("GAME", "explode Steve", "x")
    rep.check(r.returncode != 0 and len(r.stderr.strip().splitlines()) == 1 and len(box.feed_rows()) == n,
              "bad GAME fields: refused by the real writer, one stderr line", r.stderr)
    r = box.vfg("orch", "send", "Build a small castle at Steve’s position", "--context", "Steve asked (#1)")
    inbox = os.path.join(box.vhome, "dispatch", "inbox.jsonl")     # the dispatcher's (§14), VFG_DISPATCH_DIR's default
    task = r.stdout.strip()
    rec = json.loads(open(inbox).read().splitlines()[-1]) if os.path.exists(inbox) else {}
    rep.check(r.returncode == 0 and re.match(r"^T\d+$", task) and set(rec) == {"id", "at", "task", "context"}
              and rec["id"] == task and ISO_RE.match(rec["at"]),
              "vfg orch send prints T<n> and appends the §9 record to the dispatcher's inbox (nothing started)",
              r.stdout + r.stderr)
    for status, text in (("progress", "walls going up"), ("done", "small stone castle built at Steve’s position")):
        r = box.vfg("orch", "reply", task or "T1", status, text)
        got = box.wait_watch(r"^ORCH#\d+ \d\d:\d\d:\d\d %s %s \| %s$" % (task, status, re.escape(text)), 6)
        rep.check(r.returncode == 0 and got and len(got) == 1, "vfg orch reply %s %s -> ORCH line in vfg watch"
                  % (task, status), r.stderr)


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
    t0 = iso(now() - dt.timedelta(seconds=5))
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
    got = box.wait_watch(line_regex(start, "Steve voice direct_ask 0.90 -8dB", "build a tower by the lake"), 8)
    rep.check(got and len(got) == 1, "the mod's line goes on through Jev: once in vfg watch as "
              "LINE#n %s Steve voice direct_ask 0.90 -8dB | build a tower by the lake" % start,
              "%r" % box.watch_lines()[-5:])
    long_pl = json.loads(rec["long.payload"][0])
    cut = long_pl["text"][:499] + "…"
    got = box.wait_watch(line_regex(long_pl["started_at"][11:19], "Steve voice direct_ask 0.90 -12dB", cut), 8)
    rep.check(got and len(got) == 1, "its 2000-character line reaches vfg watch cut to 500 characters with …",
              "%r" % [short(l, 120) for l in box.watch_lines()[-4:]])


def path9(box, rep, ctx):
    rep.path("9", "the conversation's fixtures vs what the real feed writer and producers emit")
    # (a) a real flip: Jev's own was=not_useful, through the real filter
    time.sleep(1.0)   # every earlier line's `at` is now older than the next one's
    s, d, a_body = box.voice("Steve", "over here", -10.4, seconds=0.1)
    a = box.wait_judged("over here", 6)
    newest = box.sql("SELECT text FROM lines WHERE jev_class = 'not_useful' AND jev_confidence IS NOT NULL "
                     "ORDER BY at DESC, id DESC LIMIT 1")
    if not rep.check(a and a["jev_class"] == "not_useful" and newest and newest[0]["text"] == "over here",
                     "setup: 'over here' judged not_useful, the newest such line (so it is line_2 when asked again)",
                     "%r %r" % (a, newest)):
        return
    box.fake_jev_api("/next", {"answers": {
        "line_1": {"type": "choice", "choice": "direct_ask", "confidence": 0.88},
        "line_2": {"type": "choice", "choice": "context", "confidence": 0.66}}, "usage": {"input_tokens": 1500}})
    s, d, b_body = box.voice("Alex", "come look at this", -9.0)
    rx_a = line_regex(a_body["started_at"][11:19], "Steve voice context 0.66 -10dB was=not_useful", "over here")
    rx_b = line_regex(b_body["started_at"][11:19], "Alex voice direct_ask 0.88 -9dB", "come look at this")
    ga, gb = box.wait_watch(rx_a, 8), box.wait_watch(rx_b, 8)
    rep.check(ga and gb and len(ga) == 1, "a real flip (Jev asked again in the next line's call) reaches vfg watch as "
              "'Steve voice context 0.66 -10dB was=not_useful'", "%r" % box.watch_lines()[-4:])
    ctx["real_flip"] = ga[0] if ga else None
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
    stamp = box.console("[Server thread/INFO]: <Steve> make it bigger")
    g_n = box.wait_watch(line_regex(stamp, "Steve chat direct_ask 0.90 -", "make it bigger"), 8)
    rep.check(g_back and g_n, "Jev back: one SYS 'jev back — filtering again', next line judged normally",
              "%r" % box.watch_lines()[-4:])
    ctx["real_open"] = (g_open[0] if g_open else None, g_c[0] if g_c else None)

    # (c) every fixture feed line, rendered again by the real vfg_feed, comes out byte-identical
    scratch = os.path.join(box.root, "fixture-box")
    os.makedirs(scratch)
    env = box.ec2_env(VFG_DB=os.path.join(scratch, "vfg.db"), VFG_FEED=os.path.join(scratch, "feed.log"),
                      PYTHONPATH=os.pathsep.join([box.lib, box.bridge_dir]))
    subprocess.run([box.ec2_py, "-m", "vfg_bridge", "--init-db"], env=env, check=True, stdout=subprocess.DEVNULL)
    saved = {k: os.environ.get(k) for k in ("VFG_DB", "VFG_FEED")}
    os.environ["VFG_DB"], os.environ["VFG_FEED"] = env["VFG_DB"], env["VFG_FEED"]
    for p in (box.lib, box.bridge_dir, box.jev_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    import vfg_feed  # the deployed copy of bridge/vfg_feed.py
    from vfg_bridge import console as bconsole
    spec = importlib.util.spec_from_file_location("jev_filter_module", os.path.join(box.jev_dir, "filter.py"))
    jfilter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jfilter)
    today = now().strftime("%Y-%m-%d")
    n_now = now()
    fixtures = []
    for path in sorted(glob.glob(os.path.join(CONV, "test", "cases", "*.case"))) + \
            [os.path.join(CONV, "test", "feeds", "evening.feed")]:
        section = None
        for line in open(path, encoding="utf-8").read().splitlines():
            if line in ("state:", "prompt:", "check:"):
                section = line[:-1]
                continue
            if (section == "prompt" or path.endswith(".feed")) and re.match(r"^(LINE|GAME|ORCH|SYS)#", line):
                line = re.sub(r"\{t(?:-(\d+))?\}", lambda m: hms(n_now - dt.timedelta(seconds=int(m.group(1) or 0))), line)
                line = line.replace("{now}", hms(n_now))
                fixtures.append((os.path.relpath(path, CONV), line))
    render_bad, prod_bad, grammar_bad = [], [], []
    for where, line in fixtures:
        m = FEED_RE.match(line)
        if not m:
            render_bad.append((where, line, "does not parse as TYPE#seq HH:MM:SS fields | text"))
            continue
        typ, seq, h, fields, text = m.group(1), m.group(2), m.group(3), m.group(4) or "", m.group(5)
        try:
            got_seq = vfg_feed.append(typ, fields, text, "%sT%s.000Z" % (today, h))
            with open(env["VFG_FEED"], encoding="utf-8") as f:
                real = f.read().splitlines()[-1]
            want = re.sub(r"^(\w+)#\d+ ", r"\1#%d " % got_seq, line)
            if real != want:
                render_bad.append((where, line, "real writer gives %r" % real))
        except Exception as e:  # the writer refused it
            render_bad.append((where, line, "real writer refused: %s" % e))
            continue
        # who in the real system would write these fields?
        if typ == "LINE":
            if not C_LINE.match(fields):
                prod_bad.append((where, line, "fields break §7's LINE grammar"))
                continue
            f = fields.split()
            row = {"id": "x", "player_name": f[0], "source": f[1], "jev_class": f[2],
                   "jev_confidence": None if f[3] == "-" else float(f[3]),
                   "peak_dbfs": None if f[4] == "-" else float(f[4][:-2])}
            shell = jfilter.Filter.__new__(jfilter.Filter)   # the real class, only `flipped` set
            shell.flipped = {"x"} if len(f) > 5 else set()
            real_fields = shell.fields(row)
            if real_fields != fields:
                prod_bad.append((where, line, "the Jev filter would write %r" % real_fields))
            if f[1] == "chat" and f[4] != "-":
                prod_bad.append((where, line, "typed chat with a dB level"))
        elif typ == "GAME":
            found = bconsole.classify(text)
            kind, player = fields.split()
            if kind == "raw":
                pass
            elif not found or (found["kind"], found["player"] or "-") != (kind, player) or found["text"] != text:
                prod_bad.append((where, line, "the bridge parses that text as %r" % found))
        elif typ == "SYS":
            if text not in (jfilter.SYS_OPEN, jfilter.SYS_BACK) and not text.startswith("watch.json: "):
                prod_bad.append((where, line, "no part writes this SYS text"))
        elif typ == "ORCH" and not C_ORCH.match(fields):
            prod_bad.append((where, line, "fields break §7's ORCH grammar"))
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    rep.check(not render_bad, "all %d fixture feed lines (test/cases + feeds/evening.feed) re-render byte-identical "
              "through the real vfg_feed (field order, HH:MM:SS, spacing, ' | ')" % len(fixtures),
              "\n".join("%s: %s -> %s" % x for x in render_bad))
    rep.check(not prod_bad, "every fixture LINE's fields are what the real Jev filter writes for that row; every GAME "
              "is what the bridge parses from its text; every SYS text is one a part writes",
              "\n".join("%s: %s -> %s" % x for x in prod_bad))
    seqs = [(w, int(FEED_RE.match(l).group(2))) for w, l in fixtures if w.endswith("evening.feed")]
    dup = sorted({s for _, s in seqs if [x for _, x in seqs].count(s) > 1})
    if dup:
        rep.note("evening.feed repeats seq %s on purpose; the real vfg watch never prints a seq twice (it drops "
                 "seq <= cursor), so that case can only arrive from the fake" % dup)
    no_was = not any("was=not_useful" in l for _, l in fixtures)
    if no_was:
        rep.note("no fixture carries was=not_useful; checked against the real flip line instead")
    # (d) the real lines vfg watch printed this run, read with CLAUDE.md's grammar
    for _, line in box.watch_seen:
        m = FEED_RE.match(line)
        if not m:
            grammar_bad.append((line, "not TYPE#seq HH:MM:SS fields | text"))
            continue
        typ, fields = m.group(1), m.group(4) or ""
        if typ == "LINE" and not M_LINE.match(fields) or typ == "GAME" and not M_GAME.match(fields) \
                or typ == "ORCH" and not M_ORCH.match(fields) or typ == "SYS" and fields:
            grammar_bad.append((line, "fields outside CLAUDE.md's grammar"))
    rep.check(not grammar_bad, "all %d lines the real vfg watch printed this run read under CLAUDE.md's stated grammar"
              % len(box.watch_seen), "\n".join("%s  (%s)" % x for x in grammar_bad))
    flip = ctx.get("real_flip")
    rep.check(flip and flip.split(" | ")[0].endswith(" was=not_useful"),
              "the real was=not_useful is the last field, after the dB, as CLAUDE.md describes", "%r" % flip)
    o, c = ctx.get("real_open") or (None, None)
    jd = open(os.path.join(CONV, "test", "cases", "jev-down.case"), encoding="utf-8").read()
    rep.check(o and c and ("| " + SYS_OPEN) in jd and " context - -" in jd and o.endswith("| " + SYS_OPEN)
              and " context - -21dB " in c,
              "jev-down.case's SYS text and 'context - -NdB' match the real fail-open output", "%r %r" % (o, c))
    hist_fix = [l for l in open(os.path.join(CONV, "test", "fixtures", "history.txt"), encoding="utf-8").read().splitlines() if l]
    bad = [l for l in hist_fix if not (H_LINE.match(l) or H_EVENT.match(l))]
    real = ctx.get("history_out", []) + ctx.get("history_events_out", [])
    bad_real = [l for l in real if not (H_LINE.match(l) or H_EVENT.match(l))]
    rep.check(not bad and real and not bad_real,
              "fixtures/history.txt has the same line shapes as real vfg history output", "%r %r" % (bad, bad_real))
    rep.note("in real output, lines and events come from two calls (vfg history / --events); history.txt "
             "interleaves both, as after-compact.sh's two calls would print them")


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
    oks = [box.wait_watch(line_regex(e1["started_at"][11:19], "Steve voice direct_ask 0.90 -10dB", "build a wall here"), 8),
           box.wait_watch(line_regex(e2["started_at"][11:19], "Alex voice context 0.70 -14dB", "our base is by the river"), 8),
           box.wait_watch(line_regex(stamp, "Steve chat direct_ask 0.90 -", "make it rain"), 8)]
    haha = box.wait_judged("haha", 4)
    rep.check(all(o and len(o) == 1 for o in oks) and haha and haha["jev_class"] == "not_useful" and not haha["fed_at"],
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
    kept = {x for x in texts if (box.line_by_text(x) or {}).get("jev_class") in ("direct_ask", "context")}
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
    in_watch = box.watch_match(line_regex(body["started_at"][11:19], "Steve voice direct_ask 0.90 -8dB",
                                          "make a bridge over the river"))
    rep.check(died and box.procs["jev"].returncode is None and before == 1 and r and r["fed_at"] is None
              and after == 1 and len(in_watch) == 1,
              "SIGKILL right after append_batch, before fed_at is written: the restart does not feed it again",
              "Jev killed after writing LINE (fed_at still NULL: %r); after restart the feed holds it %d time(s) and "
              "vfg watch printed it %d time(s): %r" % (r and r["fed_at"], after, len(in_watch), in_watch))
    ctx["dup_texts"] = {"make a bridge over the river"} if after > 1 else set()
    # 7e: SIGKILL while a flipped line waits in its batch
    s, d, a2 = box.voice("Steve", "wait for me", -11.0)
    box.wait_judged("wait for me", 6)
    box.fake_jev_api("/next", {"answers": {
        "line_1": {"type": "choice", "choice": "not_useful", "confidence": 0.7},
        "line_2": {"type": "choice", "choice": "context", "confidence": 0.61}}, "usage": {"input_tokens": 1400}})
    box.voice("Alex", "hmm okay", -15.0)
    flipped = wait_for(lambda: (box.line_by_text("wait for me") or {}).get("jev_class") == "context", 5, 0.01)
    unfed = flipped and not box.line_by_text("wait for me")["fed_at"]
    box.stop("jev", signal.SIGKILL, 5)
    box.start_jev()
    rx = r"^LINE#\d+ %s Steve voice context 0\.61 -11dB( was=not_useful)? \| wait for me$" % a2["started_at"][11:19]
    got = box.wait_watch(rx, 10)
    time.sleep(1.0)
    got = box.watch_match(rx)
    rep.check(unfed and got and len(got) == 1 and len(feed_lines_with_text(box, "wait for me")) == 1,
              "SIGKILL while a flip waits in its batch: fed exactly once (the was=not_useful marker is best-effort)",
              "flipped and unfed at the kill: %r; after restart vfg watch printed %r" % (unfed, got))
    if got and len(got) == 1:
        rep.note("after that crash the flip was fed %s the marker" % ("with" if "was=not_useful" in got[0] else "without"))
    # 7f: vfg watch killed and started again resumes from its cursor
    rc = box.stop("watch", signal.SIGTERM, 10)
    r = box.vfg("feed", "post", "SYS", "", "posted while vfg watch was down")
    box.start_watch()
    got = box.wait_watch(r"^SYS#%s \d\d:\d\d:\d\d \| posted while vfg watch was down$" % r.stdout.strip(), 10)
    rep.check(rc == 0 and got and len(got) == 1, "vfg watch SIGTERMed (exit 0) and restarted: the line written while "
              "it was down arrives once, from the cursor", "rc %r, %r" % (rc, got))
    # 7g: the bridge restarted: console lines and a retried POST are stored once
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


def path6(box, rep, ctx):
    rep.path("6", "usage rows: one per Jev call for component jev; conversation and vfg usage add")
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
    r = box.vfg("usage", "add", "--component", "orchestrator", "--model", "opus", "--in", "1200", "--out", "300",
                "--cost", "0.0123", "--ref", "T1")
    row = box.sql("SELECT * FROM usage WHERE id = ?", (int(r.stdout.strip()) if r.stdout.strip().isdigit() else -1,))
    rep.check(r.returncode == 0 and row and (row[0]["component"], row[0]["model"], row[0]["input_tokens"],
                                             row[0]["output_tokens"], row[0]["cost_usd"], row[0]["ref"]) ==
              ("orchestrator", "opus", 1200, 300, 0.0123, "T1"), "vfg usage add writes the §4 row and prints its id",
              r.stdout + r.stderr + "%r" % row)
    hook_in = json.dumps({"transcript_path": os.path.join(CONV, "test", "fixtures", "transcript.jsonl"),
                          "session_id": "3f2a9c1e-7b44-4d2a-9e10-5c6d7e8f9a0b"})
    h = subprocess.run([PC_PY, os.path.join(CONV, "hooks", "usage.py")], input=hook_in, env=box.pc_env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    rows = wait_for(lambda: box.sql("SELECT * FROM usage WHERE component = 'conversation'"), 15)
    log = os.path.join(box.vhome, "conversation", "usage.log")
    logged = open(log).read().split() if os.path.exists(log) else []
    rep.check(h.returncode == 0 and rows and all(r["ref"] == "3f2a9c1e" for r in rows)
              and logged and any(r["model"] == logged[1] and str(r["input_tokens"]) == logged[2]
                                 and str(r["output_tokens"]) == logged[3] for r in rows),
              "the conversation's Stop hook (usage.py) lands a component=conversation row through the real vfg",
              "hook rc %s %s; rows %r; usage.log %r" % (h.returncode, h.stderr, rows, logged))


def final_checks(box, rep, ctx):
    rep.resume("7")
    # Everything the feed holds since the watch started reached vfg watch exactly once, in order
    time.sleep(1)
    rows = box.feed_rows(box.watch_start_seq)
    printed = [l for l in box.watch_lines() if FEED_RE.match(l) and not l.startswith("SYS#0 ")]
    pseq = [int(FEED_RE.match(l).group(2)) for l in printed]
    missing = [r["line"] for r in rows if printed.count(r["line"]) != 1]
    rep.check(not missing and pseq == sorted(set(pseq)) and box.feed_file() == [r["line"] for r in box.feed_rows()],
              "all %d feed rows since vfg watch began were printed exactly once, in seq order; feed.log == feed table"
              % len(rows), "not exactly once: %r; order ok %r" % ([short(m, 120) for m in missing],
                                                                 pseq == sorted(set(pseq))))
    kept = box.sql("SELECT text FROM lines WHERE jev_class IN ('direct_ask','context') AND fed_at IS NOT NULL")
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
    for need in (VFG, os.path.join(BRIDGE, "vfg_feed.py"), os.path.join(JEV, "jev_filter.py")):
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
    try:
        box.deploy()
        box.start_fake_rcon()
        box.start_fake_jev()
        box.start_bridge()
        box.start_jev()
        box.watch_tail_thread()
        for fn in (path0, path1, path2, path3, path4, path5, path8, path7, path9, path6, final_checks):
            try:
                fn(box, rep, ctx)
            except Exception:
                rep.crashed(sys.exc_info())
    finally:
        box.close()
    rep.summary()
    failed = rep.failed()
    if args.keep:
        print("the box and its logs are kept in %s (logs/: bridge.log, jev.log, watch.out, rcon.jsonl)" % root)
    else:
        shutil.rmtree(root, ignore_errors=True)
        if failed:
            print("run again with --keep to keep the box and its logs")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
