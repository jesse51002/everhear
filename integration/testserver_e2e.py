#!/usr/bin/env python3
"""The test server's isolation and consistency, end to end on realmc (Prototype_Implementer_Quality):
the REAL `vfg test`, run as an implementer runs it (through `integration/realmc/env NAME`'s vfg,
VFG_TEST_BOX unset), on a realmc server with its test server laid out beside it as the box's
(`up NAME --test-server`: NAME/mc-test and NAME/vfg-test, the live folders + "-test", the unit
minecraft-test, a fresh world at every start, each test in its sandbox). Each check prints PASS,
FAIL or INFO with its numbers as it goes, and a table at the end.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    python3 integration/testserver_e2e.py [--name iq-d1] [--only night,runaway,fresh,parallel,heal,refuse,guard]
        [--fresh-tests 20] [--streams 3] [--per-stream 5] [--keep-up] [--keep-made]
    python3 integration/testserver_e2e.py --box [--server main]     # the reach test on the REAL box

- night: the night of 2026-10-12 replayed safely. On the LIVE server, Carpet's fake KrownJM and
  Styrkaeopteryx stand a few blocks apart (the night's victims; an infinite effect on each), a gold
  block on the arena and one where the kit's sky stage is (x 3000, y 300, z 3000), forceloaded.
  A made ice aura (iqd_blizzard: 10 s of freeze damage and slowness round the caster, sparing her,
  then it ends), bound to Styrkaeopteryx's "freeze" on the live server as an implementer binds it,
  and its test from the kit's templates (cast as Styrkaeopteryx on the test server: KrownJM beside
  her hurt and slowed, she is not, the storm ends), run by vfg test 3 times. Every test passes
  there; the live KrownJM and Styrkaeopteryx keep their exact position, rotation, health, food,
  effects, items and xp; the live console has no new line; the live incantations.json, run/,
  server.properties, the vfg folder, both gold blocks, the forceloads, objectives, gamerules, time,
  weather and border are unchanged (the live entity count is INFO: the arena's animals live).
- runaway: an aura that never ends (iqd_runaway, as the night's first blizzard: its loop starts a
  new storm every pass; worse, it ignores SIGTERM and starts a detached copy of itself), and a test
  that waits for its end. vfg test --timeout 20 stops it ("ran past its 20 s"), in bounded time;
  afterwards no process of it is left (/proc, by VFG_RUN_DIR) and its unit is gone; the next test
  starts on a fresh world with no marker, objective, block or slowness of it. Then the same in the
  plain fallback (VFG_TEST_SANDBOX=none), as INFO: what survives without the sandbox.
- fresh: a baseline test records the fresh world (objectives, bossbars, teams, border, forceloads,
  gamerules, time, difficulty, weather, players online, entities, a block outside the stage and one
  under it, and a fake player's saved effects, items and xp, which Carpet reloads when he rejoins a
  kept world); then N tests one after another, each first checking every probe against the
  baseline, then leaving junk in all of them. Every test must pass; per-test wall time, the wait
  for the fresh world and the queue wait are reported.
- parallel: S streams of P tests at once (parallel implementers, each its own task), the same
  checks and junk; all complete and pass, their test windows never overlap; total wall time and
  queue waits.
- heal: the test server's java SIGKILLed; the unit stopped (the fake systemctl); java SIGSTOPped
  (RCON's port still takes connections, nothing answers) with VFG_TEST_BOOT_S=20: exit 5, bounded,
  its message; then SIGCONT and recovery; and SIGSTOPped again with the defaults: the restart
  clears it (TimeoutStopSec 60, then SIGKILL) and the test passes. Each next test passes.
- refuse: VFG_TEST_BOX naming the live folders, a folder without the marker, a link to the live
  folder: exit 3, at the PC or the box's shell guard. A test that reaches for the live server
  (iqd_reach: its server.properties, .env, a write, mc with the live properties, a child with them,
  mc switched to them by hand after its check passed): every reach refused in the sandbox; the
  kit's NotTestServer again in the plain fallback, its only layer (INFO: what gets through there).
  The escapes round the sandbox are integration/testserver_escape/'s test (/proc of the live
  processes, the live ports and the internet, the live RCON lock, sudo): INFO here, since the PC's
  user sandbox runs as the same uid as the live server (a known limitation; its sudo is realmc's
  fake one); --box runs it on the real box, where every attempt must be refused. The live console:
  nothing but this suite's own RCON connections and those the PC lets through (counted).
- guard: the brain's implementer guard at unit level (brain/brain/implementers/guard.py, through
  `uv run` in brain/): four tests a run, the fifth refused; a rerun with nothing changed refused;
  live probes capped with a test server; and the prompt's exit-4/5 rule against the guard.

Writes OUT/e2e.log (every vfg test's whole output), OUT/summary.json and OUT/summary.md; OUT is
$VFG_IMPL_REPLAY/testserver-e2e-NAME. Writes its abilities and tests into made/NAME/ (moved aside
first when it holds anything, into $VFG_IMPL_REPLAY/made-aside/; removed at the end unless
--keep-made). Spends nothing: no model is called. Never `down --all`, `--purge-key`, `--regen`,
`key` or `carpet`; realmc's shared env file is never read. Names start with iq-d. The server and
its test server are stopped at the end unless --keep-up.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
sys.path.insert(0, REALMC)
sys.path.insert(0, HERE)

import realmc  # noqa: E402
from impl_data import ROOT  # noqa: E402
from incantations_e2e import vfg_env  # noqa: E402

PREFIX = "iq-d"
VICTIM, CASTER = "KrownJM", "Styrkaeopteryx"
STAGE = (3000, 300, 3000)          # where the kit's sky stage is (x 3000, z 3000; floor y 295)
JUNK_AT = (3070, 300, 3000)        # outside what the kit clears (97 x 97 round the stage, y 280 up)
UNDER_STAGE = (3001, 275, 3001)    # inside the stage's x and z, under what the kit clears
ARENA_GOLD = (5, 86, 5)            # a gold block on the live arena
SERVER_LINE = re.compile(r"^SERVER\s+(\S+) \(RCON (\d+)\): (.*)$")
CHECK_LINE = re.compile(r"^(PASS|FAIL|CAN'T TELL)\s+(.*?) — (.*)$")
IQD_LINE = re.compile(r"^IQD\s+(\S+(?: \S+)*?) (\S.*)$")

# ------------------------------------------------------------------ what it writes into made/NAME/

BLIZZARD_ACTION = '''from actions._types import Action, Field, Live, Players


class IqdBlizzard(Action):
    """Blizzard: an ice storm round the caster for ten seconds that hurts and slows everyone near him but him, then ends: the one to bind to "freeze"."""

    script = "iqd_blizzard"
    runs = "once"
    takes = ["player", "radius", "seconds", "damage"]

    player: Players = "@a"
    """who casts it: the storm is round him, and spares him"""
    radius: Live[float] = Field(8, ge=1, le=32)
    """how far round him it reaches, in blocks"""
    seconds: Live[float] = Field(10, ge=1, le=60)
    """how long it lasts, in seconds; then it ends by itself"""
    damage: Live[float] = Field(1, ge=0, le=20)
    """freeze damage a second to everyone in it but the caster"""
'''

BLIZZARD_SCRIPT = '''"""Blizzard: an ice storm round the caster for `seconds` s: everyone within `radius` of him but him (players and mobs) takes `damage` freeze damage and slowness every second; then it ends by itself."""
ASKS = ["blizzard set to freeze", "an ice aura that freezes everyone near me but me, for ten seconds"]
from kit import values, result, loop
import time
import mc

v = values()
casters = loop.who(v["player"])
hits = 0
end = time.time() + float(v["seconds"])
while time.time() < end and not mc.stopping():
    for c in casters:
        sel = "@e[distance=..%s,name=!%s,type=!item,type=!experience_orb,type=!marker]" % (v["radius"], c)
        out = mc.rcon("execute at %s as %s run damage @s %s minecraft:freeze" % (c, sel, v["damage"]))
        hits += out.count("Applied")
        mc.rcon("execute at %s run effect give %s slowness 2 1 true" % (c, sel))
        mc.rcon("execute at %s run particle snowflake ~ ~1 ~ 3 1 3 0.02 80 force" % c)
    time.sleep(1.0)
result("the blizzard ended after %s s: %d hits" % (v["seconds"], hits), ended=True, hits=hits)
'''

BLIZZARD_TEST = '''"""The test of iqd_blizzard: Styrkaeopteryx says "freeze"; the storm round her hurts and slows KrownJM beside her, never her, and ends by itself."""
from kit import test

t = test.Test("Styrkaeopteryx", others=("KrownJM",))   # KrownJM stands 4 to her left
husk = t.mob("husk", 0, 0, 5, health=40)                # in the storm, 5 ahead
t.cast("freeze")                                         # the real trigger
t.watch(12)                                              # the 10 s storm and 2 s after
t.check("KrownJM, beside her, is hurt", t.hurt("KrownJM"))
t.check("KrownJM is slowed", t.effect("slowness", "KrownJM"))
t.check("the husk in it is hurt", t.hurt(husk))
t.check("Styrkaeopteryx herself is not hurt", t.unhurt())
t.check("the storm ends by itself", t.said("ended", within=3))
t.check("no error in its log", t.no_errors())
t.done()
'''

RUNAWAY_ACTION = '''from actions._types import Action, Players


class IqdRunaway(Action):
    """Blizzard, as the night's first one was: an ice storm round the caster that slows everyone near him but him, and never ends."""

    script = "iqd_runaway"
    runs = "once"
    takes = ["player"]

    player: Players = "@a"
    """who casts it: the storm is round him"""
'''

RUNAWAY_SCRIPT = '''"""Blizzard with the night's bug, and worse: its outer loop starts a new storm every pass so it never ends, it ignores a stop (SIGTERM), and it starts a detached copy of itself that does the same."""
ASKS = ["blizzard"]
from kit import values, loop
import os
import signal
import subprocess
import sys
import time
import mc

UNDER = (%(under)s)
CHILD = """
import signal, sys, time
import mc
signal.signal(signal.SIGTERM, signal.SIG_IGN)
c = sys.argv[1]
while True:
    try:
        mc.rcon("execute at %%s run effect give @e[distance=..8,name=!%%s] slowness 3 1 true" %% (c, c))
        mc.rcon("scoreboard objectives add iqd_runaway_child dummy")
    except Exception:
        mc._drop()
    time.sleep(0.5)
"""

v = values()
caster = (loop.who(v["player"]) or ["Styrkaeopteryx"])[0]
signal.signal(signal.SIGTERM, signal.SIG_IGN)   # a stop does nothing
subprocess.Popen([sys.executable, "-c", CHILD, caster], start_new_session=True, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, IQD_RUNAWAY="child"))
mc.rcon("scoreboard objectives add iqd_runaway dummy")
while True:   # the outer loop starts a new storm every pass
    mc.rcon("execute at %%s run effect give @e[distance=..8,name=!%%s] slowness 3 1 true" %% (caster, caster))
    mc.rcon('execute at %%s run summon marker ~ ~ ~ {Tags:["iqd_runaway"]}' %% caster)
    mc.rcon("setblock %%d %%d %%d blue_ice" %% UNDER)
    time.sleep(0.5)
''' % {"under": "%d, %d, %d" % UNDER_STAGE}

RUNAWAY_TEST = '''"""The test of iqd_runaway: Styrkaeopteryx says "blizzard"; it slows KrownJM, and the test waits for the storm to end."""
from kit import test

t = test.Test("Styrkaeopteryx", others=("KrownJM",))
t.cast("blizzard")
t.watch(3)
t.check("KrownJM is slowed", t.effect("slowness", "KrownJM"))
while not t.result():          # waits for the storm's end, which never comes
    t.wait(1)
t.check("the storm ends", t.said("ended"))
t.done()
'''

# A test that checks the world it starts on against the fresh world's baseline (CFG["expect"]),
# probe by probe, then leaves junk in every one of them (CFG["junk"]) for the next test to find.
FRESH_TEST = r'''"""The test of __NAME__ (integration/testserver_e2e.py): the world it starts on is the fresh one, probe by probe, then it leaves junk in all of them for the next test, were the world kept."""
import json
import time

STARTED = time.time()
import mc  # noqa: E402
from kit import test  # noqa: E402

CFG = json.loads(r"""__CFG__""")
JX, JY, JZ = CFG["junk_at"]
UX, UY, UZ = CFG["under_stage"]


def say(key, value):
    print("IQD        %s %s" % (key, json.dumps(value)), flush=True)


def online(name, timeout=10):
    end = time.time() + timeout
    while name not in mc.players() and time.time() < end:
        time.sleep(0.1)


say("started", STARTED)
seen = dict((key, mc.rcon(cmd)) for key, cmd in sorted(CFG["probes"].items()))
mc.rcon("forceload add %d %d" % (JX, JZ))       # to look there: a fresh world has it unloaded
time.sleep(1.0)                                 # its entities load a tick or more after
seen["junk block"] = mc.rcon("execute if block %d %d %d air" % (JX, JY, JZ))
seen["junk mob"] = mc.rcon("execute if entity @e[x=%d,y=%d,z=%d,distance=..16]" % (JX, JY, JZ))
seen["tagged junk"] = mc.rcon("execute if entity @e[tag=iqd_junk]")
seen["runaway markers"] = mc.rcon("execute if entity @e[tag=iqd_runaway]")
seen["under the stage"] = mc.rcon("execute if block %d %d %d air" % (UX, UY, UZ))
# a fake player the last test left: Carpet reloads his saved effects, items and xp in a kept world
mc.rcon("player Junker spawn at %d %d %d facing 0 0 in minecraft:overworld in survival" % (JX, JY + 1, JZ))
online("Junker")
for key, path in (("Junker's effects", "active_effects"), ("Junker's items", "Inventory"), ("Junker's xp", "XpLevel")):
    seen[key] = mc.rcon("data get entity Junker %s" % path)
mc.rcon("player Junker kill")
say("seen", seen)

t = test.Test(CFG["player"], others=tuple(CFG["others"]))
t.check("every probe read", test.verdict(all(seen.values()), "%d probes" % len(seen)))
for key, want in sorted((CFG.get("expect") or {}).items()):
    got = " ".join(str(seen.get(key)).split())
    want = " ".join(want.split())
    t.check("fresh: " + key, test.verdict(got == want, got[:110] if got == want else "%s; fresh: %s" % (got[:90], want[:90])))
if CFG.get("watch_slowness"):   # nothing left over keeps slowing the players
    t.watch(4)
    for who in [CFG["player"]] + CFG["others"]:
        v = t.effect("slowness", who)
        t.check("fresh: %s is never slowed" % who, test.verdict(not v[0], v[1]))
say("checked", time.time())
if CFG.get("junk"):
    j = CFG["junk"]
    for c in ("scoreboard objectives add iqd_junk_%s dummy" % j, 'bossbar add iqd:junk_%s "junk %s"' % (j, j),
              "team add iqd_junk_%s" % j, "worldborder center %d %d" % (JX, JZ), "worldborder set 4321",
              "gamerule keep_inventory true", "gamerule fall_damage false", "gamerule advance_time true",
              "gamerule mob_griefing false", "setblock %d %d %d diamond_block" % (JX, JY, JZ),
              "setblock %d %d %d emerald_block" % (UX, UY, UZ),
              'summon zombie %d %d %d {NoAI:1b,PersistenceRequired:1b,Tags:["iqd_junk"]}' % (JX, JY + 1, JZ),
              "difficulty peaceful", "time set 13000", "weather thunder",
              "player Junker spawn at %d %d %d facing 0 0 in minecraft:overworld in survival" % (JX + 2, JY + 1, JZ)):
        mc.rcon(c)
    online("Junker")
    for c in ("effect give Junker slowness infinite 2 true", "give Junker diamond 5", "xp add Junker 30 levels",
              "scoreboard players set Junker iqd_junk_%s 7" % j):
        mc.rcon(c)
    for who in [CFG["player"]] + CFG["others"]:
        mc.rcon("effect give %s slowness infinite 2 true" % who)
    say("junk", j)
t.done()
'''

# A test that reaches for the live server from inside vfg test: each reach printed as a PROBE line,
# REACHED or refused (the suite judges them for where it ran: REACHES); the kit's refusals are its
# checks too. CFG: the live server's folders and RCON port.
REACH_TEST = r'''"""The test of iqd_reach (integration/testserver_e2e.py): reaches for the live server from inside a test, each of which should be refused."""
import json
import os
import subprocess
import sys

import mc
from kit import test

LIVE = json.loads(r"""__CFG__""")
LIVE_PROPS = os.path.join(LIVE["mc"], "server.properties")


def probe(name, fn):
    try:
        got = fn()
        print("PROBE      %s: REACHED %s" % (name, str(got)[:100]), flush=True)
        return True
    except Exception as e:
        print("PROBE      %s: refused, %s: %s" % (name, type(e).__name__, str(e)[:160]), flush=True)
        return False


def read_password():
    with open(LIVE_PROPS) as f:
        return "the RCON password" if "rcon.password=" in f.read() else "read"


def live_mc_listing():
    return "%d names" % len(os.listdir(LIVE["mc"]))


def env_file():
    with open(os.path.join(LIVE["vfg"], ".env")) as f:
        return "%d bytes" % len(f.read())


def write_live():
    """Could it write there? Asked of access() and statvfs, never by writing: safe on the real box."""
    if os.access(LIVE["vfg"], os.W_OK) and not os.statvfs(LIVE["vfg"]).f_flag & os.ST_RDONLY:
        return "writable (nothing written)"
    raise PermissionError("not writable: %s" % ("a read-only mount" if os.statvfs(LIVE["vfg"]).f_flag & os.ST_RDONLY
                                               else "no write access"))


def answered(reply):
    """A server's answer, or why not: mc.rcon gives "" when it failed (and logs why)."""
    if not reply:
        raise ConnectionError("no answer: mc.rcon gave nothing (its error is in the log)")
    return reply


def on_live(fn):
    """fn() with mc pointed at the live server.properties, its cached settings and connection
    dropped; mc put back on the test server after, whatever happened, so the test's own end (t.done:
    its cleanup's RCON) still reaches the test server."""
    keep = (mc.PROPERTIES, mc._settings)
    mc.PROPERTIES, mc._settings = LIVE_PROPS, None
    mc._drop()
    try:
        return fn()
    finally:
        mc._drop()
        mc.PROPERTIES, mc._settings = keep


def kit_with_live_props():
    return on_live(lambda: answered(mc.rcon("list")))


def child_with_live_props():
    env = dict(os.environ, VFG_MC_PROPERTIES=LIVE_PROPS)
    r = subprocess.run([sys.executable, "-c", "import mc; print(mc.rcon('list'))"], env=env, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["exit %d" % r.returncode])[-1])
    return answered(r.stdout.strip())


refused_kit = not probe("mc with the live server.properties", kit_with_live_props)
refused_child = not probe("a child given the live server.properties", child_with_live_props)
probe("read the live server.properties", read_password)
probe("list the live Minecraft folder", live_mc_listing)
probe("read the live .env", env_file)
probe("write into the live vfg folder", write_live)

t = test.Test("Steve")
t.check("the kit refuses mc with the live server.properties", test.verdict(refused_kit, "NotTestServer"))
t.check("the kit refuses a child given the live server.properties", test.verdict(refused_child, "NotTestServer"))


def cached_check_then_live():
    """After a check passed on the test server, mc switched to the live properties by hand."""
    return on_live(lambda: answered(mc.rcon("list")))


probe("mc switched to the live properties after its check passed", cached_check_then_live)
t.done()
'''

# Each reach of REACH_TEST, and where it must be refused: "kit" everywhere (the kit's NotTestServer,
# the only layer without a sandbox); "files" in any sandbox. The escapes around the sandbox (/proc,
# the network, the RCON lock, sudo) are integration/testserver_escape/'s test (ESCAPE_TEST).
REACHES = (("mc with the live server.properties", "kit"), ("a child given the live server.properties", "kit"),
           ("read the live server.properties", "files"), ("list the live Minecraft folder", "files"),
           ("read the live .env", "files"), ("write into the live vfg folder", "files"),
           ("mc switched to the live properties after its check passed", "files"))

# The escape test (Builder C's, integration/testserver_escape/test.py): a vfg test file with __CFG__
# for {"mc", "vfg", "rcon_port", "game_port", "api_port", "lock"}, the live server's, that prints
# `ESCAPE     <attempt>: REACHED …` or `ESCAPE     <attempt>: refused, …` for each attempt to get
# round the sandbox. In the PC's user sandbox (the same uid as the live server) what gets through
# is a known limitation, INFO; on the box's system sandbox (--box) every attempt must be refused.
ESCAPE_TEST = os.path.join(HERE, "testserver_escape", "test.py")


def escape_test(cfg):
    """ESCAPE_TEST filled with `cfg`, or None while it is not there."""
    try:
        with open(ESCAPE_TEST, encoding="utf-8") as f:
            return f.read().replace("__CFG__", json.dumps(cfg))
    except OSError:
        return None


def escapes(run):
    """{attempt: "REACHED …" or "refused, …"} of a run of the escape test. An attempt's name may hold
    a colon (1.1.1.1:443); a count line ("a live server process is visible: 9") reached unless none."""
    out = {}
    for ln in run.out.splitlines():
        if not ln.startswith("ESCAPE "):
            continue
        body = ln[7:].strip()
        m = re.match(r"^(.*?): ((?:REACHED|refused)\b.*)$", body)
        if m:
            out[m.group(1)] = m.group(2)
            continue
        k, _, v = body.rpartition(": ")
        if k:
            out[k] = "refused, %s" % v if v.strip() in ("none", "0", "") else "REACHED %s" % v
    return out


def judge_reaches(run, must):
    """(refused, reached, missing, every reach's line) of the reaches whose kind is in `must`."""
    probes = dict((p.split(":", 1)[0], p.split(":", 1)[1].strip()) for p in run.probes if ":" in p)
    kinds = [k for k, kind in REACHES if kind in must]
    reached = [k for k in kinds if k in probes and not probes[k].startswith("refused")]
    missing = [k for k in kinds if k not in probes]
    return [k for k in kinds if k not in reached and k not in missing], reached, missing, probes


# ------------------------------------------------------------------ the board

class Board:
    """Every check: PASS, FAIL or INFO, with its numbers."""

    def __init__(self, log):
        self.rows, self.log = [], log

    def row(self, section, check, ok, numbers=""):
        word = "INFO" if ok is None else "PASS" if ok else "FAIL"
        self.rows.append({"section": section, "check": check, "result": word, "numbers": numbers})
        line = "%-4s  %-9s %s%s" % (word, section, check, " — " + numbers if numbers else "")
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()
        return ok

    def failed(self):
        return [r for r in self.rows if r["result"] == "FAIL"]


class TestRun:
    """One `vfg test`: its exit, output, times, and what its lines said."""

    def __init__(self, name, code, out, err, t0, t1, tag=""):
        self.name, self.code, self.out, self.err, self.t0, self.t1, self.tag = name, code, out, err, t0, t1, tag
        self.wall = t1 - t0
        self.server = {}
        self.checks, self.iqd, self.probes = [], {}, []
        for ln in out.splitlines():
            m = SERVER_LINE.match(ln.strip())
            if m and not self.server:
                self.server = {"mc": m.group(1), "port": m.group(2), "said": m.group(3)}
                continue
            m = CHECK_LINE.match(ln.strip())
            if m:
                self.checks.append((m.group(1), m.group(2), m.group(3)))
                continue
            if ln.startswith("IQD "):
                key, _, value = ln[4:].strip().partition(" ")
                try:
                    self.iqd[key] = json.loads(value)
                except ValueError:
                    self.iqd[key] = value
            if ln.startswith("PROBE "):
                self.probes.append(ln[6:].strip())

    @property
    def said(self):
        return self.server.get("said", "")

    @property
    def passed(self):
        return self.code == 0 and bool(self.checks) and all(c[0] == "PASS" for c in self.checks)

    @property
    def fails(self):
        return [c for c in self.checks if c[0] != "PASS"]

    @property
    def queue_s(self):
        m = re.search(r"([\d.]+) s in the queue", self.said)
        return float(m.group(1)) if m else 0.0

    @property
    def ready_s(self):
        """What the fresh world took: the wait for the last test's restart, or a restart."""
        m = re.search(r"a fresh world: [^;]*?, ([\d.]+) s", self.said)
        return float(m.group(1)) if m else 0.0

    @property
    def sandbox(self):
        m = re.search(r"(sandboxed: [^;]+|NOT sandboxed[^;]*)", self.said)
        return m.group(1) if m else "?"

    @property
    def last_err(self):
        lines = [ln.strip() for ln in self.err.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    @property
    def overhead_s(self):
        """From the call to the test's first line: deploy, queue, the fresh world, the sandbox."""
        return self.iqd["started"] - self.t0 if isinstance(self.iqd.get("started"), (int, float)) else None

    def short(self):
        return "exit %d, %.1f s%s%s" % (self.code, self.wall, ", " + self.said if self.said else "",
                                        ", " + self.last_err if self.code else "")


# ------------------------------------------------------------------ the suite

class Suite:
    def __init__(self, a, out):
        self.a, self.out = a, out
        self.name = a.name
        self.srv = realmc.Server(self.name)
        self.unit = realmc.TestUnit(self.srv)
        self.log = open(os.path.join(out, "e2e.log"), "a", encoding="utf-8")
        self.board = Board(self.log)
        self.env = None
        self.made = os.path.join(REPO, "made", self.name)
        self.baseline = None
        self.tests = []          # every TestRun
        self.lock = threading.Lock()
        self.live_conns = 0      # this suite's RCON connections to the live server

    # -- plumbing
    def note(self, msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def vfg(self, *words, extra=None, timeout=300):
        env = dict(self.env)
        env.update(extra or {})
        r = subprocess.run(["vfg", *words], env=env, capture_output=True, text=True, timeout=timeout)
        return r

    def vfg_test(self, name, *args, extra=None, tag="", timeout=900):
        env = dict(self.env)
        env.update(extra or {})
        t0 = time.time()
        r = subprocess.run(["vfg", "test", name, *args], env=env, capture_output=True, text=True, timeout=timeout)
        run = TestRun(name, r.returncode, r.stdout, r.stderr, t0, time.time(), tag)
        with self.lock:
            self.tests.append(run)
            self.log.write("\n=== vfg test %s %s [%s] exit %d, %.1f s\n%s%s\n" % (
                name, " ".join(args), tag, r.returncode, run.wall, r.stdout, ("--- stderr\n" + r.stderr) if r.stderr else ""))
            self.log.flush()
        return run

    def rcon(self, props, *cmds):
        r = realmc.Rcon(props)
        try:
            return [r.command(c) for c in cmds]
        finally:
            r.close()

    def live(self, *cmds):
        """Commands to the LIVE server, on one RCON connection of this suite's, counted: each writes a
        pair of RCON Client lines into the live console, and only those may be there (console_row)."""
        self.live_conns += 1
        return self.rcon(self.srv.props, *cmds)

    def console_row(self, section, offset, conns, allowed=0, why=""):
        """The live console since `offset`: no line but the RCON Client lines of this suite's own
        connections since then (and `allowed` more, INFO's: an escape probe's connection)."""
        lines = self.console_since(offset)
        started = [ln for ln in lines if "Thread RCON Client" in ln and ln.rstrip().endswith("started")]
        other = [ln for ln in lines if "Thread RCON Client" not in ln]
        mine = self.live_conns - conns
        ok = not other and mine <= len(started) <= mine + allowed
        return self.board.row(section, "the LIVE console: no new line but this suite's own RCON connections%s" % why, ok,
                              "%d new lines: %d RCON connections (this suite's %d%s), %d other%s" % (
                                  len(lines), len(started), mine, ", + up to %d allowed" % allowed if allowed else "",
                                  len(other), ": " + " | ".join(other[:3]) if other else ""))

    def write(self, rel, text):
        path = os.path.join(self.made, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def fresh_cfg(self, name, junk=None, expect=None, player="Steve", others=("Alex",), watch_slowness=False):
        return FRESH_TEST.replace("__NAME__", name).replace("__CFG__", json.dumps({
            "junk_at": JUNK_AT, "under_stage": UNDER_STAGE, "probes": PROBES, "expect": expect or {},
            "junk": junk, "player": player, "others": list(others), "watch_slowness": watch_slowness}))

    def sandbox_unit(self):
        """The sandbox's unit name, as toolkit/remote/test.py names it."""
        return "vfg-test-" + hashlib.sha1(os.path.realpath(self.srv.vfg_test).encode("utf-8")).hexdigest()[:10]

    def leftovers(self):
        """Processes started for the test server's tests still alive: (pid, how it was found)."""
        marker = ("VFG_RUN_DIR=" + os.path.join(self.srv.vfg_test, "run")).encode("utf-8")
        real = ("VFG_RUN_DIR=" + os.path.realpath(os.path.join(self.srv.vfg_test, "run"))).encode("utf-8")
        found = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                with open("/proc/%s/environ" % pid, "rb") as f:
                    env = f.read().split(b"\0")
            except OSError:
                continue
            if marker in env or real in env or b"IQD_RUNAWAY=child" in env:
                try:
                    with open("/proc/%s/cmdline" % pid, "rb") as f:
                        argv = [w.decode("utf-8", "replace") for w in f.read().split(b"\0") if w]
                    cmd = " ".join([os.path.basename(argv[0])] + [" ".join(w.split()) for w in argv[1:]])[:70]
                except (OSError, IndexError):
                    cmd = "?"
                found.append((int(pid), cmd))
        return found

    def kill(self, pids):
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def wait_test_server(self, timeout=240):
        """Until the test server's unit is active and its RCON answers: seconds, or None."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.unit.state() == "active" and self.unit.java_pid():
                try:
                    self.rcon(self.srv.test_props, "list")
                    return time.monotonic() - t0
                except OSError:
                    pass
            time.sleep(0.5)
        return None

    def console_size(self):
        try:
            return os.path.getsize(self.srv.console)
        except OSError:
            return 0

    def console_since(self, offset):
        try:
            with open(self.srv.console, "rb") as f:
                f.seek(offset)
                return [ln for ln in f.read().decode("utf-8", "replace").splitlines() if ln.strip()]
        except OSError:
            return []

    # -- setup
    def setup(self):
        up = [os.path.join(REALMC, "up"), self.name, "--test-server"] + (["--fresh"] if self.a.fresh_live else [])
        t0 = time.monotonic()
        r = subprocess.run(up, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise SystemExit("realmc %s failed: %s" % (" ".join(up[1:]), (r.stderr or r.stdout)[-600:]))
        self.note("realmc up %s --test-server: %.1f s" % (self.name, time.monotonic() - t0))
        self.env = vfg_env(self.name)
        self.env.pop("VFG_TEST_BOX", None)        # the box's own test server, as on the box
        for k in ("VFG_TEST_SANDBOX", "VFG_TEST_BOOT_S", "VFG_TEST_QUEUE_S", "VFG_TEST_TIMEOUT_S"):
            self.env.pop(k, None)
        self.env["VFG_SERVER"] = self.name
        if os.path.isdir(self.made) and not only_mine(self.made):
            dest = os.path.join(ROOT, "made-aside", "%s-%s" % (self.name, time.strftime("%Y%m%d-%H%M%S")))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(self.made, dest)
            self.note("made/%s set aside into %s" % (self.name, dest))
        self.write("actions/iqd_blizzard.py", BLIZZARD_ACTION)
        self.write("scripts/iqd_blizzard/main.py", BLIZZARD_SCRIPT)
        self.write("tests/iqd_blizzard.py", BLIZZARD_TEST)
        self.write("actions/iqd_runaway.py", RUNAWAY_ACTION)
        self.write("scripts/iqd_runaway/main.py", RUNAWAY_SCRIPT)
        self.write("tests/iqd_runaway.py", RUNAWAY_TEST)
        r = self.vfg("action", "list", "--json", timeout=180)
        if r.returncode != 0 or "iqd_blizzard" not in r.stdout or "iqd_runaway" not in r.stdout:
            raise SystemExit("vfg action list does not have the made actions: %s" % (r.stderr or r.stdout)[-400:])
        for player, phrase, action in ((CASTER, "freeze", "iqd_blizzard"), (CASTER, "blizzard", "iqd_runaway")):
            r = self.vfg("action", "run", "incantation_create", "player=" + player, "phrase=" + phrase,
                         "action=" + action, "--task", action, extra={"VFG_INCANT_ALIAS_MODEL": "off"}, timeout=180)
            if r.returncode != 0:
                raise SystemExit("binding %r failed: %s" % (phrase, (r.stderr or r.stdout)[-400:]))
        self.note("made/%s: iqd_blizzard and iqd_runaway with their tests; %s's \"freeze\" and \"blizzard\" bound on "
                  "the live server" % (self.name, CASTER))
        state = realmc.read_json(os.path.join(self.srv.mc_test, ".vfg-test-server"), {})
        self.note("test server %s, marker %s; the sandbox's unit %s" % (self.srv.mc_test, json.dumps(state),
                                                                       self.sandbox_unit()))

    def take_baseline(self):
        """The fresh world, as a test sees it before it changes anything: the probes of FRESH_TEST."""
        name = "iqd_baseline"
        self.write("tests/%s.py" % name, self.fresh_cfg(name))
        run = self.vfg_test(name, tag="baseline")
        seen = run.iqd.get("seen") if isinstance(run.iqd.get("seen"), dict) else None
        sane = bool(seen) and run.passed and seen.get("objectives") == "There are no objectives" and \
            not any("iqd" in str(v).lower() for v in seen.values())
        self.board.row("fresh", "the baseline: the fresh world as a test sees it", sane,
                       "%d probes; %s" % (len(seen or {}), run.short()))
        if not sane:
            raise SystemExit("no sane baseline: see %s" % self.log.name)
        self.baseline = seen
        self.note("baseline: %s" % json.dumps(seen)[:1500])

    # -- night
    def snapshot(self):
        cmds = []
        for p in (VICTIM, CASTER):
            for path in ("Pos", "Rotation", "Health", "foodLevel", "active_effects", "Inventory", "XpLevel", "Fire"):
                cmds.append(("%s %s" % (p, path), "data get entity %s %s" % (p, path)))
        cmds += [("online", "list"), ("objectives", "scoreboard objectives list"), ("forceloads", "forceload query"),
                 ("spawn_mobs", "gamerule spawn_mobs"), ("advance_time", "gamerule advance_time"),
                 ("keep_inventory", "gamerule keep_inventory"), ("time", "time query day"),
                 ("raining", 'execute if predicate {type:"minecraft:weather_check",raining:true}'),
                 ("worldborder", "worldborder get"), ("difficulty", "difficulty"),
                 ("arena gold", "execute if block %d %d %d gold_block" % ARENA_GOLD),
                 ("stage gold", "execute if block %d %d %d gold_block" % STAGE),
                 ("stage barrier floor", "execute if block %d 295 %d air" % (STAGE[0], STAGE[2])),
                 ("tagged vfg_test", "execute if entity @e[tag=vfg_test]")]
        got = dict(zip([k for k, _ in cmds], self.live(*[c for _, c in cmds])))
        files = {}
        for path in (os.path.join(self.srv.vfg, "incantations.json"), self.srv.props):
            try:
                with open(path, "rb") as f:
                    files[os.path.basename(path)] = hashlib.sha256(f.read()).hexdigest()[:16]
            except OSError:
                files[os.path.basename(path)] = None
        run = os.path.join(self.srv.vfg, "run")
        files["run/"] = sorted("%s %d %d" % (n, os.path.getsize(os.path.join(run, n)), os.path.getmtime(os.path.join(run, n)))
                               for n in (os.listdir(run) if os.path.isdir(run) else []))
        files["vfg/"] = sorted("%s %d" % (n, os.path.getmtime(os.path.join(self.srv.vfg, n)))
                               for n in os.listdir(self.srv.vfg))
        animals = self.live("execute if entity @e[type=!player]")[0]
        return got, files, animals

    def night(self):
        hostile = ["execute positioned 0 85 0 run kill @e[type=%s,distance=..96]" % m
                   for m in ("zombie", "creeper", "skeleton", "spider", "witch", "enderman", "slime", "drowned")]
        self.live("gamerule spawn_mobs false", *hostile,     # nothing near them that could hurt them
                  "forceload add %d %d" % (STAGE[0], STAGE[2]),
                  "setblock %d %d %d gold_block" % ARENA_GOLD, "setblock %d %d %d gold_block" % STAGE,
                  "player %s spawn at 3.5 85 0.5 facing 90 0 in minecraft:overworld in survival" % VICTIM,
                  "player %s spawn at 0.5 85 0.5 facing -90 0 in minecraft:overworld in survival" % CASTER)
        end = time.time() + 15
        while time.time() < end and not all(p in self.live("list")[0] for p in (VICTIM, CASTER)):
            time.sleep(0.3)
        self.live("effect give %s luck infinite 0 true" % VICTIM, "effect give %s night_vision infinite 0 true" % CASTER,
                  "effect give %s saturation 5 10 true" % VICTIM, "effect give %s saturation 5 10 true" % CASTER)
        time.sleep(6)   # the saturation over, both at full health
        offset, conns = self.console_size(), self.live_conns
        before, files0, animals0 = self.snapshot()
        self.note("live: %s at %s, %s at %s, health %s / %s" % (
            VICTIM, before["%s Pos" % VICTIM].split(": ")[-1], CASTER, before["%s Pos" % CASTER].split(": ")[-1],
            before["%s Health" % VICTIM].split(": ")[-1], before["%s Health" % CASTER].split(": ")[-1]))
        runs = [self.vfg_test("iqd_blizzard", tag="night %d" % (i + 1)) for i in range(self.a.night_runs)]
        ok = [x for x in runs if x.passed]
        self.board.row("night", "the aura's test passes on the test server, %d runs" % len(runs), len(ok) == len(runs),
                       "%d/%d passed; walls %s s" % (len(ok), len(runs), ", ".join("%.1f" % x.wall for x in runs)))
        for x in runs:
            if not x.passed:
                self.note("night run: %s; %s" % (x.short(), "; ".join("%s %s: %s" % c for c in x.fails)))
        on_test = [x for x in runs if os.path.realpath(x.server.get("mc", "")) == os.path.realpath(self.srv.mc_test)]
        self.board.row("night", "every run's SERVER line names the test server, sandboxed", len(on_test) == len(runs) and
                       all(x.sandbox.startswith("sandboxed") for x in runs),
                       "%s (RCON %s); %s" % (runs[0].server.get("mc"), runs[0].server.get("port"), runs[0].sandbox))
        hurt = [c for x in runs for c in x.checks if c[1].startswith("KrownJM, beside her, is hurt")]
        self.board.row("night", "the TEST server's KrownJM was hurt and slowed by it each run",
                       len(hurt) == len(runs) and all(c[0] == "PASS" for c in hurt),
                       "; ".join(c[2] for c in hurt)[:200])
        after, files1, animals1 = self.snapshot()
        changed = sorted(k for k in before if before[k] != after[k])
        players = [k for k in changed if k.startswith((VICTIM, CASTER))]
        self.board.row("night", "the LIVE KrownJM and Styrkaeopteryx unchanged (position, rotation, health, food, "
                       "effects, items, xp, fire)", not players,
                       "%d values compared; %s" % (sum(1 for k in before if k.startswith((VICTIM, CASTER))),
                                                   "changed: " + ", ".join(players) if players else "all equal"))
        world = [k for k in changed if not k.startswith((VICTIM, CASTER))]
        n_world = len([k for k in before if not k.startswith((VICTIM, CASTER))])
        self.board.row("night", "the LIVE world unchanged (both gold blocks, forceloads, objectives, gamerules, time, "
                       "weather, border, difficulty, players online)", not world,
                       "changed: " + ", ".join("%s: %r -> %r" % (k, before[k][:60], after[k][:60]) for k in world)
                       if world else "%d values equal" % n_world)
        fchanged = sorted(k for k in files0 if files0[k] != files1[k])
        self.board.row("night", "the LIVE incantations.json, server.properties, run/ and vfg folder unchanged",
                       not fchanged, "changed: %s" % fchanged if fchanged else "sha256 and listings equal")
        self.console_row("night", offset, conns)
        self.board.row("night", "the LIVE entity count (the arena's animals; INFO)", None,
                       "%s -> %s" % (animals0, animals1))

    # -- runaway
    def runaway(self):
        t0 = time.time()
        run = self.vfg_test("iqd_runaway", "--timeout", "20", tag="runaway sandboxed")
        stopped = run.code == 1 and "ran past its 20 s" in run.err
        self.board.row("runaway", "the runaway's test is stopped at its timeout (--timeout 20)", stopped,
                       "%s" % run.short())
        self.board.row("runaway", "...in bounded time", run.wall < 20 + 5 + 120 + 60,
                       "%.1f s from the call to its end (20 s timeout, 5 s to SIGKILL; %s)" % (run.wall, run.sandbox))
        time.sleep(1.0)
        left = self.leftovers()
        self.board.row("runaway", "no process of it is left: the test, its copy, the copy's own copy (TERM ignored)",
                       not left, "%d left%s" % (len(left), ": " + "; ".join("%d %s" % p for p in left) if left else ""))
        self.kill([p for p, _ in left])
        unit = subprocess.run(["systemctl", "--user", "is-active", self.sandbox_unit() + ".service"],
                              capture_output=True, text=True).stdout.strip()
        self.board.row("runaway", "its sandbox unit is gone", unit != "active", "%s: %s" % (self.sandbox_unit(), unit))
        name = "iqd_after_runaway"
        self.write("tests/%s.py" % name, self.fresh_cfg(name, expect=self.baseline, player=CASTER, others=(VICTIM,),
                                                        watch_slowness=True))
        nxt = self.vfg_test(name, tag="after the runaway")
        self.board.row("runaway", "the next test starts on a fresh world: no marker, objective, block or slowness of it",
                       nxt.passed, "%s%s" % (nxt.short(), "; " + "; ".join("%s: %s" % (c[1], c[2]) for c in nxt.fails)
                                            if nxt.fails else ""))
        self.note("runaway, sandboxed: %.1f s in all" % (time.time() - t0))
        if self.a.plain_runaway:
            run = self.vfg_test("iqd_runaway", "--timeout", "20", extra={"VFG_TEST_SANDBOX": "none"},
                                tag="runaway plain")
            time.sleep(1.0)
            left = self.leftovers()
            self.board.row("runaway", "without the sandbox (VFG_TEST_SANDBOX=none, the fallback; INFO)", None,
                           "%s; %d process(es) left after it: %s" % (run.short(), len(left),
                                                                     "; ".join("%d %s" % p for p in left)[:200]))
            self.kill([p for p, _ in left])
            nxt = self.vfg_test(name, tag="after the plain runaway")
            self.board.row("runaway", "...and once its leftovers are killed, the next test is fresh again", nxt.passed,
                           nxt.short())

    # -- fresh
    def fresh(self):
        runs = []
        name = "iqd_fresh"
        for i in range(1, self.a.fresh_tests + 1):
            self.write("tests/%s.py" % name, self.fresh_cfg(name, junk="f%d" % i, expect=self.baseline))
            x = self.vfg_test(name, tag="fresh %d" % i)
            runs.append(x)
            if not x.passed:
                self.note("fresh %d: %s; %s" % (i, x.short(), "; ".join("%s: %s" % (c[1], c[2]) for c in x.fails)[:600]))
        ok = [x for x in runs if x.passed]
        probes = len(runs[0].checks) if runs else 0
        self.board.row("fresh", "%d tests in a row, each on a fresh world, each leaving junk" % len(runs),
                       len(ok) == len(runs), "%d/%d passed, %d checks each (%d probes against the baseline)" % (
                           len(ok), len(runs), probes, len(self.baseline or {})))
        self.board.row("fresh", "per test: wall / overhead before its first line / the fresh world's wait / queue", None,
                       stats(runs))
        self.fresh_runs = runs

    # -- parallel
    def parallel(self):
        streams = "abcd"[:self.a.streams]
        results = {s: [] for s in streams}

        def stream(s):
            name = "iqd_par_%s" % s
            for i in range(1, self.a.per_stream + 1):
                with self.lock:
                    self.write("tests/%s.py" % name, self.fresh_cfg(name, junk="%s%d" % (s, i), expect=self.baseline))
                results[s].append(self.vfg_test(name, tag="stream %s %d" % (s, i)))

        t0 = time.time()
        threads = [threading.Thread(target=stream, args=(s,)) for s in streams]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        wall = time.time() - t0
        runs = [x for s in streams for x in results[s]]
        ok = [x for x in runs if x.passed]
        self.board.row("parallel", "%d streams of %d tests at once: all complete and pass" % (len(streams), self.a.per_stream),
                       len(ok) == len(runs) == len(streams) * self.a.per_stream,
                       "%d/%d passed in %.0f s in all" % (len(ok), len(runs), wall))
        for x in runs:
            if not x.passed:
                self.note("%s: %s; %s" % (x.tag, x.short(), "; ".join("%s: %s" % (c[1], c[2]) for c in x.fails)[:600]))
        spans = sorted((x.iqd.get("started"), x.iqd.get("checked"), x.tag) for x in runs
                       if isinstance(x.iqd.get("started"), (int, float)) and isinstance(x.iqd.get("checked"), (int, float)))
        overlaps = [(a[2], b[2]) for a, b in zip(spans, spans[1:]) if b[0] < a[1]]
        self.board.row("parallel", "no two tests on the server at once (their windows, by the clock)", not overlaps and
                       len(spans) == len(runs), "%d windows, %d overlapping%s" % (
                           len(spans), len(overlaps), ": %s" % overlaps[:3] if overlaps else ""))
        self.board.row("parallel", "per test: wall / overhead / the fresh world's wait / queue", None, stats(runs))
        worst = max(runs, key=lambda x: x.queue_s) if runs else None
        if worst is not None:   # the queue takes whoever polls first: no order of arrival
            during = [x for x in runs if x.tag[:8] != worst.tag[:8] and worst.t0 < x.t1 < worst.t0 + worst.queue_s]
            self.board.row("parallel", "the longest wait in the queue (INFO: no first-come order; vfg test gives up "
                           "past its queue budget, 245 s by default)", None, "%s: %.1f s, while the other streams "
                           "finished %d tests" % (worst.tag, worst.queue_s, len(during)))

    # -- heal
    def heal(self):
        name = "iqd_heal"

        def next_test(tag, extra=None):
            self.write("tests/%s.py" % name, self.fresh_cfg(name, junk="h%d" % len(self.tests), expect=self.baseline))
            return self.vfg_test(name, extra=extra, tag=tag)

        # a. java SIGKILLed while it waits for the next test
        self.wait_test_server()
        pid = self.unit.java_pid()
        os.kill(pid, signal.SIGKILL)
        x = next_test("heal: java killed")
        self.board.row("heal", "the test server's java SIGKILLed: the next test brings it back and passes", x.passed,
                       "java %d killed; %s" % (pid, x.short()))
        # b. the unit stopped
        self.wait_test_server()
        r = subprocess.run([os.path.join(REPO, "toolkit", "tests", "bin", "systemctl"), "stop", "minecraft-test"],
                           env=dict(os.environ, FAKE_UNITS=os.path.join(self.srv.box, "units")), capture_output=True,
                           text=True, timeout=180)
        state = self.unit.state()
        x = next_test("heal: unit stopped")
        self.board.row("heal", "the unit stopped (systemctl stop minecraft-test): the next test restarts it and passes",
                       x.passed and state == "inactive", "stop exit %d, then %s; %s" % (r.returncode, state, x.short()))
        # c. wedged, with a short boot budget: exit 5 in bounded time, its message
        self.wait_test_server()
        time.sleep(2)
        pid = self.unit.java_pid()
        os.kill(pid, signal.SIGSTOP)
        try:
            x = next_test("heal: wedged, boot 20", extra={"VFG_TEST_BOOT_S": "20"})
        finally:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        self.board.row("heal", "java SIGSTOPped (its RCON port takes connections, nothing answers), VFG_TEST_BOOT_S=20: "
                       "exit 5 in bounded time", x.code == 5 and x.wall < 120, "%s" % x.short())
        self.board.row("heal", "...what it tells the implementer (INFO)", None, x.last_err[:300])
        back = self.wait_test_server(300)
        x = next_test("heal: after the wedge")
        self.board.row("heal", "...after SIGCONT, the next test recovers and passes", x.passed,
                       "the unit active again %s s after; %s" % ("%.0f" % back if back is not None else "never", x.short()))
        # d. wedged, with the defaults: the restart clears it
        if self.a.wedge_default:
            self.wait_test_server()
            time.sleep(2)
            pid = self.unit.java_pid()
            os.kill(pid, signal.SIGSTOP)
            try:
                x = next_test("heal: wedged, defaults")
            finally:
                try:
                    os.kill(pid, signal.SIGCONT)
                except OSError:
                    pass
            self.board.row("heal", "java SIGSTOPped, the defaults (boot 120 s): the restart kills it and the test passes",
                           x.passed and x.wall < 300, x.short())

    # -- refuse
    def refuse(self):
        offset, conns = self.console_size(), self.live_conns
        box = os.path.join(self.out, "boxes")
        os.makedirs(box, exist_ok=True)

        def settings(fname, **keys):
            path = os.path.join(box, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join("%s=%s\n" % kv for kv in keys.items()))
            return path

        live = settings("live.config", VFG_REMOTE_VFG=self.srv.vfg, VFG_REMOTE_MC=self.srv.mc)
        x = self.vfg_test("iqd_blizzard", extra={"VFG_TEST_BOX": live}, tag="refuse: live folders")
        self.board.row("refuse", "VFG_TEST_BOX naming the live folders: exit 3 at the PC", x.code == 3 and
                       "live server itself" in x.err, "%s" % x.short())
        half = settings("half.config", VFG_REMOTE_VFG=self.srv.vfg_test, VFG_REMOTE_MC=self.srv.mc)
        x = self.vfg_test("iqd_blizzard", extra={"VFG_TEST_BOX": half}, tag="refuse: live mc, test vfg")
        self.board.row("refuse", "...the test vfg folder with the live Minecraft folder: exit 3", x.code == 3, x.short())
        fake = os.path.join(self.srv.dir, "iqd-nomarker")
        os.makedirs(os.path.join(fake, "mc"), exist_ok=True)
        os.makedirs(os.path.join(fake, "vfg"), exist_ok=True)
        shutil.copy(self.srv.test_props, os.path.join(fake, "mc", "server.properties"))
        if not os.path.lexists(os.path.join(fake, "vfg", "python")):
            os.symlink(os.path.join(self.srv.vfg, "python"), os.path.join(fake, "vfg", "python"))
        nomark = settings("nomarker.config", VFG_REMOTE_VFG=os.path.join(fake, "vfg"),
                          VFG_REMOTE_MC=os.path.join(fake, "mc"))
        x = self.vfg_test("iqd_blizzard", extra={"VFG_TEST_BOX": nomark}, tag="refuse: no marker")
        self.board.row("refuse", "a test server folder without the marker: exit 3", x.code == 3 and
                       ".vfg-test-server" in x.err, x.short())
        link = os.path.join(self.srv.dir, "iqd-live-link")
        if not os.path.lexists(link):
            os.symlink(self.srv.mc, link)
        linked = settings("link.config", VFG_REMOTE_VFG=self.srv.vfg_test, VFG_REMOTE_MC=link)
        x = self.vfg_test("iqd_blizzard", extra={"VFG_TEST_BOX": linked}, tag="refuse: a link to live")
        self.board.row("refuse", "a link to the live Minecraft folder under another name: exit 3 (no marker there)",
                       x.code == 3, x.short())
        shutil.rmtree(fake, ignore_errors=True)
        os.remove(link)
        port = realmc.read_props(self.srv.props).get("rcon.port") or "25575"
        self.write("tests/iqd_reach.py", REACH_TEST.replace("__CFG__", json.dumps(
            {"mc": self.srv.mc, "vfg": self.srv.vfg, "rcon_port": port})))
        x = self.vfg_test("iqd_reach", tag="refuse: reach, sandboxed")
        refused, reached, missing, probes = judge_reaches(x, ("kit", "files"))
        self.board.row("refuse", "a test reaching for the live server, sandboxed: its files, its password, a write, mc "
                       "and a child given its properties, the kit switched by hand: every reach refused",
                       x.passed and not reached and not missing, "%s; %d refused%s%s" % (
                           x.sandbox, len(refused), "; REACHED: %s" % reached if reached else "",
                           "; no line for %s" % missing if missing else ""))
        props = realmc.read_props(self.srv.props)
        rcon_hits = 0   # connections to the live RCON that the PC lets through (INFO), for the console's count
        esc = escape_test({"mc": self.srv.mc, "vfg": self.srv.vfg, "rcon_port": port,
                           "game_port": props.get("server-port") or "25565", "api_port": "8787",
                           "lock": realmc.rcon_lock_path(int(port))})
        if esc is None:
            self.board.row("refuse", "...around the sandbox: the escape test (INFO)", None,
                           "%s is not there yet" % os.path.relpath(ESCAPE_TEST, REPO))
        else:
            self.write("tests/iqd_escape.py", esc)
            x = self.vfg_test("iqd_escape", tag="refuse: escape, sandboxed")
            got = escapes(x)
            self.board.row("refuse", "...around the sandbox: the escape test ran on the test server", x.code == 0 and
                           bool(got), "%s; %d attempts" % (x.short(), len(got)))
            through = dict((k, v) for k, v in got.items() if not v.startswith("refused"))
            self.board.row("refuse", "...what gets round the PC's user sandbox (INFO: the same uid as the live "
                           "server, a known PC limitation; on the box every attempt must be refused: --box)", None,
                           "%d of %d refused; through: %s" % (len(got) - len(through), len(got), "; ".join(
                               "%s: %s" % (k, v[:30]) for k, v in through.items())[:700] or "none"))
            rcon_hits += sum(1 for k in through if re.search(r"live RCON (port|with)", k))
        x = self.vfg_test("iqd_reach", extra={"VFG_TEST_SANDBOX": "none"}, tag="refuse: reach, plain")
        refused, reached, missing, probes = judge_reaches(x, ("kit",))
        rcon_hits += sum(1 for k, v in probes.items() if k.startswith("mc switched") and v.startswith("REACHED"))
        self.board.row("refuse", "...without the sandbox (VFG_TEST_SANDBOX=none): the kit still refuses mc and a "
                       "child given the live server.properties (NotTestServer)",
                       x.passed and not reached and not missing and x.sandbox.startswith("NOT sandboxed"),
                       "%s; %s" % (x.sandbox[:40], "; ".join(c[0] + " " + c[1] for c in x.checks)))
        files = [k for k, kind in REACHES if kind == "files"]
        self.board.row("refuse", "...without the sandbox, what gets through (INFO)", None,
                       "; ".join("%s: %s" % (k, probes.get(k, "?")[:28]) for k in files))
        self.console_row("refuse", offset, conns, allowed=rcon_hits,
                         why=" (and the connections the PC lets through: INFO above)")

    # -- guard
    def guard(self):
        test_file = self.write("tests/iqd_guard.py", "x = 1\n")
        code = GUARD_CHECK % {"repo": REPO, "server": self.name, "test": test_file,
                              "prompt": os.path.join(REPO, "brain", "prompts", "implementer.md")}
        r = subprocess.run(["uv", "run", "--quiet", "python", "-c", code], cwd=os.path.join(REPO, "brain"),
                           capture_output=True, text=True, timeout=300)
        try:
            got = json.loads(r.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            self.board.row("guard", "the guard's checks ran", False, (r.stderr or r.stdout)[-300:])
            return
        for check, ok, numbers in got:
            self.board.row("guard", check, ok, numbers)

    # -- the end
    def cleanup(self):
        if self.env is None:   # no server was set up here (the guard alone): nothing of it to clean
            if not self.a.keep_made and os.path.isdir(self.made) and only_mine(self.made):
                os.remove(os.path.join(self.made, "tests", "iqd_guard.py"))
            return
        left = self.leftovers()
        if left:
            self.note("killing %d leftover process(es): %s" % (len(left), left))
            self.kill([p for p, _ in left])
        pid = self.unit.java_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        if self.env is not None:
            try:
                self.live("player %s kill" % VICTIM, "player %s kill" % CASTER)
            except OSError:
                pass
        if not self.a.keep_made and os.path.isdir(self.made):
            shutil.rmtree(self.made, ignore_errors=True)
        if not self.a.keep_up and self.env is not None:
            r = subprocess.run([os.path.join(REALMC, "down"), self.name], capture_output=True, text=True, timeout=300)
            self.note("realmc down %s: exit %d" % (self.name, r.returncode))
            time.sleep(5)   # the last test's restart (--no-block) can start it again after down: stopped once more
            if self.unit.sup_pid():
                subprocess.run([sys.executable, os.path.join(REALMC, "realmc.py"), "testserver", self.name, "--stop"],
                               capture_output=True, text=True, timeout=300)
                self.note("its test server was up again after down (the last test's restart): stopped")


# The probes every fresh test compares with the baseline (FRESH_TEST adds the junk spots and Junker).
PROBES = {"objectives": "scoreboard objectives list", "bossbars": "bossbar list", "teams": "team list",
          "worldborder": "worldborder get", "forceloads": "forceload query",
          "keep_inventory": "gamerule keep_inventory", "fall_damage": "gamerule fall_damage",
          "spawn_mobs": "gamerule spawn_mobs", "advance_time": "gamerule advance_time",
          "mob_griefing": "gamerule mob_griefing", "time": "time query day", "difficulty": "difficulty",
          "raining": 'execute if predicate {type:"minecraft:weather_check",raining:true}',
          "thundering": 'execute if predicate {type:"minecraft:weather_check",thundering:true}',
          "players online": "list", "entities": "execute if entity @e"}

# The guard at unit level, run in brain/'s environment: prints one JSON list of [check, ok, numbers].
GUARD_CHECK = r'''
import json, os, re
from brain.implementers import guard
G = guard.GuardContext(repo=%(repo)r, server=%(server)r, task="iqd_guard", test_server=True)
TEST = %(test)r
call = lambda cmd: {"tool_name": "Bash", "tool_input": {"command": cmd}}
out = []
def edit(n):
    with open(TEST, "w") as f:
        f.write("x = %%d\n" %% n)
edit(1)
said = [guard.test_reason(call("vfg test iqd_guard"), G)]
said.append(guard.test_reason(call("vfg test iqd_guard"), G))
with open(TEST, "a") as f:
    f.write("# only a comment\n")
said.append(guard.test_reason(call("vfg test iqd_guard -n 60"), G))
for n in (2, 3, 4):
    edit(n)
    said.append(guard.test_reason(call("vfg test iqd_guard"), G))
edit(5)
said.append(guard.test_reason(call("vfg test iqd_guard"), G))
out.append(["the first run of a test goes through", said[0] is None, repr(said[0])])
out.append(["a rerun with nothing changed is refused", said[1] == guard.DENY.test_again, (said[1] or "")[:90]])
out.append(["a rerun after a comment-only edit is refused too", said[2] == guard.DENY.test_again, (said[2] or "")[:60]])
out.append(["runs 2-4, each after a change, go through", said[3:6] == [None, None, None], repr(said[3:6])])
out.append(["the fifth run is refused (4 a run)", said[6] == guard.DENY.test_cap, (said[6] or "")[:90]])
p = guard.probe_reason(call("vfg cmd 'data get entity KrownJM Health'"), G)
out.append(["with its tests spent, a live probe is refused", p == guard.DENY.tests_spent, (p or "")[:90]])
G.new_run()
ps = [guard.probe_reason(call("vfg cmd 'data get entity KrownJM Health'"), G) for _ in range(4)]
out.append(["with a test server, 3 live probes a run, the 4th refused", ps[:3] == [None] * 3 and ps[3] == guard.DENY.probes_spent,
            repr([x and x[:40] for x in ps])])
G.new_run()
edit(6)
first = guard.test_reason(call("vfg test iqd_guard"), G)
again = guard.test_reason(call("vfg test iqd_guard"), G)   # as the prompt says after an exit 4 or 5: once more
prompt = open(%(prompt)r).read()
rule = [ln for ln in prompt.splitlines() if "4 or 5" in ln and "vfg test" in ln]
rule = rule[0] if rule else ""
out.append(["the prompt: vfg test exiting 4 or 5 is reported untested, never tested on the live server",
            bool(rule) and "never" in rule and "live" in rule and ("untested" in rule or "not tested" in rule), rule.strip()[:150] or "no such line"])
if re.search(r"run it (once more|again)|retry|rerun", rule):   # the prompt asks for a rerun: the guard must let it through
    out.append(["the prompt's rerun after exit 4/5 (nothing changed) goes through the guard", first is None and again is None,
                "the rerun: %%s" %% ((again or "let through")[:100])])
else:
    out.append(["the prompt asks for no rerun after exit 4/5, which the guard would refuse unchanged", again is not None,
                "an unchanged rerun: %%s" %% ((again or "let through")[:60])])
print(json.dumps(out))
'''


def only_mine(made):
    """Does made/NAME/ hold only what this suite writes there (iqd_* in actions/, scripts/, tests/)?"""
    for kind in os.listdir(made):
        folder = os.path.join(made, kind)
        if kind not in ("actions", "scripts", "tests") or not os.path.isdir(folder):
            return False
        if any(not (n.startswith("iqd_") or n == "__pycache__") for n in os.listdir(folder)):
            return False
    return True


def stats(runs):
    """wall / overhead / fresh-world wait / queue: median and most, in seconds."""
    def mm(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return "-"
        return "%.1f/%.1f" % (xs[len(xs) // 2], xs[-1])
    return "median/most: wall %s, overhead %s, fresh world %s, queue %s (n=%d)" % (
        mm([x.wall for x in runs]), mm([x.overhead_s for x in runs]), mm([x.ready_s for x in runs]),
        mm([x.queue_s for x in runs]), len(runs))


def table(board):
    rows = board.rows
    w = max(len(r["check"]) for r in rows) if rows else 10
    w = min(w, 92)
    out = ["", "=" * 120, "%-9s %-4s  %-*s  %s" % ("section", "", w, "check", "numbers"), "-" * 120]
    for r in rows:
        out.append("%-9s %-4s  %-*s  %s" % (r["section"], r["result"], w, r["check"][:w], r["numbers"]))
    failed = board.failed()
    out += ["-" * 120, "%d checks: %d pass, %d fail, %d info" % (
        len(rows), sum(1 for r in rows if r["result"] == "PASS"), len(failed),
        sum(1 for r in rows if r["result"] == "INFO")), "=" * 120]
    return "\n".join(out)


SECTIONS = ("night", "runaway", "fresh", "parallel", "heal", "refuse", "guard")


def box_mode(a, out):
    """--box: on the REAL box's test server, through this checkout's vfg and the owner's ~/.vfg/config
    (VFG_REPO this checkout, VFG_TEST_BOX unset): REACH_TEST (every reach refused) and the escape
    test (ESCAPE_TEST: every attempt round the system sandbox refused). Box-safe: both run only on
    the test server, where the fake players are; the live server (vanilla, its players) is only
    read by vfg test (its incantations), never sent a command, and nothing is written there. Only
    made/SERVER/tests/iqd_reach.py and iqd_escape.py are written on this PC, and removed after."""
    log = open(os.path.join(out, "box.log"), "a", encoding="utf-8")
    board = Board(log)
    env = dict(os.environ, VFG_REPO=REPO, VFG_SERVER=a.server)
    for k in ("VFG_TEST_BOX", "VFG_TEST_SANDBOX", "VFG_TEST_BOOT_S", "VFG_TEST_QUEUE_S", "VFG_TEST_TIMEOUT_S"):
        env.pop(k, None)
    tests = os.path.join(REPO, "made", a.server, "tests")
    live = {"mc": a.live_mc, "vfg": a.live_vfg, "rcon_port": str(a.live_rcon_port)}
    esc = escape_test(dict(live, game_port=str(a.live_game_port), api_port=str(a.live_api_port),
                           lock="/tmp/vfg-rcon-%d.lock" % a.live_rcon_port))
    runs = {}
    for name, text in (("iqd_reach", REACH_TEST.replace("__CFG__", json.dumps(live))), ("iqd_escape", esc)):
        if text is None:
            board.row("box", "the escape test", False, "%s is not there" % os.path.relpath(ESCAPE_TEST, REPO))
            continue
        os.makedirs(tests, exist_ok=True)
        path = os.path.join(tests, name + ".py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            t0 = time.time()
            r = subprocess.run([sys.executable, os.path.join(REPO, "toolkit", "vfg"), "test", name], env=env,
                               capture_output=True, text=True, timeout=900)
            runs[name] = x = TestRun(name, r.returncode, r.stdout, r.stderr, t0, time.time(), "box")
            log.write("\n=== vfg test %s on the box: exit %d, %.1f s\n%s\n--- stderr\n%s\n" % (
                name, x.code, x.wall, r.stdout, r.stderr))
        finally:
            os.remove(path)
        board.row("box", "%s ran on the box's test server (%s-test), in the system sandbox" % (name, a.live_mc),
                  x.server.get("mc") == a.live_mc + "-test" and x.sandbox.startswith("sandboxed: systemd-run as"),
                  x.short())
    if "iqd_reach" in runs:
        x = runs["iqd_reach"]
        refused, reached, missing, probes = judge_reaches(x, ("kit", "files"))
        board.row("box", "every reach for the live server refused: the kit's NotTestServer, its server.properties, "
                  "its folder, its .env, a write", x.passed and not reached and not missing,
                  "%d refused%s%s" % (len(refused), "; REACHED: %s" % reached if reached else "",
                                      "; no line for %s" % missing if missing else ""))
        for k, _ in REACHES:
            board.row("box", "  " + k, None, probes.get(k, "no line")[:150])
    if "iqd_escape" in runs:
        x = runs["iqd_escape"]
        got = escapes(x)
        through = [k for k, v in got.items() if not v.startswith("refused")]
        board.row("box", "every attempt round the sandbox refused (the escape test)", x.code == 0 and got and
                  not through, "%d attempts, %d refused%s" % (len(got), len(got) - len(through),
                                                              "; THROUGH: %s" % through if through else ""))
        for k, v in got.items():
            board.row("box", "  " + k, None, v[:150])
    print(table(board))
    return 1 if board.failed() else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", default=PREFIX + "1", help="the realmc server (iq-d…); its test server beside it")
    ap.add_argument("--only", default=",".join(SECTIONS), help="sections, comma-separated: " + ", ".join(SECTIONS))
    ap.add_argument("--night-runs", type=int, default=3)
    ap.add_argument("--fresh-tests", type=int, default=20)
    ap.add_argument("--streams", type=int, default=3, choices=(2, 3, 4))
    ap.add_argument("--per-stream", type=int, default=5)
    ap.add_argument("--no-plain-runaway", dest="plain_runaway", action="store_false",
                    help="leave out the runaway in the plain fallback (INFO)")
    ap.add_argument("--no-wedge-default", dest="wedge_default", action="store_false",
                    help="leave out the wedged server with the defaults (about 90 s)")
    ap.add_argument("--fresh-live", action="store_true", help="the live server's world fresh from the template first")
    ap.add_argument("--keep-up", action="store_true")
    ap.add_argument("--keep-made", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--box", action="store_true", help="the reach test on the REAL box's test server instead "
                                                       "(the owner's ~/.vfg/config): every reach, the escapes "
                                                       "through /proc and the network too, must be refused")
    ap.add_argument("--server", default="main", help="--box: VFG_SERVER, whose made/SERVER/tests/ holds the test")
    ap.add_argument("--live-mc", default="/opt/mc", help="--box: the live Minecraft folder")
    ap.add_argument("--live-vfg", default="/opt/vfg", help="--box: the live vfg folder")
    ap.add_argument("--live-rcon-port", type=int, default=25575, help="--box: the live RCON port")
    ap.add_argument("--live-game-port", type=int, default=25565, help="--box: the live game port")
    ap.add_argument("--live-api-port", type=int, default=8787, help="--box: the bridge's API port")
    a = ap.parse_args(argv)
    if a.box:
        out = os.path.abspath(a.out or os.path.join(ROOT, "testserver-e2e-box"))
        os.makedirs(out, exist_ok=True)
        return box_mode(a, out)
    if not a.name.startswith(PREFIX):
        ap.error("--name starts with %s: realmc's other servers are other sessions'" % PREFIX)
    only = [s for s in a.only.split(",") if s]
    bad = [s for s in only if s not in SECTIONS]
    if bad:
        ap.error("no section %s" % ", ".join(bad))
    out = os.path.abspath(a.out or os.path.join(ROOT, "testserver-e2e-%s" % a.name))
    os.makedirs(out, exist_ok=True)
    os.environ.setdefault("REALMC_ROOT", os.path.join(REALMC, ".server"))
    s = Suite(a, out)
    t0 = time.time()
    load = os.getloadavg()[0]
    s.note("testserver_e2e on realmc %s in %s; sections %s; load %.1f on %d cores" % (
        a.name, os.environ["REALMC_ROOT"], ",".join(only), load, os.cpu_count() or 0))
    try:
        if any(x != "guard" for x in only):   # the guard's checks need no server
            s.setup()
        if any(x in only for x in ("runaway", "fresh", "parallel", "heal")):
            s.take_baseline()
        for name in SECTIONS:
            if name in only:
                t1 = time.time()
                try:
                    getattr(s, name)()
                except Exception as e:  # noqa: BLE001 — one section's break is its FAIL, the rest still run
                    import traceback
                    s.log.write(traceback.format_exc())
                    s.board.row(name, "the section ran to its end", False, "%s: %s" % (type(e).__name__, e))
                s.note("%s: %.0f s" % (name, time.time() - t1))
    finally:
        s.cleanup()
    s.note("all: %.0f s; load %.1f at the start, %.1f at the end" % (time.time() - t0, load, os.getloadavg()[0]))
    text = table(s.board)
    print(text)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"name": a.name, "seconds": round(time.time() - t0), "load": [load, os.getloadavg()[0]],
                   "rows": s.board.rows, "tests": [{"name": x.name, "tag": x.tag, "code": x.code, "wall": round(x.wall, 1),
                                                     "overhead": x.overhead_s and round(x.overhead_s, 1),
                                                     "ready": x.ready_s, "queue": x.queue_s, "server": x.said}
                                                    for x in s.tests]}, f, indent=1)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("```\n%s\n```\n" % text)
    print("\nwritten: %s/summary.md, summary.json, e2e.log" % out)
    return 1 if s.board.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
