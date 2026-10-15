"""Tests of engine content on the test server: one engine started for one test, the test run against
it, the engine stopped, one JSON result (the game engine spec; Prototype_Implementer_Quality's test
server).

    python -m vfg_engine.livetest --properties /opt/mc-test/server.properties \\
        --state-dir /opt/vfg-test/state/engine \\
        --content /opt/vfg/engine/library --content /opt/vfg-test/made/main/content \\
        --test /opt/vfg-test/tests/fire_staff_steve.py [--bindings FILE] [--boot 60] [--timeout S]

It refuses any server whose folder (the one holding server.properties) has no `.vfg-test-server`
marker, or one whose marker names another RCON port: exit 3. It loads the test file, starts
`python -m vfg_engine` with VFG_TEST=1 (which refuses the same way) on the test server, its state,
export, socket and log in --state-dir (fresh: what an earlier test left there is removed), and
waits until its socket answers and its content has loaded (a reload, whose load errors are printed
as ENGINE lines); binds --bindings' incantations; sets the stage; runs the test; then stops the
engine, clears the stage, and prints one JSON line, last:

    {"code": 0, "passed": 3, "failed": 0, "cant_tell": 0, "checks": [{"item", "ok", "why"}, ...],
     "engine": {"loaded": ["fire_staff", ...], "errors": [{"file", "check", "error"}], "boot_s": 1.4,
                "exit": 0}, "seconds": 4.6, "timing": {"boot": 1.4, "stage": 0.4, "test": 2.1,
     "clear": 0.7}, "slow": false}

with "error" (in words) when it did not run to the end, and "stopped": true after SIGTERM. Every
live test is short and self-contained: under 20 s, its setup included. One over
VFG_LIVETEST_BUDGET_S (20) prints a WARN line and has "slow": true; its code is unchanged. Exit code
= "code": 0 every check passed; 1 one failed; 2 the test could not run as written (no test(t), an
error in it, a refused op, no check made); 3 not a test server; 4 the engine did not start or load;
5 stopped (SIGTERM, or --timeout). Its other output, before the JSON, is the report a person reads:
ENGINE, AT, CAST, START, LOG, then PASS / FAIL / CAN'T TELL — item — why, RESULT, and TIME (the
wall time and each phase's) with WARN when it is slow.

THE TEST FILE defines test(t). t is ready when it is called: the engine running with the content,
the stage set (a barrier floor high in the sky at 160 296 160, night, clear, no mobs spawning), Steve (the caster,
facing north) and Alex (4 to his left), both Carpet fake players the engine sees.

    def test(t):
        husk = t.mob("husk", 0, 0, 10, health=40)   # 10 ahead, on his eye line: (right, up, forward)
        t.give("fire_staff")                          # to Steve; t.give(item, who="Alex")
        t.check("Steve holds it", t.holds("fire_staff"))
        t.use()                                       # a right-click
        t.watch(4)                                    # positions, health, fire and effects, 4 a second
        t.check("it hits the husk", t.hurt(husk))
        t.check("Steve is not hurt", t.unhurt())
        t.check("no error in the engine's log", t.no_errors())

    def test(t):                                       # an incantation
        t.grant("fireball")
        t.bind("fire ball", "fireball")               # or --bindings brings the task's own
        t.check("the cast is taken", t.cast("fire ball"))
        t.watch(5)
        ...

    def test(t):                                       # a scenario, its args
        t.start("meteor", wait=10, at=t.at(0, 0, 15), count=3)
        t.check("it says how many fell", t.said("3 meteors"))

WHERE: (right, up, forward) from Steve's feet; forward is where he faces, north, -z; right is +x;
up is +y. Something in front has forward > 0 (behind=True for one meant to be behind him). Each
thing placed prints an AT line; t.at(r, u, f) is a spot as "x y z", t.xyz(r, u, f) the numbers.
THE ENGINE (each a control op; a refusal stops the test, saying why): t.give(item, who=None,
base=None), t.take, t.grant(ability, who=None), t.revoke, t.apply(mark, who=None, seconds=None,
**args), t.clear(mark, who=None), t.start(scenario, wait=0, **args) (its instance; waited up to
`wait` s for its result), t.result(within=0) (the last start's {"summary", "facts"}: its own
answer, else the engine's result op on its instance, asked for up to `within` s; {} for none),
t.stop(instance_or_scenario), t.spawn(actor, r, u, f) (its uuid),
t.bind(phrase, ability=None, who=None, scenario=None, args=None, aliases=()), t.cast(phrase,
who=None) (a heard cast, as the bridge sends it: a verdict, PASS when fired or charging),
t.instances(kind=None, who=None), t.op(name, **args) any other (its whole reply).
PRESSING, as a player does (Carpet): t.use(hold=0, draw=0, who=None) a click, held `hold` s, or a
bow drawn `draw` s; t.sneak(times=3) quick crouches, t.sneak(hold=s) one held s seconds (`times`
of them with both); t.look(pitch=0, turn=0); t.hotbar(slot); t.attack(); t.jump().
THE STAGE: t.mob(kind, r, u, f, health=None, still=True, nbt="", name=None, behind=False);
t.blocks(block, r, u, f, size=3) a cube, its bottom at u; t.region(r, u, f, size=3) a cube left as
it is; t.ground(r=0, f=10, size=9, depth=3) real ground in the floor; t.player(name, r, u, f);
t.stand_in() a villager beside Steve that knockback pushes; t.cmd(command) sets the stage (one that
changes a player is refused: that is the content's to do). t.wait(s), t.watch(s).
CHECKS, each a verdict for t.check(item, verdict) (moved and fired count from the last cast, use,
sneak, start, apply, grant, give or spawn): t.hurt(who, at_least=1), t.killed(who), t.unhurt(who=
Steve), t.others_unhurt(), t.burning(who), t.moved(at_least, way="any"|"up"|"forward", who=None),
t.near(kind, r, u, f, radius=8, count=1), t.gone(cube, share=0.5), t.turned(cube, block, share=0.1),
t.built(cube, share=0.02), t.block(r, u, f, block), t.fire_near(r, u, f, radius=4, count=1),
t.effect(name, who=None), t.holds(item, who=None) (an EverHear id or a vanilla item),
t.weather(kind), t.running(id, who=None), t.said(text, within=5) (t.result(within) holds text),
t.no_errors() (no error in the engine's log since the test began). verdict(ok, why) makes one: ok
True, False or None (CAN'T TELL); a bare True/False works too. t.unhurt and t.no_errors are CAN'T
TELL when nothing fired since the last action: fired is a t.start (one that ended within its wait
ran all the same), a new instance, more things on the stage, blocks placed or broken there (a
masked /clone census of the stage), or anyone there moved, hurt, set alight or given an effect.

The test talks to the test server only: its RCON (the box's lock held for each exchange) and the
engine's control socket. It needs no network but loopback, nothing in /tmp but its own (the RCON
lock, and the socket's folder when --state-dir's path is too long for one), and writes nowhere but
--state-dir: the package, the venv and the content may be read-only (the test server's sandbox binds
them so, for another user). Standard library only, besides the engine it starts.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import importlib.util
import inspect
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid as _uuid

from . import ctl
from .server import MAX_SOCKET_PATH, one_line

MARKER = ".vfg-test-server"
EXIT_PASS, EXIT_FAIL, EXIT_BAD_TEST, EXIT_REFUSED, EXIT_BOOT, EXIT_STOPPED = 0, 1, 2, 3, 4, 5

# The stage's middle: inside the chunks a new world has generated round its spawn (realmc's template
# has every chunk within 224 blocks), so a fresh world's first test does not wait for 49 chunks to be
# made (measured: 3.9 s of setup at 3000 3000 on a fresh world, 1.1 s once they exist), and 200
# blocks above anything built round spawn.
X, Z = 160, 160
FLOOR = 295               # its barrier floor; Steve's feet are at FLOOR + 1
HALF = 40                 # the floor is 81 x 81
CLEAR = 48                # what is cleared after: 97 x 97, from y 280 to the top
TOP = 319
SAMPLE_S = 0.25
FILL_MAX = 32768          # blocks one /fill (or /clone) may change
# The stage's census (_blocks): its non-air blocks, counted slab by slab by a masked /clone (which
# says how many it copied) into the scratch layer under it, so a build or a crater counts as fired.
CENSUS_Y = (285, TOP)           # the ground under the floor up to the build limit
SCRATCH_Y = 280                  # 4 layers, under the census, inside what the clear wipes
BUDGET_S = 20.0                  # a live test's wall time, setup included (VFG_LIVETEST_BUDGET_S)
STAND_IN_HP = 1000
SEE_PLAYER_S = 10.0       # how long a new fake player may take to be in the engine's snapshot
OP_TIMEOUT_S = 15.0
_NUM = r"(-?\d+(?:\.\d+)?(?:E-?\d+)?)"
# Minecraft's answer to a command it did not run.
_REFUSED = re.compile(r"Unknown or incomplete command|Incorrect argument|<--\[HERE\]|^Expected |^Unable to |"
                      r"^Invalid |^Unknown (block|item|entity|effect|attribute|selector)|^Can't find element|"
                      r"^Failed to parse")
# What /player presses, which t.cmd lets through: a key, not a change to him.
_KEYS = ("move", "jump", "sprint", "unsprint", "sneak", "unsneak", "look", "turn", "use", "attack", "stop", "hotbar",
         "swapHands")
_QUIET = ("nothing new on the stage, no block placed or broken, no scenario or instance started, and nobody "
          "moved, was hurt, set alight or given an effect")
_HINTS = {"cast": ": t.cast(phrase, who=None); bind it first, t.bind(phrase, ability)",
          "check": ": a check is t.check(item, verdict)", "start": ": t.start(scenario, wait=0, **args)",
          "give": ": t.give(item, who=None, base=None)"}


# ---------------------------------------------------------------- verdicts

class Verdict(tuple):
    """(ok, why): ok True (PASS), False (FAIL) or None (CAN'T TELL); true only for a PASS."""

    def __new__(cls, ok, why):
        return tuple.__new__(cls, (ok, str(why)))

    def __bool__(self):
        return self[0] is True


def verdict(ok, why):
    return Verdict(None if ok is None else bool(ok), why)


class TestError(Exception):
    """The test could not run as written: what it says is the report's ERROR line."""


class Stopped(BaseException):
    """SIGTERM, or --timeout: the test is stopped where it is."""


def _r(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _ahead(f):
    return "%s %s" % (_r(abs(f)), "ahead" if f >= 0 else "behind")


def _side(r):
    return "%s %s" % (_r(abs(r)), "right" if r >= 0 else "left")


def _span(a, b, neg, pos):
    """From a to b along one way, in words: `11 to 13 ahead`, `1 left to 1 right`."""
    if a == b:
        return "%s %s" % (_r(abs(a)), neg if a < 0 else pos) if a else "0 %s" % pos
    if a >= 0:
        return "%s to %s %s" % (_r(a), _r(b), pos)
    if b <= 0:
        return "%s to %s %s" % (_r(-b), _r(-a), neg)
    return "%s %s to %s %s" % (_r(-a), neg, _r(b), pos)


def _explained(cls):
    """Every public helper says how it is called when called wrong, as one line, not a traceback."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        sig = inspect.signature(fn)

        def wrap(fn=fn, name=name, sig=sig):
            @functools.wraps(fn)
            def call(self, *a, **k):
                try:
                    sig.bind(self, *a, **k)
                except TypeError as e:
                    raise TestError("t.%s%s: %s%s" % (name, str(sig).replace("(self, ", "(").replace("(self)", "()"),
                                                      e, _HINTS.get(name, "")))
                return fn(self, *a, **k)
            return call
        setattr(cls, name, wrap())
    return cls


# ---------------------------------------------------------------- the test server

def read_props(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def test_server_refusal(properties):
    """Why the server of `properties` is not a test server, or None when it is: the folder holding
    server.properties must have the .vfg-test-server marker, which only the test server's setup
    writes, and a marker naming an RCON port must name this server's. A test, and an engine under
    VFG_TEST=1, never talks to a server without it."""
    folder = os.path.dirname(os.path.abspath(properties))
    marker = os.path.join(folder, MARKER)
    if not os.path.isfile(marker):
        return ("%s has no %s: it is not a test server, and a test never runs on any other (the live "
                "server's least of all)" % (folder, MARKER))
    try:
        port = read_props(properties).get("rcon.port") or "25575"
    except OSError as exc:
        return "%s cannot be read: %s" % (properties, one_line(exc))
    try:
        with open(marker, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    named = data.get("rcon_port") if isinstance(data, dict) else None
    if named is not None and str(named) != port:
        return ("the marker in %s names RCON port %s, but server.properties has %s: it is not this server's"
                % (folder, named, port))
    return None


# ---------------------------------------------------------------- the engine, through its socket

class EngineClient:
    def __init__(self, socket_path):
        self.socket_path = socket_path

    def ask(self, op, timeout=OP_TIMEOUT_S, **args):
        req = {"op": op, "args": args, "timeout_s": timeout}
        try:
            return ctl.request(self.socket_path, req, timeout + ctl.MARGIN_S)
        except ctl.NotRunning as exc:
            raise TestError("the engine is not running: %s" % exc) from None
        except ctl.Timeout as exc:
            raise TestError("the engine did not answer %s: %s" % (op, exc)) from None


# ---------------------------------------------------------------- the test's t

@_explained
class T:
    """What a test file's test(t) gets: see the module's docstring, its guide."""

    def __init__(self, rcon, engine, log_path=None, caster="Steve", others=("Alex",), out=None, see_players=True):
        self._rcon, self._engine = rcon, engine
        self._log_path = log_path
        self._log_from = _size(log_path)
        self._out = out or sys.stdout
        self.caster = caster
        self._see = see_players
        self.players, self.mobs, self.cubes, self.checks = [], {}, [], []
        self.start_at, self.trace, self.casts = {}, [], []
        self._n = 0
        self._acted, self._what, self._base = time.time(), "the start of the test", {}
        self._things = None
        self._instances = None
        self._census = None
        self._ran = False             # a t.start since the last action's mark: a one-shot that ended still ran
        self._result = None
        self._last_start = None
        self._items = None
        self._others = tuple(others)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise TestError("t has no %s; its helpers: %s" % (name, ", ".join(
            n for n in sorted(vars(T)) if not n.startswith("_") and callable(vars(T)[n]))))

    def _say(self, kind, text):
        self._out.write("%-10s %s\n" % (kind, text))
        self._out.flush()

    def _mc(self, command):
        try:
            return self._rcon.command(command)
        except Exception as exc:
            raise TestError("the test server did not take %r: %s" % (command[:80], one_line(exc))) from None

    # ---- the stage
    def _setup(self):
        """Forceloaded, a barrier floor, night, clear, no mobs spawning; Steve and Alex on it."""
        self._mc("forceload add %d %d %d %d" % (X - CLEAR, Z - CLEAR, X + CLEAR, Z + CLEAR))
        self._wipe(players=False)
        for rule in ("spawn_mobs", "spawn_phantoms"):
            self._mc("gamerule %s false" % rule)
        self._mc("time set midnight")
        self._mc("weather clear")
        self._mc("fill %d %d %d %d %d %d barrier" % (X - HALF, FLOOR, Z - HALF, X + HALF, FLOOR, Z + HALF))
        self.player(self.caster, 0, 0, 0)
        for i, name in enumerate(self._others):
            self.player(name, -4 * (i + 1), 0, 0)

    def xyz(self, right=0, up=0, forward=0):
        return (X + 0.5 + right, FLOOR + 1 + up, Z + 0.5 - forward)

    def at(self, right=0, up=0, forward=0):
        return "%s %s %s" % tuple(_r(v) for v in self.xyz(right, up, forward))

    def _rel(self, x, y, z):
        r, u, f = x - X - 0.5, y - FLOOR - 1, Z + 0.5 - z
        return ", ".join(([_ahead(f)] if abs(f) >= 0.05 else []) + ([_side(r)] if abs(r) >= 0.05 else []) +
                         (["%s %s" % (_r(abs(u)), "up" if u >= 0 else "down")] if abs(u) >= 0.05 else [])) \
            or "where he stands"

    def _extent(self, cube):
        x1, y1, z1, x2, y2, z2 = cube
        return ", ".join((_span(Z - z2, Z - z1, "behind", "ahead"), _span(x1 - X, x2 - X, "left", "right"),
                          _span(y1 - FLOOR - 1, y2 - FLOOR - 1, "below his feet", "up")))

    def _placing(self, what, right, up, forward, behind):
        if forward < 0 and not behind:
            raise TestError("%s at %s: forward is the way he faces (north, -z); something in front of him has "
                            "forward > 0 (behind=True if it is meant to be behind him)" % (what, _ahead(forward)))
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        for name, cube in self.cubes:
            if all(cube[i] <= p <= cube[i + 3] for i, p in enumerate((x, y, z))):
                raise TestError("%s at %s would be inside the %s (%s): put it in front of it"
                                % (what, self._rel(x + 0.5, y, z + 0.5), name, self._extent(cube)))

    def _online(self):
        reply = self._mc("list")
        names = reply.split(":", 1)[1] if ":" in reply else ""
        return [n.strip() for n in names.split(",") if n.strip()]

    def player(self, name, right=0, up=0, forward=0):
        x, y, z = self.xyz(right, up, forward)
        if name in self._online():
            self._mc("tp %s %s %s %s 180 0" % (name, _r(x), _r(y), _r(z)))
        else:
            said = self._mc("player %s spawn at %s %s %s facing 180 0 in minecraft:overworld in survival"
                            % (name, _r(x), _r(y), _r(z)))
            if "Unknown or incomplete command" in said:
                raise TestError("this server has no Carpet: /player is unknown, so no fake player")
            end = time.time() + 10
            while name not in self._online():
                if time.time() > end:
                    raise TestError("%s did not come online: %s" % (name, said))
                time.sleep(0.1)
        for c in ("gamemode survival %s", "clear %s", "effect clear %s", "effect give %s instant_health 1 20 true",
                  "effect give %s saturation 1 20 true", "player %s hotbar 1"):
            self._mc(c % name)
        if name not in self.players:
            self.players.append(name)
        if self._see:   # the engine's snapshot has him before anything is asked of the engine about him
            end = time.time() + SEE_PLAYER_S
            while name not in (self._engine.ask("status").get("players") or []):
                if time.time() > end:
                    raise TestError("the engine does not see %s %g s after he came online" % (name, SEE_PLAYER_S))
                time.sleep(0.1)
        if self.players[0] == name:
            self._say("AT", "%s: the caster, facing north (forward is -z, right is +x), x %s y %s z %s"
                      % (name, _r(x), _r(y), _r(z)))
        else:
            self._say("AT", "%s: %s, x %s y %s z %s" % (name, self._rel(x, y, z), _r(x), _r(y), _r(z)))
        self.start_at[name] = {"pos": (x, y, z), "health": self._health(name) or 20.0}
        return name

    def mob(self, kind, right=0, up=0, forward=0, health=None, still=True, nbt="", name=None, behind=False):
        if name is None:
            self._n += 1
            name = "%s_%d" % (re.sub(r"\W", "_", kind.split(":")[-1]), self._n)
        self._placing(name, right, up, forward, behind)
        attrs = ['{id:"minecraft:movement_speed",base:0.0}'] if not still else []
        bits = ["PersistenceRequired:1b", "Silent:1b", 'Tags:["vfg_test","vfg_test_%s"]' % name]
        if still:
            bits.append("NoAI:1b")
        if health:
            attrs.append('{id:"minecraft:max_health",base:%s}' % _r(health))
            bits.append("Health:%sf" % _r(health))
        if attrs:
            bits.append("attributes:[%s]" % ",".join(attrs))
        if nbt:
            bits.append(nbt.strip("{}"))
        x, y, z = self.xyz(right, up, forward)
        said = self._mc("summon %s %s %s %s {%s}" % (kind, _r(x), _r(y), _r(z), ",".join(bits)))
        if not said.startswith("Summoned"):
            raise TestError("summon %s: %s" % (kind, said))
        self.mobs[name] = "@e[tag=vfg_test_%s,limit=1]" % name
        self.start_at[name] = {"pos": (x, y, z), "health": self._health(name)}
        self._say("AT", "%s: %s, x %s y %s z %s" % (name, self._rel(x, y, z), _r(x), _r(y), _r(z)))
        return name

    def stand_in(self):
        self.mob("villager", -1, 0, 0, health=STAND_IN_HP, still=False, name="stand_in")
        return "stand_in"

    def _cube_at(self, what, cube):
        self._say("AT", "%s: %s, x %d..%d y %d..%d z %d..%d" % ((what, self._extent(cube)) + (
            cube[0], cube[3], cube[1], cube[4], cube[2], cube[5])))

    def blocks(self, block, right=0, up=0, forward=0, size=3, behind=False):
        cube = self.region(right, up, forward, size, behind=behind, quiet=True)
        for name, start in self.start_at.items():
            p = [int(math.floor(v)) for v in start["pos"]]
            if all(cube[i] <= p[i] <= cube[i + 3] for i in range(3)):
                raise TestError("the %s cube (%s) would hold %s: put it behind it" % (block, self._extent(cube), name))
        self._fill(cube, block)
        what = "%s cube %dx%dx%d" % (block, size, size, size)
        self.cubes.append((what, cube))
        self._cube_at(what, cube)
        return cube

    def region(self, right=0, up=0, forward=0, size=3, behind=False, quiet=False):
        if forward < 0 and not behind:
            self._placing("a cube", right, up, forward, behind)
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        h = size // 2
        cube = (x - h, y, z - h, x - h + size - 1, y + size - 1, z - h + size - 1)
        if not quiet:
            self._cube_at("region %dx%dx%d" % (size, size, size), cube)
        return cube

    def ground(self, right=0, forward=10, size=9, depth=3):
        x, _, z = (int(math.floor(v)) for v in self.xyz(right, 0, forward))
        h = size // 2
        cube = (x - h, FLOOR - depth, z - h, x - h + size - 1, FLOOR, z - h + size - 1)
        self._fill(cube, "dirt")
        self._mc("fill %d %d %d %d %d %d grass_block" % (cube[0], FLOOR, cube[2], cube[3], FLOOR, cube[5]))
        self._mc("fill %d %d %d %d %d %d short_grass replace air" % (cube[0], FLOOR + 1, cube[2], cube[3], FLOOR + 1, cube[5]))
        self._cube_at("ground %dx%d, %d deep" % (size, size, depth + 1), cube)
        return cube

    def _fill(self, cube, block):
        """A fill of any size, in slabs /fill takes."""
        x1, y1, z1, x2, y2, z2 = cube
        per = max(1, FILL_MAX // ((x2 - x1 + 1) * (z2 - z1 + 1)))
        y = y1
        while y <= y2:
            top = min(y2, y + per - 1)
            said = self._mc("fill %d %d %d %d %d %d %s" % (x1, y, z1, x2, top, z2, block))
            if _REFUSED.search(said or ""):
                raise TestError("fill with %s: %s" % (block, " ".join(said.split())[:200]))
            y = top + 1

    # ---- the engine
    def op(self, name, **args):
        """Any control op, as engine ctl sends it: its whole reply."""
        return self._engine.ask(name, **args)

    def _need(self, what, reply):
        if not reply.get("ok"):
            raise TestError("%s: the engine refused it: %s" % (what, reply.get("error") or "no reason given"))
        return reply

    def _mark(self, what):
        """A cast, use, sneak, start, apply, grant, give or spawn: what moved and fired count from."""
        self._acted, self._what, self._base = time.time(), what, {}
        self._ran = False
        self._things = self._count_things()
        self._instances = self._instance_ids()
        self._census = self._blocks()
        for who in self.players + list(self.mobs):
            sel = self.mobs.get(who, who)
            pos = self._data(sel, "Pos")
            if pos:
                fire = self._data(sel, "Fire")
                self._base[who] = (tuple(pos[:3]), set(self._effects(sel)), bool(fire and fire[0] > 0))

    def give(self, item, who=None, base=None):
        who = who or self.caster
        self._mark("the give")
        args = {"player": who, "item": item}
        if base:
            args["base"] = base
        self._need("give %s to %s" % (item, who), self.op("give", **args))

    def take(self, item, who=None):
        who = who or self.caster
        self._need("take %s from %s" % (item, who), self.op("take", player=who, item=item))

    def grant(self, ability, who=None):
        who = who or self.caster
        self._mark("the grant")
        return self._need("grant %s to %s" % (ability, who), self.op("grant", who=who, ability=ability)).get("instance")

    def revoke(self, ability, who=None):
        who = who or self.caster
        self._need("revoke %s from %s" % (ability, who), self.op("revoke", who=who, ability=ability))

    def apply(self, mark, who=None, seconds=None, **args):
        who = who or self.caster
        self._mark("the mark")
        req = {"who": who, "mark": mark}
        if seconds is not None:
            req["seconds"] = seconds
        if args:
            req["args"] = args
        return self._need("apply %s to %s" % (mark, who), self.op("apply", **req)).get("instance")

    def clear(self, mark, who=None):
        who = who or self.caster
        self._need("clear %s from %s" % (mark, who), self.op("clear", who=who, mark=mark))

    def start(self, scenario, wait=0, **args):
        self._mark("the start")
        req = {"scenario": scenario, "args": args}
        if wait:
            req["wait_s"] = wait
        reply = self._need("start %s" % scenario, self._engine.ask("start", timeout=OP_TIMEOUT_S + wait, **req))
        self._ran = True
        self._last_start = reply.get("instance")
        self._result = reply.get("result") or None
        state = ("ended (%s)" % reply["ended"] if reply.get("ended") else "still running" if reply.get("running")
                 else "started")
        self._say("START", "%s with %s: %s, %s" % (scenario, json.dumps(args)[:200] if args else "its defaults",
                                                   reply.get("instance"), state))
        return reply.get("instance")

    def stop(self, what=None):
        """Stop an instance (its id) or every instance of a scenario (its id)."""
        if not what:
            raise TestError("t.stop(instance_or_scenario)")
        key = "instance" if "#" in str(what) or ":" in str(what) else "scenario"
        self._need("stop %s" % what, self.op("stop", **{key: what}))

    def spawn(self, actor, right=0, up=0, forward=10):
        self._mark("the spawn")
        reply = self._need("spawn %s" % actor, self.op("spawn", actor=actor, at=list(self.xyz(right, up, forward)),
                                                       dim="minecraft:overworld"))
        uid = reply.get("uuid")
        name = "%s_%d" % (actor, len(self.mobs) + 1)
        self.mobs[name] = uid
        x, y, z = self.xyz(right, up, forward)
        self.start_at[name] = {"pos": (x, y, z), "health": self._health(name)}
        self._say("AT", "%s: %s (the actor %s, %s)" % (name, self._rel(x, y, z), actor, uid))
        return name

    def bind(self, phrase, ability=None, who=None, scenario=None, args=None, aliases=()):
        who = who or self.caster
        req = {"player": who, "phrase": phrase, "aliases": list(aliases), "by": "test"}
        if scenario:
            req.update(scenario=scenario, args=args or {})
        elif ability:
            req["ability"] = ability
        else:
            raise TestError("t.bind(phrase, ability) or t.bind(phrase, scenario=..., args={...})")
        self._need("bind %r for %s" % (phrase, who), self.op("bind", **req))

    def cast(self, phrase, who=None):
        """A heard cast, as the bridge sends one: a verdict, PASS when the engine fired it or began
        its charge."""
        who = who or self.caster
        self._mark("the cast")
        reply = self.op("cast", player=who, phrase=phrase, cast_id="test-" + _uuid.uuid4().hex)
        status = reply.get("status") if reply.get("ok") else None
        why = "%s says %r: %s%s" % (who, phrase, status or "refused",
                                    ": " + str(reply.get("error")) if reply.get("error") else "")
        if status == "cooldown":
            why += " (%s s left)" % _r(reply.get("remaining_s") or 0)
        self._say("CAST", why)
        self.casts.append(reply)
        if status == "unknown":
            raise TestError("%s has no incantation %r in the engine: t.bind(%r, ability) first, or vfg test brings the "
                            "task's own" % (who, phrase, phrase))
        return verdict(status in ("fired", "charging"), why)

    def instances(self, kind=None, who=None):
        args = {}
        if kind:
            args["kind"] = kind
        if who:
            args["user"] = who
        return self._need("instances", self.op("instances", **args)).get("instances") or []

    def _instance_ids(self):
        try:
            return set(i.get("instance") for i in self.op("instances").get("instances") or [])
        except TestError:
            return None

    def result(self, within=0):
        """The last t.start's result, {"summary", "facts"}, or {} when there is none (yet): the start's
        own answer, else the engine's `result` op on its instance, asked again for up to `within` s
        (a scenario that outlived the start's wait, at most 30 s, reports later)."""
        end = time.time() + within
        while not self._result and self._last_start:
            reply = self.op("result", id=self._last_start)
            if reply.get("ok") and reply.get("result"):
                self._result = reply["result"]
                break
            if (reply.get("ok") and reply.get("ended")) or not reply.get("ok") or time.time() >= end:
                break   # ended with none, or the engine has no result op: none will come
            time.sleep(0.25)
        return dict(self._result or {})

    # ---- pressing
    def use(self, hold=0, draw=0, who=None):
        who = who or self.caster
        self._mark("the use")
        if draw:     # a bow, a crossbow, a trident: held down without a break, then let go
            self._mc("player %s use continuous" % who)
            time.sleep(draw)
            self._mc("player %s stop" % who)
            return
        self._mc("player %s use %s" % (who, "interval 4" if hold else "once"))
        if hold:
            time.sleep(hold)
            self._mc("player %s stop" % who)

    def sneak(self, times=None, hold=None, who=None):
        """Crouches: `times` taps (3), or with `hold` s each crouch held that long (once, unless `times`)."""
        who = who or self.caster
        if times is None:
            times = 3 if hold is None else 1
        self._mark("the crouch")
        for _ in range(int(times)):
            self._mc("player %s sneak" % who)
            time.sleep(0.15 if hold is None else float(hold))
            self._mc("player %s unsneak" % who)
            time.sleep(0.15)

    def look(self, pitch=0, turn=0, who=None):
        self._mc("rotate %s %s %s" % (who or self.caster, _r(180 + turn), _r(pitch)))

    def hotbar(self, slot, who=None):
        self._mc("player %s hotbar %d" % (who or self.caster, int(slot)))

    def attack(self, who=None):
        self._mark("the attack")
        self._mc("player %s attack once" % (who or self.caster))

    def jump(self, who=None):
        self._mc("player %s jump once" % (who or self.caster))

    def _player_target(self, tok, prefix=""):
        tok = tok.strip()
        if "tag=vfg_test" in tok:
            return False
        if tok.startswith("@s"):
            return any(self._player_target(m, "") for m in re.findall(r"\bas (\S+)", prefix))
        return (re.fullmatch(r"[A-Za-z0-9_]{1,16}", tok) is not None   # any player's name, on the stage or not
                or tok.split("[")[0] in ("@a", "@p", "@r")
                or (tok.startswith("@e") and ("type=player" in tok or "type=" not in tok)))

    def cmd(self, command):
        """One command to set the stage. One that changes a player is refused (that is the content's to
        do), and one Minecraft refuses stops the test with its answer."""
        c = command.strip().lstrip("/")
        pre, _, run = c.rpartition(" run ") if c.startswith("execute ") else ("", "", c)
        w = run.split() + [""] * 4
        p = functools.partial(self._player_target, prefix=pre)
        changes = ((w[0] == "player" and w[2] not in _KEYS) or (w[0] == "effect" and w[1] in ("give", "clear") and p(w[2]))
                   or (w[0] == "attribute" and p(w[1]) and "get" not in w[3:5])
                   or (w[0] in ("tp", "teleport") and p(w[1]))
                   or (w[0] == "scoreboard" and w[1] == "players" and w[2] in ("set", "add", "remove", "reset",
                                                                                "operation", "enable"))
                   or (w[0] == "data" and w[1] in ("modify", "merge", "remove") and w[2] == "entity" and p(w[3])))
        if changes:
            raise TestError("t.cmd(%r) refused: a test only watches: what happens to the players is the content's to "
                            "do; to press for him: t.use, t.sneak, t.look" % command)
        said = self._mc(c)
        if _REFUSED.search(said or ""):
            raise TestError("t.cmd(%r): Minecraft did not run it: %s" % (command, " ".join(said.split())[:200]))
        return said

    def wait(self, seconds):
        time.sleep(seconds)

    # ---- watching
    def _data(self, sel, path):
        out = self._mc("data get entity %s %s" % (sel, path))
        if "No entity was found" in out or "found no" in out.lower():
            return None
        return [float(v) for v in re.findall(_NUM + r"[dfbs]?(?=[,\]\s]|$)", out.split(":", 1)[-1])]

    def _health(self, who):
        got = self._data(self.mobs.get(who, who), "Health")
        return got[0] if got else None

    def _effects(self, sel):
        return re.findall(r'id: "minecraft:([a-z_]+)"', self._mc("data get entity %s active_effects" % sel))

    def _sample(self):
        s = {"t": time.time()}
        for who in self.players + list(self.mobs):
            sel = self.mobs.get(who, who)
            pos = self._data(sel, "Pos")
            if pos is None:
                s[who] = None
                continue
            hp = self._data(sel, "Health")
            fire = self._data(sel, "Fire")
            s[who] = {"pos": tuple(pos[:3]), "health": hp[0] if hp else None, "fire": fire[0] if fire else 0,
                      "effects": self._effects(sel)}
        self.trace.append(s)

    def watch(self, seconds=5):
        end = time.time() + seconds
        while True:
            t0 = time.time()
            self._sample()
            if t0 >= end:
                return
            time.sleep(max(0.0, min(SAMPLE_S - (time.time() - t0), end - time.time())))

    def _rows(self, who, since=None):
        rows = [s for s in self.trace if since is None or s["t"] >= since - 0.05]
        if not rows:
            self._sample()
            rows = self.trace[-1:]
        return [s.get(who) for s in rows]

    def _count_things(self):
        got = re.search(r"Count: (\d+)", self._mc("execute if entity @e[type=!player,x=%d,y=%d,z=%d,distance=..%d]"
                                                  % (X, FLOOR, Z, CLEAR)))
        return int(got.group(1)) if got else 0

    def _blocks(self):
        """The stage's non-air blocks, under and above the floor (CENSUS_Y): a masked /clone of each
        slab into the scratch layer says how many it copied. None when it cannot be counted."""
        x1, z1, x2, z2 = X - HALF, Z - HALF, X + HALF, Z + HALF
        per = max(1, FILL_MAX // ((x2 - x1 + 1) * (z2 - z1 + 1)))
        per = min(per, CENSUS_Y[0] - SCRATCH_Y)
        total, y = 0, CENSUS_Y[0]
        while y <= CENSUS_Y[1]:
            top = min(CENSUS_Y[1], y + per - 1)
            said = self._mc("clone %d %d %d %d %d %d %d %d %d masked force"
                            % (x1, y, z1, x2, top, z2, x1, SCRATCH_Y, z1))
            m = re.search(r"(\d+) block", said or "")
            if m:
                total += int(m.group(1))
            elif "No blocks" not in (said or "") and "0 block" not in (said or ""):
                return None
            y = top + 1
        return total

    def _fired(self):
        """Did anything happen since the last action: a scenario started (one that ended within its
        wait ran all the same), a new instance, more things on the stage, blocks placed or broken,
        or anyone there moved, hurt, set alight or given an effect?"""
        if self._ran:
            return True
        now = self._instance_ids()
        if now is not None and self._instances is not None and now - self._instances:
            return True
        if self._things is not None and self._count_things() > self._things:
            return True
        if self._census is not None:
            now_blocks = self._blocks()
            if now_blocks is not None and now_blocks != self._census:
                return True
        for who in self.players + list(self.mobs):
            p0, e0, burned = self._base.get(who) or (None, set(), False)
            h0 = (self.start_at.get(who) or {}).get("health")
            for r in self._rows(who, self._acted):
                if r is None or (r["health"] is not None and h0 is not None and r["health"] < h0) \
                        or (r["fire"] > 0 and not burned) or (p0 and math.dist(r["pos"], p0) > 0.5) \
                        or set(r["effects"]) - e0:
                    return True
        return False

    # ---- checks
    def hurt(self, who, at_least=1):
        rows, h0 = self._rows(who), (self.start_at.get(who) or {}).get("health")
        if any(r is None for r in rows) or any(r and r["health"] is not None and r["health"] <= 0 for r in rows):
            return verdict(True, "%s died" % who)
        low = min([r["health"] for r in rows if r and r["health"] is not None] or [h0 or 0])
        lost = (h0 or 0) - low
        why = "%s health %s -> %s (lost %s, at least %s)" % (who, _r(h0 or 0), _r(low), _r(lost), _r(at_least))
        if lost >= at_least:
            return verdict(True, why)
        last = [r for r in rows if r]
        if last:
            why += "; it was %s" % self._rel(*last[-1]["pos"])
        return verdict(False, why)

    def killed(self, who):
        rows = self._rows(who)
        dead = any(r is None or (r["health"] is not None and r["health"] <= 0) for r in rows)
        last = rows[-1]["health"] if rows and rows[-1] else None
        return verdict(dead, "%s %s" % (who, "died" if dead else "alive at the end, health %s" % _r(last or 0)))

    def unhurt(self, who=None):
        who = who or self.caster
        v = self.hurt(who, at_least=0.01)
        if not v[0] and not self._fired():
            return verdict(None, "nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(not v[0], v[1].split(" (")[0])

    def others_unhurt(self):
        others = self.players[1:]
        if not others:
            return verdict(None, "nobody else is on the stage")
        hurt = [w for w in others if self.hurt(w, at_least=0.01)[0]]
        if not hurt and not self._fired():
            return verdict(None, "nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(not hurt, "%s hurt" % ", ".join(hurt) if hurt else "%s not hurt" % ", ".join(others))

    def burning(self, who):
        most = max([r["fire"] for r in self._rows(who) if r] or [0])
        return verdict(most > 0, "%s %s" % (who, "burned (Fire up to %s ticks)" % _r(most) if most > 0 else "never burned"))

    def moved(self, at_least, way="any", who=None):
        who = who or self.caster
        p0 = (self._base.get(who) or (None,))[0] or (self.start_at.get(who) or {}).get("pos")
        best, end = 0.0, None
        for r in self._rows(who, self._acted):
            if not (r and p0):
                continue
            end = r["pos"]
            d = [r["pos"][i] - p0[i] for i in range(3)]
            best = max(best, {"up": d[1], "forward": -d[2]}.get(way, math.sqrt(sum(v * v for v in d))))
        why = ("%s moved %s %s (at least %s, since %s)" % (who, _r(best), "" if way == "any" else way, _r(at_least),
                                                           self._what)).replace("  ", " ")
        if best >= at_least:
            return verdict(True, why)
        if end:
            why += "; %s ended %s" % (who, self._rel(*end))
        return verdict(False, why)

    def _passes(self, tests):
        """Which `execute if ...` tests pass, one command each."""
        return ["passed" in self._mc("execute %s" % t).lower() for t in tests]

    def _count(self, cube, blocks):
        x1, y1, z1, x2, y2, z2 = cube
        spots = [(x, y, z) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1) for z in range(z1, z2 + 1)]
        hit = [False] * len(spots)
        for b in blocks:
            for i, ok in enumerate(self._passes(["if block %d %d %d %s" % (x, y, z, b) for x, y, z in spots])):
                hit[i] = hit[i] or ok
        return sum(hit), len(spots)

    def turned(self, cube, block, share=0.1):
        blocks = [block] if isinstance(block, str) else list(block)
        n, total = self._count(cube, blocks)
        why = "%d of its %d blocks are %s (at least %d%%)" % (n, total, " or ".join(blocks), round(share * 100))
        return verdict(n >= share * total, why if n >= share * total else why + "; it is %s" % self._extent(cube))

    def gone(self, cube, share=0.5):
        return self.turned(cube, "air", share)

    def built(self, cube, share=0.02):
        air, total = self._count(cube, ["air"])
        n = total - air
        why = "%d of its %d blocks are built (at least %d%%)" % (n, total, round(share * 100))
        return verdict(n >= share * total, why if n >= share * total else why + "; it is %s" % self._extent(cube))

    def block(self, right, up, forward, block):
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        said = self._mc("execute if block %d %d %d %s" % (x, y, z, block))
        return verdict("passed" in said.lower(), "%s at %d %d %d: %s" % (block, x, y, z, said or "no"))

    def fire_near(self, right=0, up=0, forward=0, radius=4, count=1):
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        spots = [(a, b, c) for a in range(x - radius, x + radius + 1) for b in range(y - 1, y + 3)
                 for c in range(z - radius, z + radius + 1)]
        n = sum(self._passes(["if block %d %d %d #minecraft:fire" % s for s in spots]))
        return verdict(n >= count, "%d fire blocks within %s of %s (at least %d)" % (
            n, radius, self._rel(x + 0.5, y, z + 0.5), count))

    def near(self, kind, right=0, up=0, forward=0, radius=8, count=1, within=3):
        x, y, z = self.xyz(right, up, forward)
        sel = "@e[type=%s,x=%s,y=%s,z=%s,distance=..%s]" % (kind, _r(x), _r(y), _r(z), _r(radius))
        end, most = time.time() + within, 0
        while True:
            got = re.search(r"Count: (\d+)", self._mc("execute if entity %s" % sel))
            most = max(most, int(got.group(1)) if got else 0)
            if most >= count or time.time() > end:
                return verdict(most >= count, "%d %s within %s of %s (at least %d)" % (
                    most, kind, _r(radius), self._rel(x, y, z), count))
            time.sleep(0.25)

    def effect(self, name, who=None):
        who = who or self.caster
        name = name.split(":")[-1]
        if name in ("fire", "burning", "on_fire", "burn"):
            raise TestError("burning is not an effect: t.burning(who) reads its Fire; fire blocks: t.fire_near(...)")
        seen = any(r and name in (r.get("effects") or []) for r in self._rows(who))
        seen = seen or ('"minecraft:%s"' % name) in self._mc("data get entity %s active_effects" % self.mobs.get(who, who))
        return verdict(seen, "%s %s %s" % (who, "had" if seen else "never had", name))

    def _everhear_ids(self):
        if self._items is None:
            try:
                self._items = set(c["id"] for c in self.op("classes").get("classes") or [] if c.get("kind") == "item")
            except TestError:
                self._items = set()
        return self._items

    def holds(self, item, who=None, within=3):
        """`who` carries `item` (anywhere, the off hand too): an EverHear item by its id, or a vanilla item."""
        who = who or self.caster
        if item in self._everhear_ids():
            pred = '*[custom_data~{everhear:{item:"%s"}}]' % item
        else:
            pred = item if ":" in item else "minecraft:" + item
        end = time.time() + within
        while True:
            has = any(self._passes(["if items entity %s %s %s" % (who, slot, pred)
                                    for slot in ("container.*", "weapon.offhand")]))
            if has or time.time() > end:
                return verdict(has, "%s %s %s" % (who, "has" if has else "has no", item))
            time.sleep(0.25)

    def weather(self, kind="rain", within=8):
        cond = {"rain": "raining:true", "thunder": "thundering:true", "clear": "raining:false"}.get(kind)
        if cond is None:
            raise TestError('t.weather takes "rain", "thunder" or "clear"')
        end = time.time() + within
        while True:
            ok = "passed" in self._mc('execute if predicate {type:"minecraft:weather_check",%s}' % cond).lower()
            if ok or time.time() > end:
                return verdict(ok, "the weather is %s%s" % ("" if ok else "not ", kind))
            time.sleep(0.5)

    def running(self, id, who=None):
        rows = [i for i in self.instances(who=who) if i.get("id") == id]
        return verdict(bool(rows), "%s %s" % (id, "running: " + ", ".join(i["instance"] for i in rows) if rows
                                              else "not running" + (" for %s" % who if who else "")))

    def said(self, text, within=5):
        """The last t.start's result holds `text` (its summary or a fact, in any case); asked of the
        engine for up to `within` s when the start's answer came before it."""
        got = self.result(within)
        if not got:
            return verdict(False, "no result from %s: t.start(scenario, wait=s) waits for one, and the engine's "
                                  "result op had none" % (self._last_start or "a t.start"))
        hay = json.dumps(got, ensure_ascii=False, default=str).lower()
        return verdict(str(text).lower() in hay, "the result: %s" % json.dumps(got, default=str)[:200])

    def no_errors(self):
        errors = [ln for ln in _log_lines(self._log_path, self._log_from) if ln.get("level") in ("error", "critical")]
        if errors:
            return verdict(False, "the engine's log: %s" % str(errors[0].get("msg"))[:300])
        if not self._fired():
            return verdict(None, "no error, but nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(True, "no error in the engine's log since the test began")

    def check(self, item, v):
        if isinstance(v, bool):
            v = verdict(v, "the test's own %s (verdict(ok, why) would say why)" % v)
        if not isinstance(v, tuple) or len(v) != 2:
            raise TestError("t.check(%r, ...) takes a verdict: t.hurt(...), verdict(ok, why) ..." % item)
        self.checks.append((item, v[0], v[1]))
        return v[0]

    # ---- the end
    def _report(self):
        words = {True: "PASS", False: "FAIL", None: "CAN'T TELL"}
        for item, ok, why in self.checks:
            self._out.write("%-10s %s — %s\n" % (words[ok], item, why))
        counts = (sum(1 for c in self.checks if c[1] is True), sum(1 for c in self.checks if c[1] is False),
                  sum(1 for c in self.checks if c[1] is None))
        self._out.write("RESULT: %d passed, %d failed, %d can't tell\n" % counts)
        self._out.flush()
        return counts

    def _wipe(self, players=True):
        """Its fake players gone, every other entity and every block round the stage too."""
        if players:
            for p in self.players:
                for c in ("player %s stop", "player %s kill"):
                    try:
                        self._rcon.command(c % p)
                    except Exception:
                        pass
        for c in ("kill @e[tag=vfg_test]",
                  "execute positioned %d %d %d run kill @e[type=!player,distance=..%d]" % (X, FLOOR, Z, CLEAR + 16)):
            try:
                self._rcon.command(c)
            except Exception:
                pass
        try:
            self._fill((X - CLEAR, 280, Z - CLEAR, X + CLEAR, TOP, Z + CLEAR), "air")
        except TestError:
            pass

    def _clear(self):
        self._wipe()
        try:
            self._rcon.command("forceload remove %d %d %d %d" % (X - CLEAR, Z - CLEAR, X + CLEAR, Z + CLEAR))
        except Exception:
            pass


def _size(path):
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def _log_lines(path, start):
    """The engine log's JSON lines from byte `start` on."""
    if not path:
        return []
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(start)
            for raw in f:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- the runner

def load_test(path):
    """The test file's test(t); TestError when there is none, or the file does not compile."""
    try:
        spec = importlib.util.spec_from_file_location("vfg_livetest_case", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except FileNotFoundError:
        raise TestError("no test file %s" % path) from None
    except Exception as exc:
        raise TestError("the test file %s does not load: %s: %s" % (path, type(exc).__name__, one_line(exc))) from None
    fn = getattr(module, "test", None)
    if not callable(fn):
        raise TestError("the test file %s has no test(t): a test file defines def test(t): ..." % path)
    return fn


def socket_path(state_dir):
    """The engine's socket in the state folder, or in a folder of its own under /tmp when that path
    is too long for a Unix socket (a realmc stand-in's is)."""
    path = os.path.join(state_dir, "engine.sock")
    if len(os.fsencode(path)) <= MAX_SOCKET_PATH:
        return path
    return os.path.join(tempfile.mkdtemp(prefix="vfg-livetest-"), "engine.sock")


def fresh_state(state_dir):
    """What an earlier test's engine left in the state folder, removed: this one starts empty."""
    os.makedirs(state_dir, exist_ok=True)
    for name in ("state.json", "incantations.json", "engine.log", "engine.out", "engine.sock", "engine.sock.lock"):
        try:
            os.remove(os.path.join(state_dir, name))
        except FileNotFoundError:
            pass


def engine_env(args, state_dir, sock):
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    for k in ("VFG_RCON_LOCK", "VFG_ENGINE_TICK_HZ"):   # the lock is the test server's port's
        env.pop(k, None)
    env.update({
        "VFG_TEST": "1",
        "VFG_MC_PROPERTIES": os.path.abspath(args.properties),
        "VFG_ENGINE_CONTENT": ":".join(os.path.abspath(c) for c in args.content),
        "VFG_ENGINE_STATE": os.path.join(state_dir, "state.json"),
        "VFG_ENGINE_EXPORT": os.path.join(state_dir, "incantations.json"),
        "VFG_ENGINE_SOCKET": sock,
        "VFG_ENGINE_LOG": os.path.join(state_dir, "engine.log"),
        "PYTHONPATH": os.pathsep.join([pkg_parent] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",   # nothing written beside the package or the content
    })
    return env


def _die_with_parent():
    """In the engine's process: SIGTERM when the runner dies, so no engine outlives its test."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:
        pass


def start_engine(args, state_dir, sock, fake_io=False):
    out = open(os.path.join(state_dir, "engine.out"), "wb")
    argv = [sys.executable, "-m", "vfg_engine"] + (["--fake-io"] if fake_io else [])
    try:
        return subprocess.Popen(argv, env=engine_env(args, state_dir, sock), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, preexec_fn=_die_with_parent,
                                cwd=state_dir)
    finally:
        out.close()


def _tail(path, n=12):
    """What the engine said before it ended: its ERROR and WARNING lines, then its last line (a
    traceback's), at most `n`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError:
        return []
    said = [ln for ln in lines if ln.startswith(("ERROR ", "WARNING ", "CRITICAL "))]
    if lines and lines[-1] not in said:
        said.append(lines[-1])
    return said[-n:]


def wait_ready(proc, client, boot):
    """Until the engine's socket answers status: (ok, why not)."""
    end = time.monotonic() + boot
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False, "the engine exited with %d before it answered" % proc.returncode
        try:
            if ctl.request(client.socket_path, {"op": "status", "timeout_s": 2.0}, 4.0).get("ok"):
                return True, ""
        except (ctl.NotRunning, ctl.Timeout):
            pass
        time.sleep(0.2)
    return False, "the engine's socket did not answer within %g s" % boot


def stop_engine(proc, wait=8.0):
    if proc is None or proc.poll() is not None:
        return None if proc is None else proc.returncode
    proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(wait)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(5)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m vfg_engine.livetest", description=__doc__.split("\n")[0])
    p.add_argument("--properties", required=True, help="the test server's server.properties")
    p.add_argument("--state-dir", required=True, help="a writable folder: the engine's state, export, socket, log")
    p.add_argument("--content", action="append", default=[], help="a content folder (several: the library first)")
    p.add_argument("--test", required=True, help="the test file: it defines test(t)")
    p.add_argument("--bindings", help="a JSON list of incantations to bind first: {player, phrase, ability | "
                                      "scenario and args, aliases?, everyday_aliases?}")
    p.add_argument("--boot", type=float, default=60.0, help="seconds the engine may take to answer (60)")
    p.add_argument("--timeout", type=float, help="seconds the whole test may take; then it is stopped as by SIGTERM")
    p.add_argument("--caster", default="Steve")
    p.add_argument("--others", default="Alex", help="other fake players, comma-separated ('' for none)")
    return p.parse_args(argv)


def main(argv=None, out=None, rcon_factory=None, fake_io=False):
    """The runner: exit code = the JSON's "code". ``rcon_factory`` and ``fake_io`` are for its own
    tests (a fake RCON, the engine on FakeIo)."""
    args = parse_args(argv)
    sys.dont_write_bytecode = True   # the test file's folder is not the test's to write
    out = out or sys.stdout
    began = time.monotonic()
    result = {"code": EXIT_BAD_TEST, "passed": 0, "failed": 0, "cant_tell": 0, "checks": [],
              "engine": {"loaded": [], "errors": [], "boot_s": None, "exit": None}}
    timing = {}                      # each phase's seconds: boot, stage, test, clear

    def lap(name, since):
        timing[name] = round(time.monotonic() - since, 2)
        return time.monotonic()
    proc, t = None, None
    previous = {}

    def stopped(signum, frame):
        raise Stopped("stopped by %s" % signal.Signals(signum).name)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, stopped)
    timer, late = None, []
    if args.timeout:
        def time_up():
            late.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
        timer = threading.Timer(args.timeout, time_up)
        timer.daemon = True
        timer.start()

    def say(kind, text):
        out.write("%-10s %s\n" % (kind, text))
        out.flush()

    try:
        refused = None if fake_io else test_server_refusal(args.properties)
        if refused:
            result.update(code=EXIT_REFUSED, error=refused)
            say("ERROR", refused)
            return EXIT_REFUSED
        test = load_test(args.test)
        state_dir = os.path.abspath(args.state_dir)
        fresh_state(state_dir)
        sock = socket_path(state_dir)
        client = EngineClient(sock)
        t0 = mark = time.monotonic()
        proc = start_engine(args, state_dir, sock, fake_io)
        ok, why = wait_ready(proc, client, args.boot)
        if not ok:
            tail = _tail(os.path.join(state_dir, "engine.out"))
            result.update(code=EXIT_BOOT, error=why + (": " + tail[-1] if tail else ""))
            say("ERROR", why)
            for ln in tail:
                say("ENGINE", ln)
            return EXIT_BOOT
        loaded = client.ask("reload", timeout=120.0)
        result["engine"].update(loaded=loaded.get("loaded") or [], errors=loaded.get("errors") or [],
                                boot_s=round(time.monotonic() - t0, 2))
        say("ENGINE", "up in %.1f s on %s: %s" % (result["engine"]["boot_s"], args.properties,
                                                  ", ".join(result["engine"]["loaded"]) or "no classes"))
        for e in result["engine"]["errors"]:
            say("ENGINE", "not loaded: %s: %s: %s" % (e.get("file"), e.get("check"), e.get("error")))
        if args.bindings:
            with open(args.bindings, encoding="utf-8") as f:
                for b in json.load(f) or []:
                    got = client.ask("bind", **dict(b, by=b.get("by") or "test"))
                    say("ENGINE", "bound %r for %s%s" % (b.get("phrase"), b.get("player"),
                                                        "" if got.get("ok") else ": NOT: %s" % got.get("error")))
        rcon = rcon_factory() if rcon_factory else _rcon(args.properties)
        others = tuple(n.strip() for n in (args.others or "").split(",") if n.strip())
        t = T(rcon, client, os.path.join(state_dir, "engine.log"), caster=args.caster, others=others, out=out,
              see_players=not fake_io)
        mark = lap("boot", mark)
        t._setup()
        mark = lap("stage", mark)
        try:
            test(t)
        except TestError as exc:
            result["error"] = str(exc)
            say("ERROR", exc)
        except Stopped:
            raise
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, one_line(exc))
            out.write(traceback.format_exc())
            say("ERROR", "the test raised %s" % result["error"])
        mark = lap("test", mark)
        passed, failed, cant = t._report()
        result.update(passed=passed, failed=failed, cant_tell=cant,
                      checks=[{"item": i, "ok": ok, "why": why} for i, ok, why in t.checks])
        if result.get("error") or not t.checks:
            result["code"] = EXIT_BAD_TEST
            result.setdefault("error", "the test made no check")
        else:
            result["code"] = EXIT_FAIL if failed else EXIT_PASS
        return result["code"]
    except TestError as exc:
        result.update(code=EXIT_BAD_TEST, error=str(exc))
        say("ERROR", exc)
        return EXIT_BAD_TEST
    except Stopped as exc:
        why = "timed out after %g s (--timeout)" % args.timeout if late else str(exc)
        result.update(code=EXIT_STOPPED, stopped=True, error=why)
        say("ERROR", "the test was %s" % why)
        if t is not None:
            result.update(checks=[{"item": i, "ok": ok, "why": why} for i, ok, why in t.checks])
        return EXIT_STOPPED
    except Exception as exc:   # the runner's own failure (a bad --bindings file, a folder it cannot write)
        result.update(code=EXIT_BAD_TEST, error="%s: %s" % (type(exc).__name__, one_line(exc)))
        out.write(traceback.format_exc())
        say("ERROR", "the runner failed: %s" % result["error"])
        return EXIT_BAD_TEST
    finally:
        if timer is not None:
            timer.cancel()
        for signum, handler in previous.items():
            signal.signal(signum, signal.SIG_IGN)   # the cleanup runs to its end
        cleared = time.monotonic()
        try:
            result["engine"]["exit"] = stop_engine(proc)
        except Exception:
            pass
        if t is not None:
            t._clear()
        if timing:
            lap("clear", cleared)
        result["seconds"] = round(time.monotonic() - began, 1)
        result["timing"] = timing
        budget = budget_s()
        result["slow"] = result["seconds"] > budget
        say("TIME", "%.1f s in all%s" % (result["seconds"], " (%s)" % ", ".join(
            "%s %.1f s" % (k, v) for k, v in timing.items()) if timing else ""))
        if result["slow"]:
            say("WARN", "this test took %.1f s, over the %g s a live test may take (VFG_LIVETEST_BUDGET_S): keep "
                        "it short and self-contained, one thing tested, waits no longer than they must"
                % (result["seconds"], budget))
        out.write(json.dumps(result, default=str) + "\n")
        out.flush()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def budget_s():
    """A live test's wall time budget: VFG_LIVETEST_BUDGET_S, else 20 s (Jesse's rule: every live test
    short and self-contained, its setup included)."""
    try:
        value = float(os.environ.get("VFG_LIVETEST_BUDGET_S") or BUDGET_S)
    except ValueError:
        return BUDGET_S
    return value if value > 0 else BUDGET_S


RCON_TIMEOUT_S = 60.0      # the stage's first forceload generates its 49 chunks: seconds, on a new world


def _rcon(properties):
    from .mc.rcon import Rcon
    return Rcon(properties, timeout=RCON_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
