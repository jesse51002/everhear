#!/usr/bin/env python3
"""Test 2 of Prototype_Implementer_Harness, the replay: tasks of integration/replay/impl/tasks.json
through the real Implementers and the Claude Code adapter, on DeepSeek V4.1 Flash through
OpenRouter, on a realmc server, each as it happened: its first ask, then every follow-up in order,
each on the brief the stand-in brain wrote for it (impl_briefs_write.py), each sent at its
recorded moment:

- a follow-up that arrived mid-run is sent as many seconds after the ask before it as the player
  spoke then (11 s after "increase its damage", 4 s after that "so that actually explodes on
  hit"), so it reaches the run while it works: a message once the runner takes one, else it waits
  behind the run, as on the night; one that comes after the run has reported is a resume, too late
  by design;
- a later part of one line goes right after the part before it;
- any other waits until the task has nothing running, then --gap seconds (at most what the player
  waited that night, after_run_s), for the player's look at what came back.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    cd brain && uv run python ../integration/impl_replay.py --name ih-d3 --only T207,T212 [--asks 2] [--judge]
        [--harness opencode] [--budget 0.5] [--stop-at 0.6] [--keep-up] [--out DIR]

Each live server has its test server beside it, laid out as the box's (`up NAME --test-server`:
NAME/mc-test and NAME/vfg-test, the live folders + "-test", the unit minecraft-test, a fresh world at
every start, each test in its sandbox), where `vfg test` runs the implementers' tests with
VFG_TEST_BOX unset, exactly the box's path; it is made fresh with the live one for every task (--test
none: no test server, VFG_TEST_BOX=none, so vfg test exits 3 and the implementers check with one
action run). At the start, one library test (--preflight railgun) checks vfg test reaches it. Every
vfg test an implementer runs is logged (testcall) with what came back (testresult, its exit when it
can be read: 3 no test server, 4 the box did not answer, 5 the test server down or busy).

At the end, also on a failure or Ctrl-C, the server and its test server are stopped (down()) unless
--keep-up. Each task starts on a clean server (clean()): the server stopped with every copy it ran, made/<server>/
moved out of the repository into ~/.vfg/impl-replay/made-aside/, the stand-in's vfg.db, feed,
incantations, copies and deployed scripts set aside in its set-aside-<time>/, never deleted, and
`up --fresh` (a fresh world and a fresh vfg.db); --no-fresh only sets made/<server>/ and the
incantations aside, once. Steve and Alex are alive and online before every task and every ask, and
a keeper respawns either within 10 s of a death while a run works, as a person respawns. --asks N
replays each task's first N asks only; tasks run one after another; --judge scores each task with
impl_judge.py as it ends, on its own clean server, and writes every task's scores into one
~/.vfg/impl-replay/judge-<out's name>.json and .md. --budget stops new tasks once the requests and
the judge cost that much; --stop-at stops the implementers mid-run once the spend passes it.
Writes OUT/brain.log (the runner's log), OUT/runs.json (every report, with the asks sent before
it; each ask's send, and whether its task had a run live then; each task's runs, requests, cost,
LIMIT warnings and forced stops) and OUT/replay.md. Spend is priced by config.py's table from each
request's tokens. OPEN_ROUTER_API is read from Internal/.env into this process's environment only."""
import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))
sys.path.insert(0, os.path.join(HERE, "realmc"))
sys.path.insert(0, HERE)

import players  # noqa: E402
import realmc  # noqa: E402
from impl_briefs_write import load_key  # noqa: E402
from impl_data import DATA, ROOT  # noqa: E402
from impl_wild import Tee, use_realmc  # noqa: E402



def clean(name: str, made_too: bool = True, test_server: bool = False) -> list[str]:
    """The server as a fresh task finds it, nothing of an earlier task left: the server stopped
    (and every copy it ran, and its test server), then set aside, never deleted: made/<name>/ into
    ROOT/made-aside/<name>-<time>/ (out of the repository, where an implementer would read an
    earlier run's work), and the stand-in's vfg.db with its -wal and -shm, feed.log,
    incantations.json, run/ (the copies' logs and values) and made/ (the scripts deployed to it), and
    its home's running.json and places.json, into its set-aside-<time>/; then `up --fresh`: a fresh
    world from the template (no saved place, nothing built) and a fresh vfg.db (no history).
    With `test_server`, its test server too, laid out beside it as the box's (`up --test-server`:
    <name>/mc-test and <name>/vfg-test, the live folders + "-test"), where the implementers' vfg
    test plays with VFG_TEST_BOX unset: what an earlier task left in vfg-test (incantations.json,
    run/, made/, tests/, state/) set aside as test-* beside the rest, laid out again, and the test
    server restarted: a fresh world from its own template."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    srv = realmc.Server(name)
    up = os.path.join(HERE, "realmc", "up")
    subprocess.run([os.path.join(HERE, "realmc", "down"), name], capture_output=True, text=True, timeout=180)
    moved = []
    made = os.path.join(REPO, "made", name)
    if made_too and os.path.isdir(made):
        dest = os.path.join(ROOT, "made-aside", "%s-%s" % (name, stamp))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(made, dest)
        moved.append(dest)
    aside = os.path.join(srv.vfg, "set-aside-%s" % stamp)
    while os.path.lexists(aside):
        aside += "-2"
    groups = [(srv.vfg, ("vfg.db", "vfg.db-wal", "vfg.db-shm", "feed.log", "incantations.json", "run", "made",
                         "tests"), ""),
              (srv.vhome, ("running.json", "places.json"), "home-")]
    if test_server:
        groups.append((srv.vfg_test, ("incantations.json", "run", "made", "tests", "state"), "test-"))
    for base, names, prefix in groups:
        for n in names:
            if os.path.lexists(os.path.join(base, n)):
                os.makedirs(aside, exist_ok=True)
                shutil.move(os.path.join(base, n), os.path.join(aside, prefix + n))
    if os.path.isdir(aside):
        moved.append(aside)
    os.makedirs(os.path.join(srv.vfg, "run"), exist_ok=True)
    r = subprocess.run([up, name, "--fresh"] + (["--test-server"] if test_server else []), capture_output=True,
                       text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("realmc up %s --fresh%s failed: %s" % (name, " --test-server" if test_server else "",
                                                                 (r.stderr or r.stdout)[-400:]))
    return moved


def down(name: str) -> str:
    """`realmc/down NAME` at a run's end: the server, its copies and its test server stopped (each
    takes 2-3 GB), and the test server again if the last test's restart (vfg test's --no-block one)
    brought it back up after down had stopped it. What was done, in a line."""
    r = subprocess.run([os.path.join(HERE, "realmc", "down"), name], capture_output=True, text=True, timeout=300)
    time.sleep(5)
    again = bool(realmc.TestUnit(realmc.Server(name)).sup_pid())
    if again:
        subprocess.run([sys.executable, os.path.join(HERE, "realmc", "realmc.py"), "testserver", name, "--stop"],
                       capture_output=True, text=True, timeout=300)
    return "realmc down %s: exit %d%s" % (name, r.returncode, "; its test server, up again after it (the last "
                                          "test's restart), stopped too" if again else "")


def set_aside(name: str) -> list[str]:
    """made/<name>/ and the stand-in's incantations.json moved aside, never deleted (--no-fresh):
    made/<name>/ out of the repository, into ROOT/made-aside/."""
    moved, stamp = [], time.strftime("%Y%m%d-%H%M%S")
    made = os.path.join(REPO, "made", name)
    if os.path.isdir(made):
        dest = os.path.join(ROOT, "made-aside", "%s-%s" % (name, stamp))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(made, dest)
        moved.append(dest)
    srv = realmc.Server(name)
    inc = os.path.join(srv.vfg, "incantations.json")
    if os.path.exists(inc):
        dest = os.path.join(srv.vfg, "set-aside-%s" % stamp)
        os.makedirs(dest, exist_ok=True)
        shutil.move(inc, os.path.join(dest, "incantations.json"))
        moved.append(dest)
    return moved


TEST_CALL = re.compile(r"(?<![\w-])vfg test\b")


def test_code(error: bool, text: str) -> str:
    """vfg test's exit, from what the harness gave back: its exit code when the shell said it, else
    by vfg's own last words (3: no test server, or not this one; 4: the box did not answer; 5: the
    test server down, busy or its sandbox unusable), else ok or failed. A run that got as far as its
    report (a RESULT line) ran; every run's SERVER line names an RCON port, so "RCON" alone says
    nothing."""
    m = re.search(r"Exit code (\d+)", text)
    if m:
        return m.group(1)
    if re.search(r"^RESULT: ", text, re.M):
        return "error" if error else "0?"
    if re.search(r"no test server|not configured|a test never runs there|cannot run a test as another user", text):
        return "3?"
    if re.search(r"cannot reach|did not answer in time|host key", text):
        return "4?"
    if re.search(r"the test server (was |is )?(down|busy)|still did not answer RCON|restart minecraft-test failed|"
                 r"sandbox failed its check|is not usable here|has no systemd-run", text):
        return "5?"
    return "error" if error else "0?"


def made_now(name: str) -> dict:
    """The task's test and script as they are now (made/<server>/tests/<name>.py and
    scripts/<name>/main.py, the newest of any server's), for a record of each vfg test run."""
    out = {}
    for key, pattern in (("test", "made/*/tests/%s.py"), ("script", "made/*/scripts/%s/main.py")):
        paths = sorted(glob.glob(os.path.join(REPO, pattern % name)), key=os.path.getmtime)
        try:
            with open(paths[-1], encoding="utf-8") as f:
                out[key] = f.read()
        except (IndexError, OSError):
            out[key] = None
    return out


def watched(Implementers):
    class Watched(Implementers):
        """The runner, with each vfg test call and its result logged (`testcall`, `testresult`): the
        runner logs the calls but not what they gave back."""

        def __init__(self, cfg, on_report, log):
            super().__init__(cfg, on_report)
            self._log, self._tests = log, {}

        def _seen(self, t, ev, *rest, **kw):
            super()._seen(t, ev, *rest, **kw)
            kind = type(ev).__name__
            if kind == "ToolCall" and ev.name == "Bash" and TEST_CALL.search(str((ev.input or {}).get("command", ""))):
                self._tests[ev.id] = (t.name, str(ev.input.get("command")), made_now(t.name))
                self._log("testcall", "%s %s %s" % (t.name, ev.id, str(ev.input.get("command"))[:300]))
            elif kind == "ToolResult" and ev.id in self._tests:
                name, command, made = self._tests.pop(ev.id)
                code = test_code(ev.error, ev.text or "")
                self._log("testresult", "%s %s code=%s %s" % (name, ev.id, code,
                                                              re.sub(r"\s+", " ", ev.text or "")[:250]))
                if os.environ.get("VFG_IMPL_TESTS"):  # the whole of it, and the files as the test ran them
                    with open(os.environ["VFG_IMPL_TESTS"], "a", encoding="utf-8") as f:
                        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": name, "id": ev.id,
                                            "command": command, "code": code, "text": ev.text or "", **made}) + "\n")
    return Watched


class PlayerKeeper:
    """Steve and Alex alive and online: before every task and every ask (ensure()), and every 10 s
    while a run works (a keeper thread), as a person respawns after a death: a dead Carpet fake
    player leaves the server, and an implementer then spends its turns looking for him. The keeper
    pauses while the server restarts."""

    def __init__(self, name: str):
        self.name = name
        self.pause = threading.Event()
        self.done = threading.Event()
        self.respawned: list[str] = []
        self.thread = threading.Thread(target=self._keep, name="keep-players", daemon=True)

    def ensure(self) -> None:
        p = players.Players(self.name)
        try:
            on = p.online()
            for who, at in (("Steve", None), ("Alex", (12.5, 85, 0.5))):
                if who in on and re.search(r"[1-9][\d.]*f|0\.\d*[1-9]\d*f", p.cmd("data get entity %s Health" % who)):
                    continue
                if who in on:  # listed, but dead or gone: taken off first
                    p.cmd("player %s kill" % who)
                    end = time.time() + 5
                    while who in p.online() and time.time() < end:
                        time.sleep(0.2)
                p.spawn(who, at=at)
                self.respawned.append("%s %s" % (time.strftime("%H:%M:%S"), who))
        finally:
            p.close()

    def _keep(self) -> None:
        while not self.done.wait(10):
            if self.pause.is_set():
                continue
            try:
                self.ensure()
            except Exception:  # noqa: BLE001 — the server restarting, or RCON busy: next time
                pass

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.done.set()


class Replay:
    def __init__(self, imps, gap: float, before_send=None):
        self.imps, self.gap, self.before_send = imps, gap, before_send
        self.reports: list[dict] = []
        self.new_report = asyncio.Event()
        self.sends: list[dict] = []

    async def on_report(self, name, status, report, description, made):
        self.reports.append({"task": name, "status": status, "report": report, "description": description,
                             "made": made, "at": time.time(),
                             "after_asks": [s["ask"] for s in self.sends if s["task"] == name]})
        self.new_report.set()

    def live(self, name: str) -> bool:
        return name in self.imps.running()

    async def idle(self, name: str, timeout: float = 900) -> None:
        """Until the task has nothing running nor waiting, at most `timeout` s of the monotonic clock,
        which a suspended PC does not advance: a wall-clock deadline passed in a sleep once let a
        replay judge and wipe a task whose run was still going."""
        end = time.monotonic() + timeout
        while self.live(name) and time.monotonic() < end:
            self.new_report.clear()
            try:
                await asyncio.wait_for(self.new_report.wait(), 2.0)
            except TimeoutError:
                pass

    async def task(self, t: dict, most: int, log, stop=None) -> None:
        name = t["name"]
        asks = [a for a in t["asks"] if a.get("brief")][:most]
        sent_at: dict[int, float] = {}
        for a in asks:
            if stop is not None and stop.is_set():
                log("replay", "%s: the rest of its asks not sent, the spend reached the ceiling" % name)
                break
            n, text = a["n"], a["brief"]["text"]
            if a["kind"] == "start":
                pass
            elif "part_of" in a:
                pass  # right after the part before it
            elif a.get("mid_run"):  # the player's own spacing from the ask before
                prev = t["asks"][n - 1]
                at = sent_at.get(n - 1, time.monotonic()) + (a["t_s"] - prev["t_s"])
                await asyncio.sleep(max(0.0, at - time.monotonic()))
            else:
                await self.idle(name)
                await asyncio.sleep(min(self.gap, a.get("after_run_s") or self.gap))
            if self.before_send is not None:  # Steve and Alex alive and online before every ask
                await asyncio.to_thread(self.before_send)
            was_live = self.live(name)
            sent = time.time()
            if a["kind"] == "start":
                await self.imps.start(name, text)
            else:
                await self.imps.follow_up(name, text)
            sent_at[n] = time.monotonic()
            self.sends.append({"task": name, "id": t["id"], "ask": n, "kind": a["kind"], "at": sent,
                               "night_mid_run": bool(a.get("mid_run")), "part_of": a.get("part_of"),
                               "live_when_sent": was_live})
            log("replay", "%s ask %d (%s%s) sent%s: %s" % (
                name, n, a["kind"], ", mid-run that night" if a.get("mid_run") else "",
                " into a live run" if was_live else "", re.sub(r"\s+", " ", text)[:200]))
        await self.idle(name)
        if self.live(name):  # still working past the timeout: never judged or wiped mid-run
            log("replay", "%s still runs after its wait: the replay waits for it" % name)
            await self.idle(name, timeout=3600)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True, help="the realmc server")
    ap.add_argument("--only", default="", help="task ids, comma-separated, in order (default: the six and the 20 of "
                                                 "2026-10-08)")
    ap.add_argument("--asks", type=int, default=99, help="each task's first N asks")
    ap.add_argument("--gap", type=float, default=3.0, help="seconds after a run before a follow-up that came later")
    ap.add_argument("--no-fresh", action="store_true", help="no fresh server per task: only made/<server>/ and the "
                                                            "incantations set aside, once, at the start")
    ap.add_argument("--judge", action="store_true", help="score each task with impl_judge.py as it ends")
    ap.add_argument("--harness", default="claude-code", help="VFG_IMPL_HARNESS: claude-code or opencode")
    ap.add_argument("--budget", type=float, default=None,
                    help="dollars, the requests' and the judge's: no task starts once they cost this")
    ap.add_argument("--stop-at", type=float, default=None,
                    help="dollars: the implementers are stopped mid-run once the spend passes this")
    ap.add_argument("--test", default="box", choices=("box", "none"),
                    help="box (the default): the test server beside NAME, as the box's (up NAME --test-server), "
                         "VFG_TEST_BOX unset; none: no test server (VFG_TEST_BOX=none)")
    ap.add_argument("--preflight", default="railgun", help="a library test vfg test runs once at the start, to "
                                                           "check the test server answers; '' for none")
    ap.add_argument("--keep-up", action="store_true", help="leave the server and its test server running at the "
                                                           "end (by default they are stopped, also on a failure)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    test = a.test == "box"
    out = os.path.abspath(a.out or os.path.join(ROOT, "replay-%s" % a.name))
    label = os.path.basename(out)
    os.makedirs(out, exist_ok=True)
    moved = set_aside(a.name) if a.no_fresh else []
    use_realmc(a.name)
    load_key()
    os.environ.update(VFG_IMPL_PROVIDER="openrouter", VFG_USAGE_QUEUE=out, VFG_IMPL_HARNESS=a.harness)
    if test:  # vfg test's server: the box's own beside NAME, VFG_TEST_BOX unset as on the box
        os.environ.pop("VFG_TEST_BOX", None)
        if a.no_fresh:  # no clean() lays it out: once, here (nothing if it is there and up)
            r = subprocess.run([os.path.join(HERE, "realmc", "up"), a.name, "--test-server"], capture_output=True,
                               text=True, timeout=600)
            if r.returncode != 0:
                raise SystemExit("realmc up %s --test-server failed: %s" % (a.name, (r.stderr or r.stdout)[-400:]))
    else:     # unset is the box's own test server now: none is said
        os.environ["VFG_TEST_BOX"] = "none"
    keeper = PlayerKeeper(a.name)
    tee = Tee(os.path.join(out, "brain.log"))
    sys.stdout = tee
    from brain.implementers.config import ImplementerConfig, log
    from brain.implementers.runner import Implementers
    Watched = watched(Implementers)

    with open(DATA, encoding="utf-8") as f:
        tasks = json.load(f)["tasks"]
    only = [x for x in a.only.split(",") if x]
    by_id = {t["id"]: t for t in tasks}
    tasks = ([by_id[x] for x in only if x in by_id] if only
             else [t for t in tasks if t["date"] in ("2026-10-11", "2026-10-08")])
    cfg = ImplementerConfig.load()
    replay = Replay(None, a.gap, before_send=keeper.ensure)
    imps = Watched(cfg, replay.on_report, log)
    replay.imps = imps
    judged: list[dict] = []
    stop = asyncio.Event()

    def spent() -> float:
        return (sum(float(m.group(1)) for ln in tee.lines for m in [re.search(r" request \S+ .* cost=([\d.]+)", ln)]
                    if m) + sum(j.get("judge_cost") or 0 for j in judged))

    async def ceiling():
        while not stop.is_set():
            await asyncio.sleep(5)
            if a.stop_at is not None and spent() > a.stop_at:
                log("replay", "the spend is $%.4f, past --stop-at $%.2f: the implementers are stopped" % (
                    spent(), a.stop_at))
                stop.set()
                await imps.close(5)
    watch = asyncio.get_running_loop().create_task(ceiling())
    log("replay", "turn cap %d (warning at %d), time limit %d s; budget %s, stop at %s; test server %s (VFG_TEST_BOX %s)" % (
        cfg.max_turns, max(1, cfg.max_turns - 5), cfg.time_limit_s, a.budget, a.stop_at,
        realmc.Server(a.name).mc_test if test else "none", os.environ.get("VFG_TEST_BOX") or "unset: the box's own"))
    if moved:
        log("replay", "set aside: %s" % ", ".join(moved))
    t0 = time.time()
    keeper.start()
    preflight_done = False
    try:
        for t in tasks:
            if stop.is_set():
                break
            if a.budget is not None and spent() >= a.budget:
                log("replay", "%s not started: $%.4f spent, the budget is $%.2f" % (t["id"], spent(), a.budget))
                continue
            if not a.no_fresh:
                keeper.pause.set()
                moved = await asyncio.to_thread(clean, a.name, True, test)
                log("replay", "%s: a fresh server%s; set aside: %s" % (
                    t["id"], " and a fresh test server" if test else "", ", ".join(moved) or "nothing"))
                subprocess.run(["vfg", "action", "list", "--json"], capture_output=True, timeout=120)
                keeper.pause.clear()
            if test and a.preflight and not preflight_done:
                preflight_done = True
                r = await asyncio.to_thread(subprocess.run, ["vfg", "test", a.preflight], capture_output=True,
                                            text=True, timeout=300)
                said = (r.stdout + r.stderr).strip().splitlines()
                log("replay", "pre-flight: vfg test %s exit %d: %s" % (a.preflight, r.returncode, " | ".join(
                    ln.strip() for ln in said[-6:])[:600]))
            await asyncio.to_thread(keeper.ensure)
            for cmd in t.get("setup") or []:
                r = subprocess.run(["vfg", *cmd], capture_output=True, text=True, timeout=120)
                log("replay", "%s setup: vfg %s: %s" % (t["id"], " ".join(cmd), (r.stdout or r.stderr).strip()[-200:]))
            await replay.task(t, a.asks, log, stop)
            write(out, a, cfg, tasks, replay, tee, t0, keeper)
            if a.judge and any(x["id"] == t["id"] for x in replay.sends):
                keeper.pause.set()  # the judge sets its own stage and players
                r = await asyncio.to_thread(subprocess.run, [
                    sys.executable, os.path.join(HERE, "impl_judge.py"), "--name", a.name, "--only", t["id"],
                    "--reports", os.path.join(out, "runs.json"), "--label", "%s-%s" % (label, t["id"])],
                    capture_output=True, text=True)
                keeper.pause.clear()
                got = read_judge("%s-%s" % (label, t["id"]))
                if got:
                    judged.append(got)
                log("replay", "%s judged: %s" % (t["id"], (r.stdout.strip().splitlines() or ["(no answer)"])[0][:200]))
        if not stop.is_set():
            await imps.close(5)
    finally:
        stop.set()
        watch.cancel()
        keeper.stop()
        sys.stdout = sys.__stdout__
        tee.flush()
        if not a.keep_up:
            print(down(a.name), flush=True)
    doc = write(out, a, cfg, tasks, replay, tee, t0, keeper)
    if a.judge:
        judge_summary(label, judged, doc)
    return 0


def read_judge(label: str) -> dict | None:
    try:
        with open(os.path.join(ROOT, "judge-%s.json" % label), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def per_task(tee, name: str) -> dict:
    """From the runner's log: the task's runs, requests, cost, LIMIT warnings read and forced stops."""
    esc = re.escape(name)
    lines = tee.lines
    return {"runs": sum(1 for ln in lines if re.search(r" implementer %s (started|resumed)\b" % esc, ln)),
            "requests": sum(1 for ln in lines if re.search(r" request %s " % esc, ln)),
            "cost": round(sum(float(m.group(1)) for ln in lines
                              for m in [re.search(r" request %s .* cost=([\d.]+)" % esc, ln)] if m), 5),
            "warned": sum(1 for ln in lines if re.search(r" implementer %s read its message: LIMIT" % esc, ln)),
            "forced": sum(1 for ln in lines if re.search(r" implementer %s (done|failed): .*stopped at its limit" % esc,
                                                          ln)),
            "vfg_tests": sum(1 for ln in lines if re.search(r" testcall %s " % esc, ln)),
            "test_codes": dict(Counter(m.group(1) for ln in lines
                                       for m in [re.search(r" testresult %s \S+ code=(\S+)" % esc, ln)] if m)),
            "probes": sum(len(re.findall(r"vfg cmd ", ln)) for ln in lines
                          if re.search(r" implementer %s Bash " % esc, ln))}


def write(out, a, cfg, tasks, replay, tee, t0, keeper) -> dict:
    """OUT/runs.json and OUT/replay.md, as the replay stands now."""
    for r in replay.reports:
        r["id"] = next((t["id"] for t in tasks if t["name"] == r["task"]), r["task"])
    sent = [t for t in tasks if any(x["id"] == t["id"] for x in replay.sends)]
    stats = {t["name"]: per_task(tee, t["name"]) for t in sent}
    doc = {"server": a.name, "harness": cfg.harness, "max_turns": cfg.max_turns, "time_limit_s": cfg.time_limit_s,
           "asks": a.asks, "seconds": round(time.time() - t0, 1),
           "sends": replay.sends, "runs": replay.reports, "tasks": stats, "respawned": keeper.respawned,
           "cost": {k: v["cost"] for k, v in stats.items()}, "requests": {k: v["requests"] for k, v in stats.items()},
           "total_cost": round(sum(v["cost"] for v in stats.values()), 5)}
    with open(os.path.join(out, "runs.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, default=str)
    L = ["## Replay on %s, %s on the %s adapter" % (a.name, cfg.model, cfg.harness), "",
         "| Task | Asks sent | Into a live run | Runs | Requests | A run | Warned | Forced | vfg test runs (exits) | "
         "vfg cmd probes | Last status | Cost at peak |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in sent:
        s = [x for x in replay.sends if x["task"] == t["name"]]
        rs = [x for x in replay.reports if x["task"] == t["name"]]
        st = stats[t["name"]]
        L.append("| %s %s | %d | %d | %d | %d | %.1f | %d | %d | %d %s | %d | %s | $%.4f |" % (
            t["id"], t["name"], len(s), sum(x["live_when_sent"] for x in s), st["runs"], st["requests"],
            st["requests"] / max(st["runs"], 1), st["warned"], st["forced"], st["vfg_tests"], st["test_codes"] or "",
            st["probes"], rs[-1]["status"] if rs else "-", st["cost"]))
    L += ["", "%.0f s in all; $%.4f; players respawned %d times." % (doc["seconds"], doc["total_cost"],
                                                                      len(keeper.respawned)), ""]
    for r in replay.reports:
        L += ["### %s, after asks %s: %s" % (r["task"], r["after_asks"], r["status"]),
              re.sub(r"\s+", " ", r["report"])[:900], ""]
    with open(os.path.join(out, "replay.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return doc


def judge_summary(label: str, judged: list[dict], doc: dict) -> None:
    """Every task's judge rows in one ROOT/judge-<label>.json and .md."""
    rows = [r for j in judged for r in j.get("rows") or []]
    cost = sum(j.get("judge_cost") or 0 for j in judged)
    with open(os.path.join(ROOT, "judge-%s.json" % label), "w", encoding="utf-8") as f:
        json.dump({"label": label, "rows": rows, "judge_cost": round(cost, 6)}, f, indent=1)
    marks = [m["mark"] for r in rows for m in r.get("items") or []]
    L = ["## %s: %d done, %d not done, %d can't tell; implementers $%.4f, judge $%.4f" % (
        label, marks.count("done"), marks.count("not done"), marks.count("can't tell"), doc["total_cost"], cost), ""]
    for r in rows:
        m = [x["mark"] for x in r.get("items") or []]
        L.append("- %s %s: %d done, %d not done, %d can't tell" % (r["id"], r.get("name") or "", m.count("done"),
                                                                   m.count("not done"), m.count("can't tell")))
    with open(os.path.join(ROOT, "judge-%s.md" % label), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
