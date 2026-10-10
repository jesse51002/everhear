#!/usr/bin/env python3
"""The replay, live: the run's tasks of 2026-10-08 sent again, one every 30 s, to the real runner,
against a real 26.3 server on this PC with three players always online. The spec's last "Test".

    python3 integration/replay/run.py [--every 30] [--only T1,T7 | --from T40] [--limit N]
                                      [--out DIR] [--cap 20] [--name replay] [--server replay]
    python3 integration/replay/measure.py DIR

1. A fresh realmc server (`integration/realmc/up replay --fresh`, Carpet), and Steve, Alex and Sam
   (the run's three players, by the names tasks.jsonl gives them) spawned at spread spots on the
   arena, in survival. Steve is the owner (realmc's VFG_OWNER). A watcher puts back any of them
   who leaves or dies, so every player is online for the whole replay.
2. The orchestrator runner, started as `vfg orch start` starts it, against the stand-in box:
   `vfg` is integration/realmc/bin/vfg (VFG_HOME in the stand-in), the runner's inbox, state and
   log are in DIR/orch (VFG_ORCH_DIR), `VFG_SERVER=replay`, so implementers write in made/replay/,
   which starts empty. The orchestrator on its defaults (Opus 5.5, low effort), implementers on
   theirs (Haiku 5.5). Nothing reads or writes ~/.vfg. HOME stays yours: the CLI's own login, on
   your Claude subscription.
3. Each task, as the conversation wrote it, with `vfg orch send TASK --context CONTEXT`, at a fixed
   pace. A task's id in the replay is the next free one; a reference in its words to an earlier
   task becomes that task's replay id, or "an earlier task" when it was not sent.
4. It waits until every task sent is closed (done or failed), at most --cap minutes after the last
   send, and for the implementers still running to end; then stops the runner, every script
   instance and the players, and puts every record in DIR: orch/ (inbox, state, replies, runner
   log), sent.jsonl (replay id, run id, when), usage.jsonl (the stand-in's usage rows), the
   session and implementer transcripts, made/ (what implementers made), box/ (instance logs and
   values), players.log, scripts.txt and state.json (what ran at the end). Then
   `integration/realmc/down replay`, unless --keep-server, and measure.py on DIR.

Python 3.9+, standard library only.
"""
import argparse
import glob
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", ".."))
REALMC = os.path.join(REPO, "integration", "realmc")
sys.path.insert(0, REALMC)
sys.path.insert(0, HERE)
import players as players_mod  # noqa: E402
import realmc  # noqa: E402
import measure  # noqa: E402

REAL_HOME = os.path.expanduser("~")
# Spread over the arena's 32 x 32 floor (x -16..15, z -16..15 at y 84), the owner in the middle.
SPOTS = {"Steve": (0.5, 85, 0.5), "Alex": (12.5, 85, 10.5), "Sam": (-11.5, 85, -9.5)}
T0 = time.time()
LOG = None


def say(*parts):
    line = "[%5.0f s] %s" % (time.time() - T0, " ".join(str(p) for p in parts))
    print(line, flush=True)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def iso_now():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


def realmc_env(name):
    """The environment `eval "$(integration/realmc/env NAME)"` gives a shell."""
    out = subprocess.run([sys.executable, os.path.join(REALMC, "realmc.py"), "env", name],
                         stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout
    env = dict(os.environ)
    for line in out.splitlines():
        if line.startswith("unset "):
            for k in line.split()[1:]:
                env.pop(k, None)
        m = re.match(r"export (\w+)='([^']*)'(.*)$", line)
        if m:
            k, v, rest = m.groups()
            env[k] = v + (":" + os.environ.get("PATH", "") if rest == ':"$PATH"' else "")
    return env


def vfg(env, *args, timeout=120):
    return subprocess.run(["vfg"] + list(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout)


def select(tasks, a):
    ids = list(tasks)
    if a.only:
        want = [x.strip().upper() for x in a.only.split(",") if x.strip()]
        missing = [x for x in want if x not in tasks]
        if missing:
            sys.exit("not in tasks.jsonl: %s" % ", ".join(missing))
        ids = [i for i in ids if i in want]
    if a.from_:
        start = a.from_.upper()
        if start not in tasks:
            sys.exit("not in tasks.jsonl: %s" % start)
        ids = ids[ids.index(start):]
    if a.limit:
        ids = ids[:a.limit]
    return ids


def rewrite_refs(text, sent_ids):
    """A reference to an earlier task in the run's numbering, as the replay numbers it."""
    def rep(m):
        rid = sent_ids.get(m.group(0))
        return rid if rid else "an earlier task"
    return re.sub(r"\bT\d+\b", rep, text or "")


class Watcher(threading.Thread):
    """Puts back any of the three players who is not online, every few seconds, at their spot."""

    def __init__(self, server, path):
        threading.Thread.__init__(self, daemon=True)
        self.p = players_mod.on(server)
        self.path = path
        self.stopping = threading.Event()
        self.respawns = 0

    def note(self, msg):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (iso_now(), msg))

    def run(self):
        while not self.stopping.wait(5):
            try:
                online = self.p.online()
                for name, at in SPOTS.items():
                    if name not in online and not self.stopping.is_set():
                        self.p.spawn(name, at=at)
                        self.respawns += 1
                        self.note("%s was not online: spawned again at %g %g %g" % ((name,) + at))
            except Exception as e:  # the server may be busy; try again next time
                self.note("watch: %s" % e)

    def stop(self):
        self.stopping.set()


class Runner:
    """The orchestrator runner, by `vfg orch start` and `vfg orch stop`, its records in DIR/orch."""

    def __init__(self, env, orch_dir):
        self.env = env
        self.dir = orch_dir
        self.log = os.path.join(orch_dir, "runner.log")

    def start(self):
        """As `vfg orch start` starts it (orchestrator/run, detached, its pid in runner.pid, so
        `vfg orch status` and `stop` find it), but with your HOME: through realmc's bin/vfg the
        runner would get the stand-in's HOME, where the CLI is not logged in."""
        env = dict(self.env, VFG_ORCH_RUNNER="1")
        with open(self.log, "ab") as logf:
            logf.write(("--- replay start %s ---\n" % iso_now()).encode())
            p = subprocess.Popen([os.path.join(REPO, "orchestrator", "run")], cwd=os.path.join(REPO, "orchestrator"),
                                 stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=env)
        with open(os.path.join(self.dir, "runner.pid"), "w") as f:
            f.write("%d\n" % p.pid)
        say("runner started, pid %d" % p.pid)
        self.wait_log(r" main following ", 180)

    def text(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def wait_log(self, pattern, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if re.search(pattern, self.text()):
                return
            time.sleep(0.5)
        raise TimeoutError("no %r in %s within %d s" % (pattern, self.log, seconds))

    def state(self):
        try:
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def alive(self):
        return vfg(self.env, "orch", "status").returncode == 0

    def running_implementers(self):
        """The count the runner last logged, 0 when it never logged one."""
        m = re.findall(r" session implementer [^\n]*?; (\d+) running", self.text())
        return int(m[-1]) if m else 0

    def stop(self):
        r = vfg(self.env, "orch", "stop", timeout=60)
        say("vfg orch stop:", (r.stdout + r.stderr).strip())

    def session_ids(self):
        ids = re.findall(r" session session ([0-9a-f-]{36})", self.text())
        sid = self.state().get("sessionId")
        if sid:
            ids.append(sid)
        return list(dict.fromkeys(ids))


def project_dir():
    return os.path.join(REAL_HOME, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", REPO))


def usage_rows(db, after_id):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("select * from usage where id > ? order by id", (after_id,))]
        c.close()
        return rows
    except sqlite3.Error as e:
        say("usage rows: %s" % e)
        return []


def last_usage_id(db):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        n = c.execute("select coalesce(max(id), 0) from usage").fetchone()[0]
        c.close()
        return n
    except sqlite3.Error:
        return 0


def _interrupt(*_):
    raise KeyboardInterrupt()


def main(argv=None):
    global LOG
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", default="replay", help="the realmc server (default replay)")
    ap.add_argument("--server", default="replay", help="VFG_SERVER: implementers write in made/<server>/ (default replay)")
    ap.add_argument("--every", type=float, default=30, help="seconds between two sends (default 30)")
    ap.add_argument("--only", help="these tasks, by the run's ids: T1,T7,...")
    ap.add_argument("--from", dest="from_", metavar="T40", help="from this task on")
    ap.add_argument("--limit", type=int, help="at most this many tasks")
    ap.add_argument("--cap", type=float, default=20, help="minutes to wait after the last send for every task to close")
    ap.add_argument("--out", help="the output folder (default: a new vfg-replay-<time> in $VFG_REPLAY_DIR or the temp folder)")
    ap.add_argument("--no-fresh", action="store_true", help="keep the server's world as it is")
    ap.add_argument("--keep-server", action="store_true", help="leave the server running at the end")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, and send nothing")
    a = ap.parse_args(argv)

    tasks = measure.load_tasks()
    ids = select(tasks, a)
    if not ids:
        sys.exit("no tasks selected")
    if a.dry_run:
        sent = {}
        for i, tid in enumerate(ids):
            sent[tid] = "T%d" % (i + 1)
            t = tasks[tid]
            print("%s -> %s [%s] %s | %s" % (tid, sent[tid], t["class"], rewrite_refs(t["task"], sent),
                                            rewrite_refs(t["context"], sent)[:160]))
        return 0

    out = os.path.abspath(a.out or os.path.join(os.environ.get("VFG_REPLAY_DIR") or os.environ.get("TMPDIR") or "/tmp",
                                                 "vfg-replay-" + time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(out, exist_ok=True)
    orch_dir = os.path.join(out, "orch")
    if os.path.exists(os.path.join(orch_dir, "inbox.jsonl")):
        sys.exit("%s already holds a replay: give another --out" % out)
    os.makedirs(orch_dir, mode=0o700, exist_ok=True)
    LOG = os.path.join(out, "harness.log")
    say("replay of %d tasks every %g s into %s" % (len(ids), a.every, out))

    # 1. the server and its players
    if not a.no_fresh or not realmc.Server(a.name).pid():
        cmd = [os.path.join(REALMC, "up"), a.name] + ([] if a.no_fresh else ["--fresh"])
        subprocess.run(cmd, check=True)
    srv = realmc.Server(a.name)
    st = srv.status()
    if st.get("mode") != "carpet":
        sys.exit("realmc %s runs without Carpet (%s): no fake players" % (a.name, st.get("mode")))
    env = realmc_env(a.name)
    env.update({"HOME": REAL_HOME, "VFG_REPO": REPO, "VFG_SERVER": a.server, "VFG_ORCH_DIR": orch_dir})
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
              "VFG_IMPL_MODEL", "VFG_IMPLEMENTER_MODEL", "VFG_ORCH_MODEL", "VFG_ORCH_EFFORT", "VFG_ORCH_MODE"):
        env.pop(k, None)
    made = os.path.join(REPO, "made", a.server)
    shutil.rmtree(made, ignore_errors=True)
    shutil.rmtree(os.path.join(srv.vfg, "made", a.server), ignore_errors=True)
    for line in vfg(env, "script", "list").stdout.splitlines():
        if line.strip() and "running" in line:
            vfg(env, "script", "stop", line.split()[0])
    db = os.path.join(srv.vfg, "vfg.db")
    usage_from = last_usage_id(db)
    p = players_mod.on(a.name)
    for name, at in SPOTS.items():
        say("%s at %s" % (name, p.spawn(name, at=at)))
    watcher = Watcher(a.name, os.path.join(out, "players.log"))
    watcher.start()
    with open(os.path.join(out, "run.json"), "w") as f:
        json.dump({"name": a.name, "server": a.server, "every": a.every, "tasks": ids, "started": iso_now(),
                   "spots": SPOTS, "owner": "Steve", "usage_after_id": usage_from}, f, indent=1)

    runner = Runner(env, orch_dir)
    sent_ids = {}
    interrupted = []
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        runner.start()  # inside the try: a runner that never comes up still gets everything stopped
        # 2. the tasks, at a fixed pace
        t_start = time.time()
        with open(os.path.join(out, "sent.jsonl"), "a", encoding="utf-8") as sent_file:
            for i, tid in enumerate(ids):
                due = t_start + i * a.every
                while time.time() < due:
                    time.sleep(0.2)
                t = tasks[tid]
                text, ctx = rewrite_refs(t["task"], sent_ids), rewrite_refs(t["context"], sent_ids)
                at = iso_now()
                r = vfg(env, "orch", "send", "--context", ctx, "--", text)
                rid = r.stdout.strip()
                if r.returncode != 0 or not re.match(r"T\d+$", rid):
                    say("send %s failed: %s" % (tid, (r.stdout + r.stderr).strip()))
                    continue
                sent_ids[tid] = rid
                sent_file.write(json.dumps({"replay_id": rid, "run_id": tid, "at": at, "class": t["class"]}) + "\n")
                sent_file.flush()
                say("sent %s as %s [%s]: %s" % (tid, rid, t["class"], text[:100]))
                if "Not logged in" in runner.text():
                    raise RuntimeError("the CLI is not logged in: see %s" % runner.log)
        # 3. until every task is closed, or the cap
        deadline = time.time() + a.cap * 60
        last, polls = None, 0
        while time.time() < deadline:
            tasks_now = runner.state().get("tasks") or {}
            open_ = [rid for rid in sent_ids.values() if (tasks_now.get(rid) or {}).get("status") not in ("done", "failed")]
            if not open_:
                break
            if open_ != last:
                say("open: %s" % " ".join(open_))
                last = open_
            polls += 1
            if polls % 15 == 0 and not runner.alive():
                say("the runner is not running")
                break
            time.sleep(2)
        else:
            say("cap reached with tasks open: %s" % " ".join(last or []))
        end = time.time() + 180
        while runner.running_implementers() and time.time() < end:
            time.sleep(2)
        time.sleep(5)  # the last result's usage rows
    except (KeyboardInterrupt, RuntimeError, TimeoutError) as e:
        interrupted.append(str(e) or "interrupted")
        say("stopping: %s" % (e or "interrupted"))
    finally:
        runner.stop()
        watcher.stop()
        watcher.join(15)  # not mid-spawn when the players are taken off
        finish(out, env, srv, runner, made, db, usage_from, a)
    say("records in %s" % out)
    if not interrupted:
        m = measure.measure(out, tasks)
        with open(os.path.join(out, "measure.md"), "w", encoding="utf-8") as f:
            f.write(measure.report(m))
        print(measure.report(m))
    return 1 if interrupted else 0


def finish(out, env, srv, runner, made, db, usage_from, a):
    """Every record into OUT, every script and player off, the server down."""
    for name, args in (("scripts.txt", ["script", "list"]), ("state.json", ["state", "--json"])):
        try:
            r = vfg(env, *args, timeout=30)
            with open(os.path.join(out, name), "w") as f:
                f.write(r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            pass
    for line in (open(os.path.join(out, "scripts.txt")).read().splitlines() if os.path.exists(os.path.join(out, "scripts.txt")) else []):
        if line.strip() and "running" in line:
            vfg(env, "script", "stop", line.split()[0], timeout=60)
    try:
        players_mod.on(a.name).kill_all()
    except Exception as e:
        say("players: %s" % e)
    if os.path.isdir(made):
        shutil.copytree(made, os.path.join(out, "made"), dirs_exist_ok=True)
    box = os.path.join(out, "box")
    os.makedirs(box, exist_ok=True)
    for f in glob.glob(os.path.join(srv.vfg, "run", "*")):
        if os.path.isfile(f) and not f.endswith(".pid"):
            shutil.copy2(f, box)
    with open(os.path.join(out, "usage.jsonl"), "w") as f:
        for r in usage_rows(db, usage_from):
            f.write(json.dumps(r) + "\n")
    pd = project_dir()
    for sid in runner.session_ids():
        dst = os.path.join(out, "transcripts", sid)
        if os.path.isdir(os.path.join(pd, sid)):
            shutil.copytree(os.path.join(pd, sid), dst, dirs_exist_ok=True)
        os.makedirs(dst, exist_ok=True)
        if os.path.exists(os.path.join(pd, sid + ".jsonl")):
            shutil.copy2(os.path.join(pd, sid + ".jsonl"), os.path.join(dst, "main.jsonl"))
    if not a.keep_server:
        subprocess.run([os.path.join(REALMC, "down"), a.name])


if __name__ == "__main__":
    sys.exit(main())
