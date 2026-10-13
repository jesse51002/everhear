#!/usr/bin/env python3
"""Incantations end to end on a real 26.3 server: the voice-triggers spec's live test, no EC2.

    REALMC_ROOT=<a .server with the jar> integration/incantations_e2e.py [--name vt] [--keep] [--speech]

Real:  a realmc server (`up NAME --fresh`) and its stand-in box, a Carpet fake player Steve, the
       bridge (`python -m vfg_bridge` on the box's Python 3.12, against the stand-in's vfg.db, feed
       and incantations.json), the kit it starts copies with, and `vfg action run
       incantation_create` (its aliases left to code: VFG_INCANT_ALIAS_MODEL=off).
Posted by hand, as the mod posts them: casts (POST /v1/casts) and lines (POST /v1/lines), each
       window on the wall clock. With --speech, the mod's own `castCheck` also says "Fire ball!"
       (Kokoro) through the real speech pipeline and spotter, as if from the mic, against this
       bridge, and the time from the end of the phrase to the effect in the world is measured.

Two incantations for Steve: "fire ball" (command: a scoreboard point, cooldown 2 s) and "ice
wall" (teleport to 100 100, its player left to the caster). Each check is checked in the world or
in vfg.db. PASS or FAIL per check, with times; exit 1 if any failed. About 40 s, most of it the
server's start and stop. Never part of a unit test run.
"""
import argparse
import datetime as dt
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
sys.path.insert(0, REALMC)

import players  # noqa: E402
import realmc  # noqa: E402

FIRE = {"commands": ["scoreboard players add Steve vt_fire 1"]}
ICE = {"to": "coords", "target": "100 100"}

fails = 0


def ok(label, detail=""):
    print("  PASS  %s%s" % (label, ("  " + detail) if detail else ""))


def bad(label, detail):
    global fails
    fails += 1
    print("  FAIL  %s\n          %s" % (label, detail))


def check(label, cond, detail=""):
    (ok if cond else bad)(label, detail)
    return cond


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bridge:
    """The real bridge, on a free port, against one realmc server's stand-in box."""

    def __init__(self, srv, log_path):
        self.port = free_port()
        self.token = secrets.token_urlsafe(16)
        self.url = "http://127.0.0.1:%d" % self.port
        self.db = os.path.join(srv.vfg, "vfg.db")
        self.feed = os.path.join(srv.vfg, "feed.log")
        env = dict(os.environ)
        env.update({
            "VFG_DB": self.db, "VFG_FEED": self.feed, "VFG_WATCH": os.path.join(srv.vfg, "watch.json"),
            "VFG_STATE": os.path.join(srv.vfg, "state"), "VFG_CONSOLE_LOG": srv.console,
            "VFG_BRIDGE_BIND": "127.0.0.1", "VFG_BRIDGE_PORT": str(self.port), "VFG_CLIENT_TOKEN": self.token,
            "VFG_KIT": os.path.join(srv.vfg, "scripts", "_lib"),
            "VFG_INCANTATIONS": os.path.join(srv.vfg, "incantations.json"),
            "VFG_BASE": srv.vfg, "VFG_MC": srv.mc,
            "PYTHONPATH": os.path.join(REPO, "bridge") + os.pathsep + os.path.join(srv.vfg, "lib"),
        })
        python = os.path.join(srv.vfg, "python", "bin", "python3")
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen([python, "-m", "vfg_bridge"], env=env, cwd=srv.vfg,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if self.get("/v1/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise SystemExit("the bridge did not answer on %s; its log: %s" % (self.url, log_path))

    def _call(self, method, path, body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw.strip() else None)

    def get(self, path):
        return self._call("GET", path)

    def cast(self, player, phrase, start, end):
        return self._call("POST", "/v1/casts", {
            "client_cast_id": str(uuid.uuid4()), "player_uuid": "", "player_name": player,
            "phrase": phrase, "started_at": iso(start), "ended_at": iso(end)})

    def line(self, player, text, start, end):
        return self._call("POST", "/v1/lines", {
            "client_line_id": str(uuid.uuid4()), "player_uuid": "", "player_name": player,
            "text": text, "started_at": iso(start), "ended_at": iso(end), "peak_dbfs": -10.0,
            "mean_dbfs": -25.0})

    def row(self, sql, *args):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return c.execute(sql, args).fetchone()

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def vfg_env(name):
    """The exports `realmc env NAME` prints, as a dict over this process's environment."""
    out = subprocess.run([sys.executable, os.path.join(REALMC, "realmc.py"), "env", name],
                         capture_output=True, text=True, check=True).stdout
    env = dict(os.environ)
    for line in out.splitlines():
        if line.startswith("unset "):
            for k in line.split()[1:]:
                env.pop(k, None)
        elif line.startswith("export "):
            k, v = line[len("export "):].split("=", 1)
            if k == "PATH":
                env["PATH"] = os.path.join(REALMC, "bin") + os.pathsep + env.get("PATH", "")
            else:
                env[k] = v.strip("'")
    env["VFG_INCANT_ALIAS_MODEL"] = "off"
    return env


def vfg(env, *words):
    r = subprocess.run(["vfg", *words], env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout.strip().splitlines() or [""])[-1], r.stderr.strip()


def score(p):
    out = p.cmd("scoreboard players get Steve vt_fire")
    try:
        return int(out.split(" has ")[1].split()[0])
    except (IndexError, ValueError):
        return 0


def wait_for(fn, timeout, every=0.01):
    """Poll fn() until it is truthy: (its value, seconds), or (None, timeout)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        got = fn()
        if got:
            return got, time.perf_counter() - t0
        time.sleep(every)
    return None, timeout


def run_checks(br, p, env):
    # The stand-in's vfg.db and feed outlive `up --fresh` (it resets the world): count from here.
    since = iso(time.time())
    fed_from = os.path.getsize(br.feed) if os.path.exists(br.feed) else 0
    print("== the list, as the mod fetches it")
    code, doc = br.get("/v1/incantations?player=Steve")
    phrases = sorted(i["phrase"] for i in (doc or {}).get("incantations", []))
    check("Steve's list holds his two", code == 200 and phrases == ["fire ball", "ice wall"], "%s %s" % (code, phrases))
    code, doc = br.get("/v1/incantations?player=Alex")
    check("Alex's list is empty", code == 200 and doc["incantations"] == [], "%s %s" % (code, doc))
    check("and its server_phrases are every player's phrases, Steve's two (his mod's speech-to-text hotwords)",
          code == 200 and doc.get("server_phrases") == ["fire ball", "ice wall"], "%s %s" % (code, doc))
    t0 = time.time()
    code, _ = br.get("/v1/incantations?player=Steve&after=%d&wait=1" % doc["version"])
    check("an unchanged list is 304 after its wait", code == 304, "%s in %.2f s" % (code, time.time() - t0))

    print("== a spotter's cast, to the effect")
    now = time.time()
    t0 = time.perf_counter()
    code, ans = br.cast("Steve", "fire ball", now - 0.6, now - 0.1)
    answered = time.perf_counter() - t0
    check("fire ball fires", code == 202 and ans and ans.get("status") == "fired",
          "%s %s, answered in %.0f ms" % (code, ans, answered * 1000))
    got, secs = wait_for(lambda: score(p) >= 1, 5)
    check("its point lands in the world", got, "%.0f ms from the post to the point" % ((secs + answered) * 1000))
    first = (now - 0.6, now - 0.1)

    fired_fire = time.time()

    print("== cooldowns and the 1 s between casts")
    now = time.time()
    code, ans = br.cast("Steve", "fire ball", first[0] + 0.1, first[1] - 0.1)
    check("the same words again are a duplicate", ans and ans.get("status") == "duplicate", str(ans))
    # The phrase said again, half a second after the first: a new window, the mod's clock ahead.
    code, ans = br.cast("Steve", "fire ball", first[1] + 0.5, first[1] + 0.9)
    check("fire ball said again at once is cooldown", ans and ans.get("status") == "cooldown", str(ans))
    code, ans = br.cast("Steve", "ice wall", now - 0.4, now - 0.05)
    check("ice wall within 1 s of fire ball is cooldown", ans and ans.get("status") == "cooldown", str(ans))
    code, ans = br.cast("Alex", "fire ball", now - 0.4, now - 0.05)
    check("Alex saying Steve's phrase casts nothing", code == 404, "%s %s" % (code, ans))

    print("== the drop rule, on lines overlapping the cast")
    code, ans = br.line("Steve", "fire ball", first[0] - 0.2, first[1] + 0.1)
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", (ans or {}).get("id"))
    check('"fire ball" is dropped before Jev', row and row["dropped"] == "cast", str(dict(row) if row else row))
    code, ans = br.line("Steve", "fire ball and build me a wall", first[0] - 0.2, first[1] + 1.5)
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", (ans or {}).get("id"))
    check('"fire ball and build me a wall" goes on, the cast noted',
          row and row["dropped"] is None and row["cast_id"], str(dict(row) if row else row))

    print("== a caster field: ice wall moves whoever casts it")
    time.sleep(max(0.0, 1.1 - (time.time() - now)))
    now = time.time()
    code, ans = br.cast("Steve", "ice wall", now - 0.6, now - 0.1)
    fired_ice = time.time()
    check("ice wall fires after the 1 s gap", ans and ans.get("status") == "fired", str(ans))
    got, secs = wait_for(lambda: abs(p.pos("Steve")[0] - 100) < 40, 15, every=0.1)
    check("Steve lands near x=100", got, "%.1f s, at %s" % (secs, p.pos("Steve")))

    print("== Layer 2: a line the spotter missed, matched on its sounds")
    # fire ball's own 2 s cooldown, and 1 s since ice wall, both passed
    time.sleep(max(0.0, fired_fire + 2.1 - time.time(), fired_ice + 1.1 - time.time()))
    before = score(p)
    now = time.time()
    code, ans = br.line("Steve", "fire bowl now", now - 0.9, now - 0.1)
    row = br.row("SELECT dropped FROM lines WHERE id = ?", (ans or {}).get("id"))
    got, secs = wait_for(lambda: score(p) > before, 5)
    check('"fire bowl now" casts fire ball from the transcript', got, "%.0f ms from the post" % (secs * 1000))
    check("and the line is dropped", row and row["dropped"] == "cast", str(dict(row) if row else row))
    cast = br.row("SELECT layer, status FROM casts WHERE layer = 'transcript' ORDER BY at DESC LIMIT 1")
    check("its cast is recorded as transcript", cast and cast["status"] == "fired", str(dict(cast) if cast else cast))
    code, ans = br.cast("Steve", "fire ball", now - 0.8, now - 0.3)
    check("the spotter's late cast of the same words is a duplicate", ans and ans.get("status") == "duplicate", str(ans))

    print("== the record: events and the feed")
    with sqlite3.connect(br.db) as c:
        events = [r[0] for r in c.execute("SELECT text FROM events WHERE kind = 'cast' AND at >= ? ORDER BY at",
                                          (since,))]
    check("three fired casts are events", len(events) == 3, "; ".join(events))
    with open(br.feed, encoding="utf-8") as f:
        f.seek(fed_from)
        fed = [ln.strip() for ln in f if " cast Steve | " in ln]
    check("and three GAME cast lines in the feed", len(fed) == 3, fed[-1] if fed else "none")


def run_audio(br, p, say):
    """castCheck speaks `say` with Kokoro and plays it through the mod's real pipeline and spotter,
    as if from the mic, against this bridge: the cast's effect lands in the world, timed from
    the end of the phrase (the cast's ended_at, on the audio clock), and the line is dropped."""
    print("== speech: castCheck says %r through the mod's pipeline and spotter" % say)
    since = iso(time.time())
    before = score(p)
    mod = os.path.join(REPO, "client-mod")
    env = dict(os.environ, JAVA_HOME=os.environ.get("VFG_JAVA_HOME", "/usr/lib/jvm/java-25-openjdk-amd64"))
    args = "--bridge %s --token %s --player Steve --say '%s'" % (br.url, br.token, say)
    proc = subprocess.Popen([os.path.join(mod, "gradlew"), "-q", "castCheck", "--args=" + args], cwd=mod,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    got, _ = wait_for(lambda: score(p) > before, 180)
    landed = time.time()
    out = proc.communicate(timeout=180)[0]
    print("\n".join("          | " + ln for ln in out.strip().splitlines()[-10:]))
    cast = br.row("SELECT ended_at, status, heard FROM casts WHERE layer = 'spotter' AND at >= ?"
                  " ORDER BY at LIMIT 1", since)
    ended = dt.datetime.strptime(cast["ended_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=dt.timezone.utc).timestamp() if cast else None
    check("the spotter's cast fires", cast and cast["status"] == "fired", str(dict(cast) if cast else cast))
    check("its effect lands in the world", got and ended,
          "%.2f s from the end of the phrase to the point" % (landed - ended) if got and ended else "")
    deadline = time.time() + 5
    line = None
    while time.time() < deadline and line is None:
        line = br.row("SELECT text, dropped FROM lines WHERE player_name = 'Steve' AND source = 'voice'"
                      " AND at >= ? ORDER BY at DESC LIMIT 1", since)
        time.sleep(0.1)
    check("its line is dropped before Jev", line and line["dropped"] == "cast", str(dict(line) if line else line))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="vt")
    ap.add_argument("--keep", action="store_true", help="leave the server and the bridge's log")
    ap.add_argument("--speech", action="store_true",
                    help="also say 'Fire ball!' through the mod's real pipeline and spotter (castCheck)")
    a = ap.parse_args(argv)

    srv = realmc.Server(a.name)
    print("== up %s --fresh" % a.name)
    t0 = time.time()
    if subprocess.run([os.path.join(REALMC, "up"), a.name, "--fresh"]).returncode:
        print("  FAIL  up")
        return 1
    ok("up", "%.1f s" % (time.time() - t0))
    env = vfg_env(a.name)
    p = players.Players(a.name)
    br = None
    try:
        p.spawn("Steve")
        p.cmd("scoreboard objectives add vt_fire dummy")
        br = Bridge(srv, os.path.join(srv.dir, "bridge.log"))
        ok("the bridge", br.url)
        print("== vfg action run incantation_create, twice")
        for phrase, action, values in (("fire ball", "command", FIRE), ("ice wall", "teleport", ICE)):
            t1 = time.time()
            code, line, err = vfg(env, "action", "run", "incantation_create", "player=Steve",
                                  "phrase=" + phrase, "action=" + action, "values=" + json.dumps(values),
                                  "cooldown_s=2")
            check('"%s" → %s' % (phrase, action), code == 0, "%.1f s  %s %s" % (time.time() - t1, line[:160], err[:200]))
        run_checks(br, p, env)
        if a.speech:
            time.sleep(2.1)  # fire ball's cooldown
            run_audio(br, p, "Fire ball!")
    finally:
        if br:
            br.stop()
        p.close()
        if not a.keep:
            subprocess.run([os.path.join(REALMC, "down"), a.name])
    print("== %s" % ("PASS" if not fails else "%d FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
