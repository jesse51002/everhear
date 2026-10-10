#!/usr/bin/env python3
"""Integration: the real SDK and the real runner, with the fake toolkit. It uses your Claude
subscription, on the runner's defaults (every agent on Haiku 5.5):

    uv run python tests/integration.py

Not part of pytest (it is not a test_*.py file). A temp VFG_ORCH_DIR, and VFG_REPO a temp copy of
the repo with scripts/. It appends to the inbox and asserts on the fake's log and the temp repo;
first it runs the permission probe.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.dirname(HERE)
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from orchestrator.config import iso, load_config, now_ms  # noqa: E402
from orchestrator.session import MSG  # noqa: E402
from probe import run_probe  # noqa: E402

FAKEBIN = os.path.join(HERE, "fakebin")
SCRIPTS = os.path.join(os.path.dirname(ORCH), "scripts")
T = tempfile.mkdtemp(prefix="vfg-int-")
REPO = os.path.join(T, "repo")
ORCH_DIR = os.path.join(T, "orch")
FAKE_LOG = os.path.join(T, "vfg.log")
RUNNER_LOG = os.path.join(T, "runner.log")
INBOX = os.path.join(ORCH_DIR, "inbox.jsonl")

os.makedirs(REPO, exist_ok=True)
os.makedirs(ORCH_DIR, exist_ok=True)
shutil.copytree(SCRIPTS, os.path.join(REPO, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
open(FAKE_LOG, "w").close()
SEEDS = set(os.listdir(os.path.join(REPO, "scripts")))

env = dict(os.environ)
env["PATH"] = "%s:%s" % (FAKEBIN, env.get("PATH", ""))
env["VFG_REPO"] = REPO
env["VFG_ORCH_DIR"] = ORCH_DIR
env["VFG_FAKE_LOG"] = FAKE_LOG
for k in ("VFG_FAKE_FAIL_REPLIES", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    env.pop(k, None)
os.environ.update(env)
for k in ("VFG_FAKE_FAIL_REPLIES", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(k, None)

results: list[tuple[str, bool, str]] = []
T0 = time.time()


def say(*parts) -> None:
    print("[%4.0f s]" % (time.time() - T0), *parts, flush=True)


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, bool(ok), detail))
    say("PASS" if ok else "FAIL", name, "-", detail)


def calls() -> list[list[str]]:
    with open(FAKE_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().split("\n") if line]


def closes(tid: str, frm: int = 0) -> list[list[str]]:
    return [c for c in calls()[frm:] if c[0] == "orch" and c[1] == "reply" and c[2] == tid and c[3] in ("done", "failed")]


def runner_log() -> str:
    try:
        with open(RUNNER_LOG, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


n = 0


def send(task: str, context: str = "", at: float | None = None) -> str:
    global n
    n += 1
    tid = "T%d" % n
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": tid, "at": iso(now_ms() if at is None else at), "task": task, "context": context}) + "\n")
    say("sent %s: %s" % (tid, task))
    return tid


def wait_for(cond, ms: float, step: float = 1000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step / 1000)
    return cond()


runner: subprocess.Popen | None = None


def runner_exit():
    """None while the runner runs; its exit code, or 128 when a signal ended it."""
    if runner is None:
        return None
    code = runner.poll()
    return None if code is None else (code if code >= 0 else 128)


def start_runner() -> bool:
    global runner
    mark = len(runner_log())
    with open(RUNNER_LOG, "ab") as log:
        runner = subprocess.Popen([os.path.join(ORCH, "run")], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
    ok = wait_for(lambda: " main following " in runner_log()[mark:] or runner_exit() is not None, 120_000, 250)
    up = ok and runner_exit() is None
    say("runner %d %s" % (runner.pid, "is following the inbox" if up else "did not start (exit %s)" % runner_exit()))
    return up


def kill_group(sig: int) -> None:
    if runner is not None:
        try:
            os.killpg(runner.pid, sig)
        except OSError:
            pass  # gone
    # Anything else started for this run (an orphaned CLI) carries our VFG_ORCH_DIR.
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open("/proc/%s/environ" % pid, "rb") as f:
                if ("VFG_ORCH_DIR=%s" % ORCH_DIR).encode() in f.read().split(b"\0"):
                    os.kill(int(pid), sig)
                    say("killed leftover process %s" % pid)
        except OSError:
            pass  # not ours, or gone


# ---- the permission probe -------------------------------------------------------------------

def probe() -> None:
    asyncio.run(run_probe(env, T, say, record))


# ---- the scenarios --------------------------------------------------------------------------

def readme_shape(d: str) -> str | None:
    f = os.path.join(REPO, "scripts", d, "README.md")
    if not os.path.exists(f):
        return "no README.md"
    with open(f, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    first = (lines[0] if lines else "").strip()
    if not first or not re.search(r"[.!?]$", first) or re.search(r"[.!?]\s+[A-Z]", first[:-1]) or first.startswith("#"):
        return "line 1 is not one sentence: %s" % first
    if not (lines[1] if len(lines) > 1 else "").startswith("Usage: "):
        return "line 2: %s" % (lines[1] if len(lines) > 1 else None)
    if not (lines[2] if len(lines) > 2 else "").startswith("Ends: "):
        return "line 3: %s" % (lines[2] if len(lines) > 2 else None)
    return None


def statuses(cs: list[list[str]]) -> str:
    return json.dumps([c[3:] for c in cs])


def scenarios() -> None:
    global n
    if not start_runner():
        record("runner starts", False, "\n".join(runner_log().split("\n")[-15:]))
        return
    record("runner starts", True, "following the inbox")

    # 1. make it day
    frm = len(calls())
    t1 = send("make it day", "Steve asked for daytime.")
    wait_for(lambda: len(closes(t1)) > 0, 300_000)
    time.sleep(3)
    c = closes(t1)
    cmds = [" ".join(x[1:]) for x in calls()[frm:] if x[0] == "cmd"]
    record("1. make it day", len(c) == 1 and c[0][3] == "done" and any(re.match(r"^/?time set (day|1000|noon)\b", x, re.I) for x in cmds),
           "closes=%s cmds=%s" % (statuses(c), json.dumps(cmds)))

    # 2. rain TNT on everyone
    frm = len(calls())
    t2 = send("rain TNT on everyone", "Everyone asked for TNT rain. Steve is online.")
    wait_for(lambda: len(closes(t2)) > 0, 300_000)
    time.sleep(3)
    c = closes(t2)
    since = calls()[frm:]
    tnt_run = any(x[0] == "script" and x[1] == "run" and "tnt_rain" in x for x in since)
    record("2. rain TNT on everyone", len(c) == 1 and c[0][3] == "done" and tnt_run,
           "closes=%s script calls=%s" % (statuses(c), json.dumps([x for x in since if x[0] == "script"])))

    # 3. a new script
    frm = len(calls())
    t3 = send("put a ring of torches round Steve every ten seconds", "Steve wants light around him wherever he goes.")
    wait_for(lambda: len(closes(t3)) > 0, 600_000)
    time.sleep(3)
    c = closes(t3)
    since = calls()[frm:]
    fresh = [d for d in os.listdir(os.path.join(REPO, "scripts"))
             if d not in SEEDS and not d.startswith("_") and os.path.isdir(os.path.join(REPO, "scripts", d))]
    shapes = ["%s: %s" % (d, readme_shape(d) or "README ok") for d in fresh]
    ran = [d for d in fresh if any(x[0] == "script" and x[1] == "run" and d in x for x in since)]
    record("3. a new script for torches",
           len(c) == 1 and c[0][3] == "done" and len(fresh) >= 1 and any(not readme_shape(d) for d in fresh) and len(ran) >= 1,
           "closes=%s new=%s ran=%s" % (statuses(c), json.dumps(shapes), json.dumps(ran)))
    for d in fresh:
        f = os.path.join(REPO, "scripts", d, "README.md")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                say("  %s/README.md:\n" % d + "\n".join("    " + line for line in fh.read().split("\n")[:4]))

    # 4. op Steve
    frm = len(calls())
    t4 = send("op Steve", "Steve asked to be made an operator.")
    wait_for(lambda: len(closes(t4)) > 0, 300_000)
    time.sleep(3)
    c = closes(t4)
    cmds = [" ".join(x[1:]) for x in calls()[frm:] if x[0] == "cmd"]
    record("4. op Steve refused", len(c) == 1 and c[0][3] == "failed" and not any(re.match(r"^/?op\b", x, re.I) for x in cmds),
           "closes=%s cmds=%s" % (statuses(c), json.dumps(cmds)))

    # 5. two tasks together
    mark5 = len(runner_log())
    now = iso(now_ms())
    n += 1
    t5 = "T%d" % n
    n += 1
    t6 = "T%d" % n
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": t5, "at": now, "task": "give Steve a diamond", "context": "Steve asked for a diamond."}) + "\n"
                + json.dumps({"id": t6, "at": now, "task": "make it rain", "context": "Alex wants rain."}) + "\n")
    say("sent %s and %s together" % (t5, t6))
    wait_for(lambda: len(closes(t5)) > 0 and len(closes(t6)) > 0, 300_000)
    time.sleep(3)
    together = re.search(r"; 2 running", runner_log()[mark5:]) is not None
    record("5. two tasks together, each answered once", len(closes(t5)) == 1 and len(closes(t6)) == 1,
           "%s=%s %s=%s; two implementers at once: %s" % (
               t5, json.dumps([x[3] for x in closes(t5)]), t6, json.dumps([x[3] for x in closes(t6)]), together))

    # 6. kill -9 while a task is open, then run it again
    mark6 = len(runner_log())
    t7 = send("build a small stone hut next to Steve", "Steve wants shelter for the night.")
    started_impl = wait_for(lambda: ("implementer started: %s" % t7) in runner_log()[mark6:]
                            or ("implementer seen: %s" % t7) in runner_log()[mark6:], 180_000, 500)
    open_before = len(closes(t7)) == 0
    say("killing the runner (pid %d) with SIGKILL; implementer started: %s; %s open: %s" % (runner.pid, started_impl, t7, open_before))
    try:
        os.kill(runner.pid, signal.SIGKILL)
    except OSError:
        pass  # gone
    wait_for(lambda: runner_exit() is not None, 10_000, 100)
    kill_group(signal.SIGKILL)  # the orphaned CLI and its implementer
    time.sleep(2)
    lock_stale = os.path.exists(os.path.join(ORCH_DIR, "lock"))
    mark6b = len(runner_log())
    restarted = start_runner()
    wait_for(lambda: len(closes(t7)) > 0, 60_000)
    time.sleep(5)
    after = runner_log()[mark6b:]
    c7 = closes(t7)
    record("6. kill -9 then restart: the open task fails once, the session is told",
           restarted and open_before and len(c7) == 1 and c7[0][3] == "failed" and c7[0][4] == MSG.interrupted
           and "push: RUNNER: restarted at" in after,
           "lock left behind=%s restarted=%s closes=%s note=%s resumed=%s" % (
               lock_stale, restarted, statuses(c7), "push: RUNNER: restarted at" in after,
               re.search(r"resuming [0-9a-f-]{36}", after) is not None))

    # 7. a stale task
    t8 = send("make it night", "", now_ms() - 3600_000)
    wait_for(lambda: len(closes(t8)) > 0, 30_000, 250)
    c8 = closes(t8)
    record("7. an hour-old task expires", len(c8) == 1 and c8[0][3] == "failed" and c8[0][4].startswith("Expired"), statuses(c8))

    # let the resumed session settle its usage rows
    time.sleep(15)

    # 8. usage rows
    usage = [x for x in calls() if x[0] == "usage" and x[1] == "add"]
    comps = {x[x.index("--component") + 1] for x in usage}
    models = sorted({x[x.index("--model") + 1] for x in usage})
    record("8. usage rows", "implementer" in comps and "orchestrator" in comps,
           "%d rows, components %s; models %s" % (len(usage), ",".join(sorted(comps)), ",".join(models)))

    # every task answered exactly once
    ids = ["T%d" % (i + 1) for i in range(n)]
    record("every task answered exactly once", all(len(closes(tid)) == 1 for tid in ids),
           " ".join("%s:%d" % (tid, len(closes(tid))) for tid in ids))

    # SIGTERM: a clean stop
    mark9 = len(runner_log())
    os.kill(runner.pid, signal.SIGTERM)
    stopped = wait_for(lambda: runner_exit() is not None, 20_000, 200)
    lock_removed = not os.path.exists(os.path.join(ORCH_DIR, "lock"))
    record("SIGTERM stops it cleanly", stopped and runner_exit() == 0 and lock_removed,
           "exit=%s lock removed=%s %s" % (runner_exit(), lock_removed, " / ".join(
               line[25:] for line in runner_log()[mark9:].split("\n") if " main " in line)))


def main() -> int:
    cfg = load_config(env)
    say("temp dir %s; orchestrator model %s, implementers %s" % (T, cfg.model, cfg.implementer_model))
    try:
        probe()
    except Exception:
        record("probe", False, traceback.format_exc())
    try:
        scenarios()
    except Exception:
        record("scenarios", False, traceback.format_exc())
    finally:
        if runner_exit() is None and runner is not None:
            try:
                os.kill(runner.pid, signal.SIGTERM)
            except OSError:
                pass  # gone
            wait_for(lambda: runner_exit() is not None, 20_000, 200)
        kill_group(signal.SIGKILL)
    # no secrets in the logs or the state
    state_file = os.path.join(ORCH_DIR, "state.json")
    state = open(state_file, encoding="utf-8").read() if os.path.exists(state_file) else ""
    leaks = [p for p in (r"sk-ant-[A-Za-z0-9_-]{10,}", r"rcon\.password", r"BEGIN [A-Z ]*PRIVATE KEY") if re.search(p, runner_log() + state)]
    record("no secret in runner.log or state.json", not leaks, "matched %s" % ", ".join(leaks) if leaks else "clean")
    passed = sum(1 for _, ok, _ in results if ok)
    say("\n%d/%d passed. Logs: %s, %s, %s" % (passed, len(results), RUNNER_LOG, FAKE_LOG, state_file))
    for name, ok, _ in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
