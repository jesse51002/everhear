#!/usr/bin/env python3
"""Incantations end to end on a real 26.3 server, each one a game engine binding: the bridge's
casts through the engine, no EC2.

    REALMC_ROOT=<a .server with the jar> VFG_ACCEPT_EULA=true integration/incantations_e2e.py [--name vt] [--keep] [--speech]

Real:  a realmc server (`up NAME --fresh`) with Carpet's Steve and Alex, and its stand-in box; the
       game engine installed there and run as the vfg-engine unit runs it, and the bridge as
       vfg-bridge runs it, against the engine's export and socket (integration/engine_e2e.py's
       install, Engine and Bridge); `vfg engine reload|grant|bind|unbind` through realmc's vfg, no
       OpenRouter key (a binding's aliases from code alone). The content is the two classes below,
       written fresh into made/NAME/content/, which the reload deploys.
Posted by hand, as the mod and the Jev filter post them: casts (POST /v1/casts), lines (POST
       /v1/lines) and the Jev filter's cast check (POST /v1/jev/casts; no Jev filter runs, so a line
       stays unjudged), each window on the wall clock. With --speech, the mod's own `castCheck`
       also says "Fire ball!" (Kokoro) through the real speech pipeline and spotter, as if from the
       mic, against this bridge, and the time from the end of the phrase to the effect is measured.

Two incantations for Steve: "fire ball", bound to the ability vt_point (a point on the scoreboard
vt_fire each cast, its cooldown 2 s, the ability's), and "ice wall", bound to the scenario vt_move
with its `who` left to the caster (a teleport 12 blocks across the arena, the binding's cooldown
2 s). Checked in the world, in vfg.db and in the feed: the list and its long poll, a cast to its
effect, the duplicate window, the engine's cooldown and no gap between a player's casts, another
player's phrase, the drop rule, Layer 2 from a transcript, the Jev filter's cast path (a long line
kept, a short one dropped, a late one stale), the events and feed lines, and an unbind. PASS or
FAIL per check, with times; exit 1 if any failed. About a minute, most of it the server's start
and stop. Never part of a unit test run.
"""
import argparse
import glob
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import engine_e2e as e2e  # noqa: E402  (puts integration/realmc on the path: players, realmc)
from engine_e2e import iso, players, realmc, wait_for  # noqa: E402

REALMC = e2e.REALMC
CONTENT = {
    "vt_point.py": '''"""VT Point, a test's triggered ability (integration/incantations_e2e.py): each cast adds one
to its caster's score on the scoreboard vt_fire."""

from vfg_engine import Ability, AbilityConfig


class VtPoint(Ability):
    class Config(AbilityConfig):
        summary = "One point on the scoreboard vt_fire for each cast (a test's)."
        damage = 0
        duration_s = 0
        charge_s = 0
        cooldown_s = 2
        scale_per_point = 0

    def on_cast(self, ctx, ev):
        self.command("scoreboard players add %s vt_fire 1" % ctx.user.name)
''',
    "vt_move.py": '''"""VT Move, a test's one-shot scenario (integration/incantations_e2e.py): moves args.who to
args.to, then ends."""

from vfg_engine import Args, Field, Place, Player, Scenario, ScenarioConfig


class VtMove(Scenario):
    class Config(ScenarioConfig):
        summary = "Moves one player to a place, at once (a test's)."
        duration_s = 5

    class Args(Args):
        who: Player = Field("", description="who is moved: one online player's name")
        to: Place = Field("0 100 0", description="where to: a saved place, x y z, or a player")

    def start(self, ctx):
        self.teleport(self.args.who, self.args.to)
        self.result("moved %s" % self.args.who, to=list(self.args.to.pos))
        self.end()
''',
}
CLASSES = sorted(n[:-3] for n in CONTENT)
COOLDOWN_S = 2.0   # vt_point's cooldown_s, and the ice wall binding's

fails = 0


def ok(label, detail=""):
    print("  PASS  %s%s" % (label, ("  " + detail) if detail else ""), flush=True)


def bad(label, detail):
    global fails
    fails += 1
    print("  FAIL  %s\n          %s" % (label, detail), flush=True)


def check(label, cond, detail=""):
    (ok if cond else bad)(label, detail)
    return bool(cond)


class Bridge(e2e.Bridge):
    """engine_e2e's bridge, with the lines the mod posts and the Jev filter's cast check."""

    def __init__(self, srv, engine, log_path):
        super().__init__(srv, engine, log_path)
        self.feed = os.path.join(srv.vfg, "feed.log")

    def line(self, player, text, start, end):
        return self.call("POST", "/v1/lines", {
            "client_line_id": str(uuid.uuid4()), "player_uuid": "", "player_name": player,
            "text": text, "started_at": iso(start), "ended_at": iso(end), "peak_dbfs": -10.0,
            "mean_dbfs": -25.0})

    def jev_cast(self, line_id, phrase, keep=False):
        body = {"line_id": line_id, "phrase": phrase}
        if keep:
            body["keep"] = True
        return self.call("POST", "/v1/jev/casts", body)


def score(p):
    out = p.cmd("scoreboard players get Steve vt_fire")
    m = re.search(r" has (-?\d+) ", out)
    return int(m.group(1)) if m else 0


def write_content(name, srv):
    """made/<name>/content/ on this PC, fresh: the two classes above; the box's folder emptied, so
    the reload's deploy sends it whole."""
    import shutil
    content = os.path.join(REPO, "made", name, "content")
    shutil.rmtree(content, ignore_errors=True)
    os.makedirs(content)
    for f, source in CONTENT.items():
        with open(os.path.join(content, f), "w") as out:
            out.write(source)
    box = os.path.join(srv.vfg, "made", name, "content")
    shutil.rmtree(box, ignore_errors=True)
    os.makedirs(box)
    return content, box


def bind(vfg, wall):
    print("== the content loaded, the two bound for Steve")
    code, out, err = vfg("engine", "reload")
    loaded = re.search(r"^loaded: (.*)$", out, re.M)
    names = sorted(x.strip() for x in loaded.group(1).split(",")) if loaded else []
    check("vfg engine reload: deployed, %s loaded, no errors" % " and ".join(CLASSES),
          code == 0 and names == CLASSES and "refused" not in out, (out + err).replace("\n", " | ")[:400])
    code, out, err = vfg("engine", "grant", "Steve", "vt_point")
    check("vfg engine grant Steve vt_point", code == 0, out + err)
    code, out, err = vfg("engine", "bind", "Steve", "fire ball", "vt_point")
    check('"fire ball" bound to the ability vt_point, its aliases from code (no OpenRouter key)',
          code == 0 and "now casts vt_point" in out and "aliases from code only" in err, (out + " | " + err)[:300])
    code, out, err = vfg("engine", "bind", "Steve", "ice wall", "--scenario", "vt_move", "who=caster",
                         "to=%g %g %g" % wall, "--cooldown", "2")
    check('"ice wall" bound to the scenario vt_move, who=caster, to=%g %g %g, cooldown 2 s' % wall,
          code == 0, (out + " | " + err)[:300])
    code, doc, err = vfg.json("engine", "incantations", "--player", "Steve")
    got = {i.get("phrase"): (i.get("ability"), i.get("scenario"), i.get("args"), i.get("cooldown_s"))
           for i in doc.get("incantations") or []}
    check("vfg engine incantations: the ability's cooldown for fire ball, the binding's own for ice wall",
          got == {"fire ball": ("vt_point", None, {}, COOLDOWN_S),
                  "ice wall": (None, "vt_move", {"who": "caster", "to": "%g %g %g" % wall}, COOLDOWN_S)}, str(got))


def run_checks(br, p, vfg, home, wall):
    # The stand-in's vfg.db and feed outlive `up --fresh` (it resets the world): count from here.
    since = iso(time.time())
    fed_from = os.path.getsize(br.feed) if os.path.exists(br.feed) else 0
    print("== the list, as the mod fetches it, and its long poll")
    code, doc = br.call("GET", "/v1/incantations?player=Steve")
    have = {i["phrase"]: i.get("cooldown_s") for i in (doc or {}).get("incantations", [])}
    check("Steve's list holds his two, each with its cooldown", code == 200 and have == {
        "fire ball": COOLDOWN_S, "ice wall": COOLDOWN_S}, "%s %s" % (code, have))
    version = (doc or {}).get("version")
    code, doc = br.call("GET", "/v1/incantations?player=Alex")
    check("Alex's list is empty", code == 200 and doc["incantations"] == [], "%s %s" % (code, doc))
    check("and its server_phrases are every player's phrases, Steve's two (his mod's speech-to-text hotwords)",
          code == 200 and doc.get("server_phrases") == ["fire ball", "ice wall"], "%s %s" % (code, doc))
    t0 = time.time()
    code, _ = br.call("GET", "/v1/incantations?player=Steve&after=%d&wait=1" % version)
    check("an unchanged list is 304 after its wait", code == 304, "%s in %.2f s" % (code, time.time() - t0))
    polled = {}

    def poll():
        t = time.perf_counter()
        polled["code"], polled["doc"] = br.call("GET", "/v1/incantations?player=Steve&after=%d&wait=20" % version,
                                                timeout=30)
        polled["at"] = time.perf_counter()
        polled["took"] = polled["at"] - t
    waiting = threading.Thread(target=poll)
    waiting.start()
    time.sleep(0.5)
    t_bind = time.perf_counter()
    code, out, err = vfg("engine", "bind", "Steve", "rock wall", "--scenario", "vt_move", "who=caster",
                         "to=%g %g %g" % home)
    waiting.join(30)
    phrases = sorted(i["phrase"] for i in (polled.get("doc") or {}).get("incantations", []))
    check("a bind while the mod waits answers its poll at once: 200, the next version, rock wall in the list",
          code == 0 and polled.get("code") == 200 and polled["doc"].get("version", 0) > version
          and phrases == ["fire ball", "ice wall", "rock wall"],
          "%s %s; the poll answered %.2f s after the bind (held %.1f s)" % (
              polled.get("code"), phrases, polled.get("at", 0) - t_bind, polled.get("took", 0)))
    code, out, err = vfg("engine", "unbind", "Steve", "rock wall")
    code2, doc = br.call("GET", "/v1/incantations?player=Steve")
    check("  unbound again: Steve's list is his two", code == 0 and sorted(
        i["phrase"] for i in doc.get("incantations", [])) == ["fire ball", "ice wall"], "%s %s" % (out + err, doc))

    print("== a spotter's cast, to the effect")
    now = time.time()
    t0 = time.perf_counter()
    code, ans, ms = br.cast("Steve", "fire ball", now - 0.6, now - 0.1)
    check("fire ball fires in the engine", code == 202 and ans and ans.get("status") == "fired",
          "%s %s, answered in %.0f ms" % (code, ans, ms))
    got, secs = wait_for(lambda: score(p) >= 1, 5, every=0.01)
    check("  its point lands in the world", got, "%.0f ms from the post to the point" % (
        (time.perf_counter() - t0) * 1000 if got else -1))
    row = br.row("SELECT incantation_id, action, layer, status FROM casts WHERE id = ?", (ans or {}).get("id"))
    check("  recorded as engine:steve.fire_ball, its action the ability, layer spotter",
          row == {"incantation_id": "engine:steve.fire_ball", "action": "vt_point", "layer": "spotter",
                  "status": "fired"}, str(row))
    first = (now - 0.6, now - 0.1)
    fired_fire = time.time()

    print("== the duplicate window, the engine's cooldown, and no gap between a player's casts")
    code, ans, ms = br.cast("Steve", "fire ball", first[0] + 0.1, first[1] - 0.1)
    check("the same words again are a duplicate", ans and ans.get("status") == "duplicate", str(ans))
    # The phrase said again, half a second after the first: a new window, the mod's clock ahead.
    code, ans, ms = br.cast("Steve", "fire ball", first[1] + 0.5, first[1] + 0.9)
    check("fire ball said again at once is the ability's cooldown, in the engine's words",
          ans and ans.get("status") == "cooldown" and re.match(r"^[\d.]+ s left of its cooldown$",
                                                                ans.get("error") or ""), str(ans))
    before = p.pos("Steve")
    now = time.time()
    code, ans, ms = br.cast("Steve", "ice wall", now - 0.4, now - 0.05)
    check("ice wall at once after fire ball fires: the bridge keeps no gap between a player's casts",
          ans and ans.get("status") == "fired", str(ans))
    got, secs = wait_for(lambda: abs(p.pos("Steve")[0] - wall[0]) < 1.5 and abs(p.pos("Steve")[2] - wall[2]) < 1.5,
                         10, every=0.05)
    check("  vt_move moved whoever cast it: Steve, from %.0f %.0f to %.0f %.0f" % (
        before[0], before[2], wall[0], wall[2]), got, "after %.2f s, at %s" % (secs or 0, p.pos("Steve")))
    row = br.row("SELECT incantation_id, action FROM casts WHERE id = ?", (ans or {}).get("id"))
    check("  recorded as engine:steve.ice_wall, its action the scenario",
          row == {"incantation_id": "engine:steve.ice_wall", "action": "vt_move"}, str(row))
    code, ans, ms = br.cast("Alex", "fire ball", now - 0.4, now - 0.05)
    check("Alex saying Steve's phrase casts nothing: 404", code == 404, "%s %s" % (code, ans))

    print("== the drop rule, on lines overlapping the cast")
    code, ans = br.line("Steve", "fire ball", first[0] - 0.2, first[1] + 0.1)
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", (ans or {}).get("id"))
    check('"fire ball" is dropped before Jev', row and row["dropped"] == "cast", str(row))
    code, ans = br.line("Steve", "fire ball and build me a wall", first[0] - 0.2, first[1] + 1.5)
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", (ans or {}).get("id"))
    check('"fire ball and build me a wall" goes on, the cast noted',
          row and row["dropped"] is None and row["cast_id"], str(row))

    print("== Layer 2: a line the spotter missed, matched on its sounds")
    time.sleep(max(0.0, fired_fire + COOLDOWN_S + 0.1 - time.time()))
    before = score(p)
    now = time.time()
    t0 = time.perf_counter()
    code, ans = br.line("Steve", "fire bowl now", now - 0.9, now - 0.1)
    row = br.row("SELECT dropped FROM lines WHERE id = ?", (ans or {}).get("id"))
    got, secs = wait_for(lambda: score(p) > before, 5, every=0.01)
    check('"fire bowl now" casts fire ball from the transcript', got,
          "%.0f ms from the post to the point" % ((time.perf_counter() - t0) * 1000 if got else -1))
    check("  and the line is dropped", row and row["dropped"] == "cast", str(row))
    cast = br.row("SELECT layer, status FROM casts WHERE layer = 'transcript' ORDER BY at DESC LIMIT 1")
    check("  its cast is recorded as transcript, fired", cast == {"layer": "transcript", "status": "fired"}, str(cast))
    fired_l2 = time.time()
    code, ans, ms = br.cast("Steve", "fire ball", now - 0.8, now - 0.3)
    check("the spotter's late cast of the same words is a duplicate", ans and ans.get("status") == "duplicate", str(ans))

    print("== the Jev filter's cast check (POST /v1/jev/casts, from the box itself)")
    # Its window, the line's 2.3 s, must start clear of Layer 2's casts, the spotter's duplicate
    # among them, widened by the slack (0.3 s): 3 s on, which is past the cooldown too.
    time.sleep(max(0.0, fired_l2 + 3.0 - time.time()))
    before = score(p)
    now = time.time()
    long_text = "okay everybody stand back and watch this fire ball"   # 9 words: past Layer 2's 6
    code, ans = br.line("Steve", long_text, now - 2.4, now - 0.1)
    long_id = (ans or {}).get("id")
    row = br.row("SELECT dropped, cast_id, jev_class FROM lines WHERE id = ?", long_id)
    check("a long line with the phrase at its end: stored, unjudged, no cast (Layer 2 is for 6 words at most)",
          code in (200, 201, 202) and row == {"dropped": None, "cast_id": None, "jev_class": None},
          "%s %s; %s" % (code, ans, row))
    t0 = time.perf_counter()
    code, ans = br.jev_cast(long_id, "fire ball", keep=True)
    check("Jev's cast of it, keep: fired, the line kept", code == 202 and ans.get("status") == "fired"
          and ans.get("line") == "kept", "%s %s, answered in %.0f ms" % (code, ans, (time.perf_counter() - t0) * 1000))
    got, secs = wait_for(lambda: score(p) > before, 5, every=0.01)
    check("  its point lands in the world", got, "%.0f ms from the post to the point" % (
        (time.perf_counter() - t0) * 1000 if got else -1))
    cast = br.row("SELECT layer, status, client_cast_id, incantation_id FROM casts WHERE id = ?", (ans or {}).get("id"))
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", long_id)
    check("  the cast is layer jev, jev:<the line's id>; the line keeps it as its cast_id",
          cast == {"layer": "jev", "status": "fired", "client_cast_id": "jev:%s" % long_id,
                   "incantation_id": "engine:steve.fire_ball"}
          and row == {"dropped": None, "cast_id": (ans or {}).get("id")}, "%s; %s" % (cast, row))
    code2, again = br.jev_cast(long_id, "fire ball", keep=True)
    check("  asked again, it answers what the first did, nothing cast twice",
          code2 == 202 and again.get("id") == ans.get("id") and score(p) == before + 1, "%s %s" % (code2, again))
    fired_jev = time.time()
    time.sleep(max(0.0, fired_jev + COOLDOWN_S + 0.1 - time.time()))
    before = score(p)
    now = time.time()
    code, ans = br.line("Steve", "tybo", now - 0.6, now - 0.1)   # how speech-to-text heard "hot fireball"
    short_id = (ans or {}).get("id")
    row = br.row("SELECT dropped FROM lines WHERE id = ?", short_id)
    check('a short mishearing, "tybo": stored, no Layer 2 cast', row == {"dropped": None}, str(row))
    code, ans = br.jev_cast(short_id, "fire ball")
    got, secs = wait_for(lambda: score(p) > before, 5, every=0.01)
    row = br.row("SELECT dropped, cast_id FROM lines WHERE id = ?", short_id)
    check("Jev's sure cast of it: fired, its point in the world, the line dropped with the cast as its cast_id",
          code == 202 and ans.get("status") == "fired" and ans.get("line") == "dropped" and got
          and row == {"dropped": "cast", "cast_id": ans.get("id")}, "%s %s; %s" % (code, ans, row))
    time.sleep(1.2)   # its window clear of the last cast's, widened by the slack: never a duplicate
    now = time.time()
    code, ans = br.line("Steve", "tybo", now - 0.6, now - 0.1)
    late_id = (ans or {}).get("id")
    time.sleep(2.3)
    code, ans = br.jev_cast(late_id, "fire ball")
    row = br.row("SELECT dropped FROM lines WHERE id = ?", late_id)
    check("a Jev cast of a line received 2.3 s before is stale, never sent to the engine; its line dropped",
          code == 202 and ans.get("status") == "stale" and "past the 2 s a Jev cast may be" in (ans.get("error") or "")
          and row == {"dropped": "cast"}, "%s %s; %s" % (code, ans, row))

    print("== the record: events and the feed")
    with sqlite3.connect(br.db) as c:
        events = [r[0] for r in c.execute("SELECT text FROM events WHERE kind = 'cast' AND at >= ? ORDER BY at",
                                          (since,))]
    want = ['Steve cast "fire ball": vt_point, fired in the engine', 'Steve cast "ice wall": vt_move, fired in the engine'] \
        + ['Steve cast "fire ball": vt_point, fired in the engine'] * 3
    check("five fired casts are events: the spotter's two, Layer 2's, Jev's two", events == want, "; ".join(events))
    with open(br.feed, encoding="utf-8") as f:
        f.seek(fed_from)
        fed = [ln.strip().split(" | ", 1)[-1] for ln in f if re.match(r"^GAME#\d+ \S+ cast Steve \| ", ln)]
    check("  and five GAME cast lines in the feed, the same words", fed == want, "; ".join(fed) or "none")
    check("Steve's score: one point a fired fire ball, four", score(p) == 4, "vt_fire %d" % score(p))

    print("== an unbind")
    code, out, err = vfg("engine", "unbind", "Steve", "ice wall")
    code2, doc = br.call("GET", "/v1/incantations?player=Steve")
    now = time.time()
    code3, ans, ms = br.cast("Steve", "ice wall", now - 0.4, now - 0.05)
    check("vfg engine unbind Steve 'ice wall': his list is fire ball alone, and a cast of it is 404",
          code == 0 and [i["phrase"] for i in doc.get("incantations", [])] == ["fire ball"] and code3 == 404,
          "%s; %s; %s %s" % (out + err, doc, code3, ans))


def find_java_home():
    """VFG_JAVA_HOME, else the newest JDK under ~/.local/share/jdk, else the system's JDK 25."""
    if os.environ.get("VFG_JAVA_HOME"):
        return os.environ["VFG_JAVA_HOME"]
    jdks = sorted(glob.glob(os.path.expanduser("~/.local/share/jdk/jdk-*")))
    return jdks[-1] if jdks else "/usr/lib/jvm/java-25-openjdk-amd64"


def run_audio(br, p, say):
    """castCheck speaks `say` with Kokoro and plays it through the mod's real pipeline and spotter,
    as if from the mic, against this bridge: the cast's effect lands in the world, timed from
    the end of the phrase (the cast's ended_at, on the audio clock), and the line is dropped."""
    print("== speech: castCheck says %r through the mod's pipeline and spotter" % say)
    since = iso(time.time())
    before = score(p)
    mod = os.path.join(REPO, "client-mod")
    env = dict(os.environ, JAVA_HOME=find_java_home())
    args = "--bridge %s --token %s --player Steve --say '%s'" % (br.url, br.token, say)
    proc = subprocess.Popen([os.path.join(mod, "gradlew"), "-q", "castCheck", "--args=" + args], cwd=mod,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    got, _ = wait_for(lambda: score(p) > before, 180)
    landed = time.time()
    out = proc.communicate(timeout=180)[0]
    print("\n".join("          | " + ln for ln in out.strip().splitlines()[-10:]))
    cast = br.row("SELECT ended_at, status, heard FROM casts WHERE layer = 'spotter' AND at >= ?"
                  " ORDER BY at LIMIT 1", since)
    ended = e2e.dt.datetime.strptime(cast["ended_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=e2e.dt.timezone.utc).timestamp() if cast else None
    check("the spotter's cast fires", cast and cast["status"] == "fired", str(cast))
    check("its effect lands in the world", got and ended,
          "%.2f s from the end of the phrase to the point" % (landed - ended) if got and ended else "")
    deadline = time.time() + 5
    line = None
    while time.time() < deadline and line is None:
        line = br.row("SELECT text, dropped FROM lines WHERE player_name = 'Steve' AND source = 'voice'"
                      " AND at >= ? ORDER BY at DESC LIMIT 1", since)
        time.sleep(0.1)
    check("its line is dropped before Jev", line and line["dropped"] == "cast", str(line))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="vt")
    ap.add_argument("--keep", action="store_true", help="leave the server up, and the engine's and bridge's logs")
    ap.add_argument("--speech", action="store_true",
                    help="also say 'Fire ball!' through the mod's real pipeline and spotter (castCheck)")
    a = ap.parse_args(argv)

    srv = realmc.Server(a.name)
    print("== up %s --fresh" % a.name, flush=True)
    t0 = time.time()
    if subprocess.run([os.path.join(REALMC, "up"), a.name, "--fresh"]).returncode:
        print("  FAIL  up")
        return 1
    ok("up", "%.1f s" % (time.time() - t0))
    p = players.Players(a.name)
    vfg = e2e.Vfg(e2e.vfg_env(a.name))
    engine = br = None
    try:
        r, secs = e2e.install(srv)
        enabled = p.cmd("datapack list enabled")
        check("the engine installed as deploy.sh engine installs it: its venv, the datapack loaded",
              r.returncode == 0 and "file/everhear" in enabled,
              "%.1f s; %s" % (secs, (r.stdout + r.stderr).strip().replace("\n", " | ")[:300]))
        content, box_content = write_content(a.name, srv)
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vfg-engine-%s.sock" % a.name)
        link = os.path.join(srv.vfg, "engine", "engine.sock")
        os.symlink(sock, link)
        engine = e2e.Engine(srv, sock, box_content, os.path.join(srv.dir, "engine-process.log"))
        e2e.stray_engine(sock, engine.dir)
        secs = engine.start()
        ok("the engine runs as vfg-engine does", "its socket answered in %.2f s" % secs)
        br = Bridge(srv, engine, os.path.join(srv.dir, "bridge.log"))
        ok("the bridge, against the engine's export and socket", br.url)
        arena = p.arena()
        home = (arena["x"] + 0.5, arena["y"], arena["z"] + 0.5)
        wall = (home[0] + 12, home[1], home[2] + 12)
        p.spawn("Steve", at=home)
        p.spawn("Alex", at=(home[0] + 4, home[1], home[2]))
        p.cmd("scoreboard objectives add vt_fire dummy")
        bind(vfg, wall)
        run_checks(br, p, vfg, home, wall)
        if a.speech:
            time.sleep(COOLDOWN_S + 0.1)
            run_audio(br, p, "Fire ball!")
    finally:
        if br:
            br.stop()
        if engine:
            code, secs = engine.stop()
            print("== the engine stopped: exit %s in %.2f s" % (code, secs))
        try:
            p.kill_all()
        except Exception:
            pass
        p.close()
        if not a.keep:
            subprocess.run([os.path.join(REALMC, "down"), a.name])
    print("== %s" % ("PASS" if not fails else "%d FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
