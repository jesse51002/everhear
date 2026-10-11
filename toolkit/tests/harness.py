"""What the toolkit's tests stand on: a fake box in a temp folder, vfg called in this process, and
a fake Minecraft speaking RCON on localhost. No EC2, no network.

    python3 -m unittest discover -s toolkit/tests

vfg needs pydantic for its actions, so where this Python has none the run starts itself again
under uv, as vfg does (the first test module to import this one does it).

A Box is the PC and the EC2 box at once: HOME, VFG_HOME and a repo, and the "box" that
VFG_REMOTE_VFG and VFG_REMOTE_MC point into, with vfg.db (its tasks table too, but with
tasks=False: a box whose bridge has not added it yet), feed.log and the feed's writer
(fake_vfg_feed.py). box.vfg(...) runs vfg's main() here with the box's environment, and every
SSH call it makes runs its remote program here too, as the box's Python would: BoxSsh below
replaces Ssh's one subprocess call and nothing else, so the master check, the retry and the
unreachable message are vfg's own. What vfg starts itself (vfg watch's follow, the router's Jev
call) runs the program with this Python. A test class shares one FakeRcon (fake_rcon.py); a
copy an action starts is a real process on the real kit and speaks RCON to it. box.process(...)
runs vfg as a process through fake_ssh, for the few end-to-end tests.
"""
import ast
import collections
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import runpy
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import warnings
from datetime import datetime, timedelta, timezone


def _under_uv():
    """Start the run again under uv with pydantic and httpx, vfg's own dependencies, when this
    Python lacks either (the usage tests import lib/py/vfg_llm.py, which needs both)."""
    if os.environ.get("VFG_TESTS_UNDER_UV") or all(importlib.util.find_spec(m) is not None
                                                   for m in ("pydantic", "httpx")):
        return
    argv = list(sys.argv)
    rest = [argv[0]] + argv[1:] if argv and argv[0].endswith(".py") else ["-m", "unittest"] + argv[1:]
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execvpe("uv", ["uv", "run", "--quiet", "--no-project", "--python", "3.12", "--with", "pydantic>=2.14",
                          "--with", "httpx>=0.28.1", "python"] + rest, dict(os.environ, VFG_TESTS_UNDER_UV="1"))
    except OSError:
        pass   # no uv: what loads the actions fails, saying pydantic is missing


_under_uv()
sys.dont_write_bytecode = True

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(TESTS)
REPO = os.path.dirname(TOOLKIT)
VFG = os.path.join(TOOLKIT, "vfg")
PY = sys.executable
REAL_LIB = os.path.join(REPO, "scripts", "_lib")
SCRIPTS_TEST = os.path.join(REPO, "scripts", "_test")
FEED_RE = re.compile(r"^([A-Z]+)#\d+ \d\d:\d\d:\d\d ")
ISO_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)
from fake_rcon import FakeRcon  # noqa: E402

SCHEMA = """
CREATE TABLE lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('action_oriented','conversational','context','not_relevant')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL,
  processed_at TEXT);
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer',
                                               'brain','respond','planner','alias')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""
# The brain's tasks, as the bridge adds the table (bridge/vfg_bridge/db.py's ADDED).
TASKS = """
CREATE TABLE tasks (name TEXT PRIMARY KEY, description TEXT NOT NULL, player TEXT,
  status TEXT NOT NULL CHECK (status IN ('open','done','failed')), line TEXT, brief TEXT,
  made TEXT, variables TEXT, session TEXT, result TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, closed_at TEXT);
"""

Result = collections.namedtuple("Result", "returncode stdout stderr")


class Captured(io.BytesIO):
    """What a stream wrote; the TextIOWrapper over it may close it, its bytes stay readable."""

    def close(self):
        pass
DOWN = b"ssh: connect to host fakebox port 22: Connection timed out\n"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except OSError:
        return False


def wait_for(pred, timeout=5, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def program_args(data):
    """The ARGS a remote program was sent with."""
    m = re.search(r"^ARGS = json\.loads\((.*)\)$", data.decode("utf-8"), re.M)
    return json.loads(ast.literal_eval(m.group(1))) if m else None


class FastPoll(object):
    """`time` for scripts/_lib/instances.py on the box here: Box.wait, which waits on a copy to the
    end of a deadline, looks for it every 5 ms, not every 50 (a test's copy ends in about 30);
    every other wait, and every clock, as they are."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(seconds):
        time.sleep(min(seconds, 0.005) if sys._getframe(1).f_code.co_name == "wait" else seconds)


FAST_POLL = FastPoll()
_COMPILED = {}


def compiled(text):
    """compile(text) once per text."""
    code = _COMPILED.get(text)
    if code is None:
        code = _COMPILED[text] = compile(text, "<stdin>", "exec")
    return code


def compile_once(source, filename, mode, *args, **kw):
    """compile(), once per source, file name and mode."""
    if args or kw:
        return compile(source, filename, mode, *args, **kw)
    key = (source, filename, mode)
    code = _COMPILED.get(key)
    if code is None:
        code = _COMPILED[key] = compile(source, filename, mode)
    return code


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ vfg in this process

_VFG = []


def vfg_module():
    """toolkit/vfg loaded once as a module, its SSH call the box's (BoxSsh), its waits short."""
    if not _VFG:
        loader = importlib.machinery.SourceFileLoader("vfg_under_test", VFG)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        mod.RealSsh = mod.Ssh
        mod.Ssh = box_ssh(mod)
        mod.CHECK_S = 0.2          # an until-stopped copy's check, 2 s in use
        mod.START_CHECK_S = 0.1    # vfg brain start's wait for an exit at once, 3 s in use
        mod.STOP_GRACE_S = 1.0     # vfg brain stop's SIGTERM before SIGKILL, 15 s in use
        parser = mod.build_parser()  # built once: a process builds it once too
        mod.build_parser = lambda: parser
        _VFG.append(mod)
    return _VFG[0]


def box_ssh(mod):
    class BoxSsh(mod.Ssh):
        """vfg's Ssh with the box here: _run, its one subprocess call, runs the remote program
        in this process (Box.run_program) or vfg feed post's command (Box.run_feed)."""

        def ensure_master(self, deadline):
            return True

        def exit_master(self):
            pass

        def argv(self, remote_cmd=None, direct=False):
            # What vfg starts itself (vfg watch, the router's Jev call) runs the program here.
            if remote_cmd is None or remote_cmd == self.remote_cmd:
                if Box.current is not None and Box.current.down:
                    return [sys.executable, "-c", "import sys; sys.stderr.write(%r); sys.exit(255)" % DOWN.decode()]
                return [sys.executable, "-u", "-"]
            return ["bash", "-c", remote_cmd]

        def _run(self, argv, data, timeout, deadline, stream, keep_master):
            box = Box.current
            feed = argv[0] == "bash"
            box.calls.append(dict({"cmd": argv[-1]} if feed else {"args": program_args(data)}, dest=self.dest))
            if box.down:
                return 255, b"", DOWN
            if box.slow:   # as _run says it after the timeout, without the wait
                raise mod.TimedOut(4, "EC2 did not answer within %s s" % mod.fmt_s(timeout))
            return box.run_feed(argv[-1]) if feed else box.run_program(data)
    return BoxSsh


class Box(object):
    """A temp world: HOME, VFG_HOME, the repo, the EC2 box, the units; RCON from `rcon`."""
    current = None

    def __init__(self, rcon, feed_lines=3, processed_at=True, tasks=True):
        self.root = tempfile.mkdtemp(prefix="vfg-test-")
        j = lambda *p: os.path.join(self.root, *p)  # noqa: E731
        self.home, self.vhome, self.repo = j("home"), j("home", ".vfg"), j("repo")
        self.rvfg, self.rmc, self.units, self.bin = j("box", "vfg"), j("box", "mc"), j("units"), j("bin")
        for d in (self.vhome, self.repo, self.rvfg, self.rmc, self.units, self.bin, os.path.join(self.rvfg, "lib")):
            os.makedirs(d, exist_ok=True)
        os.chmod(self.vhome, 0o700)
        os.makedirs(os.path.join(self.rvfg, "python", "bin"))
        os.symlink(PY, os.path.join(self.rvfg, "python", "bin", "python3"))   # the box's 3.12
        os.symlink(PY, os.path.join(self.bin, "python3"))
        self.key = write(j("key"), "not a key\n")
        self.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\n" % self.key)
        self.server = rcon
        self.port = rcon.port
        self.password = rcon.password
        self.write_props()
        self.db = os.path.join(self.rvfg, "vfg.db")
        self.feed = os.path.join(self.rvfg, "feed.log")
        self.make_db(feed_lines, processed_at, tasks)
        with open(self.feed, "w") as f:
            f.write("".join(line + "\n" for _seq, line in self.feed_rows))
        shutil.copy(os.path.join(TESTS, "fake_vfg_feed.py"), os.path.join(self.rvfg, "lib", "vfg_feed.py"))
        for u in ("minecraft", "vfg-bridge", "vfg-jev"):
            write(os.path.join(self.units, u), "active\n")
        self.calls = []         # every SSH call vfg made: {"args": the program's ARGS} or {"cmd": ...}
        self.down = False       # SSH answers 255: EC2 unreachable
        self.slow = False       # SSH out of time
        self.procs = []
        # Nothing of this PC's: no VFG_ setting, and no OpenRouter key, so no test reaches a model.
        self.base_env = {k: v for k, v in os.environ.items()
                         if not k.startswith(("VFG_", "FAKE_", "CLAUDE")) and k != "OPEN_ROUTER_API"}

    # -- setup
    def write_config(self, text):
        """~/.vfg/config: `text`, and no alias model for an incantation unless it names one. In
        the config, not the environment, as a thread of vfg reads it while a remote program here
        has the box's environment."""
        if "VFG_INCANT_ALIAS_MODEL=" not in text:
            text += "VFG_INCANT_ALIAS_MODEL=off\n"
        write(os.path.join(self.vhome, "config"), "# test config\n" + text)

    def write_props(self, password=None, enable=True, port=None):
        write(os.path.join(self.rmc, "server.properties"),
              "#Minecraft server properties\nenable-rcon=%s\nrcon.port=%d\nrcon.password=%s\nserver-port=25565\n"
              % ("true" if enable else "false", port or self.port, password or self.password))

    def rcon_down(self):
        """Minecraft not listening: the properties name a port nothing answers on."""
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.write_props(port=port)
        return port

    def make_db(self, feed_lines, processed_at, tasks=True):
        now = datetime.now(timezone.utc)
        con = sqlite3.connect(self.db)
        schema = SCHEMA if processed_at else SCHEMA.replace(",\n  processed_at TEXT)", ")")
        con.executescript(schema + (TASKS if tasks else ""))
        con.execute("PRAGMA journal_mode=WAL")
        lines = [
            ("L1", now - timedelta(minutes=5), "Steve", "voice", "build me a castle", "action_oriented", 0.93),
            ("L2", now - timedelta(minutes=4), "Alex", "chat", "that cave by spawn", "context", 0.71),
            ("L3", now - timedelta(minutes=3), "Alex", "chat", "lol", None, None),
            ("L4", now - timedelta(minutes=2), "AI", "ai", "on it — castle at spawn", None, None),
            ("L5", now - timedelta(minutes=20), "Steve", "voice", "hmm", "not_relevant", 0.4),
            ("L6", now - timedelta(hours=1), "Bob", "voice", "thanks AI", "conversational", 0.88),
        ]
        for lid, at, player, source, text, cls, conf in lines:
            con.execute("INSERT INTO lines (id, at, player_name, source, text, jev_class, jev_confidence)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)", (lid, iso(at), player, source, text, cls, conf))
        events = [("E1", now - timedelta(minutes=5), "join", "Steve", "Steve joined the game"),
                  ("E2", now - timedelta(minutes=2), "death", "Steve", "Steve was slain by Zombie"),
                  ("E3", now - timedelta(minutes=30), "server", None, "Server started")]
        for row in events:
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (row[0], iso(row[1])) + row[2:])
        self.feed_rows = []
        for i in range(1, feed_lines + 1):
            at = now - timedelta(minutes=10 - i)
            line = "SYS#%d %s | seeded line %d" % (i, at.strftime("%H:%M:%S"), i)
            con.execute("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", (i, iso(at), line))
            self.feed_rows.append((i, line))
        con.commit()
        con.close()

    def write(self, rel, text):
        """A file in the repo."""
        return write(os.path.join(self.repo, rel), text)

    def predeploy(self):
        """The repo's scripts/_lib and scripts on the box, stamped, as vfg's first deploy leaves
        them: a run then sends only the stamps, as to a box deployed once already."""
        mod = vfg_module()
        src, dest = os.path.join(self.repo, "scripts"), os.path.join(self.rvfg, "scripts")
        for name in sorted(os.listdir(src)):
            if os.path.isdir(os.path.join(src, name)) and (name == "_lib" or not name.startswith(("_", "."))):
                shutil.copytree(os.path.join(src, name), os.path.join(dest, name),
                                ignore=shutil.ignore_patterns("__pycache__", ".*", "*.pyc"))
                write(os.path.join(dest, name, ".vfg-stamp"), mod.stamp(list(mod.tree(src, name))) + "\n")

    # -- the PC
    def env(self, **extra):
        env = dict(self.base_env)
        env.update(HOME=self.home, VFG_HOME=self.vhome, VFG_REPO=self.repo, VFG_REMOTE_VFG=self.rvfg,
                   VFG_REMOTE_MC=self.rmc, VFG_SSH=os.path.join(TESTS, "fake_ssh"), FAKE_UNITS=self.units,
                   PATH=os.pathsep.join([self.bin, os.path.join(TESTS, "bin"), self.base_env.get("PATH", "")]),
                   PYTHONDONTWRITEBYTECODE="1")
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    @contextlib.contextmanager
    def environ(self, **env):
        """This box's environment as os.environ, for calling vfg's functions directly."""
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env(**env))
        Box.current = self
        quiet = warnings.catch_warnings()
        quiet.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)
        try:
            yield vfg_module()
        finally:
            quiet.__exit__(None, None, None)
            os.environ.clear()
            os.environ.update(saved)

    def vfg(self, *args, input=None, **env):
        """vfg's main() in this process, with this box's environment: Result(code, stdout, stderr)."""
        began = time.time()
        mod = vfg_module()
        repo = self.env(**env)["VFG_REPO"]
        for key, cat in list(mod._CATALOG.items()):
            if key[0] == repo:
                cat.refresh()     # as a new process would read them: what changed since, again
            else:
                del mod._CATALOG[key]
        Box.current = self
        out, err = Captured(), Captured()
        saved = (sys.stdin, sys.stdout, sys.stderr, dict(os.environ))
        sys.stdin = io.TextIOWrapper(io.BytesIO((input or "").encode("utf-8")), encoding="utf-8")
        sys.stdout = io.TextIOWrapper(out, encoding="utf-8", write_through=True)
        sys.stderr = io.TextIOWrapper(err, encoding="utf-8", write_through=True)
        os.environ.clear()
        os.environ.update(self.env(**env))
        try:
            with warnings.catch_warnings():
                # What a process leaves open is closed when it exits; here, it is not worth a warning.
                warnings.simplefilter("ignore", ResourceWarning)
                code = mod.main(list(args))
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdin, sys.stdout, sys.stderr = saved[:3]
            os.environ.clear()
            os.environ.update(saved[3])
        if os.environ.get("VFG_TESTS_TIMES"):
            sys.stderr.write("  %.3f s  vfg %s\n" % (time.time() - began, " ".join(args)[:100]))
        return Result(code, out.getvalue().decode("utf-8"), err.getvalue().decode("utf-8"))

    def process(self, *args, input=None, timeout=60, **env):
        """vfg as a process, through fake_ssh: for the end-to-end tests."""
        return subprocess.run([sys.executable, VFG] + list(args), input=input, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self.env(**env), timeout=timeout,
                              universal_newlines=True)

    def popen(self, *args, **env):
        p = subprocess.Popen([sys.executable, VFG] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.env(**env))
        self.procs.append(p)
        return p

    # -- the box
    def box_env(self):
        """The environment a program gets over SSH: the login's, none of the PC's."""
        env = {k: self.base_env[k] for k in ("USER", "LOGNAME") if k in self.base_env}
        env.update(HOME=self.home, LANG="C.UTF-8", FAKE_UNITS=self.units,
                   PATH=os.pathsep.join([self.bin, os.path.join(TESTS, "bin"), self.base_env.get("PATH", "")]))
        # A test's copies set nothing off that needs a tick to go: one that ends, ends at once,
        # not mc.py's 0.4 s later.
        env["VFG_EXIT_GRACE_S"] = "0"
        return env

    def run_here(self, run, env, argv=None):
        """run(ns) as a program on the box, ns its globals: (exit code, stdout, stderr), with `env`
        for its environment; its globals cleared after (so what it left open closes now, quietly),
        what it imported from the box forgotten, and the cwd kept."""
        out, err = Captured(), Captured()
        before = set(sys.modules)
        saved = (sys.stdout, sys.stderr, dict(os.environ), list(sys.path), list(sys.argv), os.getcwd())
        sys.stdout = io.TextIOWrapper(out, encoding="utf-8", write_through=True)
        sys.stderr = io.TextIOWrapper(err, encoding="utf-8", write_through=True)
        os.environ.clear()
        os.environ.update(env)
        if argv is not None:
            sys.argv = list(argv)
        code = 0
        ns = {"__name__": "__main__"}
        quiet = warnings.catch_warnings()
        quiet.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)
        try:
            run(ns)
        except SystemExit as e:
            if isinstance(e.code, str):
                sys.stderr.write(e.code + "\n")
                code = 1
            else:
                code = e.code or 0
        except BaseException:   # as python3 - would: the traceback, exit 1
            traceback.print_exc(file=sys.stderr)
            code = 1
        finally:
            ns.clear()
            quiet.__exit__(None, None, None)
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = saved[0], saved[1]
            os.environ.clear()
            os.environ.update(saved[2])
            sys.path[:] = saved[3]
            sys.argv = saved[4]
            os.chdir(saved[5])
            for name in set(sys.modules) - before:
                f = getattr(sys.modules[name], "__file__", None)
                if f and (not os.path.isabs(f) or f.startswith(self.root)):
                    del sys.modules[name]
        return code, out.getvalue(), err.getvalue()

    def run_program(self, data):
        """A program vfg sends, run as /opt/vfg/python/bin/python3 -u - runs it: common.py, ARGS,
        then the libraries and the program. What comes before ARGS and what after are the same
        from one call to the next, so each is compiled once (compiled())."""
        text = data.decode("utf-8")
        m = re.search(r"^ARGS = json\.loads\((.*)\)$", text, re.M)
        if m is None:
            return self.run_here(lambda ns: exec(compiled(text), ns), self.box_env())
        args = json.loads(ast.literal_eval(m.group(1)))
        head, tail = text[:m.start()], text[m.end():]
        libs = list(re.finditer(r"^\w+ = _library\(.*\)$", tail, re.M))
        cut = libs[-1].end() if libs else 0     # the libraries the program ships, then the program

        def run(ns):
            ns["compile"] = compile_once     # what _library compiles of each library: once too
            exec(compiled(head), ns)
            ns["ARGS"] = args
            exec(compiled(tail[:cut]), ns)
            if "instances" in ns:
                ns["instances"].time = FAST_POLL
            exec(compiled(tail[cut:]), ns)
        return self.run_here(run, self.box_env())

    def run_feed(self, cmd):
        """vfg feed post's command: `env VFG_DB=… VFG_FEED=… <python> <vfg_feed.py> post …`."""
        words = shlex.split(cmd)
        assert words[0] == "env", cmd
        env = self.box_env()
        i = 1
        while "=" in words[i]:
            k, v = words[i].split("=", 1)
            env[k] = v
            i += 1
        script = words[i + 1]
        return self.run_here(lambda ns: runpy.run_path(script, run_name="__main__"), env, [script] + words[i + 2:])

    def add_task(self, name, description="a test's task", status="open", minutes_ago=10, **more):
        """A row of the tasks table, written straight on the box as `vfg task set` would leave it:
        made and variables (in `more`) as JSON text, created and updated `minutes_ago`. The row."""
        at = iso(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
        row = dict({"name": name, "description": description, "status": status, "created_at": at,
                    "updated_at": at, "closed_at": None if status == "open" else at}, **more)
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO tasks (%s) VALUES (%s)" % (", ".join(row), ", ".join("?" * len(row))),
                        [json.dumps(v) if k in ("made", "variables") and v is not None else v
                         for k, v in row.items()])
            con.commit()
        finally:
            con.close()
        return row

    def tasks(self):
        """Every row of the tasks table, oldest created first, as dicts, made and variables decoded."""
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute("SELECT * FROM tasks ORDER BY created_at, name")]
        finally:
            con.close()
        for r in rows:
            for k in ("made", "variables"):
                r[k] = json.loads(r[k]) if r[k] is not None else None
        return rows

    def post(self, typ, fields, text):
        """A feed line written straight on the box, as the bridge would: its seq."""
        code, out, err = self.run_feed(shlex.join(["env", "VFG_DB=" + self.db, "VFG_FEED=" + self.feed, PY,
                                                   os.path.join(self.rvfg, "lib", "vfg_feed.py"), "post", typ,
                                                   fields, text]))
        assert code == 0, err
        return int(out)

    def programs(self, op=None):
        """The remote programs vfg ran, as their ARGS; with op, those with that op."""
        return [c["args"] for c in self.calls if "args" in c and (op is None or c["args"].get("op") == op)]

    def rcon_commands(self):
        return list(self.server.commands)

    def query(self, sql, *params):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for s in (p.stdout, p.stderr):
                if s:
                    s.close()
        run = os.path.join(self.rvfg, "run")
        pidfiles = [os.path.join(self.vhome, "brain", "brain.pid")]
        if os.path.isdir(run):
            pidfiles += [os.path.join(run, n) for n in os.listdir(run) if n.endswith(".pid")]
        for path in pidfiles:
            try:
                with open(path) as f:
                    os.killpg(int(f.read().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        if self.repo in sys.path:
            sys.path.remove(self.repo)
        shutil.rmtree(self.root, ignore_errors=True)


class BoxCase(unittest.TestCase):
    """A Box per test, one FakeRcon per class."""
    box_kwargs = {}
    shared_rcon = False

    @classmethod
    def setUpClass(cls):
        cls.rcon = FakeRcon(secrets.token_hex(8), shared=cls.shared_rcon).start()

    @classmethod
    def tearDownClass(cls):
        cls.rcon.stop()
        try:
            os.remove("/tmp/vfg-rcon-%d.lock" % cls.rcon.port)   # the box's RCON lock
        except OSError:
            pass

    def setUp(self):
        self.rcon.reset()
        self.box = Box(self.rcon, **self.box_kwargs)
        self.addCleanup(self.box.close)

    def vfg(self, *args, **kw):
        return self.box.vfg(*args, **kw)

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
