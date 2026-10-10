"""The vfg toolkit's tests: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Each test gets a temp VFG_HOME, a temp VFG_REPO and a temp "box" that
VFG_REMOTE_VFG and VFG_REMOTE_MC point into. VFG_SSH is fake_ssh, which runs
the remote command here with bash; the box's Python, <box>/vfg/python/bin/python3, is the
interpreter running the tests, so the remote programs and the copies run on it too. vfg itself
runs under uv with pydantic (it starts itself again under uv when this Python has none). The
place tests answer RCON with ../scripts/_test/fake_places_world.py, Minecraft 26.3's recorded
replies. The actions and the copies they start: test_copies.py.
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
sys.path.insert(0, TESTS)
import standins  # noqa: E402
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
        os.makedirs(os.path.join(self.rvfg, "python", "bin"))
        os.symlink(PY, os.path.join(self.rvfg, "python", "bin", "python3"))  # the box's 3.12
        self.key = j("key")
        with open(self.key, "w") as f:
            f.write("not a key\n")
        self.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\n" % self.key)
        self.ssh_log = j("ssh.jsonl")
        self.rcon_log = j("rcon.jsonl")
        open(self.rcon_log, "w").close()
        self.world = j("world.json")  # what the fake RCON says about players, time, weather
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

    def start_rcon(self, shared=False):
        """shared=True: one reply buffer for every connection, as vanilla's."""
        env = dict(os.environ, FAKE_RCON_SHARED="1" if shared else "0")
        self.rcon = subprocess.Popen([PY, os.path.join(TESTS, "fake_rcon.py"),
                                      os.path.join(self.rmc, "server.properties"), self.rcon_log,
                                      self.world],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        line = self.rcon.stdout.readline()
        assert line.strip() == b"ready", line

    def write_world(self, world):
        with open(self.world, "w") as f:
            json.dump(world, f)

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
        try:
            os.remove("/tmp/vfg-rcon-%d.lock" % self.port)  # the remote side's RCON lock
        except OSError:
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
                  "feed", "script", "action", "place", "usage", "made"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        for args, words in ((["orch", "--help"], ["start", "stop", "status", "send", "hand", "reply"]),
                            (["action", "--help"], ["run", "list", "check", "refs"]),
                            (["action", "run", "--help"], ["key=value", "as=<copy>", "--task", "JSON line"]),
                            (["action", "list", "--help"], ["--json"]),
                            (["made", "--help"], ["save", "restore"]),
                            (["action", "refs", "--help"], ["--version"]),
                            (["place", "--help"], ["save", "list", "forget"]),
                            (["place", "save", "--help"], ["--player", "--at", "--dim", "--by"]),
                            (["place", "list", "--help"], ["--json"]),
                            (["script", "--help"], ["new", "check", "deploy", "list", "set", "stop", "logs"]),
                            (["script", "list", "--help"], ["--json", "--fresh"]),
                            (["history", "--help"], ["--since", "--until", "--player", "--class",
                                                     "--source", "--events", "--feed", "--limit"]),
                            (["watch", "--help"], ["--tail"]),
                            (["usage", "add", "--help"], ["--component", "--model", "--in", "--out",
                                                          "--cost", "--ref"])):
            r = self.box.vfg(*args)
            self.assertOk(r)
            for w in words:
                self.assertIn(w, r.stdout)

    def test_no_config_exits_3(self):
        os.remove(os.path.join(self.box.vhome, "config"))
        standins.install(self.box.repo, ["command"])
        for args in (["cmd", "list"], ["watch"], ["up"], ["history"], ["feed", "post", "SYS", "", "x"]):
            r = self.box.vfg(*args, timeout=20)
            self.assertOneLine(r, 3, r"^vfg: not configured: VFG_SSH_HOST missing in .*config$")
            self.assertEqual(r.stdout, "")

    def test_unreadable_key_exits_3_without_naming_it(self):
        os.remove(self.box.key)
        r = self.box.vfg("history")
        self.assertOneLine(r, 3, r"^vfg: not configured: ")
        self.assertNotIn(self.box.key, r.stderr)

    def test_environment_wins_over_config(self):
        r = self.box.vfg("history", VFG_SSH_HOST="otherbox", VFG_SSH_USER="steve")
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
        for args in (["say", "hi"], ["history"], ["up"]):
            r = self.box.vfg(*args, FAKE_SSH_DOWN="1")
            self.assertOneLine(r, 4, r"^vfg: cannot reach EC2 \(ec2-user@fakebox\): "
                                     r"ssh: connect to host fakebox port 22: Connection timed out$")
            self.assertEqual(r.stdout, "")

    def test_timeout_exits_4(self):
        r = self.box.vfg("--timeout", "1", "history", FAKE_SSH_DELAY="5")
        self.assertOneLine(r, 4, r"^vfg: EC2 did not answer within 1 s$")

    def test_the_ssh_call(self):
        self.assertOk(self.box.vfg("history"))
        calls = self.box.ssh_calls()
        check, master, call = calls[-3], calls[-2], calls[-1]
        self.assertEqual(check[-3:], ["-O", "check", "ec2-user@fakebox"])
        self.assertIn("ControlMaster=yes", master)
        self.assertIn("ControlPersist=10m", master)
        self.assertIn("-N", master)
        self.assertIn("-f", master)
        self.assertNotIn("ControlMaster=no", master)
        self.assertEqual(call[-2:], ["ec2-user@fakebox", self.box.rvfg + "/python/bin/python3 -u -"])
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
        self.box.vfg("history", "--limit", "1")
        with open(os.path.join(self.box.vhome, "vfg.log")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r'^\d{4}-\d\d-\d\dT[\d:.]+Z \["history", "--limit", "1"\] exit=0 \d+ms$')


class TestOneRefusedList(unittest.TestCase):
    """scripts/_lib/commands.py is the one list of refused server commands: mc.py imports it,
    vfg ships it with each program, the orchestrator's guard loads it, and no other file
    writes the words out again."""
    COMMANDS = os.path.join(REPO, "scripts", "_lib", "commands.py")

    def commands(self):
        spec = importlib.util.spec_from_file_location("one_list_commands", self.COMMANDS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_mc_imports_it(self):
        code = "import mc, commands; print(mc.REFUSED is commands.REFUSED, commands.__file__)"
        env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "scripts", "_lib"), PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([PY, "-c", code], env=env, stdout=subprocess.PIPE, universal_newlines=True,
                             cwd=TESTS).stdout.split()
        self.assertEqual(out, ["True", self.COMMANDS])

    def test_vfg_ships_it(self):
        mod = vfg_module("vfgmod_one_list")
        with open(self.COMMANDS, encoding="utf-8") as f:
            self.assertIn(repr(f.read()), mod.program("rcon.py", {}, ["commands.py"]).decode())

    def test_the_guard_loads_it(self):
        import ast
        path = os.path.join(REPO, "orchestrator", "orchestrator", "guard.py")
        if not os.path.isfile(path):
            self.skipTest("no orchestrator/ beside the toolkit")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "refused")
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_COMMANDS")]
        self.assertEqual(len(keep), 2)
        self.assertFalse([n for n in tree.body if isinstance(n, ast.Assign)
                          and any(getattr(t, "id", "") == "REFUSED" for t in n.targets)])
        ns = {"os": os, "__file__": path}
        exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
        self.assertEqual(ns["refused"](), self.commands().REFUSED)
        self.assertEqual(os.path.realpath(ns["_COMMANDS"][0].__file__), os.path.realpath(self.COMMANDS))

    def test_no_other_file_writes_the_words_out(self):
        words = re.compile(r"frozenset\(\[[^\]]*\"deop\"")
        found = []
        for top in ("scripts", "toolkit", "orchestrator", "dispatcher", "actions", "lib", "jev", "integration"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, top)):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
                for fn in filenames:
                    if fn.endswith(".py") or "." not in fn:
                        path = os.path.join(dirpath, fn)
                        try:
                            with open(path, encoding="utf-8") as f:
                                text = f.read()
                        except (OSError, UnicodeDecodeError):
                            continue
                        if words.search(text):
                            found.append(os.path.relpath(path, REPO))
        self.assertEqual(found, ["scripts/_lib/commands.py"])


# ======================================================================= cmd

class TestCmd(Case):
    """vfg cmd is the command action: its stand-in (standins.py) runs on the real kit."""

    def setUp(self):
        Case.setUp(self)
        standins.install(self.box.repo, ["command"])

    def sent(self):
        """What reached RCON but the copy's own cleanup."""
        return [c for c in self.box.rcon_commands() if c != "kill @e[tag=vfg_command]"]

    def test_tildes_reach_rcon_as_written(self):
        r = self.box.vfg("cmd", "tp", "@a", "~", "~10", "~")
        self.assertOk(r)
        self.assertEqual(self.sent(), ["tp @a ~ ~10 ~"])

    def test_quotes_and_dollars_arrive_literally(self):
        text = """say he said "hi" $x $(id) `id` 'q' \\n ;|&"""
        self.assertOk(self.box.vfg("cmd", text))
        self.assertEqual(self.sent(), [text])

    def test_one_leading_slash_dropped_and_output_printed(self):
        r = self.box.vfg("cmd", "/time", "set", "day")
        self.assertOk(r)
        self.assertEqual(r.stdout, "Set the time to 1000\n")
        self.assertEqual(self.sent(), ["time set day"])

    def test_home_argument_refused(self):
        r = self.box.vfg("cmd", "tp", "@a", self.box.home, "~", "~")
        self.assertOneLine(r, 2, "your shell expanded a ~")
        r = self.box.vfg("cmd", "tp", "@a", self.box.home + "/10")
        self.assertOneLine(r, 2)
        self.assertEqual(self.box.rcon_commands(), [])

    def test_big_reply_in_three_packets(self):
        """The RCON client every remote program has (common.py) reads a long reply whole."""
        ns = {"__name__": "vfg_remote_common"}
        with open(os.path.join(TOOLKIT, "remote", "common.py")) as f:
            exec(compile(f.read(), "common.py", "exec"), ns)
        r = ns["Rcon"](self.box.rmc)
        try:
            got = r.command("big")
        finally:
            r.close()
        self.assertEqual(len(got), 9000)
        self.assertTrue(got.startswith("0000 0001 "))
        self.assertTrue(got.endswith("1799 "))

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
        self.assertEqual(call[-1], "env VFG_DB=%s/vfg.db VFG_FEED=%s/feed.log %s/python/bin/python3 "
                                   "%s/lib/vfg_feed.py post SYS '' 'smoke ~ $HOME '\"'\"'q'\"'\"' \"d\"'"
                         % (self.box.rvfg, self.box.rvfg, self.box.rvfg, self.box.rvfg))
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

    def test_logs_of_a_copy_that_never_ran(self):
        self.assertOneLine(self.box.vfg("script", "logs", "hello"), 1, "no log for hello")
        self.assertOneLine(self.box.vfg("script", "logs", "../x"), 2)

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
                                              "op": "deploy", "tar": tar_with(name, kind=kind)}, mod.SCRIPTS_LIBS)
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
    """vfg place save is the save_place action (its stand-in, standins.py, on the real kit)."""

    def setUp(self):
        World.setUp(self)
        if HAVE_WORLD:
            standins.install(self.box.repo, ["save_place"])

    def place(self, *args, **env):
        return self.box.vfg("place", *args, **env)

    def test_save_where_a_player_stands_list_and_forget(self):
        r = self.place("save", "castle", "--player", "Steve", "--by", "T23")
        self.assertOk(r)
        self.assertEqual(r.stdout, "saved castle at 197 68 251 in overworld\n")
        self.assertIn('data modify storage everhear:places castle set value '
                      '{pos:[197,68,251],dim:"minecraft:overworld",by:"T23"}', self.world.sent)
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

    # ---- the places cache: $VFG_HOME/places.json

    def cache(self):
        with open(os.path.join(self.box.vhome, "places.json")) as f:
            return json.load(f)

    def age_cache(self, seconds):
        data = self.cache()
        data["fetched_at"] -= seconds
        with open(os.path.join(self.box.vhome, "places.json"), "w") as f:
            json.dump(data, f)

    def lists(self):
        return [c for c in self.world.sent if c == "data get storage everhear:places"]

    def test_two_lists_inside_the_ttl_make_one_ssh_call(self):
        self.world.save_place("castle", [197, 68, 251], by="T23")
        first = self.place("list")
        calls = len(self.box.ssh_calls())
        second = self.place("list")
        self.assertEqual((first.stdout, second.stdout), ("castle 197 68 251 overworld (T23)\n",) * 2)
        self.assertEqual(len(self.box.ssh_calls()), calls)  # the second answered from the cache
        self.assertEqual(len(self.lists()), 1)
        self.assertEqual(json.loads(self.place("list", "--json").stdout),
                         {"castle": {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"}})
        self.assertEqual(os.stat(os.path.join(self.box.vhome, "places.json")).st_mode & 0o777, 0o600)

    def test_a_list_past_the_ttl_fetches(self):
        self.place("list")
        self.world.save_place("zoo", [1, 2, 3])  # saved by someone else, straight on the server
        self.age_cache(31)
        self.assertEqual(self.place("list").stdout, "zoo 1 2 3 overworld\n")
        self.assertEqual(len(self.lists()), 2)
        self.assertEqual(self.place("list", VFG_PLACES_TTL_S="0").stdout, "zoo 1 2 3 overworld\n")
        self.assertEqual(len(self.lists()), 3)

    def test_fresh_fetches_whatever_the_age(self):
        self.place("list")
        self.world.save_place("zoo", [1, 2, 3])
        self.assertEqual(self.place("list").stdout, "none\n")  # cached
        self.assertEqual(self.place("list", "--fresh").stdout, "zoo 1 2 3 overworld\n")
        self.assertEqual(self.place("list").stdout, "zoo 1 2 3 overworld\n")  # and the cache is rewritten

    def test_save_and_forget_write_through_to_the_cache_keeping_its_age(self):
        self.world.save_place("old", [5, 6, 7])
        self.place("list")
        self.age_cache(10)
        at = self.cache()["fetched_at"]
        self.assertOk(self.place("save", "castle", "--player", "Steve", "--by", "T23"))
        self.assertOk(self.place("save", "nest", "--at", "1", "2", "3", "--dim", "the_end"))
        self.assertEqual(self.cache()["places"], {
            "old": {"pos": [5, 6, 7], "dim": "minecraft:overworld", "by": ""},
            "castle": {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"},
            "nest": {"pos": [1, 2, 3], "dim": "minecraft:the_end", "by": ""}})
        self.assertAlmostEqual(self.cache()["fetched_at"], at, delta=1)
        self.assertOk(self.place("forget", "old"))
        self.assertEqual(sorted(self.cache()["places"]), ["castle", "nest"])
        self.assertEqual(self.place("list").stdout, "castle 197 68 251 overworld (T23)\nnest 1 2 3 the_end\n")
        self.assertEqual(len(self.lists()), 1)  # all of it from the cache

    def test_a_failed_fetch_serves_the_stale_cache_and_exits_0(self):
        self.world.save_place("castle", [197, 68, 251], by="T23")
        self.place("list")
        self.age_cache(600)
        self.box.write_props(self.box.password, enable=False)
        r = self.place("list")
        self.assertOk(r)
        self.assertEqual(r.stdout, "castle 197 68 251 overworld (T23)\n")
        self.assertRegex(r.stderr, r"^vfg: saved places from 60\d s ago: .*minecraft is not running")
        self.assertEqual(json.loads(self.place("list", "--json", "--fresh").stdout)["castle"]["by"], "T23")

    def test_no_cache_and_a_failed_fetch_exits_non_zero(self):
        self.box.write_props(self.box.password, enable=False)
        self.assertOneLine(self.place("list"), 5, "minecraft is not running")
        self.assertFalse(os.path.exists(os.path.join(self.box.vhome, "places.json")))

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

    def test_running_copies_and_no_feed(self):
        os.remove(self.box.feed)
        standins.install(self.box.repo, ["beam", "stop"])
        self.assertOk(self.box.vfg("action", "run", "beam", "report=false"))
        r = self.box.vfg("up")
        self.assertOk(r)
        self.assertIn("feed        no feed.log yet", r.stdout)
        self.assertIn("scripts     running: beam", r.stdout)
        self.assertOk(self.box.vfg("script", "stop", "beam"))


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
        self.assertTrue(call and call[0][-1] == self.box.rvfg + "/python/bin/python3 -u -")
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


# ============================================================ the library

REPO = os.path.dirname(TOOLKIT)

# A stand-in for scripts/_lib/kit in these tests: values() only, reading what kit.values
# reads — the copy's meta file, <VFG_RUN_DIR>/<VFG_INSTANCE>.json, then for live keys
# <VFG_INSTANCE>.values.json, again on every read.
FAKE_KIT = '''"""A stand-in for the kit: values() only."""
import json
import os


class _Values(dict):
    def __init__(self):
        run = os.environ.get("VFG_RUN_DIR", "/opt/vfg/run")
        name = os.environ.get("VFG_INSTANCE", "")
        with open(os.path.join(run, name + ".json")) as f:
            meta = json.load(f)
        dict.__init__(self, meta["values"])
        self.live = set(meta.get("live") or [])
        self.path = os.path.join(run, name + ".values.json")

    def __getitem__(self, key):
        value = dict.__getitem__(self, key)
        if key in self.live:
            try:
                with open(self.path) as f:
                    value = json.load(f).get(key, value)
            except (OSError, ValueError):
                pass
        return value


def values():
    return _Values()


def result(summary, **facts):
    print("result:", summary)
'''

BEAM = '''"""Beam: prints its values every 0.1 s, so a test can see them change."""
ASKS = ["a beam", "a laser that goes further"]
import json
import time
from kit import values

v = values()
while True:
    print(json.dumps(dict((k, v[k]) for k in v), sort_keys=True), flush=True)
    time.sleep(0.1)
'''

GALICK = '''"""Galick Gun: a beam that hurts what it hits and leaves a crater."""
ASKS = ["galick gun", "a big purple beam"]
from kit import values, aim, area


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)


v = values()
crater(aim.ray(v["player"], 60).end, v)
'''

GALICK_ACTION = '''from actions._types import Action, Field, Players, preset


class Galick(Action, preset("crater")):
    """Give a player a galick gun: a beam that leaves a crater."""

    script = "galick"
    runs = "until_stopped"
    takes = ["player", "damage"]
    defaults = {"crater_radius": 4}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    damage: float = Field(20, ge=0, le=100)
    """damage to what the beam hits, in half-hearts"""
'''

CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area
from actions._types import Field, Literal, Live, Values


class Crater(Values):
    crater_radius: Live[int] = Field(8, ge=2, le=64)
    """how wide the crater is, in blocks"""
    crater_leave: Live[Literal["air", "lava", "fire"]] = "air"
    """what is left in it"""


def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)
'''

EXPLOSION = '''"""An explosion at a place: one blast, a ring or a plus."""
import math
from mc import rcon
from actions._types import Field, Literal, Live, Values


class Explosion(Values):
    explosion_power: Live[float] = Field(4, ge=0, le=20)
    """how strong each blast is"""
    explosion_layout: Literal["one", "ring", "plus"] = "one"
    """one blast, a ring of eight or a plus of four"""


def explosion(at, v):
    for dx, dz in _explosion_spots(v["explosion_layout"]):
        rcon("summon tnt %s %s %s" % (at[0] + dx, at[1], at[2] + dz))


def _explosion_spots(layout):
    n = {"one": 1, "ring": 8, "plus": 4}[layout]
    return [(round(4 * math.cos(i * 6.28 / n)), round(4 * math.sin(i * 6.28 / n))) for i in range(n)]
'''

COUNTER = '''"""Count what happens at a place, once each."""
from mc import log
from actions._types import Field, Values


class Counter(Values):
    counter_every: int = Field(1, ge=1, le=10)
    """log the count every this many"""


_counter_n = [0]


def counter(at, v):
    _counter_n[0] += 1
    _counter_seen.add(at)
    if _counter_n[0] % v["counter_every"] == 0:
        log("count", _counter_n[0])


_counter_seen = set()
'''

TALLY = '''"""Tally the players a script touched."""
from actions._types import Field, Values


class Tally(Values):
    tally_max: int = Field(9, ge=1, le=99)
    """the most a player is counted"""


_tally_known = {}


def tally(p, v):
    _tally_known[p] = min(v["tally_max"], _tally_known.get(p, 0) + 1)
'''


def preset_blocks(text):
    """{preset: the code under its marker, up to the next marker or the wiring}."""
    lines = text.split("\n")
    marks = [i for i, l in enumerate(lines) if l.startswith("# --- preset: ")]
    wiring = next(i for i, l in enumerate(lines) if l.startswith("v = values("))
    out = {}
    for k, i in enumerate(marks):
        end = marks[k + 1] if k + 1 < len(marks) else wiring
        out[re.match(r"# --- preset: (\w+)", lines[i]).group(1)] = "\n".join(lines[i + 1:end]).strip()
    return out


ICE_STAFF = '''"""Ice Staff: lays ice where the player looks."""
ASKS = ["an ice staff"]
import random
from kit import values, aim
from mc import rcon

ICE_BLOCKS = ["ice", "packed_ice"]


def ice_path(p, v):
    """Lay ice along where a player looks, some of it packed."""
    for at in aim.ray(p, v["length"]).points:
        _lay(at, v)


def _lay(at, v):
    if random.random() < v["ice_rate"]:
        rcon("setblock %d %d %d %s" % (at[0], at[1], at[2], random.choice(ICE_BLOCKS)))


def shout(p):
    rcon("say %s" % p)


def loud_path(p, v):
    shout(p)
    ice_path(p, v)


v = values()
'''

ICE_STAFF_ACTION = '''from actions._types import Action, Field, Live, Players


class IceStaff(Action):
    """Give a player an ice staff: ice where they look."""

    script = "ice_staff"
    runs = "until_stopped"
    takes = ["player", "length"]

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    length: Live[int] = Field(60, ge=5, le=200)
    """how far the ice reaches, in blocks"""
    ice_rate: float = Field(0.5, ge=0, le=1)
    """how much of it is laid, from 0 to 1"""
'''


def load_library_module():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("vfg_library", os.path.join(TOOLKIT, "lib", "library.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibraryCase(Case):
    """A box whose repo has two example scripts with their actions, two presets and a fake kit."""

    def setUp(self):
        Case.setUp(self)
        for name in ("hello", "loop"):          # the deploy tests' scripts, which no action runs
            shutil.rmtree(os.path.join(self.box.repo, "scripts", name))
        self.write("scripts/beam/main.py", BEAM)
        self.write("scripts/galick/main.py", GALICK)
        self.write("scripts/_presets/crater.py", CRATER)
        self.write("scripts/_presets/explosion.py", EXPLOSION)
        self.write("scripts/_lib/kit/__init__.py", FAKE_KIT)
        standins.install_types(self.box.repo)
        self.write("actions/beam.py", standins.ACTIONS["beam"])
        self.write("actions/galick.py", GALICK_ACTION)

    def write(self, rel, text):
        path = os.path.join(self.box.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def read(self, rel):
        with open(os.path.join(self.box.repo, rel)) as f:
            return f.read()

    def made(self, *p):
        return os.path.join(self.box.repo, "made", "main", *p)


def finish_todos(path, line="It does a thing players asked for."):
    """What an implementer does after vfg script new: the action's line and descriptions."""
    with open(path) as f:
        text = f.read()
    text = text.replace("TODO: what it does, in players' words: its line on the menu.", line)
    text = text.replace("TODO: what it is, in players' words", "a value players change")
    with open(path, "w") as f:
        f.write(text)


class TestScriptNew(LibraryCase):
    def test_new_writes_a_script_and_its_action_and_check_says_what_is_left(self):
        r = self.box.vfg("script", "new", "beam_boom", "--from", "beam", "--preset", "crater",
                         "--preset", "explosion")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[:4], [
            "made made/main/scripts/beam_boom/ from scripts/beam/ (library)",
            "  + crater (library): crater_radius, crater_leave",
            "  + explosion (library): explosion_power, explosion_layout",
            'made made/main/actions/beam_boom.py: class BeamBoom(Action, preset("crater"), preset("explosion"))'])
        path = self.made("scripts", "beam_boom", "main.py")
        text = self.read("made/main/scripts/beam_boom/main.py")
        self.assertIn("from kit import values, area\n", text)
        self.assertIn("import math\n", text)
        self.assertIn("from mc import rcon\n", text)
        self.assertNotIn("actions", text)                # a values class stays on the PC
        crater = text.index("# --- preset: crater (copied from _presets/crater.py) ---\ndef crater(at, v):")
        boom = text.index("# --- preset: explosion (copied from _presets/explosion.py) ---\ndef explosion(at, v):")
        wiring = text.index("v = values()\n")
        self.assertLess(crater, boom)
        self.assertLess(boom, wiring)
        self.assertTrue(text.startswith(BEAM.split("\n")[0]))
        compile(text, path, "exec")
        action = self.read("made/main/actions/beam_boom.py")
        self.assertIn('class BeamBoom(Action, preset("crater"), preset("explosion")):\n', action)
        self.assertIn('    script = "beam_boom"\n', action)
        self.assertIn('    takes = ["player", "cooldown"]\n', action)
        self.assertIn('    fixed = {"colour": "red"}\n', action)
        self.assertIn("    cooldown: Live[float] = Field(10, ge=0, le=60)\n    \"\"\"seconds between shots\"\"\"\n",
                      action)
        self.assertNotIn("crater_leave", action)        # inherited, with its description
        # What is left to do: the action's line.
        self.assertIn("check: 1 to fix before it is done:", r.stdout)
        self.assertIn("made/main/actions/beam_boom.py: its line on the menu is still a TODO", r.stdout)
        finish_todos(self.made("actions", "beam_boom.py"))
        r = self.box.vfg("script", "check", "beam_boom")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: made/main/scripts/beam_boom/main.py\n")
        r = self.box.vfg("action", "check", "beam_boom")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/beam_boom.py\n"), r.stderr)
        # A made script and its action are copied the same way.
        r = self.box.vfg("script", "new", "boom2", "--from", "beam_boom")
        self.assertOk(r)
        self.assertIn("from made/main/scripts/beam_boom/ (made)", r.stdout)
        self.assertIn('class Boom2(Action, preset("crater"), preset("explosion")):\n',
                      self.read("made/main/actions/boom2.py"))

    def test_a_scripts_action_is_carried_with_its_presets_inherited(self):
        r = self.box.vfg("script", "new", "galick_pit", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        self.assertIn('made made/main/actions/galick_pit.py: class GalickPit(Action, preset("crater"), '
                      'preset("explosion"))', r.stdout)
        action = self.read("made/main/actions/galick_pit.py")
        self.assertNotIn("crater_radius:", action)      # inherited, with its description
        self.assertIn('    takes = ["player", "damage"]\n', action)
        self.assertIn('    defaults = {"crater_radius": 4}\n', action)
        self.assertIn('    damage: float = Field(20, ge=0, le=100)\n', action)
        self.assertEqual([l for l in r.stdout.splitlines() if "TODO" in l],
                         ["  made/main/actions/galick_pit.py: its line on the menu is still a TODO: say what it "
                          "does, in players' words"])

    def test_the_actions_own_words_for_what_it_inherits_are_carried_too(self):
        self.write("actions/galick.py", GALICK_ACTION.replace(
            '    defaults = {"crater_radius": 4}\n',
            '    defaults = {"crater_radius": 4}\n    descriptions = {"crater_radius": "how wide the galick pit is"}\n'))
        r = self.box.vfg("script", "new", "galick_pit", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        action = self.read("made/main/actions/galick_pit.py")
        self.assertIn('    defaults = {"crater_radius": 4}\n    descriptions = {"crater_radius": "how wide the galick pit '
                      'is"}\n', action)
        finish_todos(self.made("actions", "galick_pit.py"))
        r = self.box.vfg("action", "check", "galick_pit")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/galick_pit.py\n"), r.stderr)
        got = json.loads(self.box.vfg("action", "list", "--json").stdout)["actions"]["galick_pit"]
        self.assertEqual(got["fields"]["crater_radius"]["description"], "how wide the galick pit is")
        # From a script whose action gives no words of its own, none.
        r = self.box.vfg("script", "new", "beam_pit", "--from", "beam", "--preset", "crater")
        self.assertOk(r)
        self.assertNotIn("descriptions", self.read("made/main/actions/beam_pit.py"))

    def test_a_presets_own_variables_stay_in_its_block(self):
        self.write("scripts/_presets/counter.py", COUNTER)
        self.write("scripts/_presets/tally.py", TALLY)
        r = self.box.vfg("script", "new", "counted", "--from", "beam", "--preset", "counter",
                         "--preset", "tally", "--preset", "crater")
        self.assertOk(r)
        text = self.read("made/main/scripts/counted/main.py")
        L = load_library_module()
        lib = L.Library(self.box.repo, "main")
        blocks = preset_blocks(text)
        self.assertEqual(list(blocks), ["counter", "tally", "crater"])
        for name in blocks:
            self.assertEqual(blocks[name], L.preset_body(lib.preset(name)).strip(), name)
        self.assertLess(text.index("_counter_seen = set()"), text.index("# --- preset: tally"))

    def test_a_preset_the_script_already_holds_and_one_it_does_not(self):
        r = self.box.vfg("script", "new", "galick2", "--from", "galick", "--preset", "crater")
        self.assertOneLine(r, 1, "^vfg: cannot copy crater in: it already holds crater$")
        self.assertFalse(os.path.exists(self.made("scripts", "galick2")))
        self.assertFalse(os.path.exists(self.made("actions", "galick2.py")))
        r = self.box.vfg("script", "new", "galick2", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        text = self.read("made/main/scripts/galick2/main.py")
        self.assertEqual(text.count("# --- preset: "), 2)
        self.assertLess(text.index("# --- preset: crater"), text.index("# --- preset: explosion"))
        self.assertLess(text.index("# --- preset: explosion"), text.index("v = values()"))
        finish_todos(self.made("actions", "galick2.py"))
        self.assertOk(self.box.vfg("script", "check", "galick2"))

    def test_names_are_never_taken_twice(self):
        self.assertOneLine(self.box.vfg("script", "new", "beam"), 1, r"^vfg: beam is taken: scripts/beam — ")
        self.assertOneLine(self.box.vfg("script", "new", "galick"), 1, "galick is taken")
        self.assertOk(self.box.vfg("script", "new", "mine", "--from", "beam"))
        self.assertOneLine(self.box.vfg("script", "new", "mine"), 1, r"mine is taken: made/main/scripts/mine")
        self.assertOneLine(self.box.vfg("script", "new", "mine", "--library"), 1, "mine is taken")
        self.write("actions/zap.py", "# an action's name\n")
        self.assertOneLine(self.box.vfg("script", "new", "zap"), 1, r"^vfg: zap is taken: actions/zap\.py")
        for bad in ("Bad.Name", "with-dash", "9lives"):
            self.assertOneLine(self.box.vfg("script", "new", bad), 2)
        self.assertOneLine(self.box.vfg("script", "new", "x", "--preset", "nosuch"), 1, "no preset nosuch")
        self.assertOneLine(self.box.vfg("script", "new", "x", "--from", "nosuch"), 1, "no script nosuch")
        self.assertOneLine(self.box.vfg("script", "new", "x", "--preset", "crater", "--preset", "crater"), 2)
        self.assertFalse(os.path.exists(self.made("scripts", "x")))

    def test_new_from_nothing_must_be_filled_in(self):
        r = self.box.vfg("script", "new", "fresh", "--preset", "crater")
        self.assertOk(r)
        self.assertIn("made made/main/scripts/fresh/ from nothing", r.stdout)
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("ASKS is empty", r.stdout)
        action = self.read("made/main/actions/fresh.py")
        self.assertIn('class Fresh(Action, preset("crater")):\n', action)
        self.assertIn('    player: Players = "@a"\n    """who it is for: a name, several separated by commas, or @a"""', action)
        r = self.box.vfg("script", "check", "fresh")
        self.assertOneLine(r, 1, r"^vfg: 3 problems in 1 of 1 file$")
        self.assertEqual(len(r.stdout.splitlines()), 3)

    def test_library_flag_writes_into_scripts_and_actions(self):
        r = self.box.vfg("script", "new", "beam_too", "--from", "beam", "--library")
        self.assertOk(r)
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "scripts", "beam_too", "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "actions", "beam_too.py")))
        self.assertFalse(os.path.exists(self.made("scripts", "beam_too")))


BAD = {
    # (kind, text, what the problem line says)
    "undeclared": ("script", '"""Bad: x."""\nASKS = ["x"]\nv = {}\nprint(v["b"])\n',
                   r':4: v\["b"\] is read but its action has no field of that name'),
    "no_action": ("script", '"""Bad: x."""\nASKS = ["x"]\n', r":1: no action runs it"),
    "no_doc": ("script", 'ASKS = ["x"]\n', r":1: line 1 must be a docstring"),
    "no_asks": ("script", '"""Bad: x."""\n', r":1: no ASKS"),
    "imports_presets": ("script", '"""Bad: x."""\nASKS = ["x"]\nfrom _presets import crater\n',
                        r":3: imports from _presets"),
    "py310": ("script", '"""Bad: x."""\nASKS = ["x"]\nmatch 1:\n    case 1:\n        pass\n',
              r":\d+: not valid Python 3\.9"),
    "blast": ("preset", '"""A preset whose value lacks its name."""\nfrom actions._types import Values\n\n\n'
                        'class Blast(Values):\n    power: float = 1\n    """how strong"""\n\n\n'
                        'def blast(at, v):\n    return v["power"]\n',
              r":6: power does not start with the preset's name: blast_"),
    "no_class": ("preset", '"""A preset that reads a value it never declares."""\n\n\n'
                           'def nothing(at, v):\n    return v["nothing_much"]\n',
                 r':5: v\["nothing_much"\] is read but it has no values class to declare it'),
    "no_description": ("preset", '"""A preset with a value it never describes."""\nfrom actions._types import Values'
                                 '\n\n\nclass NoDescription(Values):\n    no_description_n: int = 1\n\n\n'
                                 'def no_description(at, v):\n    return v["no_description_n"]\n',
                       r":6: no_description_n has no description"),
    "bad_type": ("preset", '"""A preset whose value has a type no action takes."""\nfrom typing import Optional\n'
                           'from actions._types import Values\n\n\nclass BadType(Values):\n'
                           '    bad_type_n: Optional[int] = None\n    """how many"""\n\n\n'
                           'def bad_type(at, v):\n    return v["bad_type_n"]\n',
                 r": bad_type_n: Optional\[int\] is not one of actions/_types\.py's types"),
    "not_loading": ("preset", '"""A preset whose values class needs what the PC does not read."""\nimport datetime\n'
                              'from actions._types import Values\n\n\nclass NotLoading(Values):\n'
                              '    not_loading_when: str = str(datetime.date(2026, 1, 1))\n    """when"""\n\n\n'
                              'def not_loading(at, v):\n    return v["not_loading_when"]\n',
                    r": its values class does not load: NameError"),
}


class TestScriptCheck(LibraryCase):
    def test_check_refuses_each_bad_case(self):
        for name, (kind, text, want) in sorted(BAD.items()):
            rel = ("made/main/scripts/%s/main.py" % name) if kind == "script" else "made/main/presets/%s.py" % name
            self.write(rel, text)
            if kind == "script" and name != "no_action":
                self.write("made/main/actions/%s.py" % name, 'from actions._types import Action\n\n\n'
                           'class %s(Action):\n    """A test\'s."""\n\n    script = "%s"\n    runs = "once"\n'
                           % ("".join(w.title() for w in name.split("_")), name))
            r = self.box.vfg("script", "check", name)
            self.assertOneLine(r, 1, r"^vfg: \d+ problems? in 1 of 1 file$")
            self.assertRegex(r.stdout, "^" + re.escape(rel) + want, name)
            # Also by path.
            r = self.box.vfg("script", "check", os.path.join(self.box.repo, rel))
            self.assertEqual(r.returncode, 1, name)

    def test_check_everything_passes_and_counts(self):
        r = self.box.vfg("script", "check")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: 2 scripts and 2 presets\n")
        self.write("made/main/presets/blast.py", BAD["blast"][1])
        r = self.box.vfg("script", "check")
        self.assertOneLine(r, 1, r"^vfg: 1 problem in 1 of 5 files$")
        self.assertOneLine(self.box.vfg("script", "check", "nosuch"), 1, "no script or preset nosuch")

    def test_a_preset_value_never_starts_with_another_presets_name(self):
        self.write("made/main/presets/crater_rim.py", CRATER.replace("def crater(", "def crater_rim(")
                   .replace("class Crater(", "class CraterRim(")
                   .replace("def _crater_carve(", "def _crater_rim_carve(").replace("_crater_carve(at,", "_crater_rim_carve(at,"))
        r = self.box.vfg("script", "check", "crater_rim")
        self.assertOneLine(r, 1, r"^vfg: 2 problems in 1 of 1 file$")
        self.assertIn("crater_radius starts with crater_, another preset's name", r.stdout)

    def test_a_preset_helper_carries_its_name_and_a_script_defines_nothing_twice(self):
        self.write("made/main/presets/twin.py", CRATER.replace("def crater(", "def twin(").replace("class Crater(", "class Twin(")
                   .replace("    crater_", "    twin_").replace('v["crater_', 'v["twin_'))
        r = self.box.vfg("script", "check", "twin")
        self.assertOneLine(r, 1)
        self.assertIn("_crater_carve: a preset's helpers are private and carry its name: _twin_", r.stdout)
        self.write("made/main/scripts/double/main.py", GALICK.replace(
            "\n\nv = values()", "\n\ndef _crater_carve(at, r, block):\n    pass\n\n\nv = values()"))
        r = self.box.vfg("script", "check", "double")
        self.assertOneLine(r, 1)
        self.assertRegex(r.stdout, r"main\.py:\d+: _crater_carve is defined twice")


class TestPresetSave(LibraryCase):
    def setUp(self):
        LibraryCase.setUp(self)
        self.write("made/main/scripts/ice_staff/main.py", ICE_STAFF)
        self.write("made/main/actions/ice_staff.py", ICE_STAFF_ACTION)

    def test_lift_a_function_then_copy_it_into_a_script(self):
        r = self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0],
                         "saved made/main/presets/ice_trail.py from ice_staff.ice_path: values "
                         "ice_trail_length (was length), ice_rate; helpers _ice_trail_lay")
        self.assertIn("check: ok", r.stdout)
        self.assertIn("next: read its values class", r.stdout)
        text = self.read("made/main/presets/ice_trail.py")
        self.assertTrue(text.startswith('"""Lay ice along where a player looks, some of it packed."""\n'))
        L = load_library_module()
        self.assertEqual(L.values_class_fields(L.parse(self.made("presets", "ice_trail.py"), "preset")),
                         ["ice_trail_length", "ice_rate"])
        self.assertIn("from actions._types import Field, Live, Values  # the values class: read on the PC only\n",
                      text)
        self.assertIn('class IceTrail(Values):\n    ice_trail_length: Live[int] = Field(60, ge=5, le=200)\n'
                      '    """how far the ice reaches, in blocks"""\n    ice_rate: float = Field(0.5, ge=0, le=1)\n'
                      '    """how much of it is laid, from 0 to 1"""\n', text)
        self.assertIn("def ice_trail(p, v):", text)
        self.assertIn('aim.ray(p, v["ice_trail_length"])', text)
        self.assertIn("def _ice_trail_lay(at, v):", text)
        self.assertIn("ICE_BLOCKS = ", text)
        for imp in ("import random\n", "from kit import aim\n", "from mc import rcon\n"):
            self.assertIn(imp, text)
        self.assertNotIn("def shout", text)
        # The script it came from is not touched.
        self.assertEqual(self.read("made/main/scripts/ice_staff/main.py"), ICE_STAFF)
        r = self.box.vfg("script", "new", "beam_ice", "--from", "beam", "--preset", "ice_trail")
        self.assertOk(r)
        self.assertIn("  + ice_trail (made): ice_trail_length, ice_rate", r.stdout)
        self.assertIn('class BeamIce(Action, preset("ice_trail")):\n', self.read("made/main/actions/beam_ice.py"))
        self.assertIn("# --- preset: ice_trail (copied from made/main/presets/ice_trail.py) ---",
                      self.read("made/main/scripts/beam_ice/main.py"))
        finish_todos(self.made("actions", "beam_ice.py"))
        self.assertOk(self.box.vfg("action", "check", "beam_ice"))

    def test_a_value_the_action_does_not_describe_is_a_todo_to_fill(self):
        self.write("made/main/actions/ice_staff.py", ICE_STAFF_ACTION.replace(
            '    """how much of it is laid, from 0 to 1"""\n', '    """TODO"""\n'))
        r = self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ice_rate's description is still a TODO", r.stdout)

    def test_refusals(self):
        self.assertOk(self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail"))
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail"), 1,
                           "ice_trail is taken: made/main/presets/ice_trail.py")
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "ice_path", "crater"), 1,
                           "crater is taken: scripts/_presets/crater.py")
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "nosuch", "x"), 1,
                           "ice_staff has no top-level function nosuch")
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "loud_path", "loud"), 1,
                           r"loud_path calls ice_path, shout, which is public in ice_staff")
        self.assertOneLine(self.box.vfg("preset", "save", "nosuch", "f", "x"), 1, "no script nosuch")
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "ice_path", "Bad"), 2)
        os.remove(self.made("actions", "ice_staff.py"))
        self.assertOneLine(self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_lane"), 1,
                           r"ice_path reads length, ice_rate, which ice_staff's action has no field for "
                           r"\(no action runs ice_staff\)")
        r = self.box.vfg("preset", "save", "ice_staff", "shout", "shout_out")
        self.assertOk(r)  # saved, but its first line is a TODO to fill in
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("def shout_out(p, v):", self.read("made/main/presets/shout_out.py"))


class TestHelpLists(Case):
    def test_new_commands_are_listed_and_script_run_is_gone(self):
        r = self.box.vfg("--help")
        for c in ("preset", "state", "made"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        r = self.box.vfg("script", "--help")
        for w in ("new", "check", "set", "save", "list", "stop"):
            self.assertIn(w, r.stdout)
        self.assertNotIn("run", r.stdout.split("positional arguments")[0])
        self.assertEqual(self.box.vfg("script", "run", "hello").returncode, 2)


# ========================================================== the real kit

REAL_LIB = os.path.join(REPO, "scripts", "_lib")

class TestRealPresetsTogether(Case):
    """The repo's own presets with module-level variables of their own (_projectile_n,
    _meteor_fall_n, _replace_blocks_known), and two more, copied into one script: each
    preset's code stays whole under its marker, check passes, and it runs on the real kit."""
    PRESETS = ["projectile", "meteor_fall", "replace_blocks", "crater", "explosion"]

    def setUp(self):
        Case.setUp(self)
        have = [os.path.isfile(os.path.join(REPO, "scripts", "_presets", p + ".py")) for p in self.PRESETS]
        if not all(have) or not os.path.isfile(os.path.join(REAL_LIB, "kit", "values.py")):
            self.skipTest("needs scripts/_presets/{%s}.py and scripts/_lib/kit" % ",".join(self.PRESETS))
        lib = os.path.join(self.box.repo, "scripts", "_lib")
        shutil.rmtree(lib)
        shutil.copytree(REAL_LIB, lib, ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(self.box.repo, "scripts", "_presets"))
        standins.install_types(self.box.repo)
        for p in self.PRESETS:
            shutil.copy(os.path.join(REPO, "scripts", "_presets", p + ".py"),
                        os.path.join(self.box.repo, "scripts", "_presets", p + ".py"))

    def test_five_presets_in_one_script_each_whole_under_its_marker(self):
        args = ["script", "new", "together"]
        for p in self.PRESETS:
            args += ["--preset", p]
        self.assertOk(self.box.vfg(*args))
        path = os.path.join(self.box.repo, "made", "main", "scripts", "together", "main.py")
        with open(path) as f:
            text = f.read()
        L = load_library_module()
        lib = L.Library(self.box.repo, "main")
        blocks = preset_blocks(text)
        self.assertEqual(list(blocks), self.PRESETS)
        for name in self.PRESETS:
            self.assertEqual(blocks[name], L.preset_body(lib.preset(name)).strip(), name)
        text = text.replace("TODO <Title>: say what it does in one line.", "Together: five presets in one.") \
            .replace("ASKS = []", 'ASKS = ["all of them at once"]')
        with open(path, "w") as f:
            f.write(text)
        finish_todos(os.path.join(self.box.repo, "made", "main", "actions", "together.py"))
        r = self.box.vfg("script", "check", "together")
        self.assertOk(r)


# ======================================================= the real library

LIVE_LIBRARY = os.path.isdir(os.path.join(REPO, "scripts", "_presets"))


@unittest.skipUnless(LIVE_LIBRARY, "no scripts/_presets/ in this repo yet")
class TestRealLibrary(unittest.TestCase):
    """The repo's own examples and presets, read in place, never written."""

    @classmethod
    def setUpClass(cls):
        cls.L = load_library_module()
        cls.lib = cls.L.Library(REPO, "test-never-made")

    def test_every_library_script_preset_and_action_passes_check(self):
        """vfg script check on the repo itself: a ported script's values read from its actions'
        fields. An action whose script is not written yet is the only problem allowed."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("VFG_")}
        env.update(VFG_REPO=REPO, VFG_HOME=tempfile.mkdtemp(prefix="vfg-check-home-"), PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([PY, VFG, "script", "check"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=env, timeout=300)
        waiting = re.compile(r"^(\w+)\.py: left out: script \1: there is no scripts/\1/main\.py$")
        problems = [l for l in r.stdout.splitlines() if not (waiting.match(l) and not os.path.isdir(
            os.path.join(REPO, "scripts", waiting.match(l).group(1))))]
        if r.returncode == 0:
            self.assertTrue(r.stdout.startswith("ok: "), r.stdout)
        else:
            self.assertEqual(problems, [], r.stderr)

    def fields(self):
        """{script: the fields of the actions over it}, from the repo's own actions (pydantic)."""
        if not hasattr(type(self), "_fields"):
            sys.path.insert(0, os.path.join(REPO, "lib", "py"))
            import warnings
            import vfg_actions
            warnings.filterwarnings("ignore", message=r'Field name "copy" in ".*" shadows an attribute in parent')
            cat = vfg_actions.Catalog(REPO, None)
            out = {}
            for n in cat.names():
                out.setdefault(cat.get(n).script, set()).update(cat.get(n).model_fields)
            type(self)._fields = out
        return type(self)._fields

    def test_new_from_any_example_with_any_preset_passes_check(self):
        L, lib, fields = self.L, self.lib, self.fields()
        examples = [s for s in lib.scripts() if s.name in fields and not L.check(s, lib, fields[s.name])]
        presets = [p for p in lib.presets() if not L.check(p, lib)]
        self.assertGreaterEqual(len(examples), 54)
        failed = []
        for s in examples:
            for p in presets:
                if p.name in s.holds:
                    continue
                try:
                    text, keys, _imps = L.add_preset(s.text, p, "main")
                except ValueError as e:
                    failed.append("%s + %s: %s" % (s.name, p.name, e))
                    continue
                path = os.path.join(tempfile.mkdtemp(prefix="vfg-new-"), s.name + "_plus", "main.py")
                os.makedirs(os.path.dirname(path))
                with open(path, "w") as f:
                    f.write(text)
                got = L.check(L.parse(path, "script", s.name + "_plus", "made", path), lib, fields[s.name] | set(keys))
                shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)
                if got:
                    failed.append("%s + %s: %s" % (s.name, p.name, got[0]))
        self.assertEqual(failed, [])

    def test_every_preset_into_one_script(self):
        L, lib = self.L, self.lib
        text = L.SKELETON.replace("TODO <Title>: say what it does in one line.", "All: every preset.") \
            .replace("ASKS = []", 'ASKS = ["everything"]')
        fields = set(["player"])     # the skeleton's action's; a preset's values come with it
        for p in lib.presets():
            if not L.check(p, lib):
                text, keys, _i = L.add_preset(text, p, "main")
                fields.update(keys)
        path = os.path.join(tempfile.mkdtemp(prefix="vfg-all-"), "all", "main.py")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(text)
        try:
            self.assertEqual(L.check(L.parse(path, "script", "all", "made", path), lib, fields), [])
        finally:
            shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)


# ================================================================ RCON lock

def raw_rcon(port, password):
    """A bare RCON client with no lock: a function that sends one command and returns the reply."""
    import struct
    s = socket.create_connection(("127.0.0.1", port), timeout=10)

    def exact(n):
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise OSError("closed")
            data += chunk
        return data

    def ask(rid, typ, body):
        b = body.encode()
        s.sendall(struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\0\0")
        (n,) = struct.unpack("<i", exact(4))
        return exact(n)[8:-2].decode()
    ask(1, 3, password)
    command = lambda cmd: ask(2, 2, cmd)
    command.close = s.close
    return command


MC_CLIENT = """
import sys
import mc
wrong = 0
for i in range(int(sys.argv[1])):
    want = "script-%d" % i
    if mc.rcon("echo " + want) != want:
        wrong += 1
print(wrong)
"""


class TestRconLock(Case):
    """Vanilla answers every RCON connection from one buffer (RconConsoleSource): two clients
    with a command out at once get each other's output. Every client holds one lock file from
    send to reply; the fake answers from one shared buffer, as vanilla does."""
    box_kwargs = {"rcon": False}

    def setUp(self):
        Case.setUp(self)
        self.box.start_rcon(shared=True)
        self.saved = os.environ.get("VFG_RCON_LOCK")
        os.environ["VFG_RCON_LOCK"] = os.path.join(self.box.root, "rcon.lock")
        self.opened = []

    def tearDown(self):
        for c in self.opened:
            c.close()
        if self.saved is None:
            os.environ.pop("VFG_RCON_LOCK", None)
        else:
            os.environ["VFG_RCON_LOCK"] = self.saved
        Case.tearDown(self)

    def common(self):
        """toolkit/remote/common.py, which vfg puts in front of every remote program."""
        ns = {"__name__": "vfg_remote_common"}
        with open(os.path.join(TOOLKIT, "remote", "common.py")) as f:
            exec(compile(f.read(), "common.py", "exec"), ns)
        return ns

    def hammer(self, makers, n=150):
        """One thread per client, n echoes each, all at once: the replies that were wrong."""
        wrong = []

        def run(tag, make):
            ask = make()
            for i in range(n):
                want = "%s-%d" % (tag, i)
                got = ask(want)
                if got != want:
                    wrong.append((want, got))
        threads = [threading.Thread(target=run, args=(tag, make)) for tag, make in makers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        return wrong

    def test_without_the_lock_replies_mix(self):
        def bare():
            ask = raw_rcon(self.box.port, self.box.password)
            self.opened.append(ask)
            return lambda w: ask("echo " + w)
        wrong = self.hammer([("a", bare), ("b", bare)])
        self.assertGreater(len(wrong), 10)
        self.assertTrue(any(len(got) > len(want) for want, got in wrong), wrong[:3])  # glued

    def test_with_the_lock_vfg_scripts_and_realmc_never_mix(self):
        C = self.common()
        r = C["Rcon"](self.box.rmc)
        self.opened.append(r)
        self.assertEqual(r.lock_path, os.environ["VFG_RCON_LOCK"])

        def vfg():
            r = C["Rcon"](self.box.rmc)
            self.opened.append(r)
            return lambda w: r.command("echo " + w)
        makers = [("vfg1", vfg), ("vfg2", vfg)]
        realmc_py = os.path.join(REPO, "integration", "realmc", "realmc.py")
        if os.path.isfile(realmc_py):
            import importlib.util
            spec = importlib.util.spec_from_file_location("realmc_for_test", realmc_py)
            realmc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(realmc)

            def real():
                r = realmc.Rcon(os.path.join(self.box.rmc, "server.properties"))
                self.opened.append(r)
                return lambda w: r.command("echo " + w)
            makers.append(("realmc", real))
        # A script's own mc.rcon in another process, with the same lock, at the same time.
        lib = REAL_LIB if os.path.isfile(os.path.join(REAL_LIB, "mc.py")) else None
        script = None
        with open(os.path.join(lib or REAL_LIB, "mc.py")) if lib else io.StringIO("") as f:
            has_lock = "lock_path" in f.read()
        if has_lock:
            script = subprocess.Popen([PY, "-c", MC_CLIENT, "150"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, universal_newlines=True,
                                      env=dict(os.environ, PYTHONPATH=lib, VFG_INSTANCE="locktest",
                                               VFG_MC_PROPERTIES=os.path.join(self.box.rmc,
                                                                              "server.properties")))
        wrong = self.hammer(makers)
        self.assertEqual(wrong, [])
        if script is not None:
            o, e = script.communicate(timeout=60)
            self.assertEqual(o.strip(), "0", e)

    def test_a_thread_holding_the_lock_takes_it_again_others_wait(self):
        """live_kit.py once held mc's lock around players.cmd; realmc's client takes the lock
        itself now, and a second flock in one process on another fd would wait forever. A
        thread that holds it re-enters through any client; another thread still waits."""
        lib = REAL_LIB
        realmc_py = os.path.join(REPO, "integration", "realmc", "realmc.py")
        if not os.path.isfile(os.path.join(lib, "mc.py")) or not os.path.isfile(realmc_py):
            self.skipTest("needs scripts/_lib/mc.py and integration/realmc/realmc.py")
        code = """
import importlib.util, os, signal, sys, threading, time
import mc
signal.signal(signal.SIGALRM, lambda *a: (print("deadlock"), os._exit(3)))
signal.alarm(10)
common = {"__name__": "common"}
exec(compile(open(sys.argv[1]).read(), "common.py", "exec"), common)
spec = importlib.util.spec_from_file_location("realmc", sys.argv[2])
realmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(realmc)
props = os.environ["VFG_MC_PROPERTIES"]
vfg, real = common["Rcon"](os.path.dirname(props)), realmc.Rcon(props)
outer = mc._Flock()
outer.acquire()
got = []
other = threading.Thread(target=lambda: got.append(common["Rcon"](os.path.dirname(props)).command("echo other")))
other.start()
inner = [vfg.command("echo vfg"), real.command("echo realmc"), mc.rcon("echo mc")]
time.sleep(0.3)
waited = list(got)
outer.release()
other.join(5)
print(inner, waited, got)
"""
        env = dict(os.environ, PYTHONPATH=lib, VFG_INSTANCE="reentry",
                   VFG_MC_PROPERTIES=os.path.join(self.box.rmc, "server.properties"))
        r = subprocess.run([PY, "-c", code, os.path.join(TOOLKIT, "remote", "common.py"), realmc_py],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "['vfg', 'realmc', 'mc'] [] ['other']")

    def test_the_default_lock_path(self):
        os.environ.pop("VFG_RCON_LOCK")
        C = self.common()
        self.assertEqual(C["rcon_lock_path"](25575), "/tmp/vfg-rcon-25575.lock")
        r = C["Rcon"](self.box.rmc)
        self.assertEqual(r.lock_path, "/tmp/vfg-rcon-%d.lock" % self.box.port)
        self.assertEqual(r.command("echo hi"), "hi")
        r.close()
        os.environ["VFG_RCON_LOCK"] = os.path.join(self.box.root, "rcon.lock")


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
