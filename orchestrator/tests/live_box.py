#!/usr/bin/env python3
"""The orchestrator live, against a local stand-in box, with the REAL vfg.

    python3 tests/live_box.py --preflight         no model: the box and every vfg shape an
                                                  implementer uses, plus the guard on each
    python3 tests/live_box.py                     the live run: your subscription, once
    python3 tests/live_box.py --quick             the real defaults (every agent on Haiku 5.5): two tasks, timed

The box is integration/harness.py's Box (imported, read-only): the real bridge, Jev filter and
feed writer on the box's Python 3.12, the toolkit's fake_ssh, and a temp VFG_HOME whose config points into
it. In place of the toolkit's bare fake RCON this runs tests/realistic_rcon.py on the box's RCON
port, so an implementer can confirm what it did. `vfg orch start|stop` starts and stops the
dispatcher alongside the runner; tasks go in with `vfg orch send`, as the conversation sends them,
so the dispatcher does the quick ones itself and hands the rest on (two tasks meant to arrive
together still go straight in with `vfg orch hand`); and every answer is checked in the feed
(`vfg history --feed`): exactly one `ORCH … done|failed` per task.

The dispatcher's model key: OPEN_ROUTER_API from the environment, else the first .env above the
repo that holds it, else the file ~/.vfg/openrouter.key names; it reaches `vfg orch start` only.
Its command references are seeded from dispatcher/tests/fixtures/refs, as
integration/dispatcher_e2e.py seeds them, so nothing is fetched.
"""
import argparse
import fcntl
import glob
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.dirname(HERE)
VFG_ROOT = os.path.dirname(ORCH)
HARNESS = os.path.join(VFG_ROOT, "integration", "harness.py")
E2E = os.path.join(VFG_ROOT, "integration", "dispatcher_e2e.py")
DISPATCHER = os.path.join(VFG_ROOT, "dispatcher")
ACTIONS = os.path.join(VFG_ROOT, "actions")
TOOLKIT = os.path.join(VFG_ROOT, "toolkit")
SCRIPTS = os.path.join(VFG_ROOT, "scripts")
REAL_VFG = os.path.expanduser("~/.local/bin/vfg")
REAL_HOME = os.path.expanduser("~")
UV = os.path.expanduser("~/.local/bin/uv")
INTERRUPTED = "The orchestrator restarted while this was running; anything it started may be half done."
ORCH_RE = re.compile(r"ORCH#(\d+) (\d\d:\d\d:\d\d) (T\d+) (progress|done|failed) \| (.*)$")
REFUSAL = re.compile(r"permission|denied|not allowed|Refused|Only files in the repository|"
                     r"Only the repository|owner's|Never edit|Leave model|Write the server command", re.I)

T0 = time.time()
RESULTS = []


def say(*parts):
    print("[%4.0f s]" % (time.time() - T0), *parts, flush=True)


def record(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    say("PASS" if ok else "FAIL", name, "-", detail)


def load_harness(path=HARNESS, name="vfg_integration_harness"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Live:
    def __init__(self, H, model):
        ec2 = subprocess.run([UV, "python", "find", "3.12"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
        self.root = tempfile.mkdtemp(prefix="vfg-orch-live-")
        self.box = H.Box(self.root, ec2)
        self.model = model
        b = self.box
        b.deploy()
        b.fake_rcon = subprocess.Popen([sys.executable, os.path.join(HERE, "realistic_rcon.py"), b.props, b.rcon_log],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=b.base_env(),
                                       start_new_session=True)
        assert b.fake_rcon.stdout.readline().strip() == b"ready", "realistic RCON did not start"
        b.start_fake_jev()
        b.start_bridge()
        b.start_jev()
        shutil.copytree(SCRIPTS, os.path.join(b.repo, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        # vfg orch start runs <repo>/orchestrator/run and <repo>/dispatcher/run; vfg action run reads <repo>/actions.
        os.symlink(ORCH, os.path.join(b.repo, "orchestrator"))
        os.symlink(DISPATCHER, os.path.join(b.repo, "dispatcher"))
        os.symlink(ACTIONS, os.path.join(b.repo, "actions"))
        self.orch_dir = os.path.join(b.vhome, "orch")
        self.dispatch_dir = os.path.join(b.vhome, "dispatch")
        self.runner_log = os.path.join(self.orch_dir, "runner.log")
        self.dispatcher_log = os.path.join(self.dispatch_dir, "dispatcher.log")
        e2e = load_harness(E2E, "vfg_dispatcher_e2e")
        e2e.seed_refs(self.dispatch_dir)                 # the command references, never fetched here
        e2e.server_jar(b.mc_dir)                         # what vfg action refs --version reads
        lib = os.path.join(b.vfg_dir, "scripts", "_lib")  # mc.py for the action handlers
        os.makedirs(lib, exist_ok=True)
        shutil.copy(os.path.join(SCRIPTS, "_lib", "mc.py"), lib)
        key, _ = e2e.find_key()
        self.key_env = {"OPEN_ROUTER_API": key} if key else \
            {"VFG_OPENROUTER_KEY": os.environ.get("VFG_OPENROUTER_KEY") or os.path.join(REAL_HOME, ".vfg", "openrouter.key")}
        self.seeds = set(os.listdir(os.path.join(b.repo, "scripts")))
        self.env = b.base_env()
        if model:
            self.env["VFG_ORCH_MODEL"] = model  # unset: the runner's own default (claude-haiku-5-5)
        for k in ("VFG_ORCH_EFFORT", "VFG_IMPL_MODEL", "VFG_IMPL_EFFORT"):
            if os.environ.get(k):
                self.env[k] = os.environ[k]
        self.env.pop("OPEN_ROUTER_API", None)  # the dispatcher's only: see start()
        self.env.update({
            "HOME": REAL_HOME,  # the CLI's own Claude login; vfg finds its config by VFG_HOME
            "VFG_HOME": b.vhome, "VFG_REPO": b.repo, "VFG_ORCH_DIR": self.orch_dir,
            "VFG_DISPATCH_DIR": self.dispatch_dir,
            "FAKE_UNITS": b.units,
            # the box's python3 (3.12) first: fake_ssh runs the remote side with this PATH
            "PATH": os.pathsep.join([b.ec2bin, os.path.dirname(REAL_VFG), os.path.join(TOOLKIT, "tests", "bin"),
                                     os.environ.get("PATH", "")]),
        })
        say("box %s; RCON port %d; vfg = %s" % (self.root, b.rcon_port, os.path.realpath(REAL_VFG)))

    def vfg(self, *args, timeout=120, env=None):
        return subprocess.run([REAL_VFG] + list(args), env=dict(self.env, **(env or {})), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)

    def rcon(self, since=0.0):
        out = []
        try:
            with open(self.box.rcon_log) as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o["at"] >= since:
                        out.append(o["cmd"])
        except FileNotFoundError:
            pass
        return out

    def feed_orch(self):
        r = self.vfg("history", "--feed", "--limit", "1000")
        rows = []
        for line in r.stdout.splitlines():
            m = ORCH_RE.search(line)
            if m:
                rows.append((int(m.group(1)), m.group(3), m.group(4), m.group(5)))
        return sorted(set(rows))

    def closes(self, tid):
        return [(st, text) for _, t, st, text in self.feed_orch() if t == tid and st in ("done", "failed")]

    def log(self, path=None):
        try:
            with open(path or self.runner_log, encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def dlog(self):
        return self.log(self.dispatcher_log)

    def wait(self, cond, secs, step=1.0):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(step)
        return cond()

    def runner_pid(self):
        try:
            return int(open(os.path.join(self.orch_dir, "runner.pid")).read().strip())
        except (OSError, ValueError):
            return None

    def start(self):
        """`vfg orch start`: the runner and the dispatcher, the dispatcher's key in this call's environment only."""
        mark, dmark = len(self.log()), len(self.dlog())
        r = self.vfg("orch", "start", env=self.key_env)
        ok = r.returncode == 0 and self.wait(lambda: " main following " in self.log()[mark:], 120, 0.25)
        dok = r.returncode == 0 and (re.search(r"^dispatcher +running pid", r.stdout, re.M)  # still up from before
                                     or self.wait(lambda: re.search(r" start pid \d+", self.dlog()[dmark:]), 120, 0.25))
        say("vfg orch start: %s %s; runner %s; dispatcher %s" % (r.stdout.strip(), r.stderr.strip(),
            "following" if ok else "NOT up", "following" if dok else "NOT up"))
        return ok and dok

    def last_id(self):
        """The highest task number in either inbox, as vfg orch send counts them."""
        n = 0
        for folder in (self.orch_dir, self.dispatch_dir):
            try:
                with open(os.path.join(folder, "inbox.jsonl")) as f:
                    for line in f:
                        m = re.match(r'\{"id": ?"T(\d+)"', line)
                        if m:
                            n = max(n, int(m.group(1)))
            except FileNotFoundError:
                pass
        return n

    def hand(self, *tasks):
        """(task, context) pairs into the orchestrator's inbox in one `vfg orch hand`, as the dispatcher
        hands tasks on. Returns their ids, None for one that was not handed."""
        n = self.last_id()
        at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        lines = [{"id": "T%d" % (n + i + 1), "at": at, "task": task, "context": context} for i, (task, context) in enumerate(tasks)]
        r = self.vfg("orch", "hand", json.dumps(lines))
        ids = [x["id"] if r.returncode == 0 and ("handed %s" % x["id"]) in r.stdout.split("\n") else None for x in lines]
        say("vfg orch hand %s -> %s %s" % ([t for t, _ in tasks], ids, (r.stdout + " " + r.stderr).strip().replace("\n", " / ")))
        return ids

    def send(self, task, context=""):
        r = self.vfg("orch", "send", task, "--context", context)
        tid = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None
        say("vfg orch send %r -> %s %s" % (task, tid, r.stderr.strip()))
        return tid

    def who(self, tid):
        """Which part did it: the dispatcher, or the orchestrator (it was handed on)."""
        try:
            with open(os.path.join(self.orch_dir, "inbox.jsonl")) as f:
                return "orchestrator" if any(json.loads(l).get("id") == tid for l in f if l.strip()) else "dispatcher"
        except (OSError, ValueError):
            return "dispatcher"

    def send_stale(self, task):
        """An hour-old task, appended as vfg orch hand would (vfg cannot backdate one)."""
        inbox = os.path.join(self.orch_dir, "inbox.jsonl")
        with open(os.path.join(self.orch_dir, "inbox.lock"), "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            tid = "T%d" % (self.last_id() + 1)
            at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
            with open(inbox, "a") as f:
                f.write(json.dumps({"id": tid, "at": at, "task": task, "context": ""}) + "\n")
        say("appended stale %s: %s" % (tid, task))
        return tid

    def box_scripts_running(self):
        r = self.vfg("script", "list")
        return r.stdout

    def stop_box_scripts(self):
        out = self.box_scripts_running()
        for line in out.splitlines():
            w = line.split()
            if w and "running" in line:
                self.vfg("script", "stop", w[0])
        # anything left that runs from the box
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            if self.root in cmd and int(pid) != os.getpid():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass

    def refusals(self):
        """Every tool call refused in this run's transcripts (orchestrator and implementers)."""
        mangled = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(self.box.repo))
        out = []
        for f in sorted(glob.glob(os.path.join(REAL_HOME, ".claude", "projects", mangled, "**", "*.jsonl"), recursive=True)):
            uses = {}
            for line in open(f, encoding="utf-8", errors="replace"):
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                c = (o.get("message") or {}).get("content")
                if not isinstance(c, list):
                    continue
                for b in c:
                    if b.get("type") == "tool_use":
                        i = b.get("input") or {}
                        uses[b["id"]] = (b["name"], i.get("command") or i.get("file_path") or i.get("path") or i.get("description") or json.dumps(i)[:160])
                    elif b.get("type") == "tool_result" and b.get("is_error"):
                        t = b.get("content")
                        t = t if isinstance(t, str) else json.dumps(t)
                        if REFUSAL.search(t):
                            who = "implementer" if "/subagents/" in f else "orchestrator"
                            out.append((who, uses.get(b.get("tool_use_id"), ("?", "?")), t[:140]))
        return out

    def close(self):
        try:
            if self.runner_pid() or os.path.exists(os.path.join(self.dispatch_dir, "dispatcher.pid")):
                self.vfg("orch", "stop", timeout=60)
        except Exception:
            pass
        self.stop_box_scripts()
        self.box.close()


def readme_shape(path):
    try:
        lines = open(path, encoding="utf-8").read().split("\n")
    except OSError:
        return "no README.md"
    first = (lines[0] if lines else "").strip()
    if not first or first[-1] not in ".!?" or re.search(r"[.!?]\s+[A-Z]", first[:-1]) or first.startswith("#"):
        return "line 1 is not one sentence: %r" % first
    if not (lines[1] if len(lines) > 1 else "").startswith("Usage: "):
        return "line 2: %r" % (lines[1] if len(lines) > 1 else "")
    if not (lines[2] if len(lines) > 2 else "").startswith("Ends: "):
        return "line 3: %r" % (lines[2] if len(lines) > 2 else "")
    return None


# --------------------------------------------------------------------------- preflight

SHAPES = [
    ("vfg", "cmd", "time set day"),
    ("vfg", "cmd", "data get entity Steve Pos"),
    ("vfg", "cmd", "give Steve minecraft:diamond 1"),
    ("vfg", "script", "deploy"),
    ("vfg", "action", "run", "tnt_rain", "--task", "T1"),
    ("sleep", "3"),
    ("vfg", "script", "logs", "tnt_rain", "-n", "5"),
    ("vfg", "script", "list"),
    ("vfg", "script", "stop", "tnt_rain"),
    ("vfg", "action", "run", "gun_chests", "--task", "T1"),
    ("vfg", "script", "logs", "gun_chests", "-n", "5"),
    ("vfg", "history", "--source", "ai", "--limit", "3"),
    ("vfg", "history", "--feed", "--limit", "3"),
    ("python3", "-m", "py_compile", "scripts/tnt_rain/main.py"),
]


def preflight(H):
    live = Live(H, "none")
    try:
        import shlex
        lines = [" ".join(shlex.quote(w) for w in argv) for argv in SHAPES]
        guard = subprocess.run(
            [UV, "run", "--project", ORCH, "--frozen", "python", "-c",
             "import json, sys\nfrom orchestrator.guard import check_bash\n"
             "for l in json.loads(sys.argv[1]):\n    print(json.dumps([l, check_bash(l)]))", json.dumps(lines)],
            cwd=ORCH, stdout=subprocess.PIPE, universal_newlines=True)
        for row in guard.stdout.splitlines():
            line, why = json.loads(row)
            record("guard allows: %s" % line, why is None, why or "allowed")
        for argv in SHAPES:
            if argv[0] == "sleep":
                time.sleep(float(argv[1]))
                continue
            if argv[0] == "python3":
                r = subprocess.run(list(argv), cwd=live.box.repo, env=live.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            else:
                r = live.vfg(*argv[1:])
            out = (r.stdout.strip() + " " + r.stderr.strip()).strip().replace("\n", " / ")
            record("real %s" % " ".join(argv), r.returncode == 0, "exit %d: %s" % (r.returncode, out[:300]))
        r = live.vfg("orch", "reply", "T1", "done", "preflight: the reply path")
        rows = live.feed_orch()
        record("vfg orch reply -> one ORCH line in vfg history --feed", r.returncode == 0 and
               [x[1:] for x in rows] == [("T1", "done", "preflight: the reply path")], "%s %s" % (rows, r.stderr.strip()))
        r = live.vfg("usage", "add", "--component", "implementer", "--model", "claude-haiku", "--in", "10", "--out", "2",
                     "--cost", "0.000100", "--ref", "T1")
        rows = live.box.sql("SELECT component, model, input_tokens, ref FROM usage")
        record("vfg usage add -> a usage row in the box", r.returncode == 0 and rows, "%s %s" % (rows, r.stderr.strip()))
        cmds = live.rcon()
        record("scripts reached the realistic RCON", any("vfg_tnt_rain" in c for c in cmds) and any("GUN CHEST" in c for c in cmds),
               "%d commands; e.g. %s" % (len(cmds), [c for c in cmds if "tnt" in c][:1]))
    finally:
        live.close()
        shutil.rmtree(live.root, ignore_errors=True)


# --------------------------------------------------------------------------- the live run

def probe(live):
    env = dict(live.env)
    r = subprocess.run([UV, "run", "--project", ORCH, "--frozen", "python", os.path.join(HERE, "probe.py")], cwd=ORCH, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=400)
    for line in r.stderr.splitlines():
        if line.startswith("[") or "probe" in line:
            say("  " + line[:400])
    for line in r.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        record(o["name"], o["pass"], o["detail"])


def scenarios(live):
    if not live.start():
        record("runner starts with vfg orch start", False, live.log()[-1500:])
        return []
    record("runner starts with vfg orch start", True, "following the inbox")
    ids = []

    # 1. make it day, timed from `vfg orch send` to the ORCH done line in the feed
    t = time.time()
    t1 = live.send("make it day", "Steve asked for daytime.")
    ids.append(t1)
    live.wait(lambda: live.closes(t1), 300, 0.25)
    took = time.time() - t
    time.sleep(3)
    c, cmds = live.closes(t1), live.rcon(t)
    m_open = re.search(r"^(\S+) inbox %s open" % t1, live.log(), re.M)
    m_reply = re.search(r"^(\S+) reply %s done" % t1, live.log(), re.M)
    inner = ""
    if m_open and m_reply:
        f = lambda x: time.mktime(time.strptime(x[:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + x[19:23])  # noqa: E731
        inner = "; runner saw it -> reply tool %.1f s" % (f(m_reply.group(1)) - f(m_open.group(1)))
    record("1. make it day", len(c) == 1 and c[0][0] == "done" and any(re.match(r"time set (day|1000)\b", x) for x in cmds),
           "send -> ORCH done in the feed %.1f s%s, by the %s; feed=%s rcon=%s"
           % (took, inner, live.who(t1), c, [x for x in cmds if x.startswith("time")]))

    # 2. rain TNT on everyone
    t = time.time()
    t2 = live.send("rain TNT on everyone", "Everyone asked for TNT rain. Steve is online.")
    ids.append(t2)
    live.wait(lambda: live.closes(t2), 300)
    time.sleep(3)
    c, cmds = live.closes(t2), live.rcon(t)
    tnt = [x for x in cmds if "summon tnt" in x and "vfg_tnt_rain" in x]
    record("2. rain TNT on everyone", len(c) == 1 and c[0][0] == "done" and tnt,
           "feed=%s; %d tnt_rain summons on the box, e.g. %s" % (c, len(tnt), tnt[:1]))

    # 3. a new script
    t = time.time()
    t3 = live.send("put a ring of torches round Steve every ten seconds", "Steve wants light around him wherever he goes.")
    ids.append(t3)
    live.wait(lambda: live.closes(t3), 600)
    time.sleep(5)
    c, cmds = live.closes(t3), live.rcon(t)
    fresh = [d for d in os.listdir(os.path.join(live.box.repo, "scripts"))
             if d not in live.seeds and not d.startswith("_") and os.path.isdir(os.path.join(live.box.repo, "scripts", d))]
    shapes = {d: readme_shape(os.path.join(live.box.repo, "scripts", d, "README.md")) for d in fresh}
    deployed = [d for d in fresh if os.path.exists(os.path.join(live.box.vfg_dir, "scripts", d, "main.py"))]
    ran = [d for d in fresh if any("vfg_" + d in x for x in cmds)]
    torches = [x for x in cmds if "torch" in x]
    record("3. a new script for torches", len(c) == 1 and c[0][0] == "done" and any(v is None for v in shapes.values())
           and deployed and (ran or torches),
           "feed=%s new=%s deployed=%s tagged commands from %s; %d torch commands, e.g. %s"
           % (c, shapes, deployed, ran, len(torches), torches[:1]))
    for d in fresh:
        try:
            say("  %s/README.md: %s" % (d, " / ".join(open(os.path.join(live.box.repo, "scripts", d, "README.md")).read().split("\n")[:3])))
        except OSError:
            pass

    # 4. op Steve
    t = time.time()
    t4 = live.send("op Steve", "Steve asked to be made an operator.")
    ids.append(t4)
    live.wait(lambda: live.closes(t4), 300)
    time.sleep(3)
    c, cmds = live.closes(t4), live.rcon(t)
    record("4. op Steve refused", len(c) == 1 and c[0][0] == "failed" and not any(re.match(r"/?op\b", x, re.I) for x in cmds),
           "feed=%s; op sent to RCON: %s" % (c, [x for x in cmds if re.match(r"/?op\b", x, re.I)]))

    # 5. two tasks handed together: both reach the session at once, each answered once
    mark = len(live.log())
    t5, t6 = live.hand(("give Steve a diamond", "Steve asked for a diamond."), ("make it rain", "Alex wants rain."))
    ids += [t5, t6]
    live.wait(lambda: live.closes(t5) and live.closes(t6), 300)
    time.sleep(3)
    seg = live.log()[mark:]
    p5, p6 = seg.find("push: TASK %s " % t5), seg.find("push: TASK %s " % t6)
    end5 = seg.find(" session turn ", p5) if p5 >= 0 else -1
    together = 0 <= p5 < p6 and (end5 < 0 or p6 < end5)
    record("5. two tasks handed together, each answered once",
           len(live.closes(t5)) == 1 and len(live.closes(t6)) == 1,
           "%s=%s %s=%s; both reached the session before %s's turn ended: %s; two implementers at once: %s"
           % (t5, live.closes(t5), t6, live.closes(t6), t5, together, "; 2 running" in seg))

    # 6. kill -9 while a task is open, then start again
    mark = len(live.log())
    t7 = live.send("build a small stone hut next to Steve", "Steve wants shelter for the night.")
    ids.append(t7)
    started = live.wait(lambda: re.search(r"implementer (started|seen): %s\b" % t7, live.log()[mark:]), 180, 0.2)
    open_at_kill = not live.closes(t7)
    pid = live.runner_pid()
    say("kill -9 runner %s; work on %s under way: %s; open: %s" % (pid, t7, bool(started), open_at_kill))
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        live.wait(lambda: not os.path.exists("/proc/%d" % pid) or "zombie" in open("/proc/%d/status" % pid).read().lower(), 10, 0.1)
        time.sleep(1)
        try:
            os.killpg(pid, signal.SIGKILL)  # the orphaned CLI and its implementers
        except OSError:
            pass
    lock_left = os.path.exists(os.path.join(live.orch_dir, "lock"))
    mark2 = len(live.log())
    restarted = live.start()
    live.wait(lambda: live.closes(t7), 60)
    time.sleep(8)
    after = live.log()[mark2:]
    c = live.closes(t7)
    record("6. kill -9 mid-task, start again: that task fails once as interrupted, the session is told",
           started and open_at_kill and restarted and len(c) == 1 and c[0] == ("failed", INTERRUPTED)
           and "push: RUNNER: restarted at" in after,
           "work under way at kill=%s open=%s lock left=%s restarted=%s feed=%s RUNNER note=%s resumed same session=%s"
           % (bool(started), open_at_kill, lock_left, restarted, c, "push: RUNNER: restarted at" in after,
              bool(re.search(r"resuming [0-9a-f-]{36}", after))))

    # 7. a stale task
    t8 = live.send_stale("make it night")
    ids.append(t8)
    live.wait(lambda: live.closes(t8), 30, 0.5)
    c = live.closes(t8)
    record("7. an hour-old task expires", len(c) == 1 and c[0][0] == "failed" and c[0][1].startswith("Expired"), "feed=%s" % c)
    time.sleep(15)
    return ids


def timed(live, task, context, check):
    t = time.time()
    tid = live.send(task, context)
    live.wait(lambda: live.closes(tid), 600, 0.25)
    took = time.time() - t
    time.sleep(2)
    c = live.closes(tid)
    ok, evidence = check(live.rcon(t))
    record("%s (send -> ORCH done %.1f s, by the %s)" % (task, took, live.who(tid)), len(c) == 1 and c[0][0] == "done" and ok,
           "feed=%s; %s" % (c, evidence))
    return took


def quick_run(H, model):
    """The real defaults, two tasks, timed: make it day, and one seeded script."""
    live = Live(H, model)
    try:
        if not live.start():
            record("runner starts", False, live.log()[-1500:])
            return
        say("runner: " + " ".join(l[25:] for l in live.log().splitlines() if " main starting" in l))
        timed(live, "make it day", "Steve asked for daytime.",
              lambda cmds: (any(re.match(r"time set (day|1000)\b", x) for x in cmds), [x for x in cmds if x.startswith("time")]))
        timed(live, "rain TNT on everyone", "Everyone asked for TNT rain. Steve is online.",
              lambda cmds: (any("vfg_tnt_rain" in x for x in cmds), "%d tnt_rain commands on the box" % sum("vfg_tnt_rain" in x for x in cmds)))
        time.sleep(10)
        for l in live.log().splitlines():
            if " effort " in l or " implementer started" in l:
                say("  " + l[25:200])
        rows = live.box.sql("SELECT component, model, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, "
                            "ROUND(SUM(cost_usd), 4) cost FROM usage GROUP BY component, model")
        say("usage rows: %s" % rows)
        comps = {(r["component"], r["model"]) for r in rows}
        if live.last_id() and os.path.exists(os.path.join(live.orch_dir, "inbox.jsonl")):
            record("usage: orchestrator rows and implementer rows, each priced",
                   any(c == "orchestrator" for c, m in comps) and any(c == "implementer" for c, m in comps)
                   and all((r["cost"] or 0) > 0 for r in rows), str(rows))
        else:
            say("usage: the dispatcher did both tasks, so the orchestrator wrote no rows: %s" % rows)
        r = live.vfg("orch", "stop", timeout=60)
        record("vfg orch stop", r.returncode == 0, r.stdout.strip())
    finally:
        live.close()
        say("box and logs kept in %s" % live.root)


def live_run(H, model):
    live = Live(H, model)
    try:
        probe(live)
        ids = scenarios(live)
        # 8. usage rows, in the box's vfg.db
        rows = live.box.sql("SELECT component, model, COUNT(*) n FROM usage GROUP BY component, model")
        comps = {r["component"] for r in rows}
        ok = "implementer" in comps and "orchestrator" in comps
        record("8. usage rows land in the box", ok, str(rows))
        feed = live.feed_orch()
        counts = {tid: len([1 for _, t, st, _ in feed if t == tid and st in ("done", "failed")]) for tid in ids if tid}
        record("every task: exactly one ORCH done|failed in the feed", counts and all(n == 1 for n in counts.values()),
               " ".join("%s:%d" % kv for kv in counts.items()))
        mark = len(live.log())
        r = live.vfg("orch", "stop", timeout=60)
        tail = live.log()[mark:]
        record("vfg orch stop stops it cleanly", r.returncode == 0 and "stopped (exit 0)" in tail
               and not os.path.exists(os.path.join(live.orch_dir, "lock")),
               "%s; %s" % (r.stdout.strip(), " / ".join(l[25:] for l in tail.splitlines() if " main " in l)))
        say("ORCH lines in the feed:")
        for row in feed:
            say("  ORCH#%d %s %s | %s" % (row[0], row[1], row[2], row[3][:160]))
        say("refused tool calls in the transcripts:")
        for who, use, text in live.refusals():
            say("  %s %s %r -> %s" % (who, use[0], str(use[1])[:160], text[:110]))
        state = open(os.path.join(live.orch_dir, "state.json")).read() if os.path.exists(os.path.join(live.orch_dir, "state.json")) else ""
        leaks = [p for p in (r"sk-ant-[A-Za-z0-9_-]{10,}", r"rcon\.password", live.box.rcon_password, r"BEGIN [A-Z ]*PRIVATE KEY")
                 if re.search(p, live.log() + state)]
        record("no secret in runner.log or state.json", not leaks, "clean" if not leaks else "matched %d pattern(s)" % len(leaks))
    finally:
        live.close()
        say("box and logs kept in %s (home/.vfg/orch/runner.log, logs/rcon.jsonl, opt/vfg/feed.log)" % live.root)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preflight", action="store_true", help="no model: the box, every vfg shape, the guard")
    ap.add_argument("--quick", action="store_true", help="the real defaults: make it day and one seeded script, timed")
    args = ap.parse_args()
    H = load_harness()
    if args.preflight:
        preflight(H)
    elif args.quick:
        quick_run(H, os.environ.get("VFG_ORCH_MODEL", ""))
    else:
        live_run(H, os.environ.get("VFG_ORCH_MODEL", ""))
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n%d/%d passed" % (passed, len(RESULTS)))
    for name, ok, _ in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
