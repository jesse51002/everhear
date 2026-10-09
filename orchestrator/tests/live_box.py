#!/usr/bin/env python3
"""The orchestrator live, against a local stand-in box, with the REAL vfg.

    python3 tests/live_box.py --preflight         no model: the box and every vfg shape an
                                                  implementer uses, plus the guard on each
    VFG_ORCH_MODEL=haiku python3 tests/live_box.py the live run: your subscription, once
                                                  (hybrid by default; VFG_ORCH_MODE=implementers|direct)
    python3 tests/live_box.py --quick             the real defaults (Opus, Sonnet implementers): two tasks, timed
    python3 tests/live_box.py --hybrid            hybrid: direct tasks, a delegated house, a quick task during it

The box is integration/harness.py's Box (imported, read-only): the real bridge, Jev filter and
feed writer on Python 3.9, the toolkit's fake_ssh, and a temp VFG_HOME whose config points into
it. In place of the toolkit's bare fake RCON this runs tests/realistic_rcon.py on the box's RCON
port, so an implementer can confirm what it did. The runner is started and stopped with
`vfg orch start|stop`, tasks are sent with `vfg orch send`, and every answer is checked in the
feed (`vfg history --feed`): exactly one `ORCH … done|failed` per task.
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
TOOLKIT = os.path.join(VFG_ROOT, "toolkit")
SCRIPTS = os.path.join(VFG_ROOT, "scripts")
REAL_VFG = os.path.expanduser("~/.local/bin/vfg")
REAL_HOME = os.path.expanduser("~")
UV = os.path.expanduser("~/.local/bin/uv")
INTERRUPTED = "The orchestrator restarted while this was running; anything it started may be half done."
ORCH_RE = re.compile(r"ORCH#(\d+) (\d\d:\d\d:\d\d) (T\d+) (progress|done|failed) \| (.*)$")
REFUSAL = re.compile(r"permission|denied|not allowed|Refused, but Bash works|Only files in the repository|"
                     r"Only the repository|owner's|Never edit|Leave model|Write the server command", re.I)

T0 = time.time()
RESULTS = []


def say(*parts):
    print("[%4.0f s]" % (time.time() - T0), *parts, flush=True)


def record(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    say("PASS" if ok else "FAIL", name, "-", detail)


def load_harness():
    spec = importlib.util.spec_from_file_location("vfg_integration_harness", HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Live:
    def __init__(self, H, model):
        ec2 = subprocess.run([UV, "python", "find", "3.9"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
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
        os.symlink(ORCH, os.path.join(b.repo, "orchestrator"))  # vfg orch start runs <repo>/orchestrator/run
        self.orch_dir = os.path.join(b.vhome, "orch")
        self.runner_log = os.path.join(self.orch_dir, "runner.log")
        self.seeds = set(os.listdir(os.path.join(b.repo, "scripts")))
        m = os.environ.get("VFG_ORCH_MODE")
        self.model_mode = m if m in ("direct", "implementers") else "hybrid"
        self.env = b.base_env()
        if model:
            self.env["VFG_ORCH_MODEL"] = model  # unset: the runner's own default (opus)
        for k in ("VFG_ORCH_EFFORT", "VFG_IMPL_MODEL", "VFG_IMPL_EFFORT"):
            if os.environ.get(k):
                self.env[k] = os.environ[k]
        self.env.update({
            "HOME": REAL_HOME,  # the CLI's own Claude login; vfg finds its config by VFG_HOME
            "VFG_HOME": b.vhome, "VFG_REPO": b.repo, "VFG_ORCH_DIR": self.orch_dir,
            "VFG_ORCH_MODE": self.model_mode,
            "FAKE_UNITS": b.units,
            # the box's python3 (3.9) first: fake_ssh runs the remote side with this PATH
            "PATH": os.pathsep.join([b.ec2bin, os.path.dirname(REAL_VFG), os.path.join(TOOLKIT, "tests", "bin"),
                                     os.environ.get("PATH", "")]),
        })
        say("box %s; RCON port %d; vfg = %s" % (self.root, b.rcon_port, os.path.realpath(REAL_VFG)))

    def vfg(self, *args, timeout=120):
        return subprocess.run([REAL_VFG] + list(args), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)

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

    def log(self):
        try:
            with open(self.runner_log, encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return ""

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
        mark = len(self.log())
        r = self.vfg("orch", "start")
        ok = r.returncode == 0 and self.wait(lambda: " main following " in self.log()[mark:], 120, 0.25)
        say("vfg orch start: %s %s; runner %s" % (r.stdout.strip(), r.stderr.strip(), "following" if ok else "NOT up"))
        return ok

    def send(self, task, context=""):
        r = self.vfg("orch", "send", task, "--context", context)
        tid = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None
        say("vfg orch send %r -> %s %s" % (task, tid, r.stderr.strip()))
        return tid

    def send_stale(self, task):
        """An hour-old task, appended as vfg orch send would (vfg cannot backdate one)."""
        inbox = os.path.join(self.orch_dir, "inbox.jsonl")
        with open(os.path.join(self.orch_dir, "inbox.lock"), "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            n = 0
            with open(inbox) as f:
                for line in f:
                    m = re.match(r'\{"id": "T(\d+)"', line)
                    if m:
                        n = max(n, int(m.group(1)))
            tid = "T%d" % (n + 1)
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
            if self.runner_pid():
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
    ("vfg", "script", "run", "tnt_rain"),
    ("sleep", "3"),
    ("vfg", "script", "logs", "tnt_rain", "-n", "5"),
    ("vfg", "script", "list"),
    ("vfg", "script", "stop", "tnt_rain"),
    ("vfg", "script", "run", "--wait", "gun_chests"),
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
           "send -> ORCH done in the feed %.1f s%s; feed=%s rcon=%s" % (took, inner, c, [x for x in cmds if x.startswith("time")]))

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

    # 5. two tasks back to back: each answered once; the second waits for the first's turn
    mark = len(live.log())
    t5 = live.send("give Steve a diamond", "Steve asked for a diamond.")
    t6 = live.send("make it rain", "Alex wants rain.")
    ids += [t5, t6]
    live.wait(lambda: live.closes(t5) and live.closes(t6), 300)
    time.sleep(3)
    seg = live.log()[mark:]
    p5, p6 = seg.find("push: TASK %s " % t5), seg.find("push: TASK %s " % t6)
    end5 = seg.find(" session turn ", p5) if p5 >= 0 else -1
    in_order = 0 <= p5 < end5 < p6
    record("5. two tasks back to back, each answered once, one at a time",
           len(live.closes(t5)) == 1 and len(live.closes(t6)) == 1 and in_order,
           "%s=%s %s=%s; %s reached the session only after %s's turn ended: %s"
           % (t5, live.closes(t5), t6, live.closes(t6), t6, t5, in_order))

    # 6. kill -9 while a task is open, then start again
    mark = len(live.log())
    t7 = live.send("build a small stone hut next to Steve", "Steve wants shelter for the night.")
    ids.append(t7)
    if live.model_mode == "implementers":
        started = live.wait(lambda: re.search(r"implementer (started|seen): %s\b" % t7, live.log()[mark:]), 180, 0.2)
    else:  # direct: the orchestrator's own first tool call on it
        started = live.wait(lambda: re.search(r"push: TASK %s .*?\n(?:.*\n)*?\S+ orch (Bash|Read|Write|Edit|Glob|Grep) " % t7,
                                              live.log()[mark:]), 180, 0.1)
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
    record("%s (send -> ORCH done %.1f s)" % (task, took), len(c) == 1 and c[0][0] == "done" and ok,
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
        record("usage: orchestrator rows on opus, implementer rows on sonnet, each priced",
               any(c == "orchestrator" and "opus" in m for c, m in comps) and any(c == "implementer" and "sonnet" in m for c, m in comps)
               and all((r["cost"] or 0) > 0 for r in rows), str(rows))
        r = live.vfg("orch", "stop", timeout=60)
        record("vfg orch stop", r.returncode == 0, r.stdout.strip())
    finally:
        live.close()
        say("box and logs kept in %s" % live.root)


def hybrid_run(H, model):
    """The real defaults (hybrid: Opus orchestrator, Sonnet implementers), timed, with the route
    each task took: done by the orchestrator itself, or by an implementer."""
    live = Live(H, model)
    try:
        if not live.start():
            record("runner starts", False, live.log()[-1500:])
            return
        say("runner: " + " ".join(l[25:] for l in live.log().splitlines() if " main starting" in l))

        def route(tid):
            return "implementer" if re.search(r"implementer (started|seen): %s\b" % tid, live.log()) else "itself"

        def run(task, context, want_route, check, wait=600):
            t = time.time()
            tid = live.send(task, context)
            live.wait(lambda: live.closes(tid), wait, 0.25)
            took = time.time() - t
            c = live.closes(tid)
            ok, ev = check(live.rcon(t))
            r = route(tid)
            record("%s: %s, send -> done %.1f s" % (task, r, took), len(c) == 1 and c[0][0] == "done" and r == want_route and ok,
                   "feed=%s; %s" % (c, ev))
            return tid, took

        run("make it day", "Steve asked for daytime.", "itself",
            lambda cmds: (any(re.match(r"time set (day|1000)\b", x) for x in cmds), [x for x in cmds if x.startswith("time")]))
        run("give Steve a diamond sword", "Steve asked for a diamond sword.", "itself",
            lambda cmds: (any(x.startswith("give Steve") and "diamond_sword" in x for x in cmds), [x for x in cmds if x.startswith("give")]))

        # the house goes to an implementer; a quick task goes in while it is built
        t3s = time.time()
        t3 = live.send("build a small house at spawn", "Steve wants a small house at spawn to sleep in.")
        delegated = live.wait(lambda: route(t3) == "implementer", 180, 0.25)
        still_building = not live.closes(t3)
        t4s = time.time()
        t4 = live.send("make it rain", "Alex wants rain.")
        live.wait(lambda: live.closes(t4), 300, 0.25)
        t4took = time.time() - t4s
        t4_before_t3 = bool(live.closes(t4)) and not live.closes(t3)
        live.wait(lambda: live.closes(t3), 900, 0.5)
        t3took = time.time() - t3s
        c3, c4 = live.closes(t3), live.closes(t4)
        cmds = live.rcon(t3s)
        build = [x for x in cmds if x.split(" ")[0] in ("fill", "setblock") or " run fill " in x or " run setblock " in x]
        record("build a small house at spawn: %s, send -> done %.1f s" % (route(t3), t3took),
               delegated and len(c3) == 1 and c3[0][0] == "done" and build,
               "feed=%s; %d fill/setblock commands, e.g. %s" % (c3, len(build), build[:2]))
        record("make it rain, sent while the house was being built: %s, send -> done %.1f s" % (route(t4), t4took),
               still_building and len(c4) == 1 and c4[0][0] == "done" and route(t4) == "itself" and t4_before_t3
               and any(x.startswith("weather rain") for x in cmds),
               "house still building when sent: %s; rain done before the house: %s; feed=%s" % (still_building, t4_before_t3, c4))

        time.sleep(10)
        for l in live.log().splitlines():
            if " effort " in l or " implementer started" in l or " orch Agent " in l:
                say("  " + l[25:200])
        rows = live.box.sql("SELECT component, model, COUNT(*) n, ROUND(SUM(cost_usd), 4) cost FROM usage GROUP BY component, model")
        say("usage rows: %s" % rows)
        feed = live.feed_orch()
        counts = {}
        for _, t, st, _ in feed:
            if st in ("done", "failed"):
                counts[t] = counts.get(t, 0) + 1
        record("every task answered exactly once", counts and all(n == 1 for n in counts.values()), str(counts))
        r = live.vfg("orch", "stop", timeout=60)
        record("vfg orch stop", r.returncode == 0, r.stdout.strip())
        say("refused tool calls:")
        for who, use, text in live.refusals():
            say("  %s %s %r -> %s" % (who, use[0], str(use[1])[:120], text[:100]))
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
        if live.model_mode == "direct":
            ok = comps == {"orchestrator"}
        else:
            ok = "implementer" in comps and ("haiku" in model or "orchestrator" in comps)
        record("8. usage rows land in the box", ok, "%s (%s mode)" % (rows, live.model_mode))
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
    ap.add_argument("--hybrid", action="store_true", help="hybrid with the real defaults: two direct tasks, a house, a quick task during it")
    args = ap.parse_args()
    H = load_harness()
    if args.preflight:
        preflight(H)
    elif args.quick:
        quick_run(H, os.environ.get("VFG_ORCH_MODEL", ""))
    elif args.hybrid:
        hybrid_run(H, os.environ.get("VFG_ORCH_MODEL", ""))
    else:
        live_run(H, os.environ.get("VFG_ORCH_MODEL", "haiku"))
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n%d/%d passed" % (passed, len(RESULTS)))
    for name, ok, _ in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
