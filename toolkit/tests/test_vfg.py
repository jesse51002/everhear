"""The vfg toolkit's tests: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Each test gets a temp VFG_HOME, a temp VFG_REPO and a temp "box" that
VFG_REMOTE_VFG and VFG_REMOTE_MC point into. VFG_SSH is fake_ssh, which runs
the remote command here with bash; a python3 shim for the interpreter running
the tests goes first on PATH, so the remote programs run on it too (3.9 under
`uv run --python 3.9`).
"""
import base64
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
from datetime import datetime, timedelta, timezone

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(TESTS)
VFG = os.path.join(TOOLKIT, "vfg")
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
# A stand-in for orchestrator/run: a sleep child, then a loop.
sleep 1000 &
echo $! > "$VFG_ORCH_DIR/child.pid"
echo "fake runner $$ up"
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
                                      os.path.join(self.rmc, "server.properties"), self.rcon_log,
                                      self.world],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        pidfiles = [os.path.join(self.vhome, "orch", "runner.pid")]
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
                  "feed", "script", "usage"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        for args, words in ((["orch", "--help"], ["start", "stop", "status", "send", "reply"]),
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

    def test_twenty_sends_at_once(self):
        procs = [subprocess.Popen([PY, VFG, "orch", "send", "task %d" % i, "--context", "c%d" % i],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.box.env())
                 for i in range(20)]
        ids = []
        for p in procs:
            o, e = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, e)
            ids.append(o.decode().strip())
            self.assertEqual(e.decode(), "vfg: orchestrator runner is not running — vfg orch start\n")
        self.assertEqual(sorted(ids, key=lambda t: int(t[1:])), ["T%d" % i for i in range(1, 21)])
        with open(self.orch("inbox.jsonl")) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual(len(rows), 20)
        for row in rows:
            self.assertEqual(list(row.keys()), ["id", "at", "task", "context"])
            self.assertRegex(row["at"], ISO_RE)
            self.assertEqual(row["context"], "c" + row["task"].split()[1])
        self.assertEqual(sorted(r["id"] for r in rows), sorted(ids))

    def test_send_defaults_and_refusals(self):
        r = self.box.vfg("orch", "send", "build ~ a 'castle'")
        self.assertEqual(r.stdout, "T1\n")
        with open(self.orch("inbox.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["context"], "")
        self.assertOneLine(self.box.vfg("orch", "send", " "), 2)
        with open(self.orch("inbox.jsonl"), "a") as f:
            f.write("not json\n{\"id\": \"T41\"}\n")
        self.assertEqual(self.box.vfg("orch", "send", "next").stdout, "T42\n")

    def test_start_stop_status(self):
        r = self.box.vfg("orch", "status")
        self.assertOneLine(r, 1, "^vfg: orchestrator runner is not running$")
        self.assertEqual(r.stdout, "stopped\nopen: none\n")
        self.assertEqual(self.box.vfg("orch", "stop").stdout, "not running\n")

        r = self.box.vfg("orch", "start", CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertOk(r)
        m = re.match(r"^started pid (\d+)\n$", r.stdout)
        self.assertTrue(m, r.stdout)
        pid = int(m.group(1))
        self.assertTrue(alive(pid))  # outlived the vfg that started it
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        self.assertIn(b"VFG_ORCH_RUNNER=1", env)
        self.assertFalse(any(e.startswith((b"CLAUDECODE=", b"CLAUDE_CODE_ENTRYPOINT=")) for e in env))
        self.assertEqual(os.getpgid(pid), pid)
        r = self.box.vfg("orch", "start")
        self.assertEqual(r.stdout, "running pid %d\n" % pid)
        with open(self.orch("runner.log")) as f:
            log = f.read()
        self.assertRegex(log, r"--- vfg orch start \d{4}-\d\d-\d\dT[\d:.]+Z ---\nfake runner %d up" % pid)
        self.assertEqual(log.count("--- vfg orch start"), 1)

        self.box.vfg("orch", "send", "one")
        self.box.vfg("orch", "send", "two")
        r = self.box.vfg("orch", "status")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^running pid %d since \d\d:\d\d:\d\d\nopen: T1 T2\n$" % pid)

        r = self.box.vfg("orch", "reply", "T1", "progress", "halfway")
        self.assertOk(r)
        self.assertIn("open: T1 T2", self.box.vfg("orch", "status").stdout)
        r = self.box.vfg("orch", "reply", "T1", "done", "castle", "built")
        self.assertOk(r)
        seq = int(r.stdout)
        with open(self.box.feed) as f:
            self.assertRegex(f.read().splitlines()[-1], r"^ORCH#%d \d\d:\d\d:\d\d T1 done \| castle built$" % seq)
        self.assertIn("open: T2\n", self.box.vfg("orch", "status").stdout)
        with open(self.orch("replies.jsonl")) as f:
            replies = [json.loads(l) for l in f]
        self.assertEqual([(x["id"], x["status"]) for x in replies], [("T1", "progress"), ("T1", "done")])
        self.assertEqual(list(replies[-1].keys()), ["id", "at", "status", "seq"])
        self.assertEqual(replies[-1]["seq"], seq)
        r = self.box.vfg("orch", "reply", "T1", "failed", "again")
        self.assertOneLine(r, 0, "already closed")

        with open(self.orch("child.pid")) as f:
            child = int(f.read())
        self.assertTrue(alive(child))
        r = self.box.vfg("orch", "stop")
        self.assertEqual(r.stdout, "stopped pid %d\n" % pid)
        self.assertTrue(wait_for(lambda: not alive(pid) and not alive(child), 5))
        self.assertFalse(os.path.exists(self.orch("runner.pid")))
        self.assertOneLine(self.box.vfg("orch", "status"), 1)

    def test_start_refusals(self):
        os.chmod(self.box.run_path, 0o644)
        self.assertOneLine(self.box.vfg("orch", "start"), 1, "is missing or not executable$")
        with open(self.box.run_path, "w") as f:
            f.write("#!/usr/bin/env bash\necho broken\nexit 7\n")
        os.chmod(self.box.run_path, 0o755)
        r = self.box.vfg("orch", "start")
        self.assertOneLine(r, 1, r"^vfg: runner exited at once \(code 7\) — see .*runner\.log$")
        self.assertFalse(os.path.exists(self.orch("runner.pid")))

    def test_stale_pid_is_not_running(self):
        os.makedirs(self.orch(""), exist_ok=True)
        with open(self.orch("runner.pid"), "w") as f:
            f.write("%d\n" % os.getpid())  # alive, but no VFG_ORCH_RUNNER=1
        self.assertOneLine(self.box.vfg("orch", "status"), 1)
        self.assertFalse(os.path.exists(self.orch("runner.pid")))

    def test_reply_refusals_and_down(self):
        for args in (["7", "done", "x"], ["T7", "finished", "x"], ["T0", "done", "x"]):
            self.assertOneLine(self.box.vfg("orch", "reply", *args), 2)
        self.assertOneLine(self.box.vfg("orch", "reply", "T7", "done", "x", FAKE_SSH_DOWN="1"), 4)
        self.assertFalse(os.path.exists(self.orch("replies.jsonl")))


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


# ============================================================ the library

REPO = os.path.dirname(TOOLKIT)

# A stand-in for scripts/_lib/kit in these tests: values() only, reading what
# kit.values reads — defaults, then argv key=value, then for live keys
# <VFG_RUN_DIR>/<VFG_INSTANCE>.values.json, again on every read.
FAKE_KIT = '''"""A stand-in for the kit: values() only."""
import json
import os
import sys


def _cast(entry, raw):
    t = entry["type"]
    if t == "int":
        return int(float(raw))
    if t == "number":
        x = float(raw)
        return int(x) if x.is_integer() else x
    if t == "bool":
        return str(raw).lower() in ("true", "1", "yes", "on", "y")
    if t == "list":
        return json.loads(raw) if str(raw).startswith("[") else [p for p in str(raw).split(",") if p]
    return raw


class _Values(object):
    def __init__(self, VALUES):
        self.spec = VALUES
        self.argv = {}
        for a in sys.argv[1:]:
            k, _, raw = a.partition("=")
            if k in VALUES:
                self.argv[k] = _cast(VALUES[k], raw)
        self.path = os.path.join(os.environ.get("VFG_RUN_DIR", "/opt/vfg/run"),
                                 os.environ.get("VFG_INSTANCE", "") + ".values.json")

    def __getitem__(self, key):
        entry = self.spec[key]
        value = self.argv.get(key, entry["default"])
        if entry.get("live"):
            try:
                with open(self.path) as f:
                    live = json.load(f)
                if key in live:
                    value = live[key]
            except (OSError, ValueError):
                pass
        return value


def values(VALUES):
    return _Values(VALUES)
'''

BEAM = '''"""Beam: prints its values every 0.1 s, so a test can see them change."""
ASKS = ["a beam", "a laser that goes further"]
import json
import time
from kit import values

VALUES = {
    "player":   {"type": "player", "default": "@a"},
    "length":   {"type": "int", "min": 5, "max": 200, "default": 60},
    "cooldown": {"type": "number", "min": 0, "max": 60, "default": 10, "live": True},
    "colour":   {"type": "enum", "of": ["red", "blue"], "default": "red", "live": True},
}

v = values(VALUES)
while True:
    print(json.dumps(dict((k, v[k]) for k in VALUES), sort_keys=True), flush=True)
    time.sleep(0.1)
'''

GALICK = '''"""Galick Gun: a beam that hurts what it hits and leaves a crater."""
ASKS = ["galick gun", "a big purple beam"]
from kit import values, aim, area

VALUES = {
    "player":        {"type": "player", "default": "@a"},
    "damage":        {"type": "number", "min": 0, "max": 100, "default": 20},
    "crater_radius": {"type": "int", "min": 2, "max": 64, "default": 4, "live": True},
    "crater_leave":  {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
}


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)


v = values(VALUES)
crater(aim.ray(v["player"], 60).end, v)
'''

CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area

VALUES = {
    "crater_radius": {"type": "int", "min": 2, "max": 64, "default": 8, "live": True},
    "crater_leave":  {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
}


def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)
'''

EXPLOSION = '''"""An explosion at a place: one blast, a ring or a plus."""
import math
from mc import rcon

VALUES = {
    "explosion_power":  {"type": "number", "min": 0, "max": 20, "default": 4, "live": True},
    "explosion_layout": {"type": "enum", "of": ["one", "ring", "plus"], "default": "one"},
}


def explosion(at, v):
    for dx, dz in _explosion_spots(v["explosion_layout"]):
        rcon("summon tnt %s %s %s" % (at[0] + dx, at[1], at[2] + dz))


def _explosion_spots(layout):
    n = {"one": 1, "ring": 8, "plus": 4}[layout]
    return [(round(4 * math.cos(i * 6.28 / n)), round(4 * math.sin(i * 6.28 / n))) for i in range(n)]
'''

ICE_STAFF = '''"""Ice Staff: lays ice where the player looks."""
ASKS = ["an ice staff"]
import random
from kit import values, aim
from mc import rcon

VALUES = {
    "player":   {"type": "player", "default": "@a"},
    "length":   {"type": "int", "min": 5, "max": 200, "default": 60, "live": True},
    "ice_rate": {"type": "number", "min": 0, "max": 1, "default": 0.5},
}

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


v = values(VALUES)
'''


def load_library_module():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("vfg_library", os.path.join(TOOLKIT, "lib", "library.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def values_of(path):
    """VALUES of a file, keys in order, read with ast as library.py does."""
    import ast
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "VALUES":
            return ast.literal_eval(node.value)
    return None


class LibraryCase(Case):
    """A box whose repo has two example scripts, two presets and a fake kit."""

    def setUp(self):
        Case.setUp(self)
        self.write("scripts/beam/main.py", BEAM)
        self.write("scripts/galick/main.py", GALICK)
        self.write("scripts/_presets/crater.py", CRATER)
        self.write("scripts/_presets/explosion.py", EXPLOSION)
        self.write("scripts/_lib/kit/__init__.py", FAKE_KIT)

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


class TestScriptNew(LibraryCase):
    def test_new_from_an_example_with_presets_then_check_passes(self):
        r = self.box.vfg("script", "new", "beam_boom", "--from", "beam", "--preset", "crater",
                         "--preset", "explosion")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[:3], [
            "made made/main/scripts/beam_boom/ from scripts/beam/ (library)",
            "  + crater (library): crater_radius, crater_leave",
            "  + explosion (library): explosion_power, explosion_layout"])
        self.assertIn("check: ok", r.stdout)
        path = self.made("scripts", "beam_boom", "main.py")
        values = values_of(path)
        self.assertEqual(list(values), ["player", "length", "cooldown", "colour", "crater_radius",
                                        "crater_leave", "explosion_power", "explosion_layout"])
        self.assertEqual(values["crater_radius"], {"type": "int", "min": 2, "max": 64, "default": 8, "live": True})
        text = self.read("made/main/scripts/beam_boom/main.py")
        self.assertIn('    "crater_radius":    {"type": "int", "min": 2, "max": 64, "default": 8, "live": True},\n',
                      text)
        self.assertIn("from kit import values, area\n", text)
        self.assertIn("import math\n", text)
        self.assertIn("from mc import rcon\n", text)
        crater = text.index("# --- preset: crater (copied from _presets/crater.py) ---\ndef crater(at, v):")
        boom = text.index("# --- preset: explosion (copied from _presets/explosion.py) ---\ndef explosion(at, v):")
        wiring = text.index("v = values(VALUES)")
        self.assertLess(crater, boom)
        self.assertLess(boom, wiring)
        self.assertTrue(text.startswith(BEAM.split("\n")[0]))
        compile(text, path, "exec")
        r = self.box.vfg("script", "check", "beam_boom")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: made/main/scripts/beam_boom/main.py\n")
        # A made preset and a made script are found the same way.
        r = self.box.vfg("script", "new", "boom2", "--from", "beam_boom")
        self.assertOk(r)
        self.assertIn("from made/main/scripts/beam_boom/ (made)", r.stdout)

    def test_a_preset_the_script_already_holds_and_one_it_does_not(self):
        r = self.box.vfg("script", "new", "galick2", "--from", "galick", "--preset", "crater")
        self.assertOneLine(r, 1, "^vfg: cannot copy crater in: it already holds crater$")
        self.assertFalse(os.path.exists(self.made("scripts", "galick2")))
        r = self.box.vfg("script", "new", "galick2", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        self.assertIn("check: ok", r.stdout)
        text = self.read("made/main/scripts/galick2/main.py")
        self.assertEqual(text.count("# --- preset: "), 2)
        self.assertLess(text.index("# --- preset: crater"), text.index("# --- preset: explosion"))
        self.assertLess(text.index("# --- preset: explosion"), text.index("v = values(VALUES)"))

    def test_names_are_never_taken_twice(self):
        self.assertOneLine(self.box.vfg("script", "new", "beam"), 1, r"^vfg: beam is taken: scripts/beam — ")
        self.assertOneLine(self.box.vfg("script", "new", "hello"), 1, "hello is taken")
        self.assertOk(self.box.vfg("script", "new", "mine", "--from", "beam"))
        self.assertOneLine(self.box.vfg("script", "new", "mine"), 1, r"mine is taken: made/main/scripts/mine")
        self.assertOneLine(self.box.vfg("script", "new", "mine", "--library"), 1, "mine is taken")
        self.assertOneLine(self.box.vfg("script", "new", "Bad.Name"), 2)
        self.assertOneLine(self.box.vfg("script", "new", "x", "--preset", "nosuch"), 1, "no preset nosuch")
        self.assertOneLine(self.box.vfg("script", "new", "x", "--from", "nosuch"), 1, "no script nosuch")
        self.assertOneLine(self.box.vfg("script", "new", "x", "--preset", "crater", "--preset", "crater"), 2)
        r = self.box.vfg("script", "new", "x", "--from", "hello", "--preset", "crater")
        self.assertOneLine(r, 1, r"hello has no VALUES \(a seed script\)")
        self.assertFalse(os.path.exists(self.made("scripts", "x")))

    def test_new_from_nothing_must_be_filled_in(self):
        r = self.box.vfg("script", "new", "fresh", "--preset", "crater")
        self.assertOk(r)
        self.assertIn("made made/main/scripts/fresh/ from nothing", r.stdout)
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("ASKS is empty", r.stdout)
        values = values_of(self.made("scripts", "fresh", "main.py"))
        self.assertEqual(list(values), ["player", "crater_radius", "crater_leave"])
        r = self.box.vfg("script", "check", "fresh")
        self.assertOneLine(r, 1, r"^vfg: 2 problems in 1 of 1 file$")
        self.assertEqual(len(r.stdout.splitlines()), 2)

    def test_library_flag_writes_into_scripts(self):
        r = self.box.vfg("script", "new", "beam_too", "--from", "beam", "--library")
        self.assertOk(r)
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "scripts", "beam_too", "main.py")))
        self.assertFalse(os.path.exists(self.made("scripts", "beam_too")))


BAD = {
    # (kind, text, what the problem line says)
    "not_literal": ("script", '"""Bad: x."""\nASKS = ["x"]\nR = 5\nVALUES = {"r": {"type": "int", "default": R}}\n',
                    r":4: VALUES is not a literal"),
    "outside": ("script", '"""Bad: x."""\nASKS = ["x"]\nVALUES = {"r": {"type": "int", "min": 1, "max": 5, '
                          '"default": 9}}\n', r":3: r: default 9 is outside its limits \(1\.\.5\)"),
    "unknown_type": ("script", '"""Bad: x."""\nASKS = ["x"]\nVALUES = {"c": {"type": "colour", "default": 1}}\n',
                     r":3: c: type 'colour' is not one of int, number"),
    "twice": ("script", '"""Bad: x."""\nASKS = ["x"]\nVALUES = {\n    "crater_radius": {"type": "int", '
                        '"default": 3},\n    "crater_radius": {"type": "int", "default": 4},\n}\n',
              r":5: crater_radius is in VALUES twice \(lines 4 and 5\)"),
    "undeclared": ("script", '"""Bad: x."""\nASKS = ["x"]\nVALUES = {"a": {"type": "int", "default": 3}}\n'
                             'v = {}\nprint(v["b"])\n', r':5: v\["b"\] is read but VALUES does not declare it'),
    "no_doc": ("script", 'ASKS = ["x"]\nVALUES = {}\n', r":1: line 1 must be a docstring"),
    "no_asks": ("script", '"""Bad: x."""\nVALUES = {}\n', r":1: no ASKS"),
    "imports_presets": ("script", '"""Bad: x."""\nASKS = ["x"]\nfrom _presets import crater\nVALUES = {}\n',
                        r":3: imports from _presets"),
    "py310": ("script", '"""Bad: x."""\nASKS = ["x"]\nVALUES = {}\nmatch 1:\n    case 1:\n        pass\n',
              r":\d+: not valid Python 3\.9"),
    "blast": ("preset", '"""A preset whose value lacks its name."""\nVALUES = {"power": {"type": "number", '
                        '"default": 1}}\n\n\ndef blast(at, v):\n    return v["power"]\n',
              r":2: power does not start with the preset's name: blast_"),
}


class TestScriptCheck(LibraryCase):
    def test_check_refuses_each_bad_case(self):
        for name, (kind, text, want) in sorted(BAD.items()):
            rel = ("made/main/scripts/%s/main.py" % name) if kind == "script" else "made/main/presets/%s.py" % name
            self.write(rel, text)
            r = self.box.vfg("script", "check", name)
            self.assertOneLine(r, 1, r"^vfg: \d+ problems? in 1 of 1 file$")
            self.assertRegex(r.stdout, "^" + re.escape(rel) + want, name)
            # Also by path.
            r = self.box.vfg("script", "check", os.path.join(self.box.repo, rel))
            self.assertEqual(r.returncode, 1, name)

    def test_check_everything_passes_and_counts(self):
        r = self.box.vfg("script", "check")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: 4 scripts (2 seeds) and 2 presets\n")
        self.write("made/main/presets/blast.py", BAD["blast"][1])
        r = self.box.vfg("script", "check")
        self.assertOneLine(r, 1, r"^vfg: 1 problem in 1 of 7 files$")
        self.assertOneLine(self.box.vfg("script", "check", "nosuch"), 1, "no script or preset nosuch")

    def test_a_preset_helper_carries_its_name_and_a_script_defines_nothing_twice(self):
        self.write("made/main/presets/twin.py", CRATER.replace("def crater(", "def twin(")
                   .replace('"crater_', '"twin_').replace('v["crater_', 'v["twin_'))
        r = self.box.vfg("script", "check", "twin")
        self.assertOneLine(r, 1)
        self.assertIn("_crater_carve: a preset's helpers are private and carry its name: _twin_", r.stdout)
        self.write("made/main/scripts/double/main.py", GALICK.replace(
            "\n\nv = values(VALUES)", "\n\ndef _crater_carve(at, r, block):\n    pass\n\n\nv = values(VALUES)"))
        r = self.box.vfg("script", "check", "double")
        self.assertOneLine(r, 1)
        self.assertRegex(r.stdout, r"main\.py:\d+: _crater_carve is defined twice")


class TestPresetSave(LibraryCase):
    def setUp(self):
        LibraryCase.setUp(self)
        self.write("made/main/scripts/ice_staff/main.py", ICE_STAFF)

    def test_lift_a_function_then_copy_it_into_a_script(self):
        r = self.box.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0],
                         "saved made/main/presets/ice_trail.py from ice_staff.ice_path: values "
                         "ice_trail_length (was length), ice_rate; helpers _ice_trail_lay")
        self.assertIn("check: ok", r.stdout)
        path = self.made("presets", "ice_trail.py")
        text = self.read("made/main/presets/ice_trail.py")
        self.assertTrue(text.startswith('"""Lay ice along where a player looks, some of it packed."""\n'))
        self.assertEqual(list(values_of(path)), ["ice_trail_length", "ice_rate"])
        self.assertIn("def ice_trail(p, v):", text)
        self.assertIn('aim.ray(p, v["ice_trail_length"])', text)
        self.assertIn("def _ice_trail_lay(at, v):", text)
        self.assertIn("ICE_BLOCKS = ", text)
        for imp in ("import random\n", "from kit import aim\n", "from mc import rcon\n"):
            self.assertIn(imp, text)
        self.assertNotIn("values", text.split("VALUES")[0])
        self.assertNotIn("def shout", text)
        # The script it came from is not touched.
        self.assertEqual(self.read("made/main/scripts/ice_staff/main.py"), ICE_STAFF)
        r = self.box.vfg("script", "new", "beam_ice", "--from", "beam", "--preset", "ice_trail")
        self.assertOk(r)
        self.assertIn("  + ice_trail (made): ice_trail_length, ice_rate", r.stdout)
        self.assertIn("check: ok", r.stdout)
        self.assertIn("# --- preset: ice_trail (copied from made/main/presets/ice_trail.py) ---",
                      self.read("made/main/scripts/beam_ice/main.py"))

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
        r = self.box.vfg("preset", "save", "ice_staff", "shout", "shout_out")
        self.assertOk(r)  # saved, but its first line is a TODO to fill in
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("def shout_out(p, v):", self.read("made/main/presets/shout_out.py"))


class TestInstances(LibraryCase):
    def box_file(self, name):
        with open(os.path.join(self.box.rvfg, "run", name)) as f:
            return json.load(f)

    def log_values(self, instance, after=0):
        """The values the fake beam printed last, read from its log."""
        with open(os.path.join(self.box.rvfg, "run", instance + ".log")) as f:
            rows = [json.loads(l) for l in f.read().splitlines()[1:] if l.startswith("{")]
        return rows[-1] if len(rows) > after else None

    def started(self, r, name):
        self.assertOk(r)
        m = re.search(r"^running %s pid (\d+) — vfg script logs %s$" % (name, name), r.stdout, re.M)
        self.assertTrue(m, r.stdout)
        return int(m.group(1))

    def test_two_instances_live_set_restart_stop_one_save(self):
        a = self.started(self.box.vfg("script", "run", "beam", "as=beam-a", "player=Steve", "cooldown=5",
                                      "--task", "T5"), "beam-a")
        b = self.started(self.box.vfg("script", "run", "beam", "player=Alex", "as=beam-b"), "beam-b")
        self.assertNotEqual(a, b)
        r = self.box.vfg("script", "run", "beam", "as=beam-a")
        self.assertOneLine(r, 1, r"^vfg: beam-a is running \(pid %d\) — vfg script stop beam-a$" % a)
        with open("/proc/%d/environ" % a, "rb") as f:
            env = f.read().split(b"\0")
        for want in (b"VFG_INSTANCE=beam-a", b"VFG_SCRIPT=beam-a", b"VFG_LIVE_POLL_S=0.5",
                     b"VFG_RUN_DIR=" + os.path.join(self.box.rvfg, "run").encode()):
            self.assertIn(want, env)
        with open("/proc/%d/cmdline" % a, "rb") as f:
            argv = f.read().split(b"\0")
        self.assertEqual(argv[-3:], [b"player=Steve", b"cooldown=5", b""])
        meta = self.box_file("beam-a.json")
        self.assertEqual({k: meta[k] for k in ("instance", "script", "source", "values", "task")},
                         {"instance": "beam-a", "script": "beam", "source": "library",
                          "values": {"player": "Steve", "cooldown": 5}, "task": "T5"})
        self.assertRegex(meta["started"], ISO_RE)
        self.assertTrue(wait_for(lambda: self.log_values("beam-a") is not None, 5))
        self.assertEqual(self.log_values("beam-a"), {"player": "Steve", "length": 60, "cooldown": 5,
                                                     "colour": "red"})
        # A live value: the file changes, the pid does not, the script reads it.
        r = self.box.vfg("script", "set", "beam-a", "cooldown=0", "colour=BLUE")
        self.assertOk(r)
        self.assertEqual(r.stdout, "beam-a: cooldown=0 colour=blue — live, read again within 0.5 s\n")
        self.assertEqual(self.box_file("beam-a.values.json"), {"cooldown": 0, "colour": "blue"})
        self.assertTrue(alive(a))
        self.assertTrue(wait_for(lambda: (self.log_values("beam-a") or {}).get("cooldown") == 0, 5))
        # Not live: a restart, with the rest kept.
        r = self.box.vfg("script", "set", "beam-a", "length=1000")
        self.assertOk(r)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "length=1000 is above its max: clamped to 200")
        m = re.match(r"^beam-a restarted: length is not live, pid (\d+); kept player=Steve cooldown=0 "
                     r"colour=blue$", lines[1])
        self.assertTrue(m, lines)
        a2 = int(m.group(1))
        self.assertNotEqual(a2, a)
        self.assertTrue(wait_for(lambda: not alive(a), 5))
        self.assertTrue(alive(a2))
        self.assertEqual(self.box_file("beam-a.json")["values"], {"player": "Steve", "cooldown": 5, "length": 200})
        self.assertEqual(self.box_file("beam-a.values.json"), {"cooldown": 0, "colour": "blue"})
        self.assertTrue(wait_for(lambda: self.log_values("beam-a") == {"player": "Steve", "length": 200,
                                                                         "cooldown": 0, "colour": "blue"}, 5))
        # list: each instance, its script, task and what differs from the defaults.
        r = self.box.vfg("script", "list")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^beam-a running pid %d since \d\d:\d\d:\d\d — beam, T5, player=Steve "
                                   r"length=200 cooldown=0 colour=blue\nbeam-b running pid %d since "
                                   r"\d\d:\d\d:\d\d — beam, player=Alex\n$" % (a2, b))
        # Stopping one leaves the other running.
        self.assertEqual(self.box.vfg("script", "stop", "beam-a").stdout, "stopped beam-a\n")
        self.assertTrue(wait_for(lambda: not alive(a2), 5))
        self.assertTrue(alive(b))
        r = self.box.vfg("script", "list")
        self.assertRegex(r.stdout, r"^beam-a stopped — beam, T5, player=Steve length=200 cooldown=0 colour=blue\n"
                                   r"beam-b running pid %d " % b)
        # save: the instance's values become a made script's defaults.
        self.assertOk(self.box.vfg("script", "set", "beam-b", "cooldown=2.5"))
        r = self.box.vfg("script", "save", "beam-b", "beam_alex")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0], "saved made/main/scripts/beam_alex/ from beam-b (beam), "
                                                   "with player=Alex cooldown=2.5 as its defaults")
        self.assertIn("check: ok", r.stdout)
        values = values_of(self.made("scripts", "beam_alex", "main.py"))
        self.assertEqual([values[k]["default"] for k in values], ["Alex", 60, 2.5, "red"])
        self.assertOneLine(self.box.vfg("script", "save", "beam-b", "beam_alex"), 1, "beam_alex is taken")
        # A made script runs from the server's folder on the box.
        c = self.started(self.box.vfg("script", "run", "beam_alex"), "beam_alex")
        self.assertTrue(os.path.isfile(os.path.join(self.box.rvfg, "made", "main", "scripts", "beam_alex",
                                                    "main.py")))
        self.assertEqual(self.box_file("beam_alex.json")["source"], "made")
        self.assertTrue(wait_for(lambda: (self.log_values("beam_alex") or {}).get("player") == "Alex", 5))
        r = self.box.vfg("script", "list")
        self.assertIn("beam_alex running pid %d since" % c, r.stdout)
        self.assertRegex(r.stdout, r"beam_alex running pid \d+ since \d\d:\d\d:\d\d — beam_alex \(made\)\n")
        for name in ("beam-b", "beam_alex"):
            self.assertOk(self.box.vfg("script", "stop", name))

    def test_values_are_checked_before_anything_is_sent(self):
        before = len(self.box.ssh_calls())
        for args, code, pattern in (
                (["nope=1"], 2, r"^vfg: beam: nope is not one of its values: player, length, cooldown, colour$"),
                (["Steve"], 2, r"^vfg: beam: 'Steve' is not key=value"),
                (["colour=green"], 2, r"colour is one of red, blue, not 'green'"),
                (["length=3.5"], 2, r"length takes a whole number"),
                (["player=@e[type=zombie]"], 2, r"player is @a, @r, @p or player names"),
                (["as=Bad!"], 2, r"as=Bad! is not an instance name"),
                (["--task", "7"], 2, r"--task is T<n>")):
            self.assertOneLine(self.box.vfg("script", "run", "beam", *args), code, pattern)
        self.assertEqual(len(self.box.ssh_calls()), before)
        r = self.box.vfg("script", "run", "beam", "cooldown=-4", "player=Steve,Alex")
        self.started(r, "beam")
        self.assertTrue(r.stdout.startswith("cooldown=-4 is below its min: clamped to 0\n"))
        self.assertEqual(self.box_file("beam.json")["values"], {"cooldown": 0, "player": "Steve,Alex"})
        self.assertOk(self.box.vfg("script", "stop", "beam"))

    def test_set_refusals(self):
        self.assertOneLine(self.box.vfg("script", "set", "beam", "cooldown=1"), 1, "no instance beam")
        self.started(self.box.vfg("script", "run", "beam"), "beam")
        self.assertOneLine(self.box.vfg("script", "set", "beam", "nope=1"), 2, "nope is not one of its values")
        self.assertOneLine(self.box.vfg("script", "set", "beam", "cooldown"), 2, "is not key=value")
        self.assertOk(self.box.vfg("script", "stop", "beam"))
        self.assertOneLine(self.box.vfg("script", "set", "beam", "cooldown=1"), 1, "^vfg: beam is not running")
        self.started(self.box.vfg("script", "run", "loop", "as=loop2"), "loop2")
        self.assertOneLine(self.box.vfg("script", "set", "loop2", "x=1"), 1, "loop2 \\(loop\\) has no VALUES")
        self.assertOk(self.box.vfg("script", "stop", "loop2"))

    def test_a_seed_keeps_its_words_and_runs_as_an_instance(self):
        r = self.box.vfg("script", "run", "hello", "as=hello-two", "a=b", "world", "--wait", "5")
        self.assertOk(r)
        self.assertEqual(r.stdout, "hello a=b world\nlib fake mc script hello-two\n")
        meta = self.box_file("hello-two.json")
        self.assertEqual((meta["script"], meta["values"], meta["args"]), ("hello", None, ["a=b", "world"]))
        self.assertIn("hello-two stopped — hello, a=b world", self.box.vfg("script", "list").stdout)

    def test_deploy_takes_made_scripts_too(self):
        self.assertOk(self.box.vfg("script", "new", "mine", "--from", "beam"))
        r = self.box.vfg("script", "deploy")
        self.assertOk(r)
        self.assertEqual(r.stdout, "deployed: _lib beam galick hello loop\ndeployed made/main: mine\n")
        r = self.box.vfg("script", "deploy", "mine")
        self.assertEqual(r.stdout, "deployed: _lib\ndeployed made/main: mine\n")


class TestState(LibraryCase):
    WORLD = {"players": {"Steve": {"pos": [120.7, 64.0, -40.2], "dim": "minecraft:overworld",
                                   "shown": "[Owner] Steve"},
                         "Alex": {"pos": [98.0, 70.0, -12.5], "dim": "minecraft:the_nether"}},
             "daytime": 18000, "day": 3, "raining": False, "thundering": False}

    def test_state_in_one_call(self):
        self.box.write_world(self.WORLD)
        r = self.box.vfg("script", "run", "beam", "as=beam-a", "player=Steve", "--task", "T5")
        self.assertOk(r)
        self.assertOk(self.box.vfg("script", "run", "loop", "Alex", "as=hi"))
        before = len(self.box.ssh_calls())
        r = self.box.vfg("state")
        self.assertOk(r)
        calls = [c for c in self.box.ssh_calls()[before:] if c[-1] == "python3 -u -"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(r.stdout.splitlines(), [
            "online    Steve 120 64 -41 overworld, Alex 98 70 -13 the_nether",
            "time      night (18000), day 3",
            "weather   clear",
            "running   beam-a — beam, T5, player=Steve; touches Steve",
            "running   hi — loop, Alex; touches Alex",
        ])
        r = self.box.vfg("state", "--json")
        self.assertOk(r)
        got = json.loads(r.stdout)
        self.assertEqual(got["online"][0], {"name": "Steve", "pos": [120, 64, -41], "dimension": "overworld"})
        self.assertEqual(got["time"], {"daytime": 18000, "day": 3, "phase": "night"})
        self.assertEqual(got["weather"], "clear")
        beam = [i for i in got["instances"] if i["instance"] == "beam-a"][0]
        self.assertEqual((beam["script"], beam["source"], beam["task"], beam["touches"], beam["changed"]),
                         ("beam", "library", "T5", ["Steve"], {"player": "Steve"}))
        self.assertEqual(beam["values"], {"player": "Steve", "length": 60, "cooldown": 10, "colour": "red"})
        self.assertLess(got["ms"], 1000)
        self.box.write_world(dict(self.WORLD, raining=True, thundering=True, daytime=6000))
        got = json.loads(self.box.vfg("state", "--json").stdout)
        self.assertEqual((got["weather"], got["time"]["phase"]), ("thunder", "day"))
        self.box.write_world(dict(self.WORLD, raining=True))
        self.assertIn("weather   rain\n", self.box.vfg("state").stdout)
        for name in ("beam-a", "hi"):
            self.assertOk(self.box.vfg("script", "stop", name))

    def test_nobody_online_and_minecraft_down(self):
        self.box.write_world({"players": {}, "daytime": 23500, "day": 0})
        r = self.box.vfg("state")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), ["online    nobody", "time      dawn (23500), day 0",
                                                 "weather   clear", "running   nothing"])
        self.assertOk(self.box.vfg("script", "run", "beam"))
        self.box.stop_rcon()
        r = self.box.vfg("state")
        self.assertOneLine(r, 5, r"^vfg: minecraft is not running")
        self.assertRegex(r.stdout, r"^minecraft minecraft is not running .*\nrunning   beam; touches @a\n$")
        self.assertOk(self.box.vfg("script", "stop", "beam"))


class TestHelpLists(Case):
    def test_new_commands_are_listed(self):
        r = self.box.vfg("--help")
        for c in ("preset", "state"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        r = self.box.vfg("script", "--help")
        for w in ("new", "check", "set", "save"):
            self.assertIn(w, r.stdout)
        self.assertIn("--task", self.box.vfg("script", "run", "--help").stdout)


# ========================================================== the real kit

REAL_LIB = os.path.join(REPO, "scripts", "_lib")

TICKER = '''"""Ticker: logs its values every 0.1 s."""
ASKS = ["tick"]
import time
from kit import values
from mc import log

VALUES = {
    "cooldown": {"type": "number", "min": 0, "max": 60, "default": 10, "live": True},
    "length":   {"type": "int", "min": 1, "max": 9, "default": 3},
}

v = values(VALUES)
while True:
    log("cooldown", v["cooldown"], "length", v["length"])
    time.sleep(0.1)
'''


@unittest.skipUnless(os.path.isfile(os.path.join(REAL_LIB, "kit", "values.py")), "no scripts/_lib/kit yet")
class TestRealKit(Case):
    """The repo's own scripts/_lib (mc.py and the kit) on the fake box: what vfg script run
    and set write is what kit.values reads, and the tag follows the instance."""

    def setUp(self):
        Case.setUp(self)
        lib = os.path.join(self.box.repo, "scripts", "_lib")
        shutil.rmtree(lib)
        shutil.copytree(REAL_LIB, lib, ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(self.box.repo, "scripts", "ticker"))
        with open(os.path.join(self.box.repo, "scripts", "ticker", "main.py"), "w") as f:
            f.write(TICKER)

    def log(self, name):
        with open(os.path.join(self.box.rvfg, "run", name + ".log")) as f:
            return f.read()

    def test_live_value_reaches_the_kit_and_stop_kills_the_instance_tag(self):
        r = self.box.vfg("script", "run", "ticker", "as=tick-a", "length=5", "cooldown=4")
        self.assertOk(r)
        pid = int(re.search(r"pid (\d+)", r.stdout).group(1))
        self.assertTrue(wait_for(lambda: re.search(r"tick-a cooldown 4(\.0)? length 5", self.log("tick-a")), 10),
                        self.log("tick-a")[-500:])
        r = self.box.vfg("script", "set", "tick-a", "cooldown=0")
        self.assertOk(r)
        self.assertTrue(wait_for(lambda: re.search(r"tick-a cooldown 0(\.0)? length 5", self.log("tick-a")), 3),
                        self.log("tick-a")[-500:])
        self.assertTrue(alive(pid))
        r = self.box.vfg("script", "stop", "tick-a")
        self.assertEqual(r.stdout, "stopped tick-a\n")
        self.assertTrue(wait_for(lambda: not alive(pid), 3))
        self.assertIn("kill @e[tag=vfg_tick-a]", self.box.rcon_commands())


# ======================================================= the real library

LIVE_LIBRARY = os.path.isdir(os.path.join(REPO, "scripts", "_presets"))


@unittest.skipUnless(LIVE_LIBRARY, "no scripts/_presets/ in this repo yet")
class TestRealLibrary(unittest.TestCase):
    """The repo's own examples and presets, read in place, never written."""

    @classmethod
    def setUpClass(cls):
        cls.L = load_library_module()
        cls.lib = cls.L.Library(REPO, "test-never-made")

    def test_every_library_script_and_preset_passes_check(self):
        problems = []
        for it in self.lib.scripts() + self.lib.presets():
            problems.extend(self.L.check(it, self.lib))
        self.assertEqual(problems, [])

    def test_new_from_any_example_with_any_preset_passes_check(self):
        L, lib = self.L, self.lib
        examples = [s for s in lib.scripts() if not s.legacy and not L.check(s, lib)]
        presets = [p for p in lib.presets() if not L.check(p, lib)]
        failed = []
        for s in examples:
            for p in presets:
                if p.name in s.holds:
                    continue
                try:
                    text, _keys, _imps = L.add_preset(s.text, p, "main")
                except ValueError as e:
                    if "is in both" in str(e):
                        continue  # the example already has those values under another marker
                    failed.append("%s + %s: %s" % (s.name, p.name, e))
                    continue
                path = os.path.join(tempfile.mkdtemp(prefix="vfg-new-"), s.name + "_plus", "main.py")
                os.makedirs(os.path.dirname(path))
                with open(path, "w") as f:
                    f.write(text)
                got = L.check(L.parse(path, "script", s.name + "_plus", "made", path), lib)
                shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)
                if got:
                    failed.append("%s + %s: %s" % (s.name, p.name, got[0]))
        self.assertEqual(failed, [])

    def test_every_preset_into_one_script(self):
        L, lib = self.L, self.lib
        text = L.SKELETON.replace("TODO <Title>: say what it does in one line.", "All: every preset.") \
            .replace("ASKS = []", 'ASKS = ["everything"]')
        for p in lib.presets():
            if not L.check(p, lib):
                text, _k, _i = L.add_preset(text, p, "main")
        path = os.path.join(tempfile.mkdtemp(prefix="vfg-all-"), "all", "main.py")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(text)
        try:
            self.assertEqual(L.check(L.parse(path, "script", "all", "made", path), lib), [])
        finally:
            shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)


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
