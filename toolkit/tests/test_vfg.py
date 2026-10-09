"""The vfg toolkit's tests: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Each test gets a temp VFG_HOME, a temp VFG_REPO and a temp "box" that
VFG_REMOTE_VFG and VFG_REMOTE_MC point into. VFG_SSH is fake_ssh, which runs
the remote command here with bash; a python3 shim for the interpreter running
the tests goes first on PATH, so the remote programs run on it too (3.9 under
`uv run --python 3.9`). The place and handler tests answer RCON with
../scripts/_test/fake_places_world.py, Minecraft 26.3's recorded replies.
"""
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(TESTS)
VFG = os.path.join(TOOLKIT, "vfg")
REPO = os.path.dirname(TOOLKIT)
SCRIPTS_TEST = os.path.join(REPO, "scripts", "_test")
PY = sys.executable
FEED_RE = re.compile(r"^(LINE|GAME|ORCH|SYS)#\d+ \d\d:\d\d:\d\d ")
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

SCHEMA = """
CREATE TABLE lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('direct_ask','context','not_useful')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE INDEX lines_player_at ON lines(player_name, at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX events_at ON events(at);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""

RUNNER = """#!/usr/bin/env bash
# A stand-in for orchestrator/run: a sleep child, then a loop; SIGTERM is noted in order.
trap 'echo "orchestrator stopping $(date +%s.%N)" >> "$VFG_ORCH_DIR/order"; exit 0' TERM
sleep 1000 &
echo $! > "$VFG_ORCH_DIR/child.pid"
echo "fake runner $$ up"
while true; do sleep 0.2; done
"""

DISPATCHER = """#!/usr/bin/env bash
# A stand-in for dispatcher/run: on SIGTERM it takes a second to hand on what it holds.
trap 'sleep 1; echo "dispatcher handed $(date +%s.%N)" >> "$VFG_ORCH_DIR/order"; exit 0' TERM
echo "fake dispatcher $$ up"
while true; do sleep 0.2; done
"""

HELLO = """import sys
import mc
print("hello", " ".join(sys.argv[1:]))
print("lib", mc.NAME, "script", __import__("os").environ.get("VFG_SCRIPT"))
if sys.argv[1:] == ["fail"]:
    sys.exit(3)
if sys.argv[1:] == ["nap"]:
    __import__("time").sleep(3)
    print("woke")
"""

LOOP = """import time
n = 0
while True:
    print("tick", n)
    n += 1
    time.sleep(0.2)
"""


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except OSError:
        return False


def vfg_module(name="vfgmod"):
    """toolkit/vfg loaded as a module, to call its functions in-process."""
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader(name, VFG)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def wait_for(pred, timeout=10, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class Box(object):
    """A temp world: HOME, VFG_HOME, the repo, the EC2 box, units, RCON."""

    def __init__(self, rcon=True, feed_lines=3, feed_file=True):
        self.root = tempfile.mkdtemp(prefix="vfg-test-")
        j = lambda *p: os.path.join(self.root, *p)
        self.home, self.vhome, self.repo = j("home"), j("home", ".vfg"), j("repo")
        self.rvfg, self.rmc, self.units, self.bin = j("box", "vfg"), j("box", "mc"), j("units"), j("bin")
        for d in (self.home, self.vhome, self.repo, self.rvfg, self.rmc, self.units, self.bin,
                  os.path.join(self.rvfg, "lib")):
            os.makedirs(d, exist_ok=True)
        os.chmod(self.vhome, 0o700)
        os.symlink(PY, os.path.join(self.bin, "python3"))
        self.key = j("key")
        with open(self.key, "w") as f:
            f.write("not a key\n")
        self.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\n" % self.key)
        self.ssh_log = j("ssh.jsonl")
        self.rcon_log = j("rcon.jsonl")
        open(self.rcon_log, "w").close()
        self.port = free_port()
        self.password = secrets.token_hex(8)
        self.write_props(self.password)
        self.db = os.path.join(self.rvfg, "vfg.db")
        self.feed = os.path.join(self.rvfg, "feed.log")
        self.make_db(feed_lines)
        if feed_file:
            with open(self.feed, "w") as f:
                for seq, line in self.feed_rows:
                    f.write(line + "\n")
        shutil.copy(os.path.join(TESTS, "fake_vfg_feed.py"), os.path.join(self.rvfg, "lib", "vfg_feed.py"))
        for u in ("minecraft", "vfg-bridge", "vfg-jev"):
            with open(os.path.join(self.units, u), "w") as f:
                f.write("active\n")
        self.make_repo()
        self.rcon = None
        if rcon:
            self.start_rcon()
        self.procs = []

    # -- setup
    def write_config(self, text):
        with open(os.path.join(self.vhome, "config"), "w") as f:
            f.write("# test config\n" + text)

    def write_props(self, password, enable=True):
        with open(os.path.join(self.rmc, "server.properties"), "w") as f:
            f.write("#Minecraft server properties\nenable-rcon=%s\nrcon.port=%d\nrcon.password=%s\n"
                    "server-port=25565\n" % ("true" if enable else "false", self.port, password))

    def make_db(self, feed_lines):
        now = datetime.now(timezone.utc)
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.execute("PRAGMA journal_mode=WAL")
        lines = [
            ("L1", now - timedelta(minutes=5), "Steve", "voice", "build me a castle", "direct_ask", 0.93),
            ("L2", now - timedelta(minutes=4), "Alex", "chat", "that cave by spawn", "context", 0.71),
            ("L3", now - timedelta(minutes=3), "Alex", "chat", "lol", None, None),
            ("L4", now - timedelta(minutes=2), "AI", "ai", "on it — castle at spawn", None, None),
            ("L5", now - timedelta(minutes=20), "Steve", "voice", "hmm", "not_useful", 0.4),
            ("L6", now - timedelta(hours=1), "Bob", "voice", "old ask", "direct_ask", 0.88),
        ]
        for lid, at, player, source, text, cls, conf in lines:
            con.execute("INSERT INTO lines (id, at, player_name, source, text, jev_class, jev_confidence)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)", (lid, iso(at), player, source, text, cls, conf))
        events = [
            ("E1", now - timedelta(minutes=5), "join", "Steve", "Steve joined the game"),
            ("E2", now - timedelta(minutes=2), "death", "Steve", "Steve was slain by Zombie"),
            ("E3", now - timedelta(minutes=30), "server", None, "Server started"),
        ]
        for eid, at, kind, player, text in events:
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (eid, iso(at), kind, player, text))
        self.feed_rows = []
        for i in range(1, feed_lines + 1):
            at = now - timedelta(minutes=10 - i)
            line = "SYS#%d %s | seeded line %d" % (i, at.strftime("%H:%M:%S"), i)
            con.execute("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", (i, iso(at), line))
            self.feed_rows.append((i, line))
        con.commit()
        con.close()

    def make_repo(self):
        orch = os.path.join(self.repo, "orchestrator")
        os.makedirs(orch)
        self.run_path = os.path.join(orch, "run")
        with open(self.run_path, "w") as f:
            f.write(RUNNER)
        os.chmod(self.run_path, 0o755)
        os.makedirs(os.path.join(self.repo, "dispatcher"))
        self.dispatcher_path = os.path.join(self.repo, "dispatcher", "run")
        with open(self.dispatcher_path, "w") as f:
            f.write(DISPATCHER)
        os.chmod(self.dispatcher_path, 0o755)
        s = os.path.join(self.repo, "scripts")
        files = {
            "_lib/mc.py": "NAME = 'fake mc'\n",
            "_lib/__pycache__/mc.cpython-39.pyc": "junk",
            "_test/run_tests.py": "# not a script\n",
            "hello/main.py": HELLO,
            "hello/README.md": "Says hello and exits.\nUsage: hello [words]\nEnds: by itself\n",
            "hello/.secret": "dotfile, never deployed\n",
            "hello/__pycache__/main.cpython-39.pyc": "junk",
            "loop/main.py": LOOP,
            "loop/README.md": "Prints a tick every 0.2 s.\nUsage: loop\nEnds: when stopped\n",
            "README.md": "The scripts folder.\n",
        }
        for rel, text in files.items():
            path = os.path.join(s, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)

    def start_rcon(self):
        self.rcon = subprocess.Popen([PY, os.path.join(TESTS, "fake_rcon.py"),
                                      os.path.join(self.rmc, "server.properties"), self.rcon_log],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        line = self.rcon.stdout.readline()
        assert line.strip() == b"ready", line

    def stop_rcon(self):
        if self.rcon:
            self.rcon.kill()
            self.rcon.wait()
            self.rcon.stdout.close()
            self.rcon = None

    # -- running
    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("VFG_", "FAKE_", "CLAUDE"))}
        env.update({
            "HOME": self.home,
            "VFG_HOME": self.vhome,
            "VFG_REPO": self.repo,
            "VFG_REMOTE_VFG": self.rvfg,
            "VFG_REMOTE_MC": self.rmc,
            "VFG_SSH": os.path.join(TESTS, "fake_ssh"),
            "PATH": os.pathsep.join([self.bin, os.path.join(TESTS, "bin"), os.environ.get("PATH", "")]),
            "FAKE_UNITS": self.units,
            "FAKE_SSH_LOG": self.ssh_log,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def vfg(self, *args, input=None, timeout=90, **env):
        return subprocess.run([PY, VFG] + list(args), input=input, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self.env(**env), timeout=timeout,
                              universal_newlines=True)

    def popen(self, *args, **env):
        p = subprocess.Popen([PY, VFG] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.env(**env))
        self.procs.append(p)
        return p

    def post(self, text, typ="SYS", fields=""):
        env = dict(os.environ, VFG_DB=self.db, VFG_FEED=self.feed)
        r = subprocess.run([PY, os.path.join(self.rvfg, "lib", "vfg_feed.py"), "post", typ, fields, text],
                           stdout=subprocess.PIPE, check=True, universal_newlines=True, env=env)
        return int(r.stdout.strip())

    def rcon_commands(self):
        with open(self.rcon_log) as f:
            return [json.loads(l) for l in f if l.strip()]

    def query(self, sql, *params):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def ssh_calls(self):
        try:
            with open(self.ssh_log) as f:
                return [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def cursor(self):
        try:
            with open(os.path.join(self.vhome, "cursor")) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for s in (p.stdout, p.stderr):
                if s:
                    s.close()
        self.stop_rcon()
        pidfiles = [os.path.join(self.vhome, "orch", "runner.pid"),
                    os.path.join(self.vhome, "dispatch", "dispatcher.pid")]
        run = os.path.join(self.rvfg, "run")
        if os.path.isdir(run):
            pidfiles += [os.path.join(run, n) for n in os.listdir(run) if n.endswith(".pid")]
        for path in pidfiles:
            try:
                with open(path) as f:
                    os.killpg(int(f.read().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        shutil.rmtree(self.root, ignore_errors=True)


class Case(unittest.TestCase):
    box_kwargs = {}

    def setUp(self):
        self.box = Box(**self.box_kwargs)

    def tearDown(self):
        self.box.close()

    def assertOneLine(self, r, code, pattern=None):
        """Exit code, and exactly one line on stderr matching pattern."""
        self.assertEqual(r.returncode, code, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        lines = r.stderr.splitlines()
        self.assertEqual(len(lines), 1, "stderr=%r" % r.stderr)
        self.assertTrue(lines[0].startswith("vfg: "), lines[0])
        if pattern:
            self.assertRegex(lines[0], pattern)

    def assertOk(self, r):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))


# ===================================================================== basics

class TestBasics(Case):
    def test_version_and_help_list_every_command(self):
        r = self.box.vfg("--version")
        self.assertOk(r)
        self.assertEqual(r.stdout.strip(), "vfg 0.1.0")
        r = self.box.vfg("--help")
        self.assertOk(r)
        for c in ("up", "watch", "say", "cmd", "history", "watchers", "lookout", "orch",
                  "feed", "script", "action", "place", "usage"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        for args, words in ((["orch", "--help"], ["start", "stop", "status", "send", "hand", "reply"]),
                            (["action", "--help"], ["run", "refs"]),
                            (["action", "run", "--help"], ["--args", "--task", "--timeout", "124"]),
                            (["action", "refs", "--help"], ["--version"]),
                            (["place", "--help"], ["save", "list", "forget"]),
                            (["place", "save", "--help"], ["--player", "--at", "--dim", "--by"]),
                            (["place", "list", "--help"], ["--json"]),
                            (["script", "--help"], ["deploy", "run", "stop", "logs", "list"]),
                            (["history", "--help"], ["--since", "--until", "--player", "--class",
                                                     "--source", "--events", "--feed", "--limit"]),
                            (["watch", "--help"], ["--tail"]),
                            (["usage", "add", "--help"], ["--component", "--model", "--in", "--out",
                                                          "--cost", "--ref"]),
                            (["script", "run", "--help"], ["--wait", "--no-deploy"])):
            r = self.box.vfg(*args)
            self.assertOk(r)
            for w in words:
                self.assertIn(w, r.stdout)

    def test_no_config_exits_3(self):
        os.remove(os.path.join(self.box.vhome, "config"))
        for args in (["cmd", "list"], ["watch"], ["up"], ["history"], ["feed", "post", "SYS", "", "x"]):
            r = self.box.vfg(*args, timeout=20)
            self.assertOneLine(r, 3, r"^vfg: not configured: VFG_SSH_HOST missing in .*config$")
            self.assertEqual(r.stdout, "")

    def test_unreadable_key_exits_3_without_naming_it(self):
        os.remove(self.box.key)
        r = self.box.vfg("cmd", "list")
        self.assertOneLine(r, 3, r"^vfg: not configured: ")
        self.assertNotIn(self.box.key, r.stderr)

    def test_environment_wins_over_config(self):
        r = self.box.vfg("cmd", "list", VFG_SSH_HOST="otherbox", VFG_SSH_USER="steve")
        self.assertOk(r)
        self.assertIn("steve@otherbox", self.box.ssh_calls()[-1])

    def test_repo_defaults_to_the_folder_the_toolkit_is_in(self):
        import importlib.machinery
        import importlib.util
        sys.dont_write_bytecode = True
        loader = importlib.machinery.SourceFileLoader("vfgmod_repo", VFG)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("vfgmod_repo", loader))
        loader.exec_module(mod)
        saved = {k: os.environ.pop(k) for k in ("VFG_REPO", "VFG_HOME") if k in os.environ}
        os.environ["VFG_HOME"] = self.box.vhome
        try:
            self.assertEqual(mod.Config().repo, os.path.dirname(os.path.realpath(TOOLKIT)))
            os.environ["VFG_REPO"] = self.box.repo
            self.assertEqual(mod.Config().repo, self.box.repo)
        finally:
            os.environ.pop("VFG_REPO", None)
            os.environ.pop("VFG_HOME", None)
            os.environ.update(saved)

    def test_ssh_down_exits_4(self):
        for args in (["cmd", "list"], ["say", "hi"], ["history"], ["up"]):
            r = self.box.vfg(*args, FAKE_SSH_DOWN="1")
            self.assertOneLine(r, 4, r"^vfg: cannot reach EC2 \(ec2-user@fakebox\): "
                                     r"ssh: connect to host fakebox port 22: Connection timed out$")
            self.assertEqual(r.stdout, "")

    def test_timeout_exits_4(self):
        r = self.box.vfg("--timeout", "1", "cmd", "list", FAKE_SSH_DELAY="5")
        self.assertOneLine(r, 4, r"^vfg: EC2 did not answer within 1 s$")

    def test_the_ssh_call(self):
        self.assertOk(self.box.vfg("cmd", "list"))
        calls = self.box.ssh_calls()
        check, master, call = calls[-3], calls[-2], calls[-1]
        self.assertEqual(check[-3:], ["-O", "check", "ec2-user@fakebox"])
        self.assertIn("ControlMaster=yes", master)
        self.assertIn("ControlPersist=10m", master)
        self.assertIn("-N", master)
        self.assertIn("-f", master)
        self.assertNotIn("ControlMaster=no", master)
        self.assertEqual(call[-2:], ["ec2-user@fakebox", "python3 -u -"])
        for opt in ("BatchMode=yes", "ConnectTimeout=10", "ServerAliveInterval=15",
                    "ServerAliveCountMax=3", "LogLevel=ERROR", "StrictHostKeyChecking=accept-new",
                    "UserKnownHostsFile=%s/known_hosts" % self.box.vhome,
                    "ControlPath=%s/ssh/%%C" % self.box.vhome, "ControlMaster=no"):
            self.assertIn(opt, call)
        self.assertEqual(call[call.index("-i") + 1], self.box.key)
        self.assertEqual(oct(os.stat(os.path.join(self.box.vhome, "ssh")).st_mode & 0o777), "0o700")

    def test_bad_arguments_are_one_line_and_2(self):
        for args in (["say"], ["nosuch"], ["history", "--limit", "x"], ["usage", "add"],
                     ["watchers"], ["orch", "send"]):
            r = self.box.vfg(*args)
            self.assertOneLine(r, 2)

    def test_program_ships_libraries_as_modules(self):
        mod = vfg_module("vfgmod_program")

        def read(name):
            with open(os.path.join(TOOLKIT, "remote", name), encoding="utf-8") as f:
                return f.read()
        self.assertEqual(mod.program("rcon.py", {"a": 1}).decode(),
                         read("common.py") + "\nARGS = json.loads('{\"a\": 1}')\n\n" + read("rcon.py"))
        text = mod.program("place.py", {"op": "list"}, ["places.py"]).decode()
        self.assertTrue(text.endswith("\n" + read("place.py")))
        head = text[:-len(read("place.py"))]
        ns = {"__name__": "__main__"}
        saved = sys.modules.pop("places", None)
        try:
            exec(compile(head, "<program>", "exec"), ns)
            self.assertIs(sys.modules["places"], ns["places"])
            self.assertEqual(ns["places"].STORAGE, "everhear:places")
            self.assertNotIn("all", ns)  # places.all stays inside its module
            self.assertNotIn("snbt", ns)
        finally:
            sys.modules.pop("places", None)
            if saved is not None:
                sys.modules["places"] = saved

    def test_every_invocation_is_logged(self):
        self.box.vfg("--version")
        self.box.vfg("cmd", "list")
        with open(os.path.join(self.box.vhome, "vfg.log")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r'^\d{4}-\d\d-\d\dT[\d:.]+Z \["cmd", "list"\] exit=0 \d+ms$')


# ======================================================================= cmd

class TestCmd(Case):
    def test_tildes_reach_rcon_as_written(self):
        r = self.box.vfg("cmd", "tp", "@a", "~", "~10", "~")
        self.assertOk(r)
        self.assertEqual(self.box.rcon_commands(), ["tp @a ~ ~10 ~"])

    def test_quotes_and_dollars_arrive_literally(self):
        text = """say he said "hi" $x $(id) `id` 'q' \\n ;|&"""
        self.assertOk(self.box.vfg("cmd", text))
        self.assertEqual(self.box.rcon_commands(), [text])

    def test_one_leading_slash_dropped_and_output_printed(self):
        r = self.box.vfg("cmd", "/time", "set", "day")
        self.assertOk(r)
        self.assertEqual(r.stdout, "Set the time to 1000\n")
        self.assertEqual(self.box.rcon_commands(), ["time set day"])

    def test_home_argument_refused(self):
        r = self.box.vfg("cmd", "tp", "@a", self.box.home, "~", "~")
        self.assertOneLine(r, 2, "your shell expanded a ~")
        r = self.box.vfg("cmd", "tp", "@a", self.box.home + "/10")
        self.assertOneLine(r, 2)
        self.assertEqual(self.box.rcon_commands(), [])

    def test_big_reply_in_three_packets(self):
        r = self.box.vfg("cmd", "big")
        self.assertOk(r)
        self.assertEqual(len(r.stdout), 9001)
        self.assertTrue(r.stdout.startswith("0000 0001 "))
        self.assertTrue(r.stdout.endswith("1799 \n"))

    def test_minecraft_refusal_exits_1_with_output(self):
        r = self.box.vfg("cmd", "bogus")
        self.assertOneLine(r, 1, "^vfg: minecraft rejected the command$")
        self.assertIn("Unknown or incomplete command", r.stdout)

    def test_rcon_stopped_exits_5(self):
        self.box.stop_rcon()
        r = self.box.vfg("cmd", "list")
        self.assertOneLine(r, 5, r"^vfg: minecraft is not running \(RCON on 127\.0\.0\.1:%d refused\)$"
                           % self.box.port)

    def test_rcon_disabled_exits_5(self):
        self.box.write_props(self.box.password, enable=False)
        self.assertOneLine(self.box.vfg("cmd", "list"), 5, "minecraft is not running")

    def test_wrong_password_exits_5(self):
        self.box.write_props("not-the-password")
        r = self.box.vfg("cmd", "list")
        self.assertOneLine(r, 5, "^vfg: RCON refused the password$")
        self.assertNotIn(self.box.password, r.stdout + r.stderr)

    def test_too_long_and_empty(self):
        r = self.box.vfg("cmd", "say " + "x" * 1496)
        self.assertOneLine(r, 1, r"^vfg: command is 1500 bytes; RCON takes at most 1400 — put it in a script$")
        self.assertOneLine(self.box.vfg("cmd"), 2)
        self.assertOneLine(self.box.vfg("cmd", "/"), 2)
        self.assertEqual(self.box.rcon_commands(), [])


# ======================================================================= say

class TestSay(Case):
    def ai_rows(self):
        return self.box.query("SELECT id, at, player_uuid, player_name, source, text, jev_class,"
                              " ended_at, client_line_id FROM lines WHERE source = 'ai' AND id != 'L4'")

    def test_exact_tellraw_and_stored_row(self):
        before = datetime.now(timezone.utc)
        r = self.box.vfg("say", 'hi ~ "there"')
        self.assertOk(r)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")
        self.assertEqual(self.box.rcon_commands(),
                         ['tellraw @a [{"text":""},{"text":"[AI] ","color":"aqua"},{"text":"hi ~ \\"there\\""}]'])
        rows = self.ai_rows()
        self.assertEqual(len(rows), 1)
        rid, at, uuid, name, source, text, cls, ended, client = rows[0]
        self.assertRegex(rid, r"^[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertRegex(at, ISO_RE)
        self.assertGreaterEqual(at, iso(before - timedelta(seconds=1)))
        self.assertEqual((uuid, name, source, text, cls, ended, client), (None, "AI", "ai", 'hi ~ "there"', None, None, None))

    def test_prefix_from_config_and_unicode(self):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_CHAT_PREFIX=\"<Bot> \"\n" % self.box.key)
        self.assertOk(self.box.vfg("say", "on it — castle at spawn"))
        self.assertEqual(self.box.rcon_commands(),
                         ['tellraw @a [{"text":""},{"text":"<Bot> ","color":"aqua"},{"text":"on it — castle at spawn"}]'])

    def test_split_words_are_joined_wherever_to_sits(self):
        for args, target, text in (
                (["can", "t", "do", "that", "right", "now"], "@a", "can t do that right now"),
                (["hello", "--to", "Steve", "world"], "Steve", "hello world"),
                (["--to", "Steve", "your", "base"], "Steve", "your base"),
                (["on", "it", "--to=Steve"], "Steve", "on it"),
                (["--", "--to", "Steve"], "@a", "--to Steve"),
                (["-5", "hearts"], "@a", "-5 hearts")):
            open(self.box.rcon_log, "w").close()
            r = self.box.vfg("say", *args)
            self.assertOk(r)
            self.assertEqual(self.box.rcon_commands(),
                             ['tellraw %s [{"text":""},{"text":"[AI] ","color":"aqua"},{"text":%s}]'
                              % (target, json.dumps(text))])
        self.assertOneLine(self.box.vfg("say", "hi", "--to"), 2)
        self.assertOneLine(self.box.vfg("say", "--to", "Steve"), 2)

    def test_control_characters_become_spaces(self):
        self.assertOk(self.box.vfg("say", "  one\ntwo\tthree\r\n "))
        self.assertIn('{"text":"one two three"}', self.box.rcon_commands()[0])
        self.assertOneLine(self.box.vfg("say", " \n "), 2)

    def test_whisper_to_nobody_exits_1_and_stores_nothing(self):
        r = self.box.vfg("say", "hello", "--to", "Nobody")
        self.assertOneLine(r, 1, "^vfg: Nobody is not online$")
        self.assertEqual(self.ai_rows(), [])

    def test_whisper_stores_to_prefix(self):
        self.assertOk(self.box.vfg("say", "your base is ready", "--to", "Steve"))
        self.assertTrue(self.box.rcon_commands()[0].startswith('tellraw Steve [{"text":""}'))
        self.assertEqual(self.ai_rows()[0][5], "(to Steve) your base is ready")
        self.assertOneLine(self.box.vfg("say", "x", "--to", "not a name"), 2)

    def test_long_text_split_in_order_and_stored_once(self):
        words = ["w%04d" % i for i in range(500)]
        text = " ".join(words)  # 2999 characters
        text = text + "!"
        self.assertEqual(len(text), 3000)
        self.assertOk(self.box.vfg("say", text))
        cmds = self.box.rcon_commands()
        self.assertGreater(len(cmds), 1)
        parts = []
        for c in cmds:
            self.assertLessEqual(len(c.encode("utf-8")), 1400)
            m = re.match(r'^tellraw @a \[\{"text":""\},\{"text":"\[AI\] ","color":"aqua"\},\{"text":(".*")\}\]$', c)
            self.assertTrue(m, c)
            parts.append(json.loads(m.group(1)))
        self.assertEqual(" ".join(parts), text)
        rows = self.ai_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], text)

    def test_read_only_history_still_says(self):
        os.chmod(self.box.db, 0o444)
        r = self.box.vfg("say", "still goes")
        self.assertOneLine(r, 0, "^vfg: said, but not stored in history: ")
        self.assertEqual(len(self.box.rcon_commands()), 1)
        os.chmod(self.box.db, 0o644)
        self.assertEqual(self.ai_rows(), [])

    def test_rcon_down_exits_5(self):
        self.box.stop_rcon()
        self.assertOneLine(self.box.vfg("say", "hi"), 5)
        self.assertEqual(self.ai_rows(), [])


# =================================================================== history

class TestHistory(Case):
    def lines(self, *args):
        r = self.box.vfg("history", *args)
        self.assertOk(r)
        return r.stdout.splitlines()

    def test_default_is_every_line_oldest_first(self):
        out = self.lines()
        self.assertEqual(len(out), 6)
        self.assertIn(" Bob voice direct_ask 0.88 | old ask", out[0])
        self.assertRegex(out[2], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d Steve voice direct_ask 0\.93 \| build me a castle$")
        self.assertTrue(out[3].endswith(" Alex chat context 0.71 | that cave by spawn"))
        self.assertTrue(out[4].endswith(" Alex chat - | lol"))
        self.assertTrue(out[5].endswith(" AI ai - | on it — castle at spawn"))

    def test_since_relative_and_until(self):
        self.assertEqual(len(self.lines("--since", "-10m")), 4)
        self.assertEqual(len(self.lines("--since=-30m")), 5)
        self.assertEqual(len(self.lines("--since", "-2h", "--until", "-10m")), 2)
        self.assertEqual(len(self.lines("--since", "-90s")), 0)
        self.assertEqual(len(self.lines("--since", "-1d")), 6)

    def test_iso_and_bare_dates(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(len(self.lines("--until", tomorrow)), 6)
        self.assertEqual(len(self.lines("--since", tomorrow)), 0)
        t = iso(datetime.now(timezone.utc) - timedelta(minutes=10))[:19] + "Z"
        self.assertEqual(len(self.lines("--since", t)), 4)
        self.assertOneLine(self.box.vfg("history", "--since", "yesterday"), 2)
        self.assertGreaterEqual(len(self.lines("--since", today)), 0)

    def test_player_any_case(self):
        out = self.lines("--player", "steve")
        self.assertEqual([l.split(" | ")[1] for l in out], ["hmm", "build me a castle"])

    def test_classes(self):
        self.assertEqual([l.split(" | ")[1] for l in self.lines("--class", "unjudged")], ["lol"])
        self.assertEqual([l.split(" | ")[1] for l in self.lines("--class", "direct_ask,context", "--since", "-10m")],
                         ["build me a castle", "that cave by spawn"])
        self.assertEqual([l.split(" | ")[1] for l in self.lines("--class", "not_useful,unjudged")], ["hmm", "lol"])
        self.assertOneLine(self.box.vfg("history", "--class", "bogus"), 2)

    def test_source(self):
        self.assertEqual(self.lines("--source", "ai")[0].split(" | ")[1], "on it — castle at spawn")
        self.assertEqual(len(self.lines("--source", "voice")), 3)
        self.assertOneLine(self.box.vfg("history", "--source", "radio"), 2)

    def test_events(self):
        out = self.lines("--events")
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0].endswith(" server - | Server started"))
        self.assertRegex(out[2], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d death Steve \| Steve was slain by Zombie$")
        self.assertEqual(len(self.lines("--events", "--player", "STEVE", "--since", "-10m")), 2)

    def test_feed(self):
        out = self.lines("--feed")
        self.assertEqual(out, [l for _, l in self.box.feed_rows])
        self.assertEqual(self.lines("--feed", "--limit", "1"), [self.box.feed_rows[-1][1]])

    def test_limit_newest_n_oldest_first(self):
        out = self.lines("--limit", "2")
        self.assertEqual([l.split(" | ")[1] for l in out], ["lol", "on it — castle at spawn"])
        self.assertOneLine(self.box.vfg("history", "--limit", "1001"), 2)
        self.assertOneLine(self.box.vfg("history", "--limit", "0"), 2)

    def test_bad_combinations(self):
        for args in (["--class", "context", "--events"], ["--source", "ai", "--feed"],
                     ["--player", "Steve", "--feed"], ["--events", "--feed"],
                     ["--since", "-1m", "--until", "-1h"]):
            self.assertOneLine(self.box.vfg("history", *args), 2)

    def test_nothing_matching_prints_nothing(self):
        r = self.box.vfg("history", "--player", "Nobody")
        self.assertOk(r)
        self.assertEqual(r.stdout, "")

    def test_no_database_exits_1(self):
        os.remove(self.box.db)
        r = self.box.vfg("history")
        self.assertOneLine(r, 1, r"^vfg: no transcript history yet \(.*/vfg\.db missing\)$")
        self.assertFalse(os.path.exists(self.box.db))


# ===================================================================== usage

class TestUsage(Case):
    def usage(self):
        return self.box.query("SELECT id, at, component, model, input_tokens, output_tokens, cost_usd, ref FROM usage")

    def test_row_lands_with_the_pcs_time(self):
        before = iso(datetime.now(timezone.utc))
        r = self.box.vfg("usage", "add", "--component", "conversation", "--model", "sonnet",
                         "--in", "10", "--out", "5", "--cost", "0.0012", "--ref", "abcd1234")
        self.assertOk(r)
        self.assertEqual(r.stdout.strip(), "1")
        rows = self.usage()
        self.assertEqual(rows[0][2:], ("conversation", "sonnet", 10, 5, 0.0012, "abcd1234"))
        self.assertRegex(rows[0][1], ISO_RE)
        self.assertGreaterEqual(rows[0][1], before[:19])
        r = self.box.vfg("usage", "add", "--component", "jev", "--model", "jev-latest", "--in", "1", "--out", "0")
        self.assertEqual(r.stdout.strip(), "2")
        self.assertIsNone(self.usage()[1][6])

    def test_queued_when_down_then_inserted_first(self):
        r = self.box.vfg("usage", "add", "--component", "orchestrator", "--model", "opus",
                         "--in", "100", "--out", "50", "--ref", "T1", FAKE_SSH_DOWN="1")
        self.assertOneLine(r, 4, "cannot reach EC2")
        pending = os.path.join(self.box.vhome, "usage.pending.jsonl")
        with open(pending) as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        self.assertEqual(self.usage(), [])
        r = self.box.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "7", "--out", "3")
        self.assertOk(r)
        self.assertEqual(r.stdout.strip(), "2")
        rows = self.usage()
        self.assertEqual([x[2] for x in rows], ["orchestrator", "implementer"])
        self.assertLess(rows[0][1], rows[1][1])
        self.assertFalse(os.path.exists(pending) and os.path.getsize(pending))
        self.assertEqual([n for n in os.listdir(self.box.vhome) if n.startswith("usage.sending")], [])

    def test_completes_with_stdout_and_stderr_closed(self):
        cmd = "exec %s %s usage add --component conversation --model sonnet --in 1 --out 2 >&- 2>&-" % (PY, VFG)
        r = subprocess.run(["bash", "-c", cmd], env=self.box.env(), timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.usage()), 1)

    def test_bad_arguments(self):
        base = ["usage", "add", "--model", "m", "--in", "1", "--out", "1"]
        for extra in (["--component", "player"], ["--component", "jev", "--cost", "-1"],
                      ["--component", "jev", "--ref", "x" * 65], ["--component", "jev", "--in", "-3"]):
            self.assertOneLine(self.box.vfg(*(base + extra)), 2)
        self.assertEqual(self.usage(), [])


# ================================================================ watchers & lookout

class TestFiles(Case):
    def watch_json(self):
        with open(os.path.join(self.box.rvfg, "watch.json")) as f:
            return json.load(f)

    def test_watchers_set_replaces_the_whole_file(self):
        r = self.box.vfg("watchers", "set", "death,join", "--players", "Steve,Alex",
                         "--contains", "diamond", "--contains", r"^\[.*\] x+$")
        self.assertOk(r)
        want = {"events": ["join", "death"], "players": ["Steve", "Alex"], "contains": ["diamond", r"^\[.*\] x+$"]}
        self.assertEqual(json.loads(r.stdout), want)
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertEqual(self.watch_json(), want)
        self.assertEqual(list(self.watch_json().keys()), ["events", "players", "contains"])
        self.assertEqual(oct(os.stat(os.path.join(self.box.rvfg, "watch.json")).st_mode & 0o777), "0o644")
        self.assertOk(self.box.vfg("watchers", "set", "all"))
        self.assertEqual(self.watch_json(), {"events": ["join", "leave", "death", "advancement", "server"],
                                             "players": [], "contains": []})
        self.assertOk(self.box.vfg("watchers", "set", "none"))
        self.assertEqual(self.watch_json()["events"], [])
        r = self.box.vfg("watchers", "show")
        self.assertOk(r)
        self.assertEqual(json.loads(r.stdout), {"events": [], "players": [], "contains": []})

    def test_watchers_bad_input(self):
        for args in (["dance"], ["join", "--players", "bad name"], ["join", "--contains", "(unclosed"],
                     ["join,none"]):
            self.assertOneLine(self.box.vfg("watchers", "set", *args), 2)
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "watch.json")))

    def test_watchers_show_missing_prints_default(self):
        r = self.box.vfg("watchers", "show")
        self.assertOneLine(r, 0, "watch.json missing — the bridge uses the default")
        self.assertEqual(json.loads(r.stdout)["events"], ["join", "leave", "death", "advancement", "server"])

    def test_lookout(self):
        r = self.box.vfg("lookout", "set", "  look out for builds ~ and $HOME  ")
        self.assertOk(r)
        self.assertEqual(r.stdout, "lookout.md: 31 characters\n")
        path = os.path.join(self.box.rvfg, "lookout.md")
        with open(path) as f:
            self.assertEqual(f.read(), "look out for builds ~ and $HOME\n")
        r = self.box.vfg("lookout", "show")
        self.assertEqual(r.stdout, "look out for builds ~ and $HOME\n")
        r = self.box.vfg("lookout", "set", "-", input="line one\nline two\n")
        self.assertEqual(r.stdout, "lookout.md: 17 characters\n")
        with open(path) as f:
            self.assertEqual(f.read(), "line one\nline two\n")
        self.assertOneLine(self.box.vfg("lookout", "set", "x" * 8001), 2)
        self.assertOneLine(self.box.vfg("lookout", "set", "-", input="  \n"), 2)

    def test_lookout_show_missing(self):
        r = self.box.vfg("lookout", "show")
        self.assertOneLine(r, 0, "lookout.md missing")
        self.assertEqual(r.stdout, "")


# ====================================================================== feed

class TestFeed(Case):
    def test_post_prints_the_seq_and_appends_the_line(self):
        r = self.box.vfg("feed", "post", "SYS", "", "smoke ~ $HOME 'q' \"d\"")
        self.assertOk(r)
        self.assertEqual(r.stdout, "4\n")
        with open(self.box.feed) as f:
            last = f.read().splitlines()[-1]
        self.assertRegex(last, r"^SYS#4 \d\d:\d\d:\d\d \| smoke ~ \$HOME 'q' \"d\"$")
        call = self.box.ssh_calls()[-1]
        self.assertEqual(call[-1], "env VFG_DB=%s/vfg.db VFG_FEED=%s/feed.log python3 %s/lib/vfg_feed.py "
                                   "post SYS '' 'smoke ~ $HOME '\"'\"'q'\"'\"' \"d\"'"
                         % (self.box.rvfg, self.box.rvfg, self.box.rvfg))
        self.assertFalse(os.path.exists("/opt/vfg/feed.log.lock"))
        r = self.box.vfg("feed", "post", "GAME", "death Steve", "--", "-5 hearts")
        self.assertOk(r)
        r = self.box.vfg("feed", "post", "ORCH", "T7 done", "castle built")
        self.assertOk(r)
        with open(self.box.feed) as f:
            self.assertRegex(f.read().splitlines()[-1], r"^ORCH#6 \d\d:\d\d:\d\d T7 done \| castle built$")

    def test_bad_posts(self):
        for args in (["NOISE", "", "x"], ["SYS", "f", "x"], ["ORCH", "T7 finished", "x"],
                     ["ORCH", "T0 done", "x"], ["GAME", "death | Steve", "x"], ["LINE", "a\nb", "x"]):
            self.assertOneLine(self.box.vfg("feed", "post", *args), 2)
        with open(self.box.feed) as f:
            self.assertEqual(len(f.read().splitlines()), 3)


# ====================================================================== orch

class TestOrch(Case):
    def orch(self, name):
        return os.path.join(self.box.vhome, "orch", name)

    def dispatch(self, name):
        return os.path.join(self.box.vhome, "dispatch", name)

    def test_twenty_sends_at_once(self):
        procs = [subprocess.Popen([PY, VFG, "orch", "send", "task %d" % i, "--context", "c%d" % i],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.box.env())
                 for i in range(20)]
        ids = []
        for p in procs:
            o, e = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, e)
            ids.append(o.decode().strip())
            self.assertEqual(e.decode(), "vfg: dispatcher and orchestrator runner are not running — vfg orch start\n")
        self.assertEqual(sorted(ids, key=lambda t: int(t[1:])), ["T%d" % i for i in range(1, 21)])
        with open(self.dispatch("inbox.jsonl")) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual(len(rows), 20)
        for row in rows:
            self.assertEqual(list(row.keys()), ["id", "at", "task", "context"])
            self.assertRegex(row["at"], ISO_RE)
            self.assertEqual(row["context"], "c" + row["task"].split()[1])
        self.assertEqual(sorted(r["id"] for r in rows), sorted(ids))
        self.assertFalse(os.path.exists(self.orch("inbox.jsonl")))  # the orchestrator's: only hand writes it

    def test_send_defaults_and_refusals(self):
        r = self.box.vfg("orch", "send", "build ~ a 'castle'")
        self.assertEqual(r.stdout, "T1\n")
        with open(self.dispatch("inbox.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["context"], "")
        self.assertOneLine(self.box.vfg("orch", "send", " "), 2)
        with open(self.dispatch("inbox.jsonl"), "a") as f:
            f.write("not json\n{\"id\": \"T41\"}\n")
        self.assertEqual(self.box.vfg("orch", "send", "next").stdout, "T42\n")

    def test_send_ids_never_repeat_across_both_inboxes(self):
        os.makedirs(self.orch(""))
        with open(self.orch("inbox.jsonl"), "w") as f:  # sent before the dispatcher, or handed
            f.write('{"id": "T41", "at": "2026-10-08T20:00:00.000Z", "task": "old", "context": ""}\n')
        self.assertEqual(self.box.vfg("orch", "send", "one").stdout, "T42\n")
        self.assertEqual(self.box.vfg("orch", "send", "two").stdout, "T43\n")
        with open(self.orch("inbox.jsonl")) as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # untouched
        with open(self.dispatch("inbox.jsonl"), "a") as f:
            f.write('{"id": "T50", "at": "2026-10-09T10:00:00.000Z", "task": "x", "context": ""}\n')
        self.assertEqual(self.box.vfg("orch", "send", "three").stdout, "T51\n")
        # VFG_DISPATCH_DIR from the environment, then from the config file.
        other = os.path.join(self.box.root, "elsewhere")
        r = self.box.vfg("orch", "send", "four", VFG_DISPATCH_DIR=other)
        self.assertEqual(r.stdout, "T42\n")  # its inbox is empty; the orchestrator's has T41
        self.assertTrue(os.path.isfile(os.path.join(other, "inbox.jsonl")))
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_DISPATCH_DIR=%s\n" % (self.box.key, other))
        self.assertEqual(self.box.vfg("orch", "send", "five").stdout, "T43\n")

    def test_start_stop_status(self):
        r = self.box.vfg("orch", "status")
        self.assertOneLine(r, 1, "^vfg: dispatcher and orchestrator runner are not running$")
        self.assertEqual(r.stdout, "dispatcher    stopped\norchestrator  stopped\nopen: none\n"
                                   "with the orchestrator: none\n")
        self.assertEqual(self.box.vfg("orch", "stop").stdout, "dispatcher    not running\n"
                                                              "orchestrator  not running\n")

        r = self.box.vfg("orch", "start", CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli", VFG_ORCH_RUNNER="1")
        self.assertOk(r)
        m = re.match(r"^dispatcher    started pid (\d+)\norchestrator  started pid (\d+)\n$", r.stdout)
        self.assertTrue(m, r.stdout)
        dpid, pid = int(m.group(1)), int(m.group(2))
        for p, mine, other in ((dpid, b"VFG_DISPATCHER=1", b"VFG_ORCH_RUNNER="),
                               (pid, b"VFG_ORCH_RUNNER=1", b"VFG_DISPATCHER=")):
            self.assertTrue(alive(p))  # outlived the vfg that started it
            with open("/proc/%d/environ" % p, "rb") as f:
                env = f.read().split(b"\0")
            self.assertIn(mine, env)
            self.assertFalse(any(e.startswith(other) for e in env))
            self.assertFalse(any(e.startswith((b"CLAUDECODE=", b"CLAUDE_CODE_ENTRYPOINT=")) for e in env))
            self.assertIn(b"VFG_DISPATCH_DIR=" + self.dispatch("").rstrip("/").encode(), env)
            self.assertIn(b"VFG_ORCH_DIR=" + self.orch("").rstrip("/").encode(), env)
            self.assertIn(b"VFG_REPO=" + self.box.repo.encode(), env)
            self.assertEqual(os.getpgid(p), p)
        with open(self.dispatch("dispatcher.pid")) as f:
            self.assertEqual(int(f.read()), dpid)
        r = self.box.vfg("orch", "start")
        self.assertEqual(r.stdout, "dispatcher    running pid %d\norchestrator  running pid %d\n" % (dpid, pid))
        with open(self.orch("runner.log")) as f:
            log = f.read()
        self.assertRegex(log, r"--- vfg orch start \d{4}-\d\d-\d\dT[\d:.]+Z ---\nfake runner %d up" % pid)
        self.assertEqual(log.count("--- vfg orch start"), 1)
        with open(self.dispatch("dispatcher.log")) as f:
            log = f.read()
        self.assertRegex(log, r"^--- vfg orch start \d{4}-\d\d-\d\dT[\d:.]+Z ---\nfake dispatcher %d up\n$" % dpid)

        self.assertEqual(self.box.vfg("orch", "send", "one").stderr, "")  # both running: no warning
        self.box.vfg("orch", "send", "two")
        r = self.box.vfg("orch", "status")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^dispatcher    running pid %d since \d\d:\d\d:\d\d\n"
                                   r"orchestrator  running pid %d since \d\d:\d\d:\d\d\n"
                                   r"open: T1 T2\nwith the orchestrator: none\n$" % (dpid, pid))
        r = self.box.vfg("orch", "hand", json.dumps([{"id": "T2", "at": "2026-10-09T10:00:00.000Z",
                                                      "task": "two", "context": ""}]))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "handed T2\n", ""))
        self.assertIn("open: T1 T2\nwith the orchestrator: T2\n", self.box.vfg("orch", "status").stdout)

        r = self.box.vfg("orch", "reply", "T1", "progress", "halfway")
        self.assertOk(r)
        self.assertIn("open: T1 T2\n", self.box.vfg("orch", "status").stdout)
        r = self.box.vfg("orch", "reply", "T1", "done", "castle", "built")
        self.assertOk(r)
        seq = int(r.stdout)
        with open(self.box.feed) as f:
            self.assertRegex(f.read().splitlines()[-1], r"^ORCH#%d \d\d:\d\d:\d\d T1 done \| castle built$" % seq)
        self.assertIn("open: T2\nwith the orchestrator: T2\n", self.box.vfg("orch", "status").stdout)
        with open(self.orch("replies.jsonl")) as f:
            replies = [json.loads(l) for l in f]
        self.assertEqual([(x["id"], x["status"]) for x in replies], [("T1", "progress"), ("T1", "done")])
        self.assertEqual(list(replies[-1].keys()), ["id", "at", "status", "seq"])
        self.assertEqual(replies[-1]["seq"], seq)
        r = self.box.vfg("orch", "reply", "T1", "failed", "again")
        self.assertOneLine(r, 0, "already closed")
        self.box.vfg("orch", "reply", "T2", "done", "ok")
        self.assertIn("open: none\nwith the orchestrator: none\n", self.box.vfg("orch", "status").stdout)

        with open(self.orch("child.pid")) as f:
            child = int(f.read())
        self.assertTrue(alive(child))
        r = self.box.vfg("orch", "stop")
        self.assertEqual(r.stdout, "dispatcher    stopped pid %d\norchestrator  stopped pid %d\n" % (dpid, pid))
        self.assertTrue(wait_for(lambda: not alive(pid) and not alive(child) and not alive(dpid), 5))
        # The dispatcher was waited for (it took a second to hand on) before the runner was stopped.
        with open(self.orch("order")) as f:
            order = [l.split() for l in f.read().splitlines()]
        self.assertEqual([o[:2] for o in order], [["dispatcher", "handed"], ["orchestrator", "stopping"]])
        self.assertLessEqual(float(order[0][2]), float(order[1][2]))
        self.assertFalse(os.path.exists(self.orch("runner.pid")))
        self.assertFalse(os.path.exists(self.dispatch("dispatcher.pid")))
        self.assertOneLine(self.box.vfg("orch", "status"), 1)

    def test_one_down_is_named(self):
        os.chmod(self.box.run_path, 0o644)
        r = self.box.vfg("orch", "start")
        self.assertOneLine(r, 1, r"^vfg: .*/orchestrator/run is missing or not executable$")
        m = re.match(r"^dispatcher    started pid (\d+)\norchestrator  not started\n$", r.stdout)
        self.assertTrue(m, r.stdout)
        r = self.box.vfg("orch", "send", "x")
        self.assertEqual(r.stderr, "vfg: orchestrator runner is not running — vfg orch start\n")
        r = self.box.vfg("orch", "status")
        self.assertOneLine(r, 1, "^vfg: orchestrator runner is not running$")
        self.assertTrue(r.stdout.startswith("dispatcher    running pid %s since" % m.group(1)))
        r = self.box.vfg("orch", "stop")
        self.assertEqual(r.stdout, "dispatcher    stopped pid %s\norchestrator  not running\n" % m.group(1))

    def test_start_refusals(self):
        os.chmod(self.box.run_path, 0o644)
        os.chmod(self.box.dispatcher_path, 0o644)
        r = self.box.vfg("orch", "start")
        self.assertOneLine(r, 1, r"^vfg: .*/orchestrator/run is missing or not executable; "
                                 r".*/dispatcher/run is missing or not executable$")
        self.assertEqual(r.stdout, "dispatcher    not started\norchestrator  not started\n")
        os.chmod(self.box.run_path, 0o755)
        with open(self.box.dispatcher_path, "w") as f:
            f.write("#!/usr/bin/env bash\necho broken\nexit 7\n")
        os.chmod(self.box.dispatcher_path, 0o755)
        r = self.box.vfg("orch", "start")
        self.assertOneLine(r, 1, r"^vfg: dispatcher exited at once \(code 7\) — see .*dispatch/dispatcher\.log$")
        self.assertRegex(r.stdout, r"^dispatcher    exited at once \(code 7\)\norchestrator  started pid \d+\n$")
        self.assertFalse(os.path.exists(self.dispatch("dispatcher.pid")))
        with open(self.dispatch("dispatcher.log")) as f:
            self.assertIn("broken\n", f.read())
        self.assertOk(self.box.vfg("orch", "stop"))
        with open(self.box.run_path, "w") as f:
            f.write("#!/usr/bin/env bash\necho broken\nexit 7\n")
        r = self.box.vfg("orch", "start")
        self.assertOneLine(r, 1, r"^vfg: orchestrator exited at once \(code 7\) — see .*runner\.log; "
                                 r"dispatcher exited at once \(code 7\) — see .*dispatcher\.log$")
        self.assertFalse(os.path.exists(self.orch("runner.pid")))

    def test_stale_pid_is_not_running(self):
        for folder, name in ((self.orch(""), "runner.pid"), (self.dispatch(""), "dispatcher.pid")):
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, name), "w") as f:
                f.write("%d\n" % os.getpid())  # alive, but no VFG_ORCH_RUNNER=1 or VFG_DISPATCHER=1
        self.assertOneLine(self.box.vfg("orch", "status"), 1, "are not running$")
        self.assertFalse(os.path.exists(self.orch("runner.pid")))
        self.assertFalse(os.path.exists(self.dispatch("dispatcher.pid")))

    def test_reply_refusals_and_down(self):
        for args in (["7", "done", "x"], ["T7", "finished", "x"], ["T0", "done", "x"]):
            self.assertOneLine(self.box.vfg("orch", "reply", *args), 2)
        self.assertOneLine(self.box.vfg("orch", "reply", "T7", "done", "x", FAKE_SSH_DOWN="1"), 4)
        self.assertFalse(os.path.exists(self.orch("replies.jsonl")))


class TestHand(Case):
    def inbox(self):
        try:
            with open(os.path.join(self.box.vhome, "orch", "inbox.jsonl")) as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []

    def hand(self, lines, **env):
        return self.box.vfg("orch", "hand", lines if isinstance(lines, str) else json.dumps(lines), **env)

    def test_lines_land_in_the_inbox_shape(self):
        before = iso(datetime.now(timezone.utc))
        r = self.hand([{"id": "T3", "at": "2026-10-09T10:00:00.000Z", "task": "build a castle ~ $HOME",
                        "context": "Steve asked (#812)"},
                       {"task": "a tower", "id": "T4"},
                       {"id": "T5", "task": "a wall", "context": None, "picks": ["extra keys are dropped"]}])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "handed T3\nhanded T4\nhanded T5\n")
        self.assertEqual(r.stderr, "vfg: orchestrator runner is not running — vfg orch start\n")
        rows = self.inbox()
        self.assertEqual([list(x.keys()) for x in rows], [["id", "at", "task", "context"]] * 3)
        self.assertEqual(rows[0], {"id": "T3", "at": "2026-10-09T10:00:00.000Z", "task": "build a castle ~ $HOME",
                                   "context": "Steve asked (#812)"})
        self.assertRegex(rows[1]["at"], ISO_RE)
        self.assertGreaterEqual(rows[1]["at"], before[:19])
        self.assertEqual((rows[1]["task"], rows[1]["context"]), ("a tower", ""))
        self.assertEqual(rows[2]["context"], "")

    def test_idempotent(self):
        line = {"id": "T7", "at": "2026-10-09T10:00:00.000Z", "task": "x", "context": ""}
        self.assertEqual(self.hand([line]).stdout, "handed T7\n")
        r = self.hand([line, dict(line, id="T8"), dict(line, id="T8", task="twice in one call")])
        self.assertEqual((r.returncode, r.stdout), (0, "skipped T7 (already handed)\nhanded T8\n"
                                                       "skipped T8 (already handed)\n"))
        r = self.hand([line])
        self.assertEqual((r.returncode, r.stdout), (0, "skipped T7 (already handed)\n"))
        self.assertEqual(r.stderr, "")  # nothing handed: nothing to warn about
        self.assertEqual([x["id"] for x in self.inbox()], ["T7", "T8"])
        self.assertEqual(self.inbox()[1]["task"], "x")

    def test_one_write_under_the_lock(self):
        mod = vfg_module("vfgmod_hand")
        writes = []
        real = mod.append_line

        def spy(path, line, mode=0o600):
            writes.append((path, line))
            return real(path, line, mode)
        mod.append_line = spy
        lines = [{"id": "T%d" % i, "at": "2026-10-09T10:00:00.000Z", "task": "t%d" % i, "context": ""}
                 for i in range(1, 6)]
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.box.env())
        try:
            with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()):
                code = mod.main(["orch", "hand", json.dumps(lines)])
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(code, 0)
        self.assertEqual(o.getvalue().splitlines(), ["handed T%d" % i for i in range(1, 6)])
        inbox = [w for w in writes if w[0] == os.path.join(self.box.vhome, "orch", "inbox.jsonl")]
        self.assertEqual(len(inbox), 1)
        self.assertEqual([json.loads(l) for l in inbox[0][1].splitlines()], lines)

    def test_concurrent_hands_write_each_id_once(self):
        line = lambda i: {"id": "T%d" % i, "at": "2026-10-09T10:00:00.000Z", "task": "t%d" % i, "context": ""}
        procs = [subprocess.Popen([PY, VFG, "orch", "hand", json.dumps([line(j) for j in range(i % 3 + 1, i % 3 + 4)])],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.box.env())
                 for i in range(10)]
        handed = []
        for p in procs:
            o, e = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, e)
            handed += [l.split()[1] for l in o.decode().splitlines() if l.startswith("handed ")]
        self.assertEqual(sorted(handed), ["T1", "T2", "T3", "T4", "T5"])
        self.assertEqual(sorted(x["id"] for x in self.inbox()), ["T1", "T2", "T3", "T4", "T5"])

    def test_bad_input_exits_2_and_writes_nothing(self):
        good = {"id": "T1", "task": "x"}
        for text, why in (("not json", "not JSON"), ("{}", "a JSON list"), ("[]", "a JSON list"),
                          ("[1]", "line 1 is not a JSON object"),
                          ([good, {"id": "7", "task": "x"}], "line 2: the id is T<n>"),
                          ([{"id": "T0", "task": "x"}], "the id is T<n>"),
                          ([{"id": "T1", "task": "  "}], "T1: empty task"),
                          ([{"id": "T1"}], "T1: empty task"),
                          ([{"id": "T1", "task": 5}], "T1: empty task"),
                          ([good, {"id": "T2", "task": "x", "context": 5}], "T2: context is text"),
                          ([{"id": "T1", "task": "x", "at": 5}], "T1: at is an ISO-8601 time")):
            r = self.hand(text)
            self.assertOneLine(r, 2, re.escape(why))
            self.assertEqual(r.stdout, "")
        self.assertOneLine(self.box.vfg("orch", "hand"), 2)
        self.assertEqual(self.inbox(), [])


# =================================================================== scripts

class TestScripts(Case):
    def deployed(self, *p):
        return os.path.join(self.box.rvfg, "scripts", *p)

    def test_deploy_all_and_one(self):
        r = self.box.vfg("script", "deploy")
        self.assertOk(r)
        self.assertEqual(r.stdout, "deployed: _lib hello loop\n")
        self.assertTrue(os.path.isfile(self.deployed("hello", "main.py")))
        self.assertTrue(os.path.isfile(self.deployed("_lib", "mc.py")))
        self.assertFalse(os.path.exists(self.deployed("_test")))
        self.assertFalse(os.path.exists(self.deployed("hello", ".secret")))
        self.assertFalse(os.path.exists(self.deployed("hello", "__pycache__")))
        self.assertFalse(os.path.exists(self.deployed("_lib", "__pycache__")))
        self.assertTrue(os.path.isdir(os.path.join(self.box.rvfg, "run")))
        os.makedirs(self.deployed("orphan"))
        with open(self.deployed("hello", "stale.txt"), "w") as f:
            f.write("old\n")
        r = self.box.vfg("script", "deploy", "hello")
        self.assertEqual(r.stdout, "deployed: _lib hello\n")
        self.assertFalse(os.path.exists(self.deployed("hello", "stale.txt")))  # swapped whole
        self.assertTrue(os.path.isdir(self.deployed("orphan")))  # no local twin: left alone
        self.assertEqual([n for n in os.listdir(self.deployed()) if n.startswith(".")], [])

    def test_folder_without_readme_is_skipped(self):
        os.makedirs(os.path.join(self.box.repo, "scripts", "broken"))
        with open(os.path.join(self.box.repo, "scripts", "broken", "main.py"), "w") as f:
            f.write("print(1)\n")
        r = self.box.vfg("script", "deploy")
        self.assertOneLine(r, 1, "skipped.*: broken$")
        self.assertEqual(r.stdout, "deployed: _lib hello loop\n")
        self.assertFalse(os.path.exists(self.deployed("broken")))
        self.assertOneLine(self.box.vfg("script", "deploy", "broken"), 1, "has no README.md")
        self.assertOneLine(self.box.vfg("script", "deploy", "Bad.Name"), 2)

    def test_run_wait_prints_output_and_exit(self):
        r = self.box.vfg("script", "run", "hello", "--wait", "5")
        self.assertOk(r)
        self.assertEqual(r.stdout, "hello \nlib fake mc script hello\n")
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "run", "hello.pid")))
        with open(os.path.join(self.box.rvfg, "run", "hello.log")) as f:
            self.assertRegex(f.readline(), r'^--- \d{4}-\d\d-\d\dT[\d:.]+Z run hello \[\] ---$')
        r = self.box.vfg("script", "run", "--wait", "5", "hello", "~", "$x", "a b", "--", "--wait")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0], "hello ~ $x a b --wait")
        r = self.box.vfg("script", "run", "hello", "fail", "--wait", "5")
        self.assertOneLine(r, 1, "^vfg: hello exited with code 3$")
        self.assertIn("hello fail", r.stdout)
        self.assertOneLine(self.box.vfg("script", "run", "hello", self.box.home + "/x"), 2, "home folder")

    def test_wait_bare_with_seconds_and_equals(self):
        # Without --wait: checked after 2 s, so a 3 s nap is still running.
        r = self.box.vfg("script", "run", "hello", "nap")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^running hello pid \d+ — vfg script logs hello\n$")
        pid = int(r.stdout.split()[3])
        self.assertTrue(wait_for(lambda: not alive(pid), 10))
        # A bare --wait, last: waits (30 s) for the nap to end and prints it.
        r = self.box.vfg("script", "run", "hello", "nap", "--wait")
        self.assertOk(r)
        self.assertEqual(r.stdout, "hello nap\nlib fake mc script hello\nwoke\n")
        # A bare --wait before the name: the name is not swallowed as seconds.
        r = self.box.vfg("script", "run", "--wait", "hello", "nap")
        self.assertOk(r)
        self.assertTrue(r.stdout.startswith("hello nap\n"), r.stdout)
        self.assertIn("woke", r.stdout)
        # A word after --wait is not seconds: it passes to the script.
        r = self.box.vfg("script", "run", "hello", "--wait", "world")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0], "hello world")
        # --wait S and --wait=S, before and after the name.
        for args in (["hello", "--wait", "5"], ["hello", "--wait=5"], ["--wait", "5", "hello"],
                     ["--wait=5", "hello"], ["--wait", "4.5", "--no-deploy", "hello"]):
            r = self.box.vfg("script", "run", *args)
            self.assertOk(r)
            self.assertEqual(r.stdout, "hello \nlib fake mc script hello\n", args)
        # --wait 1 is shorter than the nap: started, not waited out.
        r = self.box.vfg("script", "run", "hello", "nap", "--wait", "1")
        self.assertRegex(r.stdout, r"^running hello pid \d+")
        self.assertTrue(wait_for(lambda: not alive(int(r.stdout.split()[3])), 10))
        r = self.box.vfg("--timeout", "60", "script", "run", "hello", "--wait")
        self.assertEqual(r.stdout, "hello \nlib fake mc script hello\n")
        self.assertOneLine(self.box.vfg("script", "run", "hello", "--wait=soon"), 2, "--wait takes seconds")
        self.assertOneLine(self.box.vfg("script", "run", "--wait"), 2, "which script")
        r = self.box.vfg("script", "run", "--help")
        self.assertOk(r)
        self.assertIn("--wait [S]", r.stdout)

    def test_run_passes_the_owner_from_the_config(self):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_OWNER=Sunny\n" % self.box.key)
        r = self.box.vfg("script", "run", "loop")
        self.assertOk(r)
        pid = int(re.match(r"^running loop pid (\d+)", r.stdout).group(1))
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        self.assertIn(b"VFG_OWNER=Sunny", env)
        self.assertOk(self.box.vfg("script", "stop", "loop"))
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_OWNER=not a name\n" % self.box.key)
        r = self.box.vfg("script", "run", "loop")
        self.assertOneLine(r, 3, r"^vfg: not configured: VFG_OWNER must be a Minecraft name$")

    def test_run_loop_twice_stop_logs_list(self):
        r = self.box.vfg("script", "run", "loop")
        self.assertOk(r)
        m = re.match(r"^running loop pid (\d+) — vfg script logs loop\n$", r.stdout)
        self.assertTrue(m, r.stdout)
        pid = int(m.group(1))
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        self.assertIn(b"VFG_SCRIPT=loop", env)
        self.assertIn(b"PYTHONPATH=" + self.deployed("_lib").encode(), env)
        self.assertIn(b"PYTHONUNBUFFERED=1", env)
        self.assertIn(b"VFG_MC_PROPERTIES=" + os.path.join(self.box.rmc, "server.properties").encode(), env)
        self.assertFalse(any(e.startswith(b"VFG_OWNER=") for e in env))  # none configured
        # Like real ssh, the fake carries none of the caller's settings across.
        self.assertFalse(any(e.startswith((b"VFG_HOME=", b"VFG_REMOTE_VFG=", b"FAKE_SSH_LOG="))
                             for e in env))
        self.assertEqual(os.getpgid(pid), pid)  # setsid kept the pid
        r = self.box.vfg("script", "run", "loop")
        self.assertOneLine(r, 1, r"^vfg: loop is running \(pid %d\) — vfg script stop loop$" % pid)
        self.assertOk(self.box.vfg("script", "run", "hello", "--wait", "5"))
        r = self.box.vfg("script", "list")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^hello stopped\nloop running pid %d since \d\d:\d\d:\d\d\n$" % pid)
        time.sleep(0.5)
        r = self.box.vfg("script", "logs", "loop", "-n", "2")
        self.assertOk(r)
        self.assertEqual(len(r.stdout.splitlines()), 2)
        self.assertTrue(all(l.startswith("tick ") for l in r.stdout.splitlines()))
        r = self.box.vfg("script", "stop", "loop")
        self.assertEqual(r.stdout, "stopped loop\n")
        self.assertTrue(wait_for(lambda: not alive(pid), 5))
        self.assertEqual(self.box.vfg("script", "stop", "loop").stdout, "loop not running\n")
        self.assertIn("loop stopped", self.box.vfg("script", "list").stdout)

    def test_logs_follow_until_killed(self):
        self.assertOk(self.box.vfg("script", "run", "loop"))
        p = self.box.popen("script", "logs", "loop", "-n", "1", "--follow")
        got = []
        end = time.time() + 10
        while len(got) < 4 and time.time() < end:
            got.append(p.stdout.readline().decode())
        self.assertEqual(len(got), 4)
        self.assertTrue(all(g.startswith("tick ") for g in got), got)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(10), 0)
        self.box.vfg("script", "stop", "loop")

    def test_refusals(self):
        self.assertOneLine(self.box.vfg("script", "logs", "hello"), 1, "no log for hello")
        self.assertOneLine(self.box.vfg("script", "run", "--no-deploy", "hello"), 1, "not deployed")
        self.assertOneLine(self.box.vfg("script", "run", "nosuch"), 1, "no scripts/nosuch/")
        self.assertOneLine(self.box.vfg("script", "run"), 2)
        self.assertOneLine(self.box.vfg("script", "stop", "../x"), 2)

    def test_unsafe_tar_member_refused(self):
        def tar_with(name, data=b"x", kind=tarfile.REGTYPE):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                ti = tarfile.TarInfo(name)
                ti.type = kind
                if kind == tarfile.SYMTYPE:
                    ti.linkname = "/etc/passwd"
                    tf.addfile(ti)
                else:
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
            return base64.b64encode(buf.getvalue()).decode()
        import importlib.machinery
        import importlib.util
        sys.dont_write_bytecode = True
        loader = importlib.machinery.SourceFileLoader("vfgmod", VFG)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("vfgmod", loader))
        loader.exec_module(mod)
        for name, kind in (("hello/../../escape.txt", tarfile.REGTYPE), ("/abs.txt", tarfile.REGTYPE),
                           ("hello/link", tarfile.SYMTYPE)):
            prog = mod.program("scripts.py", {"vfg": self.box.rvfg, "mc": self.box.rmc,
                                              "op": "deploy", "tar": tar_with(name, kind=kind)})
            r = subprocess.run(["python3", "-u", "-"], input=prog, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=self.box.env())
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn(b"deploy refused", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.box.root, "box", "escape.txt")))
        self.assertFalse(os.path.exists(self.deployed("hello")))


# ============================================================ actions & places

def scripts_test(name):
    """A module from ../scripts/_test, by path: tests/ has a fake_rcon.py of its own."""
    if SCRIPTS_TEST not in sys.path:
        sys.path.append(SCRIPTS_TEST)  # fake_places_world imports mc_26_3
    spec = importlib.util.spec_from_file_location("scripts_test_" + name, os.path.join(SCRIPTS_TEST, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HAVE_WORLD = all(os.path.isfile(os.path.join(SCRIPTS_TEST, n)) for n in ("fake_places_world.py", "fake_rcon.py", "mc_26_3.py"))
REAL_MC = os.path.join(REPO, "scripts", "_lib", "mc.py")

PROBE = """import sys
from mc import rcon, log
import places


def run(args):
    print("chatter on stdout")  # never reaches the JSON line
    log("a log line")
    if args.get("boom"):
        raise ValueError("boom")
    return {"ok": True, "summary": rcon("list"), "args": args, "storage": places.STORAGE,
            "line": places.line("castle", {"pos": [1, 2, 3], "dim": "minecraft:the_nether", "by": "T1"}),
            "mc": sys.modules["mc"].__file__}
"""


class World(Case):
    """RCON answered by fake_places_world.FakeWorld, Minecraft 26.3's recorded replies."""
    box_kwargs = {"rcon": False}

    def setUp(self):
        if not HAVE_WORLD:
            self.skipTest("no scripts/_test/fake_places_world.py beside the toolkit")
        Case.setUp(self)
        self.world = scripts_test("fake_places_world").FakeWorld(players={"Steve": (197.5, 68.0, 251.5)})
        self.world.add("Alex", (10.2, -5.0, 3.9), dim="minecraft:the_nether")
        self.server = scripts_test("fake_rcon").FakeRcon(self.box.password, port=self.box.port,
                                                         responder=self.world.respond).start()

    def tearDown(self):
        if HAVE_WORLD:
            self.server.stop()
        Case.tearDown(self)


class TestAction(Case):
    def setUp(self):
        Case.setUp(self)
        for rel, text in (("actions/__init__.py", ""), ("actions/command/__init__.py", ""),
                          ("actions/command/action.py", "# the class, imported on the PC only\n"),
                          ("actions/probe/handler.py", PROBE), ("actions/save_place/action.py", "\n")):
            path = os.path.join(self.box.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        os.makedirs(os.path.join(self.box.rvfg, "scripts", "_lib"))
        shutil.copy(REAL_MC, os.path.join(self.box.rvfg, "scripts", "_lib", "mc.py"))  # vfg script deploy

    def run_action(self, name, fields, *extra, **env):
        return self.box.vfg("action", "run", name, "--args", json.dumps(fields), "--task", "T7", *extra, **env)

    def result(self, r):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertTrue(r.stdout.endswith("\n"))
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)
        return json.loads(r.stdout)

    def test_command_runs_in_order(self):
        r = self.run_action("command", {"commands": ["/time set day", "list"]})
        self.assertEqual(self.result(r), {"ok": True, "replies": ["Set the time to 1000",
                                                                  "There are 1 of a max of 20 players online: Steve"],
                                          "summary": "Set the time to 1000; There are 1 of a max of 20 players "
                                                     "online: Steve", "failed": None})
        self.assertEqual(self.box.rcon_commands(), ["time set day", "list"])

    def test_command_stops_at_minecraft_error_and_still_exits_0(self):
        r = self.run_action("command", {"commands": ["time set day", "bogus", "list"], "action": "command"})
        res = self.result(r)
        self.assertEqual((res["ok"], res["failed"]), (False, "bogus"))
        self.assertIn("Unknown or incomplete command", res["summary"])
        self.assertEqual(self.box.rcon_commands(), ["time set day", "bogus"])
        open(self.box.rcon_log, "w").close()
        res = self.result(self.run_action("command", {"commands": ["give @a minecraft:diamond", "stop"]}))
        self.assertFalse(res["ok"])  # the owner's command: refused on EC2 before anything is sent
        self.assertEqual(self.box.rcon_commands(), [])

    def test_command_refusals(self):
        for fields, code, why in (({"commands": ["tp @a %s/x" % self.box.home]}, 2, "your shell expanded a ~"),
                                  ({"commands": ["say " + "x" * 1496]}, 1, "command is 1500 bytes"),
                                  ({"commands": []}, 2, "at least one command"),
                                  ({"commands": "time set day"}, 2, "at least one command"),
                                  ({"commands": ["time set day", " / "]}, 2, "empty command"),
                                  ({"commands": ["x"], "player": "Steve"}, 2, "command takes only commands, not player"),
                                  ({"action": "teleport", "commands": ["x"]}, 2, "--args is not for command")):
            self.assertOneLine(self.run_action("command", fields), code, why)
        for args, why in ((["action", "run", "command", "--args", "{bad", "--task", "T7"], "--args is not JSON"),
                          (["action", "run", "command", "--args", "[1]", "--task", "T7"], "a JSON object"),
                          (["action", "run", "command", "--args", "{}", "--task", "7"], "--task is T<n>"),
                          (["action", "run", "command", "--args", "{}"], "--task"),
                          (["action", "run", "../x", "--args", "{}", "--task", "T7"], "not an action name"),
                          (["action", "run", "command", "--args", "{}", "--task", "T7", "--timeout", "0"],
                           "--timeout is seconds")):
            self.assertOneLine(self.box.vfg(*args), 2, re.escape(why))
        self.assertOneLine(self.run_action("nosuch", {}), 1, r"^vfg: no actions/nosuch/ in ")
        self.assertOneLine(self.run_action("save_place", {"name": "x"}), 1,
                           r"^vfg: actions/save_place/ has no handler\.py, and only command runs without one$")
        self.assertEqual(self.box.rcon_commands(), [])
        self.assertEqual(self.box.ssh_calls(), [])

    def test_handler_runs_as_a_module_with_places(self):
        r = self.run_action("probe", {"player": "Steve", "n": 3})
        res = self.result(r)
        self.assertEqual(res, {"ok": True, "summary": "There are 1 of a max of 20 players online: Steve",
                               "args": {"player": "Steve", "n": 3, "task": "T7"}, "storage": "everhear:places",
                               "line": "castle 1 2 3 the_nether (T1)",
                               "mc": os.path.join(self.box.rvfg, "scripts", "_lib", "mc.py")})
        self.assertIn("chatter on stdout", r.stderr)  # the handler's prints went to stderr
        self.assertEqual(self.box.rcon_commands(), ["list"])

    def test_handler_exception_is_a_result(self):
        res = self.result(self.run_action("probe", {"boom": True}))
        self.assertEqual(res, {"ok": False, "summary": "handler error: ValueError: boom"})

    def test_could_not_run(self):
        self.assertOneLine(self.run_action("probe", {}, FAKE_SSH_DOWN="1"), 4, "cannot reach EC2")
        os.remove(os.path.join(self.box.rvfg, "scripts", "_lib", "mc.py"))
        self.assertOneLine(self.run_action("probe", {}), 3, r"_lib/mc\.py is missing: vfg script deploy$")
        self.box.stop_rcon()
        self.assertOneLine(self.run_action("command", {"commands": ["list"]}), 5, "minecraft is not running")

    def test_timeout_is_124_and_keeps_the_shared_master(self):
        r = self.run_action("command", {"commands": ["list"]}, "--timeout", "1", FAKE_SSH_DELAY="4")
        self.assertOneLine(r, 124, r"^vfg: timed out: command did not finish within 1 s$")
        self.assertEqual(r.stdout, "")
        self.assertFalse(any("exit" in c and "-O" in c for c in self.box.ssh_calls()))
        r = self.box.vfg("--timeout", "1", "action", "run", "command", "--args", '{"commands": ["list"]}',
                         "--task", "T7", FAKE_SSH_DELAY="4")
        self.assertOneLine(r, 124, "within 1 s$")

    @unittest.skipUnless(HAVE_WORLD and os.path.isfile(os.path.join(REPO, "actions", "teleport", "handler.py")),
                         "no actions/teleport/handler.py or scripts/_test/fake_places_world.py beside the toolkit")
    def test_the_real_teleport_handler_reaches_a_saved_place(self):
        self.box.stop_rcon()
        world = scripts_test("fake_places_world").FakeWorld(players={"Steve": (0.5, 64.0, 0.5)})
        world.save_place("castle", [197, 68, 251], by="T23")
        server = scripts_test("fake_rcon").FakeRcon(self.box.password, port=self.box.port,
                                                    responder=world.respond).start()
        try:
            shutil.rmtree(os.path.join(self.box.repo, "actions"))
            os.symlink(os.path.join(REPO, "actions"), os.path.join(self.box.repo, "actions"))
            res = self.result(self.run_action("teleport", {"player": "Steve", "to": "place", "target": "castle",
                                                           "dimension": "overworld"}))
        finally:
            server.stop()
        self.assertTrue(res["ok"], res)
        # a saved place is reached exactly when its spot is safe, not 8 blocks round it
        self.assertEqual(world.players["Steve"].pos, [197.5, 68.0, 251.5])
        self.assertIn("data get storage everhear:places castle", world.sent)


FAKE_JAVA = """#!%(py)s
# A stand-in for the data generator: java -DbundlerMainClass=... -jar <jar> --reports --output <dir>.
import json, os, sys, time
argv = sys.argv[1:]
out = argv[argv.index("--output") + 1]
with open(%(record)r, "w") as f:
    json.dump({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid()}, f)
print("[ServerMain/INFO]: Starting provider: vanilla/Command Syntax", flush=True)
sys.stderr.write("a warning on stderr\\n")
os.makedirs("logs", exist_ok=True)  # as log4j does, in the working directory
with open(os.path.join("logs", "latest.log"), "w") as f:
    f.write("log\\n")
mode = %(mode)r
if mode == "fail":
    print("Exception in thread main: boom")
    sys.exit(1)
if mode == "slow":
    time.sleep(60)
if mode != "nothing":
    with open(%(tree)r) as f:
        tree = json.load(f)
    os.makedirs(os.path.join(out, "reports"), exist_ok=True)
    with open(os.path.join(out, "reports", "commands.json"), "w") as f:
        json.dump(tree, f, indent=2)
"""

TREE = {"type": "root", "children": {
    "give": {"type": "literal", "children": {"targets": {"type": "argument", "parser": "minecraft:entity",
                                                          "properties": {"amount": "multiple", "type": "players"},
                                                          "executable": True}}},
    "time": {"type": "literal", "children": {"set": {"type": "literal", "executable": True}}}}}


class TestRefs(Case):
    def make_jar(self, version=None, raw=None):
        jar = os.path.join(self.box.rmc, "server.jar")
        if raw is not None:
            with open(jar, "wb") as f:
                f.write(raw)
            return
        with zipfile.ZipFile(jar, "w") as z:
            if version is not None:
                z.writestr("version.json", json.dumps(version, indent=4))
            z.writestr("META-INF/versions.list", "x\\t26.3\\tversions/26.3/server-26.3.jar\n")

    def java(self, mode="ok", tree=TREE):
        self.record = os.path.join(self.box.root, "java.json")
        path = os.path.join(self.box.root, "tree.json")
        with open(path, "w") as f:
            json.dump(tree, f)
        java = os.path.join(self.box.bin, "java")
        with open(java, "w") as f:
            f.write(FAKE_JAVA % {"py": PY, "record": self.record, "mode": mode, "tree": path})
        os.chmod(java, 0o755)

    def ran(self):
        with open(self.record) as f:
            return json.load(f)

    def test_version_from_the_jar(self):
        self.make_jar({"id": "26.3", "name": "26.3", "world_version": 5023, "java_version": 25})
        r = self.box.vfg("action", "refs", "--version")
        self.assertOk(r)
        self.assertEqual(r.stdout, "26.3\n")
        self.make_jar({"id": "26.4-pre1"})
        self.assertEqual(self.box.vfg("action", "refs", "--version").stdout, "26.4-pre1\n")
        self.make_jar(None)
        self.assertOneLine(self.box.vfg("action", "refs", "--version"), 1, "server.jar has no version.json$")
        self.make_jar(raw=b"not a zip")
        self.assertOneLine(self.box.vfg("action", "refs", "--version"), 1, "^vfg: cannot read .*server.jar")
        os.remove(os.path.join(self.box.rmc, "server.jar"))
        self.assertOneLine(self.box.vfg("action", "refs", "--version"), 1, "^vfg: no server.jar in ")

    def test_the_command_tree_from_the_generator(self):
        self.make_jar({"id": "26.3", "name": "26.3"})
        self.java()
        r = self.box.vfg("action", "refs")
        self.assertOk(r)
        self.assertEqual(len(r.stdout.splitlines()), 1)  # the generator's chatter stayed on EC2
        self.assertEqual(json.loads(r.stdout), {"version": "26.3", "commands": TREE})
        ran = self.ran()
        tmp = ran["argv"][-1]
        self.assertEqual(ran["argv"], ["-DbundlerMainClass=net.minecraft.data.Main", "-jar",
                                       os.path.join(self.box.rmc, "server.jar"), "--reports", "--output", tmp])
        self.assertTrue(tmp.startswith("/tmp/vfg-refs-"), tmp)
        self.assertEqual(os.path.realpath(ran["cwd"]), os.path.realpath(tmp))  # its logs/ went there
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(os.path.join(self.box.rmc, "logs")))

    def test_a_big_tree_gets_through(self):
        self.make_jar({"name": "26.3"})
        tree = {"type": "root", "children": {
            "c%05d" % i: {"type": "literal", "children": {"arg": {"type": "argument", "parser": "brigadier:string",
                                                                   "properties": {"type": "greedy"},
                                                                   "executable": True}}}
            for i in range(20000)}}
        self.java(tree=tree)
        r = self.box.vfg("action", "refs")
        self.assertOk(r)
        self.assertGreater(len(r.stdout), 2 * 1024 * 1024)
        self.assertEqual(json.loads(r.stdout)["commands"], tree)

    def test_generator_failures_clean_up(self):
        self.make_jar({"name": "26.3"})
        for mode, why in (("fail", r"^vfg: the data generator exited 1: Exception in thread main: boom$"),
                          ("nothing", r"^vfg: the data generator wrote no reports/commands\.json$")):
            self.java(mode)
            r = self.box.vfg("action", "refs")
            self.assertOneLine(r, 1, why)
            self.assertEqual(r.stdout, "")
            self.assertFalse(os.path.exists(self.ran()["argv"][-1]))
        self.java(tree={"type": "root", "children": {}})
        self.assertOneLine(self.box.vfg("action", "refs"), 1, "holds no commands$")

    def test_a_slow_generator_is_stopped_before_the_call_gives_up(self):
        self.make_jar({"name": "26.3"})
        self.java("slow")
        r = self.box.vfg("--timeout", "6", "action", "refs")
        self.assertOneLine(r, 1, r"^vfg: the data generator did not finish within 3 s$")
        ran = self.ran()
        self.assertFalse(alive(ran["pid"]))
        self.assertFalse(os.path.exists(ran["argv"][-1]))


class TestPlace(World):
    def place(self, *args, **env):
        return self.box.vfg("place", *args, **env)

    def test_save_where_a_player_stands_list_and_forget(self):
        r = self.place("save", "castle", "--player", "Steve", "--by", "T23")
        self.assertOk(r)
        self.assertEqual(r.stdout, "saved castle at 197 68 251 in overworld\n")
        self.assertEqual(self.world.sent, ["data get entity Steve Pos", "data get entity Steve Dimension",
                                           'data modify storage everhear:places castle set value '
                                           '{pos:[197,68,251],dim:"minecraft:overworld",by:"T23"}'])
        self.assertEqual(self.place("save", "base", "--player", "Alex").stdout, "saved base at 10 -5 3 in the_nether\n")
        r = self.place("list")
        self.assertOk(r)
        self.assertEqual(r.stdout, "base 10 -5 3 the_nether\ncastle 197 68 251 overworld (T23)\n")
        r = self.place("list", "--json")
        self.assertEqual(json.loads(r.stdout), {
            "base": {"pos": [10, -5, 3], "dim": "minecraft:the_nether", "by": ""},
            "castle": {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"}})
        self.assertEqual(len(r.stdout.splitlines()), 1)
        r = self.place("forget", "castle")
        self.assertEqual((r.returncode, r.stdout), (0, "forgot castle\n"))
        self.assertOneLine(self.place("forget", "castle"), 1, "^vfg: no place named castle$")
        self.assertEqual(self.place("list").stdout, "base 10 -5 3 the_nether\n")

    def test_save_coordinates_whole_blocks_and_overwrite(self):
        self.assertEqual(self.place("save", "home", "--at", "-368.7", "70", "912.2").stdout,
                         "saved home at -369 70 912 in overworld\n")
        self.assertEqual(self.place("save", "nest", "--at", "1", "2", "3", "--dim", "the_end", "--by", "T5").stdout,
                         "saved nest at 1 2 3 in the_end\n")
        self.assertEqual(self.place("save", "pit", "--at", "1", "2", "3", "--dim", "NETHER").stdout,
                         "saved pit at 1 2 3 in the_nether\n")
        self.assertEqual(self.place("save", "home", "--at", "5", "6", "7").stdout, "saved home at 5 6 7 in overworld\n")
        self.assertEqual(self.world.storage["home"], {"pos": [5, 6, 7], "by": "", "dim": "minecraft:overworld"})
        self.assertEqual(self.world.storage["nest"], {"pos": [1, 2, 3], "by": "T5", "dim": "minecraft:the_end"})
        self.assertEqual(self.place("list").stdout, "home 5 6 7 overworld\nnest 1 2 3 the_end (T5)\n"
                                                    "pit 1 2 3 the_nether\n")

    def test_none_saved(self):
        self.assertEqual(self.place("list").stdout, "none\n")
        self.assertEqual(self.place("list", "--json").stdout, "{}\n")

    def test_offline_player(self):
        self.assertOneLine(self.place("save", "x", "--player", "Nobody"), 1, "^vfg: Nobody is not online$")
        self.assertEqual(self.world.storage, {})

    def test_bad_arguments_never_reach_minecraft(self):
        for args in (["save", "Castle", "--player", "Steve"], ["save", "a-b", "--at", "1", "2", "3"],
                     ["save", "x" * 33, "--at", "1", "2", "3"], ["save", "x", "--player", "bad name"],
                     ["save", "x", "--at", "1", "2"], ["save", "x", "--at", "a", "2", "3"],
                     ["save", "x", "--at", "nan", "2", "3"], ["save", "x", "--player", "Steve", "--dim", "the_end"],
                     ["save", "x", "--at", "1", "2", "3", "--dim", "bad dim!"],
                     ["save", "x", "--player", "Alex", "--by", "7"],
                     ["save", "x"], ["save", "x", "--player", "Alex", "--at", "1", "2", "3"],
                     ["forget", "Castle"], ["list", "x"]):
            self.assertOneLine(self.place(*args), 2)
        self.assertEqual(self.world.sent, [])
        self.assertEqual(self.box.ssh_calls(), [])

    def test_rcon_down_exits_5(self):
        self.box.write_props(self.box.password, enable=False)
        self.assertOneLine(self.place("list"), 5, "minecraft is not running")
        self.assertOneLine(self.place("save", "x", "--at", "1", "2", "3"), 5, "minecraft is not running")


# ======================================================================== up

class TestUp(Case):
    def test_starts_what_is_down(self):
        with open(os.path.join(self.box.units, "minecraft"), "w") as f:
            f.write("inactive\n")
        r = self.box.vfg("up")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), [
            "ssh         ok  ec2-user@fakebox",
            "minecraft   active  started  rcon ok",
            "vfg-bridge  active",
            "vfg-jev     active",
            "feed        last seq 3",
            "scripts     running: none",
        ])

    def test_missing_unit(self):
        os.remove(os.path.join(self.box.units, "vfg-jev"))
        r = self.box.vfg("up")
        self.assertOneLine(r, 1, "^vfg: not up: vfg-jev missing$")
        self.assertIn("vfg-jev     missing", r.stdout)

    def test_unit_that_will_not_start(self):
        with open(os.path.join(self.box.units, "vfg-jev"), "w") as f:
            f.write("failed\n")
        open(os.path.join(self.box.units, "vfg-jev.fail"), "w").close()
        with open(os.path.join(self.box.units, "vfg-jev.journal"), "w") as f:
            f.write("starting\nKeyError: TYPESAFE_API_KEY\n")
        r = self.box.vfg("up")
        self.assertOneLine(r, 1, r"^vfg: not up: vfg-jev failed \(KeyError: TYPESAFE_API_KEY\)$")
        self.assertIn("vfg-jev     failed  start failed  KeyError: TYPESAFE_API_KEY", r.stdout)

    def test_running_scripts_and_no_feed(self):
        os.remove(self.box.feed)
        self.assertOk(self.box.vfg("script", "run", "loop"))
        r = self.box.vfg("up")
        self.assertOk(r)
        self.assertIn("feed        no feed.log yet", r.stdout)
        self.assertIn("scripts     running: loop", r.stdout)
        self.box.vfg("script", "stop", "loop")


# ===================================================================== watch

class Watch(object):
    """A running `vfg watch`, its stdout read line by line on a thread."""

    def __init__(self, box, *args, **env):
        self.p = box.popen("watch", *args, **env)
        self.q = queue.Queue()
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.p.stdout.readline, b""):
            self.q.put(raw.decode("utf-8").rstrip("\n"))
        self.q.put(None)

    def expect(self, n, timeout=10):
        got = []
        end = time.time() + timeout
        while len(got) < n:
            try:
                line = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            got.append(line)
        self.lines.extend(got)
        return got

    def quiet(self, seconds=1.0):
        return self.expect(10 ** 6, timeout=seconds)

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
        return self.p.wait(10)


class TestWatch(Case):
    box_kwargs = {"feed_lines": 0}

    def tearDown(self):
        for line in getattr(self, "_watched", []):
            self.assertRegex(line, FEED_RE)
        Case.tearDown(self)

    def watch(self, *args, **env):
        w = Watch(self.box, *args, **env)
        self._watched = getattr(self, "_watched", [])
        self._watches = getattr(self, "_watches", []) + [w]
        return w

    def collect(self):
        for w in getattr(self, "_watches", []):
            self._watched.extend(w.lines)

    def append_lines(self, first, last):
        """Append seqs first..last to feed.log in one write, as a batch."""
        stamp = time.strftime("%H:%M:%S", time.gmtime())
        data = "".join("SYS#%d %s | batch line %d\n" % (s, stamp, s) for s in range(first, last + 1))
        fd = os.open(self.box.feed, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        os.write(fd, data.encode())
        os.close(fd)

    def connected(self):
        return wait_for(lambda: self.box.cursor() is not None, 10)

    def test_no_cursor_prints_nothing_old_then_three_posts(self):
        self.append_lines(1, 5)
        w = self.watch()
        self.assertTrue(self.connected())
        self.assertEqual(self.box.cursor(), 5)
        self.assertEqual(w.quiet(0.5), [])
        for t in ("one ~", "two $HOME", "three"):
            self.box.post(t)
        got = w.expect(3)
        self.assertEqual([g.split(" | ")[1] for g in got], ["one ~", "two $HOME", "three"])
        self.assertEqual([int(g.split("#")[1].split()[0]) for g in got], [6, 7, 8])
        self.assertTrue(wait_for(lambda: self.box.cursor() == 8, 3))
        self.assertEqual(w.stop(), 0)
        self.collect()

    def test_cursor_at_three_from_an_empty_feed_via_feed_post(self):
        open(self.box.feed, "w").close()
        w = self.watch()
        self.assertTrue(self.connected())
        for t in ("a", "b", "c"):
            self.assertOk(self.box.vfg("feed", "post", "SYS", "", t))
        self.assertEqual(len(w.expect(3)), 3)
        self.assertTrue(wait_for(lambda: self.box.cursor() == 3, 3))
        call = [c for c in self.box.ssh_calls() if "ControlPath=none" in c]
        self.assertTrue(call and call[0][-1] == "python3 -u -")
        self.assertFalse(any("ControlMaster=no" in c for c in call))
        w.stop()
        self.collect()

    def test_kill_9_then_restart_prints_exactly_the_missed_lines(self):
        open(self.box.feed, "w").close()
        w = self.watch()
        self.assertTrue(self.connected())
        self.box.post("first")
        self.assertEqual(len(w.expect(1)), 1)
        self.assertTrue(wait_for(lambda: self.box.cursor() == 1, 3))
        w.p.kill()
        w.p.wait()
        self.box.post("missed one")
        self.box.post("missed two")
        w2 = self.watch()
        got = w2.expect(2)
        self.assertEqual([g.split(" | ")[1] for g in got], ["missed one", "missed two"])
        self.assertEqual(w2.quiet(1.0), [])
        w2.stop()
        self.collect()

    def test_drops_reconnect_with_no_gap_and_no_repeat(self):
        open(self.box.feed, "w").close()
        w = self.watch(FAKE_SSH_DROP_AFTER="1.5")
        self.assertTrue(self.connected())
        n = 40
        for i in range(n):
            self.box.post("line %d" % i)
            time.sleep(0.12)
        got = w.expect(n, timeout=20)
        seqs = [int(g.split("#")[1].split()[0]) for g in got]
        self.assertEqual(seqs, list(range(1, n + 1)))
        self.assertEqual(w.quiet(1.0), [])
        drops = [c for c in self.box.ssh_calls() if "ControlPath=none" in c]
        self.assertGreaterEqual(len(drops), 3)
        w.stop()
        self.collect()

    def test_shorter_feed_gives_the_reset_notice(self):
        with open(os.path.join(self.box.vhome, "cursor"), "w") as f:
            f.write("50\n")
        self.append_lines(1, 3)
        w = self.watch()
        got = w.expect(4)
        self.assertRegex(got[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: feed restarted — cursor reset$")
        self.assertEqual([g.split(" | ")[1] for g in got[1:]], ["batch line 1", "batch line 2", "batch line 3"])
        self.assertTrue(wait_for(lambda: self.box.cursor() == 3, 3))
        w.stop()
        self.collect()

    def test_far_behind_gives_the_skip_notice_and_the_last_100(self):
        with open(os.path.join(self.box.vhome, "cursor"), "w") as f:
            f.write("0\n")
        self.append_lines(1, 500)
        w = self.watch()
        got = w.expect(101)
        self.assertRegex(got[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: skipped 400 lines \(seq 1–400\) "
                                 r"while away — vfg history --feed has them$")
        self.assertEqual([int(g.split("#")[1].split()[0]) for g in got[1:]], list(range(401, 501)))
        self.assertTrue(wait_for(lambda: self.box.cursor() == 500, 3))
        w.stop()
        self.collect()

    def test_second_watch_takes_over(self):
        open(self.box.feed, "w").close()
        w1 = self.watch()
        self.assertTrue(self.connected())
        w2 = self.watch()
        self.assertEqual(w1.p.wait(10), 0)
        self.assertEqual(w1.quiet(0.2), [])
        self.assertEqual(w1.p.stderr.read(), b"")
        self.box.post("for the new one")
        got = w2.expect(1)
        self.assertEqual(got[0].split(" | ")[1], "for the new one")
        with open(os.path.join(self.box.vhome, "watch.pid")) as f:
            self.assertEqual(int(f.read()), w2.p.pid)
        w2.stop()
        self.collect()

    def test_tail_is_for_a_person(self):
        self.append_lines(1, 5)
        main = self.watch()
        self.assertTrue(self.connected())
        t = self.watch("--tail", "2")
        got = t.expect(2)
        self.assertEqual([g.split(" | ")[1] for g in got], ["batch line 4", "batch line 5"])
        self.assertIsNone(main.p.poll())  # the tail took neither the lock nor the cursor
        self.box.post("both see this")
        self.assertEqual(t.expect(1)[0].split(" | ")[1], "both see this")
        self.assertEqual(main.expect(1)[0].split(" | ")[1], "both see this")
        t.stop()
        main.stop()
        self.collect()

    def test_valve_holds_back_a_flood(self):
        open(self.box.feed, "w").close()
        w = self.watch(VFG_WATCH_MAX_PER_MIN="5")
        self.assertTrue(self.connected())
        self.append_lines(1, 12)
        got = w.expect(6)
        self.assertEqual([int(g.split("#")[1].split()[0]) for g in got[:5]], [1, 2, 3, 4, 5])
        self.assertRegex(got[5], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: held back 7 lines \(seq 6–12\), "
                                 r"feed too busy — vfg history --feed has them$")
        self.assertTrue(wait_for(lambda: self.box.cursor() == 12, 3))
        w.stop()
        self.collect()

    def test_unreachable_then_reconnected_notices(self):
        down = os.path.join(self.box.root, "down")
        open(down, "w").close()
        self.append_lines(1, 2)
        with open(os.path.join(self.box.vhome, "cursor"), "w") as f:
            f.write("2\n")
        w = self.watch(FAKE_SSH_DOWN_FILE=down, VFG_WATCH_UNREACHABLE_S="1.5")
        got = w.expect(1, timeout=6)
        self.assertRegex(got[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: EC2 unreachable since "
                                 r"\d\d:\d\d:\d\d — retrying$")
        self.append_lines(3, 3)
        os.remove(down)
        got = w.expect(2, timeout=20)
        self.assertRegex(got[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: reconnected — nothing missed$")
        self.assertEqual(got[1].split(" | ")[1], "batch line 3")
        self.assertEqual(w.quiet(0.5), [])
        w.stop()
        self.collect()

    def test_stops_quietly_and_never_on_its_own(self):
        open(self.box.feed, "w").close()
        w = self.watch(FAKE_SSH_DOWN="1")
        time.sleep(2.5)
        self.assertIsNone(w.p.poll())
        self.assertEqual(w.stop(), 0)
        self.assertEqual(w.quiet(0.2), [])
        r = self.box.vfg("watch", VFG_SSH_KEY=os.path.join(self.box.root, "nokey"))
        self.assertOneLine(r, 3)

    def test_closed_stdout_ends_it(self):
        open(self.box.feed, "w").close()
        p = self.box.popen("watch")
        self.assertTrue(self.connected())
        p.stdout.close()
        p.stdout = None
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(p.stderr.read(), b"")


# ======================================================== the real feed writer

REAL_FEED = os.path.join(os.path.dirname(TOOLKIT), "bridge", "vfg_feed.py")


@unittest.skipUnless(os.path.isfile(REAL_FEED), "no bridge/vfg_feed.py beside the toolkit")
class TestRealFeedWriter(Case):
    """The bridge's vfg_feed.py (copied, never edited) in place of the fake:
    feed post, orch reply, watch and history must agree with the EC2 writer."""
    box_kwargs = {"feed_lines": 2}

    def setUp(self):
        Case.setUp(self)
        shutil.copy(REAL_FEED, os.path.join(self.box.rvfg, "lib", "vfg_feed.py"))
        # Deliberately no VFG_DB / VFG_FEED here: vfg must set them on the
        # remote command itself, since ssh carries no environment.
        self.env = {}

    def test_post_reply_watch_and_history_agree(self):
        w = Watch(self.box, **self.env)
        self.box.procs.append(w.p)
        self.assertTrue(wait_for(lambda: self.box.cursor() == 2, 10))
        r = self.box.vfg("feed", "post", "SYS", "", "smoke ~ $HOME\nsecond 'line'", **self.env)
        self.assertOk(r)
        self.assertEqual(r.stdout, "3\n")
        r = self.box.vfg("feed", "post", "GAME", "death Steve", "--", "-5 hearts", **self.env)
        self.assertEqual(r.stdout, "4\n")
        self.box.vfg("orch", "send", "noop")
        r = self.box.vfg("orch", "reply", "T1", "done", "ok", **self.env)
        self.assertOk(r)
        self.assertEqual(r.stdout, "5\n")
        got = w.expect(3)
        self.assertEqual(len(got), 3, got)
        for line in got:
            self.assertRegex(line, FEED_RE)
        self.assertRegex(got[0], r"^SYS#3 \d\d:\d\d:\d\d \| smoke ~ \$HOME second 'line'$")
        self.assertRegex(got[1], r"^GAME#4 \d\d:\d\d:\d\d death Steve \| -5 hearts$")
        self.assertRegex(got[2], r"^ORCH#5 \d\d:\d\d:\d\d T1 done \| ok$")
        self.assertTrue(wait_for(lambda: self.box.cursor() == 5, 3))
        r = self.box.vfg("history", "--feed", "--limit", "3", **self.env)
        self.assertEqual(r.stdout.splitlines(), got)
        self.assertOneLine(self.box.vfg("feed", "post", "GAME", "dance Steve", "x", **self.env), 1)
        w.stop()


if __name__ == "__main__":
    unittest.main()
