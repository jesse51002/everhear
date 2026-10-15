#!/usr/bin/env python3
"""The game engine end to end on a real 26.3 server, run as it runs on the box: the game engine
spec's Done list, checked in the world. No EC2.

    REALMC_ROOT=<a realmc .server with the jar> VFG_ACCEPT_EULA=true integration/engine_e2e.py [--name ge-e2e] [--keep]

Real:  a realmc server (`up NAME --fresh`) with Carpet's Steve and Alex, and its stand-in box. The
       engine is installed into the stand-in as `deploy.sh engine` installs it on the box:
       engine/vfg_engine/, the datapack rsynced to <vfg>/engine/, then
       engine/deploy/install.sh (the venv on the box's Python 3.12 with pydantic by hash, the
       datapack copied into the world and loaded). The engine runs as the vfg-engine unit runs it,
       `<vfg>/engine/.venv/bin/python -m vfg_engine` in <vfg>/engine, with the stand-in's paths.
       The bridge runs as vfg-bridge does (`python -m vfg_bridge` on the box's Python, against the
       stand-in's vfg.db and the engine's export and socket).
Driven: `vfg engine …` and `vfg action run engine_*` through realmc's vfg (VFG_SERVER=NAME), whose
       content deploy sends made/NAME/content/ (written fresh each run: 
       test classes below) to <vfg>/made/NAME/content/, the engine's VFG_ENGINE_CONTENT; the
       bridge's HTTP API, as the mod calls it; Carpet's Steve and Alex (realmc/players.py).

A Unix socket's address holds 107 bytes, and the stand-in's <vfg>/engine/engine.sock is longer, so
the engine's socket is in $XDG_RUNTIME_DIR and <vfg>/engine/engine.sock links to it: vfg's remote
program reaches it through its folder (toolkit/remote/engine.py), the bridge by VFG_ENGINE_SOCKET.
No OpenRouter key reaches vfg (OPEN_ROUTER_API is taken out of its environment, and the stand-in's
HOME has none), so a bind makes its aliases from code alone and says so in a note: recorded.

Every step checks the world (attributes and their modifiers, health, entity counts, gamerules, the
inventory, a scoreboard), not only the engine's replies. PASS or FAIL per check, then the tick
times `status` reported along the way and the latencies measured; exit 1 if any check failed.
About 90 s on a quiet PC. Never part of a unit test run.
"""
import argparse
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
ENGINE_SRC = os.path.join(REPO, "engine")
E2E_CONTENT_SRC = os.path.join(HERE, "engine_e2e_content")
sys.path.insert(0, REALMC)

import players  # noqa: E402
import realmc  # noqa: E402

STAFF = 'custom_data~{everhear:{item:"fire_staff"}}'
FLOAT = r"-?\d+(?:\.\d+)?(?:E-?\d+)?"

# The test's own content, beside the four examples, written into made/<server>/content/.
CONTENT = {
    "e2e_retort.py": '''"""E2E Retort, a test's passive ability (integration/engine_e2e.py): each melee hit its user
lands counts one on the scoreboard e2e_hits and, 0.6 s later, strikes the target again, by its
user. The engine never hands its own damage back as a hit, so one hit is one strike: no loop."""

from vfg_engine import Ability, AbilityConfig, Field, Hit, UNTIL_STOPPED


class E2eRetort(Ability):
    class Config(AbilityConfig):
        summary = "Each melee hit strikes the target again 0.6 s later (a test's)."
        strike: float = Field(2, ge=0, le=20)
        damage = 2
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    def start(self, ctx):
        self.listen(Hit, "on_hit")

    def on_hit(self, ctx, ev):
        self.command("scoreboard players add %s e2e_hits 1" % ctx.user.name)
        self.after(0.6, "strike", ev.target)

    def strike(self, ctx, target):
        self.damage(target, self.config.strike, "minecraft:generic")
''',
    "e2e_slow.py": '''"""E2E Slow, a test's passive ability (integration/engine_e2e.py): its tick sleeps 200 ms, four
times the engine's 50 ms cut. The reload checks refuse a call named sleep; this one hides it, so
the cut is what stops it: only this instance ends, and the loop goes on."""

import time

from vfg_engine import Ability, AbilityConfig, UNTIL_STOPPED

PAUSE = time.sleep


class E2eSlow(Ability):
    every = 0.5

    class Config(AbilityConfig):
        summary = "Sleeps 200 ms every half second (a test's: the cut ends it)."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    def tick(self, ctx):
        PAUSE(0.2)
''',
    "e2e_roar.py": '''"""E2E Roar, a test's passive ability for actors (integration/engine_e2e.py): a puff of smoke
over whoever has it, every second."""

from vfg_engine import Ability, AbilityConfig, UNTIL_STOPPED


class E2eRoar(Ability):
    every = 1

    class Config(AbilityConfig):
        summary = "A puff of smoke every second (a test's)."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    def tick(self, ctx):
        x, y, z = ctx.user.pos
        self.particle("minecraft:smoke", (x, y + 2, z), count=3)
''',
    "e2e_brute.py": '''"""E2E Brute, a test's actor (integration/engine_e2e.py): a husk, 30 health, 1.25 the size, and
no attack damage, so the test's players outlive it however slow the server is."""

from vfg_engine import Actor, ActorConfig


class E2eBrute(Actor):
    class Config(ActorConfig):
        summary = "A big husk with 30 health (a test's)."
        entity_type = "minecraft:husk"
        health = 30
        size = 1.25
        damage = 0
        name = "E2E Brute"
''',
    "e2e_horde.py": '''"""E2E Horde, a test's scenario (integration/engine_e2e.py): at args.at it spawns an E2E
Brute, grants it E2E Roar, and spawns a plain pig beside it. Everything it spawns is its own,
so stopping it leaves nothing."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig, State, UNTIL_STOPPED


class E2eHorde(Scenario):
    class Config(ScenarioConfig):
        summary = "One brute with a roar and one pig (a test's)."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        at: Place = Field("8 85 8", description="where the brute stands: a saved place, x y z, or a player")

    class State(State):
        brute: str = ""
        pig: str = ""

    def start(self, ctx):
        x, y, z = self.args.at.pos
        self.state.brute = self.spawn("e2e_brute", at=(x, y, z))
        self.grant(self.state.brute, "e2e_roar")
        self.state.pig = self.spawn("minecraft:pig", at=(x + 2, y, z))
        self.result("a brute and a pig at %g %g %g" % (x, y, z), brute=self.state.brute)   # it runs on
''',
    "e2e_blast.py": '''"""E2E Blast, a test's triggered ability (integration/engine_e2e.py): an explosion round its
caster that spares the caster (self_damage off) and hits everyone else in its radius once."""

from vfg_engine import Ability, AbilityConfig, Field


class E2eBlast(Ability):
    class Config(AbilityConfig):
        summary = "A blast round its caster that spares the caster (a test's)."
        blast: float = Field(4, ge=0, le=20)
        radius: float = Field(5, ge=1, le=8)
        damage = 4
        duration_s = 1
        charge_s = 0
        cooldown_s = 3
        scale_per_point = 0
        self_damage = False

    def on_cast(self, ctx, ev):
        self.explode(at=ctx.user.pos, radius=self.config.radius, damage=self.config.blast)
        self.end()
''',
    "e2e_rule.py": '''"""E2E Rule, a test's scenario (integration/engine_e2e.py): holds the gamerule args.rule at
args.value, priority args.priority, while it runs."""

from vfg_engine import Args, Field, Scenario, ScenarioConfig, UNTIL_STOPPED


class E2eRule(Scenario):
    class Config(ScenarioConfig):
        summary = "Holds a gamerule while it runs (a test's)."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        rule: str = Field("mob_griefing", description="the gamerule, by its 26.3 name")
        value: bool = Field(True, description="what it is held at")
        priority: int = Field(0, ge=0, le=100, description="higher wins; on a tie the newer")

    def start(self, ctx):
        self.hold(self.args.rule, self.args.value, self.args.priority)
        self.result("holding %s at %s" % (self.args.rule, self.args.value))   # it runs on
''',
}
CLASSES = sorted(["fire_staff", "e2e_fireball", "giant", "shrunk"] + [n[:-3] for n in CONTENT])

fails = 0
ticks = []        # (when, p50, p95, tick) from status along the way
measured = []     # (what, value)


def ok(label, detail=""):
    print("  PASS  %s%s" % (label, ("  " + detail) if detail else ""))


def bad(label, detail):
    global fails
    fails += 1
    print("  FAIL  %s\n          %s" % (label, detail))


def check(label, cond, detail=""):
    (ok if cond else bad)(label, detail)
    return bool(cond)


def note(what, value):
    measured.append((what, value))


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout, every=0.05):
    """Poll fn() until it is truthy: (its value, seconds), or (its last value, None)."""
    t0 = time.perf_counter()
    got = None
    while time.perf_counter() - t0 < timeout:
        got = fn()
        if got:
            return got, time.perf_counter() - t0
        time.sleep(every)
    return got, None


# ------------------------------------------------------------------ the parts, as on the box

class Engine:
    """vfg-engine on the stand-in: the unit's command, in <vfg>/engine, with the stand-in's paths."""

    def __init__(self, srv, sock, content, log_path):
        self.dir = os.path.join(srv.vfg, "engine")
        self.sock = sock
        self.log_path = log_path
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("VFG_") and k != "OPEN_ROUTER_API"}
        self.env.update({
            "PYTHONPATH": self.dir, "PYTHONUNBUFFERED": "1",          # the unit's Environment=
            "VFG_ENGINE_CONTENT": content,
            "VFG_MC_PROPERTIES": os.path.join(srv.mc, "server.properties"),
            "VFG_ENGINE_STATE": os.path.join(self.dir, "state.json"),
            "VFG_ENGINE_EXPORT": os.path.join(self.dir, "incantations.json"),
            "VFG_ENGINE_LOG": os.path.join(self.dir, "engine.log"),
            "VFG_ENGINE_SOCKET": sock,
        })
        self.proc = None

    def start(self, timeout=30):
        """Started; the seconds until its socket answered status."""
        log = open(self.log_path, "a")
        t0 = time.perf_counter()
        self.proc = subprocess.Popen([os.path.join(self.dir, ".venv", "bin", "python"), "-m", "vfg_engine"],
                                     cwd=self.dir, env=self.env, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        while time.perf_counter() - t0 < timeout:
            if self.proc.poll() is not None:
                raise SystemExit("the engine exited %s at start; its output: %s" % (self.proc.returncode, self.log_path))
            try:
                if self.ask("status").get("ok"):
                    return time.perf_counter() - t0
            except OSError:
                pass
            time.sleep(0.05)
        raise SystemExit("the engine's socket did not answer in %d s; its output: %s" % (timeout, self.log_path))

    def ask(self, op, args=None, timeout=10.0):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout + 2)
            s.connect(self.sock)
            s.sendall((json.dumps({"op": op, "args": args or {}, "timeout_s": timeout}) + "\n").encode())
            return json.loads(s.makefile("rb").readline())

    def stop(self, timeout=20):
        """SIGTERM, as systemd stops it: (exit code, seconds)."""
        if self.proc is None or self.proc.poll() is not None:
            return (self.proc.returncode if self.proc else None), 0.0
        t0 = time.perf_counter()
        self.proc.send_signal(signal.SIGTERM)
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        return code, time.perf_counter() - t0


class Bridge:
    """The real bridge, on a free port, against the stand-in box and the engine's socket."""

    def __init__(self, srv, engine, log_path):
        self.port = free_port()
        self.token = secrets.token_urlsafe(16)
        self.url = "http://127.0.0.1:%d" % self.port
        self.db = os.path.join(srv.vfg, "vfg.db")
        env = {k: v for k, v in os.environ.items() if not k.startswith("VFG_")}
        env.update({
            "VFG_DB": self.db, "VFG_FEED": os.path.join(srv.vfg, "feed.log"),
            "VFG_WATCH": os.path.join(srv.vfg, "watch.json"), "VFG_STATE": os.path.join(srv.vfg, "state"),
            "VFG_CONSOLE_LOG": os.path.join(srv.mc, "console.log"),
            "VFG_BRIDGE_BIND": "127.0.0.1", "VFG_BRIDGE_PORT": str(self.port), "VFG_CLIENT_TOKEN": self.token,
            "VFG_BASE": srv.vfg,
            "VFG_ENGINE_SOCKET": engine.sock, "VFG_ENGINE_EXPORT": engine.env["VFG_ENGINE_EXPORT"],
            "PYTHONPATH": os.path.join(REPO, "bridge") + os.pathsep + os.path.join(srv.vfg, "lib"),
        })
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen([os.path.join(srv.vfg, "python", "bin", "python3"), "-m", "vfg_bridge"],
                                     env=env, cwd=srv.vfg, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if self.call("GET", "/v1/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise SystemExit("the bridge did not answer on %s; its log: %s" % (self.url, log_path))

    def call(self, method, path, body=None, timeout=10):
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

    def cast(self, player, phrase, start, end):
        """POST /v1/casts as the mod posts it: (code, answer, ms to the answer)."""
        t0 = time.perf_counter()
        code, ans = self.call("POST", "/v1/casts", {
            "client_cast_id": str(uuid.uuid4()), "player_uuid": "", "player_name": player,
            "phrase": phrase, "started_at": iso(start), "ended_at": iso(end)})
        return code, ans, (time.perf_counter() - t0) * 1000

    def row(self, sql, *args):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            r = c.execute(sql, args).fetchone()
            return dict(r) if r else None

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def vfg_env(name):
    """The exports `realmc env NAME` prints, over this process's environment, with VFG_SERVER=NAME
    and no OpenRouter key."""
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
    env["VFG_SERVER"] = name
    env.pop("OPEN_ROUTER_API", None)
    env.pop("VFG_INCANT_ALIAS_MODEL", None)
    return env


class Vfg:
    def __init__(self, env):
        self.env = env

    def __call__(self, *words, input=None, timeout=180):
        """(exit code, stdout, stderr)."""
        r = subprocess.run(["vfg", *words], env=self.env, input=input, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def json(self, *words, **kw):
        code, out, err = self(*words, "--json", **kw)
        try:
            return code, json.loads(out.splitlines()[-1]) if out else {}, err
        except ValueError:
            return code, {"unreadable": out}, err

    def action(self, *words):
        code, out, err = self("action", "run", *words)
        try:
            return code, json.loads(out.splitlines()[-1]), err
        except (ValueError, IndexError):
            return code, {"unreadable": out}, err


# ------------------------------------------------------------------ reading the world

class World:
    """Reads Minecraft over RCON (realmc's client, which holds the box's RCON lock)."""

    def __init__(self, p):
        self.p = p
        self.cmd = p.cmd

    def attr(self, who, attribute):
        out = self.cmd("attribute %s minecraft:%s get" % (who, attribute))
        m = re.search(r" is (%s)$" % FLOAT, out)
        return float(m.group(1)) if m else None

    def mods(self, who, attribute):
        """{modifier id: amount} on the attribute; {} for none."""
        out = self.cmd('data get entity %s attributes[{id:"minecraft:%s"}].modifiers' % (who, attribute))
        got = {}
        for part in re.findall(r"\{([^{}]*)\}", out):
            i = re.search(r'id: "([^"]+)"', part)
            a = re.search(r"amount: (%s)d" % FLOAT, part)
            if i and a:
                got[i.group(1)] = float(a.group(1))
        return got

    def health(self, sel):
        m = re.search(r"data: (%s)f" % FLOAT, self.cmd("data get entity %s Health" % sel))
        return float(m.group(1)) if m else None

    def count(self, sel):
        out = self.cmd("execute if entity %s" % sel)
        m = re.search(r"Count: (\d+)", out)
        return int(m.group(1)) if m else 0

    def holds(self, who, slot="container.*", what="*[%s]" % STAFF):
        m = re.search(r"Count: (\d+)", self.cmd("execute if items entity %s %s %s" % (who, slot, what)))
        return int(m.group(1)) if m else 0

    def gamerule(self, name):
        m = re.search(r"currently set to (\S+)", self.cmd("gamerule %s" % name))
        return m.group(1) if m else None

    def score(self, who, objective):
        m = re.search(r"has (-?\d+) ", self.cmd("scoreboard players get %s %s" % (who, objective)))
        return int(m.group(1)) if m else 0


def status(engine, when):
    s = engine.ask("status")
    tm = s.get("tick_ms") or {}
    ticks.append((when, tm.get("p50"), tm.get("p95"), s.get("tick")))
    return s


def instances(vfg, **kw):
    words = ["engine", "instances"]
    for k, v in kw.items():
        words += ["--" + k, v]
    code, doc, err = vfg.json(*words)
    return doc.get("instances") or []


def meta_row(vfg, cid):
    code, doc, err = vfg.json("engine", "meta", cid)
    rows = doc.get("rows") or []
    return rows[0] if rows else {}


def own(row, name):
    """A class's own field in a meta row."""
    return (row.get("fields") or {}).get(name)


def damage_at(row, power):
    """Its damage at power 0, 10 or 20, as meta gives it."""
    return (row.get("damage") or {}).get(str(power))


# ------------------------------------------------------------------ the steps

def step1_status_and_reload(vfg, engine):
    print("== 1. status, then the content reloaded")
    code, out, err = vfg("engine", "status")
    check("vfg engine status", code == 0 and "tick" in out and "Alex, Steve" in out, out.replace("\n", " | ")[:300])
    ctl = subprocess.run([os.path.join(engine.dir, ".venv", "bin", "python"), "-m", "vfg_engine.ctl"],
                         input='{"op": "status"}', capture_output=True, text=True, env=engine.env, timeout=20)
    got = json.loads(ctl.stdout) if ctl.stdout.strip() else {}
    check("python -m vfg_engine.ctl status, on the box's side", ctl.returncode == 0 and got.get("ok") is True,
          "exit %d, tick %s" % (ctl.returncode, got.get("tick")))
    code, out, err = vfg("engine", "reload")
    loaded = re.search(r"^loaded: (.*)$", out, re.M)
    names = sorted(x.strip() for x in loaded.group(1).split(",")) if loaded else []
    check("vfg engine reload: deployed, every class loaded, no errors",
          code == 0 and names == CLASSES and "refused" not in out, out.replace("\n", " | ")[:400])
    code, doc, err = vfg.json("engine", "classes")
    check("vfg engine classes lists all %d" % len(CLASSES),
          sorted(c.get("id") for c in doc.get("classes") or []) == CLASSES, str([c.get("id") for c in doc.get("classes") or []]))
    code, ov, err = vfg.json("engine", "overview")
    check("vfg engine overview --json: the classes and the instances in one call",
          code == 0 and ov.get("ok") is True and sorted(c.get("id") for c in ov.get("classes") or []) == CLASSES
          and isinstance(ov.get("instances"), list), "%d classes, %d instances" % (
              len(ov.get("classes") or []), len(ov.get("instances") or [])))
    s = status(engine, "after the reload")
    check("status: both players, %d classes" % len(CLASSES),
          s.get("players") == ["Alex", "Steve"] and s.get("classes") == len(CLASSES), str(s)[:200])


def step2_items(vfg, engine, w, p, spots):
    print("== 2. items: the Fire Staff")
    steve, target = spots["steve"], spots["target"]
    code, out, err = vfg("engine", "give", "Steve", "fire_staff", "--base", "stick")
    check("a give on the wrong base is refused", code == 1 and "carrot_on_a_stick" in err, err[:200])
    check("  and Steve has no staff", w.holds("Steve") == 0, "")
    t0 = time.perf_counter()
    code, out, err = vfg("engine", "give", "Steve", "fire_staff")
    check("vfg engine give Steve fire_staff", code == 0, out + err)
    check("  the stack is in Steve's main hand, its id in custom_data",
          w.holds("Steve", "weapon.mainhand") == 1, w.cmd("data get entity Steve SelectedItem")[:200])
    got, secs = wait_for(lambda: w.mods("Steve", "movement_speed").get("everhear:item/fire_staff"), 3)
    check("  everhear:item/fire_staff is on his movement_speed, read back", got == 0.2,
          "%s, %.2f s from the give" % (w.mods("Steve", "movement_speed"), time.perf_counter() - t0))
    note("give -> the item's modifier on the holder (incl. vfg's SSH round trip)", "%.2f s" % (time.perf_counter() - t0))
    speed = w.attr("Steve", "movement_speed")
    check("  his speed reads 0.12 (0.1 x 1.2)", speed is not None and abs(speed - 0.12) < 1e-6, str(speed))
    staff_ins = [i for i in instances(vfg, kind="item") if i.get("id") == "fire_staff"]
    check("  one instance, Steve's", [i.get("user") for i in staff_ins] == ["Steve"], str(staff_ins))

    code, out, err = vfg("engine", "grant", "Steve", "e2e_retort")
    check("E2E Retort granted to Steve (it answers each of his melee hits)", code == 0, out + err)

    row = meta_row(vfg, "e2e_fireball")
    declared = (row.get("required") or {}).get("damage")          # meta shows a formula at power 1
    print("          fireball declares %s at power 1 (meta: %s at 0, %s at 10, %s at 20)" % (
        declared, damage_at(row, 0), damage_at(row, 10), damage_at(row, 20)))
    p.look_at("Steve", target[0], target[1] + 1.0, target[2])
    time.sleep(0.3)
    h0 = w.health("@n[tag=e2e_target]")
    trace = []
    t0 = time.perf_counter()
    p.use("Steve")
    first, secs = wait_for(lambda: (w.health("@n[tag=e2e_target]") or h0) < h0, 3, every=0.02)
    check("Steve's right-click casts Fireball: the zombie he looks at is hurt", first,
          "first burn %.2f s after the click" % (secs or 0))
    if secs:
        note("right-click -> the first burn on the zombie (Fireball burns once a second)", "%.2f s" % secs)
    while time.perf_counter() - t0 < 3.6:
        trace.append((round(time.perf_counter() - t0, 1), w.health("@n[tag=e2e_target]")))
        time.sleep(0.2)
    h1 = w.health("@n[tag=e2e_target]")
    dealt = h0 - h1
    # natural armour 2 takes 1.6% of the explosion's part; on_fire passes armour
    check("  it took about the declared damage", declared and 0.97 * declared <= dealt <= declared + 0.01,
          "%.2f of %.2f declared (%.0f%%); health %s" % (dealt, declared or 0, 100 * dealt / (declared or 1),
                                                         " ".join("%s:%.2f" % t for t in trace[::3])))
    p.use("Steve")                                   # ~3.7 s after the first: inside its 5 s cooldown
    t_second = time.perf_counter()
    time.sleep(1.8)
    h2 = w.health("@n[tag=e2e_target]")
    check("  a second click within 5 s is refused by the cooldown: nothing lands", h2 == h1,
          "%.1f s after the first click, health %.2f then %.2f" % (t_second - t0, h1, h2))
    time.sleep(max(0.0, t0 + 5.3 - time.perf_counter()))
    p.use("Steve")
    third, secs = wait_for(lambda: (w.health("@n[tag=e2e_target]") or h2) < h2, 2.5, every=0.05)
    check("  a click after the 5 s casts again", third, "%.1f s after the first click" % (time.perf_counter() - t0))
    time.sleep(3.3)                                  # its run ends
    check("the engine's damage by Steve is never handed back as his hit: no Retort fired",
          w.score("Steve", "e2e_hits") == 0, "e2e_hits %d after two Fireballs by Steve" % w.score("Steve", "e2e_hits"))

    # a real melee hit: Retort answers it once, by Steve, and its strike does not come back as a hit
    p.cmd("tp @n[tag=e2e_target] %g %g %g" % (steve[0], steve[1], steve[2] + 2.0))
    p.look_at("Steve", steve[0], steve[1] + 1.0, steve[2] + 2.0)
    time.sleep(0.4)
    h3 = w.health("@n[tag=e2e_target]")
    p.attack("Steve")
    got, secs = wait_for(lambda: w.score("Steve", "e2e_hits") >= 1, 2)
    time.sleep(3.0)
    h4 = w.health("@n[tag=e2e_target]")
    check("one melee hit by Steve: Retort answered it once, and its strike did not loop",
          w.score("Steve", "e2e_hits") == 1 and h3 - h4 >= 2.5,
          "e2e_hits %d 3 s on; the zombie lost %.2f (his punch 1, the strike 2)" % (w.score("Steve", "e2e_hits"), h3 - h4))
    p.cmd("tp @n[tag=e2e_target] %g %g %g" % target)

    print("== 2. items: dropped, then picked up by Alex")
    p.look("Steve", 0, 0)
    first_iid = staff_ins[0]["instance"] if staff_ins else None
    p.drop("Steve")
    gone, secs = wait_for(lambda: "everhear:item/fire_staff" not in w.mods("Steve", "movement_speed"), 3)
    check("Steve drops it: the modifier is gone from him", gone, "%s after %.2f s" % (w.mods("Steve", "movement_speed"), secs or 0))
    dropped = '@n[type=item,nbt={Item:{components:{"minecraft:custom_data":{everhear:{item:"fire_staff"}}}}}]'
    wait_for(lambda: w.cmd("data get entity %s OnGround" % dropped).endswith("1b"), 3)   # where it lands
    m = re.findall(FLOAT + "d", w.cmd("data get entity %s Pos" % dropped))
    at = [float(x[:-1]) for x in m[:3]] if len(m) >= 3 else [steve[0], steve[1], steve[2] + 3]
    p.tp("Alex", at[0], at[1], at[2])
    got, secs = wait_for(lambda: w.mods("Alex", "movement_speed").get("everhear:item/fire_staff"), 6)
    check("Alex picks it up: everhear:item/fire_staff on his movement_speed", got == 0.2,
          "%s, %.1f s after he got there (a dropped stack waits 2 s)" % (w.mods("Alex", "movement_speed"), secs or 0))
    mine = [i for i in instances(vfg, kind="item") if i.get("id") == "fire_staff"]
    check("  his own instance, and Steve's ended", [i.get("user") for i in mine] == ["Alex"]
          and mine[0].get("instance") != first_iid, "%s (Steve's was %s)" % ([(i["instance"], i["user"]) for i in mine], first_iid))
    p.tp("Alex", *spots["alex"])
    status(engine, "after the Fire Staff")


def step3_marks(vfg, engine, w, p):
    print("== 3. marks: Giant and Shrunk on Steve, beside a vanilla Speed II")
    w.cmd("effect give Steve minecraft:speed 300 1")
    speed0 = w.mods("Steve", "movement_speed")
    check("Speed II gives its own modifier", speed0.get("minecraft:effect.speed") is not None, str(speed0))
    t_applied = time.perf_counter()
    c1 = vfg("engine", "apply", "Steve", "giant", "--seconds", "6")
    c2 = vfg("engine", "apply", "Steve", "shrunk")
    check("vfg engine apply Steve giant (6 s), then shrunk", c1[0] == 0 and c2[0] == 0, c1[1] + " / " + c2[1])
    got, secs = wait_for(lambda: w.attr("Steve", "scale") == 1.5, 2)
    scale = w.mods("Steve", "scale")
    check("Giant plus Shrunk: Steve's scale reads 1.5", got,
          "%s, modifiers %s" % (w.attr("Steve", "scale"), scale))
    check("  each by its own id", set(scale) == {"everhear:mark/giant", "everhear:mark/shrunk"}, str(scale))
    code, out, err = vfg("engine", "clear", "Steve", "shrunk")
    got, secs = wait_for(lambda: w.attr("Steve", "scale") == 2.0, 2)
    check("Shrunk ended early: exactly 2", got and set(w.mods("Steve", "scale")) == {"everhear:mark/giant"},
          "%s %s" % (w.attr("Steve", "scale"), w.mods("Steve", "scale")))
    check("  Speed II's modifier untouched", w.mods("Steve", "movement_speed") == speed0, str(w.mods("Steve", "movement_speed")))
    got, secs = wait_for(lambda: w.attr("Steve", "scale") == 1.0, 9, every=0.1)
    ended = time.perf_counter() - t_applied
    check("Giant expires: its id goes, scale 1", got and not w.mods("Steve", "scale"),
          "%.1f s after it was applied for 6 s; %s" % (ended, w.mods("Steve", "scale")))
    note("Giant applied for 6 s -> its id gone from Minecraft", "%.2f s" % ended)
    check("  no mark instance left", not instances(vfg, kind="mark"), str(instances(vfg, kind="mark")))
    check("  Speed II and its modifier still there, as given",
          w.mods("Steve", "movement_speed") == speed0 and "minecraft:speed" in w.cmd("data get entity Steve active_effects"),
          str(w.mods("Steve", "movement_speed")))
    w.cmd("effect clear Steve minecraft:speed")       # the test's own potion, not the engine's
    status(engine, "after the marks")


def step4_rewrite(vfg, engine, content, box_content, marker):
    print("== 4. reload and rewrite: fireball's Config")
    pc = os.path.join(content, "e2e_fireball.py")
    boxed = os.path.join(box_content, "e2e_fireball.py")
    code, out, err = vfg("engine", "grant", "Steve", "e2e_fireball")
    running = [i["instance"] for i in instances(vfg, kind="ability", user="Steve") if i.get("id") == "e2e_fireball"]
    check("Steve granted fireball: a running instance", code == 0 and len(running) == 1, str(running))
    code, doc, err = vfg.json("engine", "config", "get", "e2e_fireball")
    source = doc.get("source") or ""
    check("vfg engine config get fireball", code == 0 and "class Config(AbilityConfig)" in source,
          "%s lines %s to %s" % (doc.get("file"), doc.get("start"), doc.get("end")))
    to12 = source.replace("explosion_base: float = Field(8,", "explosion_base: float = Field(12,")
    code, out, err = vfg("engine", "config", "set", "e2e_fireball", input=to12)
    m = re.search(r"(\d+) running instances? switched", out)
    check("config set: explosion 8 -> 12, reloaded for everyone", code == 0 and m and int(m.group(1)) >= 1,
          (out + err)[:300])
    row = meta_row(vfg, "e2e_fireball")
    check("  meta shows it: explosion_base 12, damage at power 0 15",
          own(row, "explosion_base") == 12 and damage_at(row, 0) == 15,
          "explosion_base=%s damage@0=%s" % (own(row, "explosion_base"), damage_at(row, 0)))
    still = [i["instance"] for i in instances(vfg, kind="ability", user="Steve") if i.get("id") == "e2e_fireball"]
    check("  the running instance switched in place", still == running, str(still))

    with open(pc) as f:
        before = f.read()
    dropped = "".join(ln for ln in to12.splitlines(True) if "radius: float = Field(" not in ln)
    code, out, err = vfg("engine", "config", "set", "e2e_fireball", input=dropped)
    with open(pc) as f:
        after_pc = f.read()
    with open(boxed) as f:
        after_box = f.read()
    check("a rewrite that drops a field is refused", code == 1 and "radius" in err, err[:200])
    check("  the PC file and the box's are as they were", after_pc == before and after_box == before, "")
    # a rewrite comes from anyone, by voice: code in it must never run, in a formula or in a value
    touch = "__import__('os').system('touch %s')" % marker
    in_formula = to12.replace("return self.burn_s + 1", "return %s * 0 + self.burn_s + 1" % touch)
    in_value = to12.replace("radius: float = Field(3,", "radius: float = Field(%s * 0 + 3," % touch)
    for what, text, word in (("code in a formula", in_formula, "formula"), ("__import__ in a value", in_value, "value")):
        code, out, err = vfg("engine", "config", "set", "e2e_fireball", input=text)
        with open(pc) as f:
            after_pc = f.read()
        with open(boxed) as f:
            after_box = f.read()
        check("a rewrite with %s is refused before it is sent" % what,
              text != to12 and code == 1 and word in err and "before it was sent" in err, err[:240])
        check("  the PC file and the box's unchanged, and the code never ran",
              after_pc == before and after_box == before and not os.path.exists(marker), "")
    out_of_bounds = to12.replace("explosion_base: float = Field(12,", "explosion_base: float = Field(40,")
    code, out, err = vfg("engine", "config", "set", "e2e_fireball", input=out_of_bounds)
    with open(pc) as f:
        after_pc = f.read()
    with open(boxed) as f:
        after_box = f.read()
    check("a rewrite the engine's checks refuse (explosion 40, over its bound of 30)", code == 1 and "30" in err, err[:240])
    check("  the PC file is put back, and the box's", after_pc == before and after_box == before, "")
    row = meta_row(vfg, "e2e_fireball")
    check("  the old version keeps running: explosion_base 12", own(row, "explosion_base") == 12,
          str(own(row, "explosion_base")))
    code, out, err = vfg("engine", "config", "undo", "e2e_fireball")
    row = meta_row(vfg, "e2e_fireball")
    with open(pc) as f:
        undone = f.read()
    check("config undo: explosion_base 8 again, on the PC, the box and in the engine",
          code == 0 and own(row, "explosion_base") == 8 and "Field(8," in undone and damage_at(row, 0) == 11,
          (out + err)[:200])
    with open(os.path.join(content, ".history", "log.jsonl")) as f:
        log = [json.loads(x) for x in f if x.strip()]
    check("  every change and refusal is in .history/log.jsonl",
          [(x["op"], x["ok"]) for x in log] == [("set", True)] + [("set", False)] * 4 + [("undo", True)],
          str([(x["op"], x["ok"]) for x in log]))
    status(engine, "after the rewrites")


def step5_incantations(vfg, engine, br, w, p, spots):
    print("== 5. incantations: bound in the engine, cast through the bridge")
    target = spots["target"]
    code, out, err = vfg("engine", "bind", "Steve", "fire ball", "e2e_fireball")
    check("vfg engine bind Steve 'fire ball' e2e_fireball (after the grant)", code == 0 and "now casts e2e_fireball" in out, out)
    nokey = "aliases from code only: the alias call failed" in err
    check("  with no OpenRouter key the alias call fails as a note; the code's aliases stand, exit 0",
          nokey and "aliases fireball" in out, err[:240])
    code, doc = br.call("GET", "/v1/incantations?player=Steve")
    mine = [i for i in (doc or {}).get("incantations", []) if i.get("phrase") == "fire ball"]
    check("GET /v1/incantations?player=Steve lists it", code == 200 and len(mine) == 1 and mine[0].get("cooldown_s") == 5,
          "%s %s" % (code, mine))
    code, doc = br.call("GET", "/v1/incantations?player=Alex")
    check("  Alex's list has none", code == 200 and not doc.get("incantations"), "%s %s" % (code, doc))
    p.look_at("Steve", target[0], target[1] + 1.0, target[2])
    time.sleep(0.4)
    h0 = w.health("@n[tag=e2e_target]")
    now = time.time()
    code, ans, ms = br.cast("Steve", "fire ball", now - 0.6, now - 0.1)
    t0 = time.perf_counter()
    check("POST /v1/casts 'fire ball' fires in the engine", code == 202 and ans.get("status") == "fired",
          "%s %s, answered in %.0f ms" % (code, ans, ms))
    note("POST /v1/casts -> the bridge's answer (fired in the engine)", "%.0f ms" % ms)
    got, secs = wait_for(lambda: (w.health("@n[tag=e2e_target]") or h0) < h0, 3, every=0.02)
    check("  the zombie is hurt", got, "first burn %.2f s after the answer" % (secs or 0))
    time.sleep(max(0.0, t0 + 3.6 - time.perf_counter()))
    dealt = h0 - w.health("@n[tag=e2e_target]")
    check("  by about fireball's 11.55", 11.2 <= dealt <= 11.56, "%.2f" % dealt)
    row = br.row("SELECT incantation_id, action, status, layer FROM casts ORDER BY at DESC LIMIT 1")
    check("  recorded as engine:steve.fire_ball, its action the ability",
          row and row["incantation_id"] == "engine:steve.fire_ball" and row["action"] == "e2e_fireball", str(row))
    now = time.time()
    code, ans, ms = br.cast("Steve", "fire ball", now - 0.5, now - 0.1)
    check("a second cast within the cooldown answers cooldown", code == 202 and ans.get("status") == "cooldown",
          "%s %s" % (code, ans))
    check("Retort never heard the cast's damage as Steve's hit", w.score("Steve", "e2e_hits") == 1,
          "e2e_hits %d" % w.score("Steve", "e2e_hits"))
    status(engine, "after the casts")


def step5b_blast(vfg, engine, br, w):
    print("== 5b. an explosion with self_damage off, beside its caster")
    regen = w.gamerule("natural_health_regeneration")
    w.cmd("gamerule natural_health_regeneration false")     # the test's own: health reads stay put
    vfg("engine", "grant", "Steve", "e2e_blast")
    code, out, err = vfg("engine", "bind", "Steve", "big blast", "e2e_blast")
    check("e2e_blast granted to Steve and bound to 'big blast'", code == 0, out)
    s0, a0 = w.health("Steve"), w.health("Alex")
    now = time.time()
    code, ans, ms = br.cast("Steve", "big blast", now - 0.6, now - 0.1)
    check("POST /v1/casts 'big blast' fires", code == 202 and ans.get("status") == "fired", "%s %s" % (code, ans))
    got, secs = wait_for(lambda: (w.health("Alex") or a0) < a0, 2, every=0.02)
    time.sleep(1.5)
    s1, a1 = w.health("Steve"), w.health("Alex")
    check("Steve, its caster, at the centre: unhurt", s1 == s0, "%.2f then %.2f" % (s0, s1))
    # explosion damage scales with difficulty for players, whoever caused it (26.3's damage type
    # minecraft:explosion has scaling "always"): hard is 1.5 times
    level = (re.search(r"difficulty is (\w+)", w.cmd("difficulty")) or [None, "?"])[1].lower()
    want = {"peaceful": 0.0, "easy": min(4 / 2 + 1, 4), "normal": 4.0, "hard": 6.0}.get(level)
    check("Alex, 4 blocks off: hit once, by the blast's 4 as Minecraft scales it for a player (%s: %s)"
          % (level, want), want is not None and a0 - a1 == want,
          "%.2f then %.2f; twice would be %s" % (a0, a1, 2 * want if want else "?"))
    code, doc, err = vfg.json("engine", "log", "-n", "30")
    dealt = [str(x.get("msg")) for x in doc.get("lines") or [] if "e2e_blast" in str(x.get("msg")) and "dealt" in str(x.get("msg"))]
    check("  and the engine counted 4 to Alex alone", dealt and dealt[-1].endswith("dealt 4.0 to Alex"), str(dealt[-1:]))
    w.cmd("gamerule natural_health_regeneration %s" % (regen or "true"))
    w.cmd("effect give Alex minecraft:instant_health 1 3 true")


def step6_world(vfg, engine, w):
    print("== 6. world settings: mob_griefing held twice, released in both orders")
    original = w.gamerule("mob_griefing")

    def start(value, priority):
        code, doc, err = vfg.json("engine", "start", "e2e_rule", "value=%s" % value, "priority=%d" % priority)
        return doc.get("instance")

    def stop(iid):
        return vfg("engine", "stop", "--instance", iid)[0] == 0

    def value_is(want):
        got, _ = wait_for(lambda: w.gamerule("mob_griefing") == want, 2)
        return got

    a = start("false", 1)
    s1 = value_is("false")
    b = start("true", 10)
    s2 = value_is("true")
    code, out, err = vfg("engine", "world")
    shown = ("held by %s: true at priority 10" % b in out) and ("held by %s: false at priority 1" % a in out)
    s3 = stop(b) and value_is("false")
    s4 = stop(a) and value_is(original)
    check("held false at 1, then true at 10 (it wins), released high first: false, then %s again" % original,
          s1 and s2 and s3 and s4, "steps %s" % [s1, s2, s3, s4])
    check("  vfg engine world shows both holds", shown, out.replace("\n", " | ")[:300])
    a = start("false", 10)
    s1 = value_is("false")
    b = start("true", 1)
    time.sleep(0.4)
    s2 = w.gamerule("mob_griefing") == "false"          # the lower priority does not win, though newer
    s3 = stop(b) and (time.sleep(0.4) or w.gamerule("mob_griefing") == "false")
    s4 = stop(a) and value_is(original)
    code, out, err = vfg("engine", "world")
    check("held false at 10, then true at 1 (it waits), released low first: false, then %s again" % original,
          s1 and s2 and s3 and s4, "steps %s" % [s1, s2, s3, s4])
    check("  and no hold is left", "no world setting is held" in out, out[:200])


def step7_scenario(vfg, engine, w, spots):
    print("== 7. a scenario and its actor")
    x, y, z = spots["horde"]
    near = "@e[x=%g,y=%g,z=%g,distance=..8,type=!minecraft:player]" % (x, y, z)
    tagged0, near0 = w.count("@e[tag=everhear]"), w.count(near)
    code, doc, err = vfg.json("engine", "start", "e2e_horde", "at=%g %g %g" % (x, y, z))
    iid = doc.get("instance")
    check("vfg engine start e2e_horde", code == 0 and iid, str(doc))
    got, secs = wait_for(lambda: w.count("@e[tag=everhear]") == tagged0 + 2, 3)
    check("  two entities in the world: the brute and a pig", got, "everhear-tagged %d (was %d)"
          % (w.count("@e[tag=everhear]"), tagged0))
    rows = instances(vfg)
    brute = [r for r in rows if r.get("id") == "e2e_brute"]
    uid = brute[0]["user"] if brute else None
    roar = [r for r in rows if r.get("id") == "e2e_roar" and r.get("user") == uid]
    check("  the actor is an instance with its granted ability", len(brute) == 1 and len(roar) == 1,
          "%s %s" % ([r["instance"] for r in brute], [r["instance"] for r in roar]))
    if uid:
        check("  as its Config says: 30 health, 1.25 the size, its name",
              w.health(uid) == 30 and w.attr(uid, "scale") == 1.25 and "E2E Brute" in w.cmd("data get entity %s CustomName" % uid),
              "health %s scale %s" % (w.health(uid), w.attr(uid, "scale")))
    code, out, err = vfg("engine", "stop", "e2e_horde")
    got, secs = wait_for(lambda: w.count("@e[tag=everhear]") == tagged0 and w.count(near) == near0, 3)
    check("stopping it leaves nothing: the counts are back", got,
          "everhear-tagged %d (was %d); near the spot %d (was %d)" % (w.count("@e[tag=everhear]"), tagged0, w.count(near), near0))
    check("  its actor and the actor's ability ended", not [r for r in instances(vfg) if r.get("id") in ("e2e_brute", "e2e_roar")], "")
    code, doc, err = vfg.json("engine", "spawn", "e2e_brute", "--at", str(x), str(y), str(z))
    v = doc.get("uuid")
    vfg("engine", "grant", v or "-", "e2e_roar")
    time.sleep(0.5)
    check("vfg engine spawn e2e_brute, granted e2e_roar", v and w.health(v) == 30 and
          [r for r in instances(vfg) if r.get("id") == "e2e_roar" and r.get("user") == v], str(doc))
    w.cmd("kill %s" % v)
    got, secs = wait_for(lambda: not [r for r in instances(vfg) if r.get("user") == v], 3)
    code, doc, err = vfg.json("engine", "log", "-n", "30")
    gone = any("ended (gone)" in str(x.get("msg")) for x in doc.get("lines") or [])
    check("killed in the world: its instance ends (gone), and its ability with it", got and gone,
          "%.1f s after the kill" % (secs or 0))
    status(engine, "after the scenario")


def step7b_orphans(vfg, engine, w, p, spots):
    print("== 7b. the orphan sweep: an actor whose chunk unloaded, and one the engine lost track of")
    x, y, z = spots["horde"]
    # A barrier box far off, in chunks loaded only while it is built: the brute is put there, its
    # chunk unloads, the engine takes it as gone; then Alex goes there, the chunk loads, the brute
    # (it persists) is back with nothing driving it, and the sweep removes it.
    far = (3000.5, 150.0, 3000.5)
    w.cmd("forceload add 2992 2992 3008 3008")
    w.cmd("fill 2996 149 2996 3004 153 3004 minecraft:barrier hollow")
    code, doc, err = vfg.json("engine", "spawn", "e2e_brute", "--at", str(x), str(y), str(z))
    u = doc.get("uuid") or "-"
    wait_for(lambda: w.health(u) == 30, 3)
    iid = next((r["instance"] for r in instances(vfg) if r.get("user") == u), None)
    out = w.cmd("tp %s %g %g %g" % ((u,) + far))
    w.cmd("forceload remove 2992 2992 3008 3008")
    gone, secs = wait_for(lambda: not [r for r in instances(vfg) if r.get("user") == u], 30, every=0.5)
    code, doc, err = vfg.json("engine", "log", "-n", "40")
    said = [str(x.get("msg")) for x in doc.get("lines") or [] if iid and str(x.get("msg")).startswith(iid + " ended (gone)")]
    check("an E2E Brute moved far off: its chunk unloads, and the engine takes it as gone", out.startswith("Teleported")
          and gone and said, "%s, %.1f s after the forceload went" % (iid, secs or 0))
    p.tp("Alex", 3001.5, 150, 3001.5)
    back, _ = wait_for(lambda: w.count(u) == 1, 5, every=0.1)
    removed, secs = wait_for(lambda: w.count(u) == 0, 12, every=0.2)
    code, doc, err = vfg.json("engine", "log", "-n", "40")
    said = [str(x.get("msg")) for x in doc.get("lines") or [] if u in str(x.get("msg")) and "nothing owns" in str(x.get("msg"))]
    # the log line is the sweep seeing it back (in the mirror); `back` is this test seeing it too
    check("Alex goes there: the chunk loads, the brute is back, and the sweep removes it", removed and said,
          "back %s; removed %.1f s after it was back; %s" % (bool(back), secs or 0, said[-1][:120] if said else "no log line"))
    p.tp("Alex", *spots["alex"])
    code, doc, err = vfg.json("engine", "spawn", "e2e_brute", "--at", str(x), str(y), str(z))
    u = doc.get("uuid") or "-"
    got, _ = wait_for(lambda: w.health(u) == 30, 3)
    check("another brute, its instance live", got and [r for r in instances(vfg) if r.get("user") == u], u)
    time.sleep(1.2)                                   # saved about once a second
    code, secs = engine.stop()
    # An engine that lost it: its record out of the saved state, as after an unload taken for gone.
    path = engine.env["VFG_ENGINE_STATE"]
    with open(path) as f:
        st = json.load(f)
    had = u in json.dumps(st)
    st["records"] = [r for r in st.get("records") or [] if r.get("user") != u and r.get("iid") != u]
    st["confirmed"] = [c for c in st.get("confirmed") or [] if c != u]
    (st.get("actor_cooldowns") or {}).pop(u, None)
    with open(path + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)
    check("the engine stopped (exit 0); its saved state no longer has the brute",
          code == 0 and had and u not in json.dumps(st), "exit %s" % code)
    check("  the brute is still in the world, tagged everhear.actor",
          w.count("@e[tag=everhear.actor]") == 1 and w.health(u) == 30, "")
    engine.start()
    t0 = time.perf_counter()
    got, secs = wait_for(lambda: w.count(u) == 0, 15, every=0.2)
    code, doc, err = vfg.json("engine", "log", "-n", "40")
    said = [str(x.get("msg")) for x in doc.get("lines") or [] if u in str(x.get("msg")) and "nothing owns" in str(x.get("msg"))]
    check("the sweep removes it once the mirror sees it: nothing owns it", got and said,
          "%.1f s after the start; %s" % (time.perf_counter() - t0, said[-1][:160] if said else "no log line"))
    if got:
        note("start -> an orphaned actor removed by the sweep", "%.1f s" % (time.perf_counter() - t0))
    check("  nothing else tagged by the engine is left", w.count("@e[tag=everhear]") == 0, "")


def step8_limits(vfg, engine, w):
    print("== 8. limits: a call that sleeps 200 ms")
    before = sorted((r["instance"], r.get("user")) for r in instances(vfg))
    s0 = status(engine, "before the slow call")
    t0 = time.perf_counter()
    code, out, err = vfg("engine", "grant", "Alex", "e2e_slow")
    check("vfg engine grant Alex e2e_slow", code == 0, out + err)
    time.sleep(2.0)
    s1 = status(engine, "with the slow call cut")
    secs = time.perf_counter() - t0
    after = sorted((r["instance"], r.get("user")) for r in instances(vfg))
    code, doc, err = vfg.json("engine", "log", "-n", "40")
    lines = [str(x.get("msg")) for x in doc.get("lines") or []]
    took = [float(m.group(1)) for ln in lines for m in [re.search(r"e2e_slow#\d+\.tick took (%s) ms" % FLOAT, ln)] if m]
    cut = [ln for ln in lines if "e2e_slow" in ln and "cut: the call ran over 50 ms" in ln]
    check("the call is cut at about 50 ms", took and 45 <= took[0] <= 80 and cut,
          "took %s ms; %s" % (took, cut[-1] if cut else "no cut line"))
    if took:
        note("the slow call, cut", "%.1f ms" % took[0])
    check("  only that instance ended: every other one still runs", after == before,
          "%d before, %d after" % (len(before), len(after)))
    n = s1.get("tick", 0) - s0.get("tick", 0)
    check("  and the ticks went on", n >= 0.8 * 10 * secs - 3, "%d ticks in %.1f s" % (n, secs))


def step9_restart(vfg, engine, br, w, p):
    print("== 9. restart: SIGTERM, then started again")
    vfg("engine", "take", "Alex", "fire_staff")
    gone, _ = wait_for(lambda: "everhear:item/fire_staff" not in w.mods("Alex", "movement_speed"), 3)
    check("vfg engine take Alex fire_staff: the stack and its modifier gone",
          gone and w.holds("Alex") == 0, str(w.mods("Alex", "movement_speed")))
    vfg("engine", "give", "Steve", "fire_staff")
    vfg("engine", "apply", "Steve", "giant", "--seconds", "90")
    wait_for(lambda: w.attr("Steve", "scale") == 2.0 and w.mods("Steve", "movement_speed").get("everhear:item/fire_staff"), 3)
    before = {(r["kind"], r["id"], r.get("user")): r.get("ends") for r in instances(vfg)}
    code, inc_before, err = vfg.json("engine", "incantations")
    check("before: Steve's grants, his staff and Giant", {("ability", "e2e_fireball", "Steve"), ("ability", "e2e_retort", "Steve"),
                                                          ("item", "fire_staff", "Steve"), ("mark", "giant", "Steve")} <= set(before),
          str(sorted(before)))
    code, secs = engine.stop()
    check("SIGTERM: the engine stops with its state saved (exit 0)", code == 0, "exit %s in %.2f s" % (code, secs))
    note("SIGTERM -> the engine's exit", "%.2f s" % secs)
    code, out, err = vfg("engine", "status")
    check("  vfg engine status says it is not running: exit 6", code == 6, err[:160])
    out = w.cmd("attribute Alex minecraft:movement_speed modifier add everhear:item/fire_staff 0.2 add_multiplied_total")
    check("a stray everhear:item/fire_staff added by hand to Alex, who holds none", out.startswith("Added modifier")
          and "everhear:item/fire_staff" in w.mods("Alex", "movement_speed"), out)
    time.sleep(1.0)
    secs = engine.start()
    note("start -> the socket answers (the state restored)", "%.2f s" % secs)
    t_up = time.perf_counter()
    after = {(r["kind"], r["id"], r.get("user")): r.get("ends") for r in instances(vfg)}
    check("after: every instance is back", set(after) == set(before),
          "missing %s; new %s" % (sorted(set(before) - set(after)), sorted(set(after) - set(before))))
    g = ("mark", "giant", "Steve")
    check("  Giant with its time: the same end", g in after and before.get(g) and abs(after[g] - before[g]) < 0.01,
          "%s then %s" % (before.get(g), after.get(g)))
    code, inc_after, err = vfg.json("engine", "incantations")
    ids = sorted(e.get("id") for e in inc_after.get("incantations") or [])
    check("  the bindings are back", ids == sorted(e.get("id") for e in inc_before.get("incantations") or [])
          == ["steve.big_blast", "steve.fire_ball"], str(ids))
    code, doc = br.call("GET", "/v1/incantations?player=Steve")
    check("  and the bridge lists them for the mod", code == 200 and sorted(
        i.get("phrase") for i in doc.get("incantations") or []) == ["big blast", "fire ball"], "%s %s" % (code, doc))
    got, secs = wait_for(lambda: "everhear:item/fire_staff" not in w.mods("Alex", "movement_speed"), 6)
    check("  the stray modifier on Alex is removed at start-up", got,
          "%.2f s after the socket answered; %s" % ((time.perf_counter() - t_up), w.mods("Alex", "movement_speed")))
    if got:
        note("socket answers -> the stray modifier removed", "%.2f s" % (time.perf_counter() - t_up))
    check("  Steve's staff modifier and Giant's size are still on him, in Minecraft",
          w.mods("Steve", "movement_speed").get("everhear:item/fire_staff") == 0.2 and w.attr("Steve", "scale") == 2.0,
          "%s scale %s" % (w.mods("Steve", "movement_speed"), w.attr("Steve", "scale")))
    status(engine, "after the restart")


def step10_actions(vfg, engine, w):
    print("== 10. the brain's engine actions, through vfg action run")
    code, line, err = vfg.action("engine_grant", "player=Alex", "ability=e2e_fireball")
    mine = [r for r in instances(vfg, kind="ability", user="Alex") if r.get("id") == "e2e_fireball"]
    check("vfg action run engine_grant player=Alex ability=e2e_fireball", code == 0 and line.get("ok") and mine,
          "%s; Alex's: %s" % (line.get("summary") or line, [r["instance"] for r in mine]))
    code, line, err = vfg.action("engine_give", "player=Alex", "item=fire_staff")
    got, secs = wait_for(lambda: w.mods("Alex", "movement_speed").get("everhear:item/fire_staff"), 3)
    check("vfg action run engine_give player=Alex item=fire_staff: in his hand, its modifier on him",
          code == 0 and line.get("ok") and w.holds("Alex", "weapon.mainhand") == 1 and got == 0.2,
          "%s; %s" % (line.get("summary") or line, w.mods("Alex", "movement_speed")))
    code, line, err = vfg.action("engine_give", "player=Alex", "item=nope")
    check("  one the engine refuses is exit 1, in its words", code == 1 and line.get("error") == "'nope' is not a loaded item",
          str(line.get("error")))
    status(engine, "at the end")


# ------------------------------------------------------------------ setup

def install(srv):
    """The engine into the stand-in's /opt/vfg, as deploy.sh engine does on the box."""
    e = os.path.join(srv.vfg, "engine")
    for d in ("datapack", "deploy"):
        os.makedirs(os.path.join(e, d), exist_ok=True)
    for name in ("state.json", "incantations.json", "engine.log", "engine.sock"):
        try:
            os.remove(os.path.join(e, name))
        except FileNotFoundError:
            pass
    rsync = ["rsync", "--recursive", "--delete", "--exclude=__pycache__/"]
    subprocess.run(rsync + [os.path.join(ENGINE_SRC, "vfg_engine"), e + "/"], check=True)
    subprocess.run(rsync + [os.path.join(ENGINE_SRC, "datapack", "everhear"), os.path.join(e, "datapack") + "/"], check=True)
    for name in ("install.sh", "requirements.txt"):
        shutil.copy(os.path.join(ENGINE_SRC, "deploy", name), os.path.join(e, "deploy", name))
    shutil.copy(os.path.join(REPO, "bridge", "deploy", "rcon.py"), os.path.join(srv.mc, "rcon.py"))
    env = dict(os.environ, VFG_DIR=srv.vfg, MC_DIR=srv.mc, UV=shutil.which("uv") or "uv")
    t0 = time.perf_counter()
    r = subprocess.run(["bash", os.path.join(e, "deploy", "install.sh")], env=env, capture_output=True, text=True)
    return r, time.perf_counter() - t0


def stray_engine(sock, folder):
    """An engine an earlier run left on this stand-in (killed before its own cleanup) holds the
    engine's lock, and a second one would refuse to start: stopped, by the pid in the lock."""
    try:
        with open(sock + ".lock") as f:
            pid = int(f.read().strip() or 0)
        if pid and os.path.realpath("/proc/%d/cwd" % pid) == os.path.realpath(folder):
            os.kill(pid, signal.SIGTERM)
            wait_for(lambda: not os.path.exists("/proc/%d" % pid), 15)
            print("  (stopped an engine an earlier run left, pid %d)" % pid)
    except (OSError, ValueError):
        pass


def write_content(name, srv):
    """made/<name>/content/ on this PC, fresh: content from integration/engine_e2e_content/ and test's classes;
    the box's folder emptied, so the deploy sends it whole."""
    content = os.path.join(REPO, "made", name, "content")
    shutil.rmtree(content, ignore_errors=True)
    os.makedirs(content)
    for f in sorted(os.listdir(E2E_CONTENT_SRC)):
        if f.endswith(".py"):
            shutil.copy(os.path.join(E2E_CONTENT_SRC, f), content)
    for f, source in CONTENT.items():
        with open(os.path.join(content, f), "w") as out:
            out.write(source)
    box = os.path.join(srv.vfg, "made", name, "content")
    shutil.rmtree(box, ignore_errors=True)
    os.makedirs(box)
    return content, box


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="ge-e2e")
    ap.add_argument("--keep", action="store_true", help="leave the server up, and the engine's and bridge's logs")
    a = ap.parse_args(argv)

    srv = realmc.Server(a.name)
    print("== up %s --fresh" % a.name)
    t0 = time.time()
    if subprocess.run([os.path.join(REALMC, "up"), a.name, "--fresh"]).returncode:
        print("  FAIL  up")
        return 1
    ok("up", "%.1f s" % (time.time() - t0))
    p = players.Players(a.name)
    w = World(p)
    vfg = Vfg(vfg_env(a.name))
    engine = br = None
    try:
        r, secs = install(srv)
        enabled = p.cmd("datapack list enabled")
        check("install.sh: the venv and the datapack, loaded", r.returncode == 0 and "file/everhear" in enabled,
              "%.1f s; %s" % (secs, (r.stdout + r.stderr).strip().replace("\n", " | ")[:300]))
        content, box_content = write_content(a.name, srv)
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "vfg-engine-%s.sock" % a.name)
        link = os.path.join(srv.vfg, "engine", "engine.sock")
        os.symlink(sock, link)
        engine = Engine(srv, sock, box_content, os.path.join(srv.dir, "engine-process.log"))
        stray_engine(sock, engine.dir)
        secs = engine.start()
        ok("the engine runs as vfg-engine does", "socket answered in %.2f s; %s -> %s" % (secs, link[-40:], sock))
        br = Bridge(srv, engine, os.path.join(srv.dir, "bridge.log"))
        ok("the bridge", br.url)
        status(engine, "fresh server, nobody on")
        arena = p.arena()
        base = (arena["x"] + 0.5, arena["y"], arena["z"] + 0.5)
        spots = {"steve": base, "alex": (base[0] + 4, base[1], base[2]), "target": (base[0], base[1], base[2] + 6),
                 "horde": (base[0] + 8, base[1], base[2] - 8)}
        p.spawn("Steve", at=spots["steve"])
        p.spawn("Alex", at=spots["alex"])
        p.cmd("scoreboard objectives add e2e_hits dummy")
        out = p.cmd('summon minecraft:zombie %g %g %g {NoAI:1b,PersistenceRequired:1b,Silent:1b,Tags:["e2e_target"],'
                    'Health:100f,attributes:[{id:"minecraft:max_health",base:100}],'
                    'equipment:{head:{id:"minecraft:carved_pumpkin",count:1}}}' % spots["target"])
        check("Steve, Alex, and a zombie with 100 health 6 blocks in front of Steve (a pumpkin keeps the sun off)",
              out.startswith("Summoned") and w.health("@n[tag=e2e_target]") == 100, out)
        time.sleep(0.5)
        status(engine, "two players joined")

        step1_status_and_reload(vfg, engine)
        step2_items(vfg, engine, w, p, spots)
        step3_marks(vfg, engine, w, p)
        marker = os.path.join(srv.dir, "e2e-rewrite-ran-code")
        if os.path.exists(marker):
            os.remove(marker)
        step4_rewrite(vfg, engine, content, box_content, marker)
        step5_incantations(vfg, engine, br, w, p, spots)
        step5b_blast(vfg, engine, br, w)
        step6_world(vfg, engine, w)
        step7_scenario(vfg, engine, w, spots)
        step7b_orphans(vfg, engine, w, p, spots)
        step8_limits(vfg, engine, w)
        step9_restart(vfg, engine, br, w, p)
        step10_actions(vfg, engine, w)
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

    print("== tick times from status (the last 200 ticks each time)")
    for when, p50, p95, n in ticks:
        print("  %-28s p50 %5s ms  p95 %5s ms  (tick %s)" % (when, p50, p95, n))
    print("== measured")
    for what, value in measured:
        print("  %-72s %s" % (what, value))
    print("== %s" % ("PASS" if not fails else "%d FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
