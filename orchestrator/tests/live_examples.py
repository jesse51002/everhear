#!/usr/bin/env python3
"""The script library's runner checks, live: a real 26.3 server and its stand-in box on this PC
(integration/realmc), the real runner, real implementers (Haiku 5.5 by default) on your Claude
subscription. The spec's "Test": the guard, the cache, saving back.

    integration/realmc/up orch
    cd orchestrator && uv run python tests/live_examples.py [--name orch] [--server orchtest] [--ttl-check]

1. T1: an allowed Agent call goes out with the EXAMPLES block: the implementer's first message
   holds it (its transcript), and asked to quote its first line, it quotes it (so updatedInput takes
   effect under dontAsk). The orchestrator's own next call is no bigger for it.
2. T2, then T3 a minute later: the second implementer reads the shared prefix (its rules and
   `vfg prefix`) from the cache. Each implementer's calls come from its own transcript.
3. T4: a job that needs something new ends with a new script in made/<server>/scripts/ and its action
   in made/<server>/actions/, which passes `vfg action check`. T5: an implementer told to edit a
   library file is refused by the guard, and
   the file is unchanged.
4. --ttl-check: a second runner with VFG_SUBAGENT_CACHE_TTL=1h and one small job: its implementer's
   cache writes are 1-hour ones, so the setting reaches the CLI.

Nothing here touches ~/.vfg: vfg is integration/realmc/bin/vfg (VFG_HOME in the stand-in), and the
runner's inbox, state and log are in a temp VFG_ORCH_DIR. HOME stays yours: the CLI's own login.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.dirname(HERE)
REPO = os.path.realpath(os.path.dirname(ORCH))
REALMC = os.path.join(REPO, "integration", "realmc")
PYTHON = os.path.join(ORCH, ".venv", "bin", "python")
REAL_HOME = os.path.expanduser("~")
CRATER = os.path.join(REPO, "scripts", "_presets", "crater.py")

T0 = time.time()
RESULTS: list[tuple[str, bool, str]] = []


def say(*parts):
    print("[%4.0f s]" % (time.time() - T0), *parts, flush=True)


def record(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    say("PASS" if ok else "FAIL", name, "-", detail)


def iso_now() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


def realmc_env(name: str) -> tuple[dict, list[str]]:
    """The exports `integration/realmc/env <name>` prints, as a dict; and the names it unsets."""
    out = subprocess.run([sys.executable, os.path.join(REALMC, "realmc.py"), "env", name],
                         stdout=subprocess.PIPE, text=True, check=True).stdout
    env, unset = {}, []
    for line in out.splitlines():
        if line.startswith("unset "):
            unset += line.split()[1:]
        m = re.match(r"export (\w+)='([^']*)'(.*)$", line)
        if m:
            k, v, rest = m.groups()
            env[k] = v + (":" + os.environ.get("PATH", "") if rest == ':"$PATH"' else "")
    return env, unset


class Runner:
    def __init__(self, base_env: dict, root: str, label: str, extra: dict | None = None):
        self.dir = os.path.join(root, label)
        self.orch_dir = os.path.join(self.dir, "orch")
        os.makedirs(self.orch_dir)
        self.log = os.path.join(self.dir, "runner.log")
        self.inbox = os.path.join(self.orch_dir, "inbox.jsonl")
        self.env = {**base_env, "VFG_ORCH_DIR": self.orch_dir, **(extra or {})}
        self.proc = None

    def start(self):
        logf = open(self.log, "ab")
        self.proc = subprocess.Popen([PYTHON, "-m", "orchestrator"], cwd=ORCH, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        logf.close()
        self.wait_log(r" main following ", 120)
        say("runner up, pid %d, log %s" % (self.proc.pid, self.log))

    def text(self) -> str:
        with open(self.log, encoding="utf-8", errors="replace") as f:
            return f.read()

    def wait_log(self, pattern: str, seconds: float) -> str:
        end = time.time() + seconds
        while time.time() < end:
            m = re.search(pattern, self.text())
            if m:
                return m.group(0)
            if self.proc.poll() is not None:
                raise RuntimeError("the runner exited (%s); see %s" % (self.proc.returncode, self.log))
            time.sleep(0.5)
        raise TimeoutError("no %r in %s within %d s" % (pattern, self.log, seconds))

    def send(self, tid: str, task: str, context: str = ""):
        with open(self.inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": tid, "at": iso_now(), "task": task, "context": context}) + "\n")
        say("sent %s: %s" % (tid, task[:100]))

    def state(self) -> dict:
        try:
            with open(os.path.join(self.orch_dir, "state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def task(self, tid: str) -> dict:
        return (self.state().get("tasks") or {}).get(tid) or {}

    def wait_closed(self, tids: list[str], seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if all(self.task(t).get("status") in ("done", "failed") for t in tids):
                for t in tids:
                    r = [x for x in self.task(t).get("replies") or [] if x["status"] != "progress"]
                    say("%s %s: %s" % (t, self.task(t).get("status"), r[-1]["text"] if r else ""))
                return
            time.sleep(1)
        raise TimeoutError("%s not closed within %d s" % (", ".join(tids), seconds))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        say("runner stopped")


# ------------------------------------------------------------------ transcripts

def project_dir() -> str:
    return os.path.join(REAL_HOME, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", REPO))


def calls(path: str) -> list[dict]:
    """A transcript's model calls, in order, one per message id (the last line of each wins):
    {"id", "at", "usage", "content"}."""
    out: dict[str, dict] = {}
    order = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            mid = m.get("id") or d.get("uuid")
            if mid not in out:
                order.append(mid)
            out[mid] = {"id": mid, "at": d.get("timestamp"), "usage": m.get("usage") or {},
                        "content": (out.get(mid, {}).get("content") or []) + (m.get("content") or [])}
    return [out[i] for i in order]


def first_user(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") == "user":
                c = (d.get("message") or {}).get("content")
                return c if isinstance(c, str) else "".join(b.get("text", "") for b in c or [] if isinstance(b, dict))
    return ""


def total_in(u: dict) -> int:
    return (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)


def subagents(session_id: str) -> dict[str, dict]:
    """Each implementer of the session by its description's task id: its transcript and meta."""
    out = {}
    for meta in sorted(glob.glob(os.path.join(project_dir(), session_id, "subagents", "agent-*.meta.json"))):
        with open(meta) as f:
            m = json.load(f)
        tid = (re.match(r"\s*(T\d+)", m.get("description") or "") or [None, None])[1]
        out.setdefault(tid, []).append({"meta": m, "path": meta.replace(".meta.json", ".jsonl")})
    return out


def usage_line(u: dict) -> str:
    cc = u.get("cache_creation") or {}
    return ("in %d, cache read %d, cache write %d (5m %s, 1h %s), out %d" % (
        u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
        cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens"), u.get("output_tokens") or 0))


def vfg(env: dict, *args, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["vfg", *args], env=env, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=120)


# ------------------------------------------------------------------ the run

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="orch", help="the realmc server (integration/realmc/up <name>)")
    ap.add_argument("--server", default="orchtest", help="VFG_SERVER: the made/ folder implementers write in")
    ap.add_argument("--ttl-check", action="store_true", help="also run one job with VFG_SUBAGENT_CACHE_TTL=1h")
    ap.add_argument("--gap", type=float, default=60, help="seconds between the two cache implementers")
    a = ap.parse_args()

    rm_env, unset = realmc_env(a.name)
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env.update(rm_env)
    env.update({"HOME": REAL_HOME, "VFG_REPO": REPO, "VFG_SERVER": a.server})
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "VFG_ORCH_DIR"):
        env.pop(k, None)
    key_file = os.path.join(REALMC, ".server", "shared", "env")
    with open(key_file) as f:
        has_key = any(line.startswith("TYPESAFE_API_KEY=") and len(line.strip()) > len("TYPESAFE_API_KEY=") for line in f)
    say("realmc %s, VFG_SERVER %s, the TypeSafe key in the stand-in: %s" % (a.name, a.server, "yes" if has_key else "no"))

    st = json.loads(vfg(env, "state", "--json").stdout)
    if not any(p["name"] == "Steve" for p in st.get("online") or []):
        subprocess.run([sys.executable, os.path.join(REALMC, "players.py"), "--name", a.name, "spawn", "Steve"], check=True)
    made = os.path.join(REPO, "made", a.server)
    shutil.rmtree(made, ignore_errors=True)
    crater_before = hashlib.sha256(open(CRATER, "rb").read()).hexdigest()
    prefix = vfg(env, "prefix").stdout
    rules = open(os.path.join(ORCH, "prompts", "implementer.md")).read()
    say("vfg prefix: %d characters; implementer.md %d characters" % (len(prefix), len(rules)))

    root = tempfile.mkdtemp(prefix="vfg-live-examples-", dir=os.environ.get("VFG_LIVE_DIR") or None)
    r = Runner(env, root, "main")
    r.start()
    try:
        # 1. the EXAMPLES block reaches the implementer
        r.send("T1", "A test of the implementers' briefs: change nothing in the game and run no command. Start one "
               "implementer and tell it exactly this: 'Near the end of your brief there is a block whose first line "
               "starts with EXAMPLES. Copy that first line exactly, character for character, as the NOTES line of "
               "your report. Run no command and change nothing in the game; RESULT is done.' Then reply done with "
               "the line it quoted, exactly.")
        r.wait_closed(["T1"], 360)
        # 2. two implementers about a minute apart
        r.send("T2", "What time of day is it in the game, and what is the weather? Only look; change nothing.")
        t2 = time.time()
        while time.time() - t2 < a.gap:
            time.sleep(1)
        r.send("T3", "Who is online right now, and where is each player standing? Only look; change nothing.")
        r.wait_closed(["T2", "T3"], 480)
        # 3. saving back, and the library refused
        r.send("T4", "Steve wants a new trick: lightning strikes a few blocks in front of wherever he is looking, once "
               "every 10 seconds, for as long as it runs. Make it a new script for this server, check it, run it on "
               "Steve for about 20 seconds to see that it works, then stop it. Report the script's name.",
               "Steve is online, in the overworld near 0 85 0.")
        r.send("T5", "A maintenance request: in the library preset file scripts/_presets/crater.py, change the default "
               "crater_radius from 8 to 12, by editing that file directly with the Edit tool. If the edit is refused, "
               "report the refusal message word for word and do nothing else.")
        r.wait_closed(["T4", "T5"], 900)
    finally:
        r.stop()
        for line in vfg(env, "script", "list", "--fresh").stdout.splitlines():
            copy = line.split()[0] if line.strip() else ""
            if copy and line.strip() != "nothing running":
                vfg(env, "script", "stop", copy)

    analyse(r, env, a, prefix, rules, crater_before, has_key)

    if a.ttl_check:
        r2 = Runner(env, root, "ttl", {"VFG_SUBAGENT_CACHE_TTL": "1h"})
        r2.start()
        try:
            r2.send("T1", "Is it raining in the game? Only look; change nothing.")
            r2.wait_closed(["T1"], 360)
        finally:
            r2.stop()
        sid = r2.state().get("sessionId")
        imp = (subagents(sid).get("T1") or [None])[0]
        if imp:
            c = calls(imp["path"])
            one_h = sum(((x["usage"].get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0) for x in c)
            five = sum(((x["usage"].get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0) for x in c)
            record("ttl: VFG_SUBAGENT_CACHE_TTL=1h makes the implementer's cache writes 1-hour ones", one_h > 0 and five == 0,
                   "1h written %d, 5m written %d; first call: %s" % (one_h, five, usage_line(c[0]["usage"]) if c else "-"))
        else:
            record("ttl: VFG_SUBAGENT_CACHE_TTL=1h", False, "no implementer transcript found")

    say("logs and state in %s" % root)
    failed = [x for x in RESULTS if not x[1]]
    say("%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


def analyse(r: Runner, env: dict, a, prefix: str, rules: str, crater_before: str, has_key: bool) -> None:
    log = r.text()
    state = r.state()
    sid = state.get("sessionId")
    say("session %s; transcripts in %s" % (sid, project_dir()))
    subs = subagents(sid) if sid else {}

    # how each brief was routed
    for tid in ("T1", "T2", "T3", "T4", "T5"):
        for e in (state.get("tasks") or {}).get(tid, {}).get("implementers") or []:
            route = e.get("route") or {}
            src = ("failed: %s" % route["failed"]) if "failed" in route else (
                "fallback to words (%s)" % route.get("why") if route.get("fallback") else "Jev")
            say("route %s: %s; %d ms %s tok; scripts %s; presets %s; loaded %d files, %d characters, step %d ms" % (
                tid, src, route.get("ms") or 0, route.get("tokens"),
                ", ".join("%s %s" % (s["name"], s["verdict"]) for s in route.get("scripts") or []) or "none",
                ", ".join(p["name"] for p in route.get("presets") or []) or "none",
                len(e.get("loaded") or []), e.get("characters") or 0, e.get("ms") or 0))
    for line in re.findall(r"^.* route route .*$", log, re.M):
        say("router log:", line.split(" route ", 1)[1][:300])

    # 1. the guard: the block in the implementer's first message, quoted back
    m = re.search(r" examples T1: [^;]*; (EXAMPLES[^\n]*)", log)
    first_line = m.group(1).strip() if m else None
    imp = (subs.get("T1") or [None])[0]
    msg = first_user(imp["path"]) if imp else ""
    record("guard: the EXAMPLES block is in the T1 implementer's first message",
           bool(first_line) and first_line in msg and "\nSAVED PLACES:" in msg,
           "first line %r; first message %d characters" % (first_line, len(msg)))
    replies = [x["text"] for x in (state.get("tasks") or {}).get("T1", {}).get("replies") or []]
    report = ""
    if imp:
        for c in reversed(calls(imp["path"])):
            report = "".join(b.get("text", "") for b in c["content"] if b.get("type") == "text")
            if report:
                break
    quoted = bool(first_line) and (first_line in report or any(first_line in t for t in replies))
    record("guard: the implementer quotes the block's first line", quoted,
           "report NOTES: %r; reply: %r" % (next((l for l in report.splitlines() if l.startswith("NOTES")), ""),
                                             replies[-1] if replies else None))
    # the orchestrator's next call is no bigger for it: every call that started implementers, against
    # what their briefs grew by on the way to them
    main_path = os.path.join(project_dir(), "%s.jsonl" % sid)
    mc = calls(main_path) if sid and os.path.exists(main_path) else []
    by_tool_use = {s["meta"].get("toolUseId"): s for group in subs.values() for s in group}
    seen = []
    for i, c in enumerate(mc[:-1]):
        agents = [b for b in c["content"] if b.get("type") == "tool_use" and b.get("name") == "Agent"]
        if not agents:
            continue
        grew = 0
        for b in agents:
            s = by_tool_use.get(b.get("id"))
            if s:
                grew += len(first_user(s["path"])) - len((b.get("input") or {}).get("prompt") or "")
        before, after = total_in(c["usage"]), total_in(mc[i + 1]["usage"])
        seen.append((grew, before, after, ", ".join(str((b.get("input") or {}).get("description")) for b in agents)))
    for grew, before, after, what in seen:
        say("orchestrator: the call starting %s read %d tokens, the next %d (+%d); those briefs grew by %d characters"
            % (what, before, after, after - before, grew))
    if seen:
        grew, before, after, what = max(seen)
        record("guard: the orchestrator's next call is no bigger for the block",
               all(a - b < max(2000, g // 12) for g, b, a, _ in seen),
               "largest: %s, whose briefs grew by %d characters on their way to the implementers; the orchestrator's "
               "call read %d tokens, its next %d (+%d)" % (what, grew, before, after, after - before))
    else:
        record("guard: the orchestrator's next call is no bigger for the block", False, "no Agent call in %s" % main_path)

    # 2. the cache
    firsts = {}
    for tid in ("T1", "T2", "T3", "T4", "T5"):
        for n, s in enumerate(subs.get(tid) or []):
            c = calls(s["path"])
            if c:
                firsts["%s#%d" % (tid, n + 1)] = c[0]
                say("implementer %s#%d (%s): %d calls; first: %s; at %s" % (
                    tid, n + 1, s["meta"].get("description"), len(c), usage_line(c[0]["usage"]), c[0]["at"]))
    t2, t3 = firsts.get("T2#1"), firsts.get("T3#1")
    if t2 and t3:
        shared = t3["usage"].get("cache_read_input_tokens") or 0
        # the system prompt alone, at 4 characters a token (code takes more tokens than that): a floor
        est = (len(rules) + len(prefix)) // 4
        gap = (datetime.fromisoformat(t3["at"].replace("Z", "+00:00"))
               - datetime.fromisoformat(t2["at"].replace("Z", "+00:00"))).total_seconds()
        record("cache: the second implementer reads the shared prefix from the cache",
               shared >= est and (t2["usage"].get("cache_read_input_tokens") or 0) > 0,
               "T2 first call: %s; T3 first call, %.0f s later: %s; the system prompt is %d characters, so at least "
               "%d tokens" % (usage_line(t2["usage"]), gap, usage_line(t3["usage"]), len(rules) + len(prefix), est))
    else:
        record("cache: two implementers' transcripts", False, "T2 %s, T3 %s" % (bool(t2), bool(t3)))
    five_only = all(not ((c["usage"].get("cache_creation") or {}).get("ephemeral_1h_input_tokens")) for c in firsts.values())
    record("cache: implementers write 5-minute cache entries (VFG_SUBAGENT_CACHE_TTL default)", five_only and bool(firsts),
           "; ".join("%s 1h=%s" % (k, (v["usage"].get("cache_creation") or {}).get("ephemeral_1h_input_tokens")) for k, v in firsts.items()))
    rows = re.findall(r"^\S+ usage (implementer|orchestrator) (\S+) in=(\d+) out=(\d+) cache_read=(\d+) cache_write=(\d+).*$", log, re.M)
    record("usage: rows go out with --cache-read and --cache-write", bool(rows),
           "; ".join("%s %s in=%s read=%s write=%s" % (x[0], x[1], x[2], x[4], x[5]) for x in rows[:8]))

    # 3. saving back
    made_scripts = sorted(glob.glob(os.path.join(REPO, "made", a.server, "scripts", "*", "main.py")))
    made_presets = sorted(glob.glob(os.path.join(REPO, "made", a.server, "presets", "*.py")))
    names = [os.path.basename(os.path.dirname(p)) for p in made_scripts] + \
            [os.path.splitext(os.path.basename(p))[0] for p in made_presets]
    made_actions = sorted(os.path.splitext(os.path.basename(p))[0]
                          for p in glob.glob(os.path.join(REPO, "made", a.server, "actions", "*.py")))
    checks = [(n, vfg(env, "script", "check", n)) for n in names]
    checks += [("action " + n, vfg(env, "action", "check", n)) for n in made_actions]
    record("saving back: T4 left a new script in made/%s/scripts/ and its action, and both pass their checks" % a.server,
           bool(made_scripts) and bool(made_actions) and all(c.returncode == 0 for _, c in checks),
           "; ".join("%s: exit %d %s" % (n, c.returncode, (c.stdout + c.stderr).strip().replace("\n", " ")[:160])
                     for n, c in checks) or "nothing in made/%s/" % a.server)
    for p in made_scripts:
        with open(p) as f:
            say("made %s: %s" % (os.path.relpath(p, REPO), f.readline().strip()))
    denied = [l for l in log.splitlines() if " guard denied implementer" in l and ("Edit" in l or "Write" in l)]
    lib_denied = [l for l in denied if "is the library" in l]
    crater_after = hashlib.sha256(open(CRATER, "rb").read()).hexdigest()
    record("saving back: an implementer told to edit a library file is refused, and the file is unchanged",
           bool(lib_denied) and crater_after == crater_before,
           "%d library writes refused (%s); crater.py unchanged: %s" % (
               len(lib_denied), lib_denied[0].split(" guard ", 1)[1][:240] if lib_denied else "-", crater_after == crater_before))
    other = [l for l in denied if l not in lib_denied]
    if other:
        say("other write refusals:", " | ".join(l.split(" guard ", 1)[1][:160] for l in other))
    say("TypeSafe key in the stand-in: %s" % ("yes: routes came from Jev unless a line above says fallback" if has_key
                                               else "no: vfg route fell back to words"))


if __name__ == "__main__":
    sys.exit(main())
