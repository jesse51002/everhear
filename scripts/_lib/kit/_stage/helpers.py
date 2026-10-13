"""The test helpers behind kit/test.py, whose docstring is their guide and their API: kept in a
folder of their own so the implementer's prompt holds the guide, not this code. Run only under
vfg test (VFG_TEST=1), on the test server."""
import atexit
import functools
import inspect
import json
import math
import os
import re
import sys
import time

import mc
import instances
from .. import area, items, world

X, Z = 3000, 3000         # the stage's middle: far from spawn
FLOOR = 295               # its barrier floor; the caster's feet are at FLOOR + 1
HALF = 40                 # the floor is 81 x 81
CLEAR = 48                # what is cleared after: 97 x 97, from y 280 to the top
SAMPLE_S = 0.25
SETTLE_S = 1.0            # after an until-stopped copy's first result: its item given, its first look taken
STAND_IN_HP = 1000
_NUM = r"(-?\d+(?:\.\d+)?(?:E-?\d+)?)"
# A copy's error: Python's, or Minecraft's refusal of a command it sent (and logged).
_ERROR = re.compile(r"Traceback \(most recent call last\)|\b[A-Z][A-Za-z]*Error\b|\bException\b|"
                    r"Incorrect argument|Unknown or incomplete command|<--\[HERE\]")
# Minecraft's answer to a command it did not run.
_REFUSED = re.compile(r"Unknown or incomplete command|Incorrect argument|<--\[HERE\]|^Expected |^Unable to |"
                      r"^Invalid |^Unknown (block|item|entity|effect|attribute|selector)|^Can't find element|"
                      r"^Failed to parse")
# What /player presses, which t.cmd lets through: a key, not a change to him.
_KEYS = ("move", "jump", "sprint", "unsprint", "sneak", "unsneak", "look", "turn", "use", "attack", "stop", "hotbar",
         "swapHands")
_XYZ = re.compile(r"(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)")
_made = []
_QUIET = "no result or log line, nothing new on the stage, and nobody moved, was hurt or set alight"
TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def kinds():
    """The kinds of ability with a template: kit/templates/<kind>.py, which vfg fills for a task."""
    try:
        return sorted(f[:-3] for f in os.listdir(TEMPLATES) if f.endswith(".py"))
    except OSError:
        return []


class Verdict(tuple):
    """(ok, why): ok True (PASS), False (FAIL) or None (CAN'T TELL); true only for a PASS."""

    def __new__(cls, ok, why):
        return tuple.__new__(cls, (ok, str(why)))

    def __bool__(self):
        return self[0] is True


def verdict(ok, why):
    return Verdict(None if ok is None else bool(ok), why)


class TestError(Exception):
    """The test could not run as written: what it says is the report's last line."""


class _NoHelper(TestError, AttributeError):
    pass


def _r(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _ahead(f):
    return "%s %s" % (_r(abs(f)), "ahead" if f >= 0 else "behind")


def _side(r):
    return "%s %s" % (_r(abs(r)), "right" if r >= 0 else "left")


def _span(a, b, neg, pos):
    """From a to b along one way, in words: `11 to 13 ahead`, `1 left to 1 right`, `1 to 4 below his feet`."""
    if a == b:
        return "%s %s" % (_r(abs(a)), neg if a < 0 else pos) if a else "0 %s" % pos
    if a >= 0:
        return "%s to %s %s" % (_r(a), _r(b), pos)
    if b <= 0:
        return "%s to %s %s" % (_r(-b), _r(-a), neg)
    return "%s %s to %s %s" % (_r(-a), neg, _r(b), pos)


# What a wrong call most likely meant.
_HINTS = {"cast": "; its values are the binding's: t.bind(action, phrase, **values) sets them",
          "check": "; a check is t.check(item, verdict)", "box": "; a copy's result: t.result(copy); its log: t.log(n)",
          "copy": "; the copies started: t.copies", "stage": "; a cube: t.blocks or t.region; ground: t.ground"}


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


@_explained
class Test(object):
    def __init__(self, player="Steve", others=None):
        if os.environ.get("VFG_TEST") != "1":
            sys.exit("kit.test runs only through vfg test, on the test server")
        if _made:
            raise TestError("one test per file: use the same t, made once")
        _made.append(self)
        self.caster = player
        self.players, self.mobs, self.copies, self.cubes = [], {}, [], []
        self.start, self.trace, self.checks = {}, [], []
        self._n = 0
        self.bound, self._casted, self._warned = [], False, False
        self._acted, self._what, self._base, self._ready = time.time(), "the start of the test", {}, {}
        self._lines, self._things = {}, None
        self._cleared = False
        self._box = instances.here(rcon=mc.rcon)
        hook = sys.excepthook
        sys.excepthook = lambda k, e, tb: print("ERROR      %s" % e) if issubclass(k, TestError) else hook(k, e, tb)
        area.forceload((X - CLEAR, FLOOR, Z - CLEAR, X + CLEAR, FLOOR, Z + CLEAR), wait=10)
        self._wipe()              # what a test stopped before its end left
        mc.on_stop(self._clear)   # a stop (vfg test's timeout): newer than the forceload, so it goes first
        atexit.register(self._clear)
        world.gamerule("spawn_mobs", False)
        world.gamerule("spawn_phantoms", False)
        mc.rcon("time set midnight")
        mc.rcon("weather clear")
        mc.rcon("fill %d %d %d %d %d %d barrier" % (X - HALF, FLOOR, Z - HALF, X + HALF, FLOOR, Z + HALF))
        self.player(player, 0, 0, 0)
        if others is None:   # Alex beside him, to show the ability spares who it should
            others = ("Steve",) if player.lower() == "alex" else ("Alex",)
        for i, name in enumerate(others):
            self.player(name, -4 * (i + 1), 0, 0)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise _NoHelper("t has no %s%s; its helpers: %s" % (name, _HINTS.get(name, ""), ", ".join(
            n for n in sorted(vars(Test)) if not n.startswith("_") and callable(vars(Test)[n]))))

    # ---- the stage
    def xyz(self, right=0, up=0, forward=0):
        return (X + 0.5 + right, FLOOR + 1 + up, Z + 0.5 - forward)

    def at(self, right=0, up=0, forward=0):
        return "%s %s %s" % tuple(_r(v) for v in self.xyz(right, up, forward))

    def _rel(self, x, y, z):
        """`10 ahead, 2 left, 1 up`: a world spot as the caster faces."""
        r, u, f = x - X - 0.5, y - FLOOR - 1, Z + 0.5 - z
        return ", ".join(([_ahead(f)] if abs(f) >= 0.05 else []) + ([_side(r)] if abs(r) >= 0.05 else []) +
                         (["%s %s" % (_r(abs(u)), "up" if u >= 0 else "down")] if abs(u) >= 0.05 else [])) \
            or "where he stands"

    def _extent(self, cube):
        """`11 to 13 ahead, 1 left to 1 right, 0 to 2 up`: a cube as the caster faces."""
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

    def player(self, name, right=0, up=0, forward=0):
        x, y, z = self.xyz(right, up, forward)
        if name in mc.players():
            mc.rcon("tp %s %s %s %s 180 0" % (name, _r(x), _r(y), _r(z)))
        else:
            said = mc.rcon("player %s spawn at %s %s %s facing 180 0 in minecraft:overworld in survival"
                           % (name, _r(x), _r(y), _r(z)))
            if "Unknown or incomplete command" in said:
                raise TestError("this server has no Carpet: /player is unknown, so no fake player")
            end = time.time() + 10
            while name not in mc.players():
                if time.time() > end:
                    raise TestError("%s did not come online: %s" % (name, said))
                time.sleep(0.1)
        for c in ("gamemode survival %s", "clear %s", "effect clear %s", "effect give %s instant_health 1 20 true",
                  "effect give %s saturation 1 20 true", "player %s hotbar 1"):
            mc.rcon(c % name)
        if name not in self.players:
            self.players.append(name)
        if self.players[0] == name:
            print("AT         %s: the caster, facing north (forward is -z, right is +x), x %s y %s z %s"
                  % (name, _r(x), _r(y), _r(z)), flush=True)
        else:
            print("AT         %s: %s, x %s y %s z %s" % (name, self._rel(x, y, z), _r(x), _r(y), _r(z)), flush=True)
        time.sleep(0.3)
        self.start[name] = {"pos": (x, y, z), "health": self._health(name) or 20.0}
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
        said = mc.rcon("summon %s %s %s %s {%s}" % (kind, _r(x), _r(y), _r(z), ",".join(bits)))
        if not said.startswith("Summoned"):
            raise TestError("summon %s: %s" % (kind, said))
        self.mobs[name] = "@e[tag=vfg_test_%s,limit=1]" % name
        self.start[name] = {"pos": (x, y, z), "health": self._health(name)}
        print("AT         %s: %s, x %s y %s z %s" % (name, self._rel(x, y, z), _r(x), _r(y), _r(z)), flush=True)
        return name

    def stand_in(self):
        self.mob("villager", -1, 0, 0, health=STAND_IN_HP, still=False, name="stand_in")
        return "stand_in"

    def _cube_at(self, what, cube):
        print("AT         %s: %s, x %d..%d y %d..%d z %d..%d" % ((what, self._extent(cube)) + (
            cube[0], cube[3], cube[1], cube[4], cube[2], cube[5])), flush=True)

    def blocks(self, block, right=0, up=0, forward=0, size=3, behind=False):
        cube = self.region(right, up, forward, size, behind=behind, quiet=True)
        for name, start in self.start.items():
            p = [int(math.floor(v)) for v in start["pos"]]
            if all(cube[i] <= p[i] <= cube[i + 3] for i in range(3)):
                raise TestError("the %s cube (%s) would hold %s: put it behind it" % (block, self._extent(cube), name))
        area.fill(cube, block)
        what = "%s cube %dx%dx%d" % (block, size, size, size)
        self.cubes.append((what, cube))
        self._cube_at(what, cube)
        return cube

    def region(self, right=0, up=0, forward=0, size=3, behind=False, quiet=False):
        if forward < 0 and not behind:
            self._placing("a cube", right, up, forward, behind)
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        h = size // 2
        cube = world.Box(x - h, y, z - h, x - h + size - 1, y + size - 1, z - h + size - 1)
        if not quiet:
            self._cube_at("region %dx%dx%d" % (size, size, size), cube)
        return cube

    def ground(self, right=0, forward=10, size=9, depth=3):
        """Real ground in the floor: grass on `depth` blocks of dirt, short grass on it to burn; its cube."""
        x, _, z = (int(math.floor(v)) for v in self.xyz(right, 0, forward))
        h = size // 2
        cube = world.Box(x - h, FLOOR - depth, z - h, x - h + size - 1, FLOOR, z - h + size - 1)
        area.fill(cube, "dirt")
        mc.rcon("fill %d %d %d %d %d %d grass_block" % (cube[0], FLOOR, cube[2], cube[3], FLOOR, cube[5]))
        mc.rcon("fill %d %d %d %d %d %d short_grass replace air" % (cube[0], FLOOR + 1, cube[2], cube[3], FLOOR + 1, cube[5]))
        self._cube_at("ground %dx%d, %d deep" % (size, size, depth + 1), cube)
        return cube

    # ---- starting and using it
    def _mark(self, what):
        """A cast, run, use or crouch: what moved, said and result count from, and where everyone was then."""
        self._acted, self._what, self._base = time.time(), what, {}
        self._lines = dict((c, len(self._box.tail(c, 10 ** 6))) for c in self.copies)
        self._things = self._count_things()
        for who in self.players + list(self.mobs):
            sel = self.mobs.get(who, who)
            pos = self._data(sel, "Pos")
            if pos:
                fire = self._data(sel, "Fire")
                self._base[who] = (tuple(pos[:3]), set(self._effects(sel)), bool(fire and fire[0] > 0))

    def _go(self, meta, base=None, what="the run"):
        self._mark(what)
        try:
            copy, _, proc = self._box.start(meta, base=base)
        except instances.CopyError as e:
            raise TestError("%s did not start: %s" % (meta["action"], e))
        self.copies.append(copy)
        self._lines[copy] = 0
        if meta.get("runs") == "until_stopped":   # its first result, then a second for its item and its loop
            self._box.wait(copy, proc, 5, on_result=True)
            # its first result and what it logged by then say it is ready (an item given, a loop begun): not
            # that it fired
            self._ready[copy] = float((self._box.result(copy) or {}).get("at") or 0)
            time.sleep(SETTLE_S)
            self._lines[copy] = len(self._box.tail(copy, 10 ** 6))
        return copy

    def _entry(self, action):
        try:
            with open(os.environ.get("VFG_TEST_ACTIONS") or "", encoding="utf-8") as f:
                return json.load(f).get(action)
        except (OSError, ValueError):
            return None

    def cast(self, phrase, who=None):
        import incantations
        who = who or self.caster
        e = incantations.Store().lookup(who, phrase)
        if e is None:
            raise TestError("%s has no incantation %r on the test server: bind it first, on the live server "
                            "(vfg action run incantation_create player=%s phrase='%s' action=<action> --task <task>), "
                            "and vfg test brings it; or t.bind(action, phrase) in a library test" % (who, phrase, who, phrase))
        meta = incantations.cast_meta(e, who)
        caster = set(e.get("caster_fields") or ())
        bound = e.get("values") or {}
        # as the box starts it: what the binding lacks, or did not ask for, from its action as deployed
        got = self._box.with_defaults(meta)["values"]
        print("CAST       %s says %r: %s with %s" % (who, phrase, e.get("action"), ", ".join(
            "%s=%s" % (k, json.dumps(v)) for k, v in sorted(got.items()) if k not in caster)[:300] or
            "its defaults"), flush=True)
        now = ["%s=%s (%s)" % (k, json.dumps(got[k]), "not bound" if k not in bound else "bound %s" % json.dumps(bound[k]))
               for k in sorted(got) if k not in caster and (k not in bound or bound[k] != got[k])]
        if now:
            print("BINDING    from %s's defaults now, as a heard cast takes them: %s" % (e.get("action"), ", ".join(now)[:300]),
                  flush=True)
        self._casted = True
        return self._go(meta, what="the cast")   # the copy the bridge starts for a heard cast

    def bind(self, action, phrase, who=None, **values):
        """`who`'s (the caster's) incantation of `action` on the test server, as incantation_create binds it:
        a player value left out is whoever casts it. For a library test; a task's come from the live server."""
        import incantations
        e, v = self._values(action, values)
        fields = e.get("fields") or {}
        caster = [k for k, f in fields.items() if f.get("type") in ("player", "players") and k not in values]
        v.update(dict((k, "") for k in caster))
        incantations.Store().put({"player": who or self.caster, "phrase": phrase, "action": action,
                                  "script": e["script"], "source": e.get("source") or "library",
                                  "server": os.environ.get("VFG_SERVER") or "main", "values": v,
                                  "live": [k for k, f in fields.items() if f.get("live")], "runs": e.get("runs") or "once",
                                  "caster_fields": caster, "asked": dict(values), "cooldown_s": 0, "by": "test"})
        self.bound.append((who or self.caster, phrase))

    def _values(self, action, values):
        """(its actions.json entry, every value: its defaults, these, clamped as vfg clamps, its fixed)."""
        e = self._entry(action)
        if not e:
            raise TestError("no action %s here: vfg test sends the actions the test names" % action)
        fields = e.get("fields") or {}
        v = dict((k, f.get("default")) for k, f in fields.items())
        v.update(e.get("defaults") or {})
        for k, x in values.items():
            f = fields.get(k)
            if f is None:
                raise TestError("%s has no value %s; it has %s" % (action, k, ", ".join(sorted(fields))))
            if isinstance(x, (int, float)) and not isinstance(x, bool):   # clamped, as vfg clamps it
                x = x if f.get("ge") is None else max(x, f["ge"])
                x = x if f.get("le") is None else min(x, f["le"])
            v[k] = x
        v.update(e.get("fixed") or {})
        return e, v

    def run(self, action, **values):
        e, v = self._values(action, values)
        fields = e.get("fields") or {}
        return self._go({"action": action, "script": e["script"], "source": e.get("source") or "library",
                         "server": os.environ.get("VFG_SERVER") or "main", "values": v,
                         "live": [k for k, f in fields.items() if f.get("live")], "runs": e.get("runs") or "once",
                         "task": "test"}, base="test_" + action)

    def _pressed(self):
        if self._casted and not self.checks and not self._warned:
            self._warned = True
            print("WARN       a cast fires the ability at once: a crouch or click after the cast tests a trigger "
                  "the real cast will not press", flush=True)

    def use(self, hold=0, who=None, draw=0):
        who = who or self.caster
        self._pressed()
        self._mark("the use")
        if draw:     # a bow, a crossbow, a trident: held down without a break, then let go
            mc.rcon("player %s use continuous" % who)
            time.sleep(draw)
            mc.rcon("player %s stop" % who)
            return
        mc.rcon("player %s use %s" % (who, "interval 4" if hold else "once"))
        if hold:
            import threading
            threading.Timer(hold, mc.rcon, ["player %s stop" % who]).start()

    def sneak(self, times=3, who=None):
        self._pressed()
        self._mark("the crouch")
        for _ in range(times):
            mc.rcon("player %s sneak" % (who or self.caster))
            time.sleep(0.15)
            mc.rcon("player %s unsneak" % (who or self.caster))
            time.sleep(0.15)

    def look(self, pitch=0, turn=0, who=None):
        self._pressed()
        mc.rcon("rotate %s %s %s" % (who or self.caster, _r(180 + turn), _r(pitch)))

    def _player_target(self, tok, prefix=""):
        """`tok` names a player: one by name, @a, @p, @r, an @e that can hold one; @s when `prefix` (an
        execute's) runs as one. A test's own mobs (tag=vfg_test) are not."""
        tok = tok.strip()
        if "tag=vfg_test" in tok:
            return False
        if tok.startswith("@s"):
            return any(self._player_target(m, "") for m in re.findall(r"\bas (\S+)", prefix))
        return (tok.lower() in [p.lower() for p in self.players] or tok.split("[")[0] in ("@a", "@p", "@r")
                or (tok.startswith("@e") and ("type=player" in tok or "type=" not in tok)))

    def cmd(self, command):
        """One command to set the stage; a test only watches, so one that changes a player is refused, and
        one Minecraft refuses stops the test with its answer."""
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
            raise TestError("t.cmd(%r) refused: a test only watches: what happens to the players is the ability's to "
                            "do (no effect, attribute, tp, score, /player or data change on them); to press for him: "
                            "t.use, t.sneak, t.look" % command)
        said = mc.rcon(c)
        if _REFUSED.search(said or ""):
            raise TestError("t.cmd(%r): Minecraft did not run it: %s" % (command, " ".join(said.split())[:200]))
        return said

    def wait(self, seconds):
        time.sleep(seconds)

    # ---- what a copy said
    def _copy(self, copy):
        """A copy the test started: the last, else by its name or its action's (t.run's are test_<action>)."""
        if not self.copies:
            raise TestError("nothing was started: t.cast or t.run first")
        if copy is None:
            return self.copies[-1]
        return copy if copy in self.copies or "test_" + copy not in self.copies else "test_" + copy

    def result(self, copy=None, within=3):
        """A copy's latest result() since the last cast, run or use, waiting up to `within` s for one:
        {"summary", "facts"}, or {} when none came."""
        copy, end = self._copy(copy), time.time() + within
        while True:
            got = self._box.result(copy) or {}
            if float(got.get("at") or 0) >= self._acted - 0.05:
                return {"summary": got.get("summary", ""), "facts": got.get("facts") or {}}
            if time.time() > end:
                return {}
            time.sleep(0.2)

    def said(self, text, copy=None, within=3):
        """A verdict: the copy's latest result (t.result) holds `text`, in its summary or a fact, in any case."""
        got = self.result(copy, within)
        if not got:
            return verdict(False, "%s wrote no result() within %s s of %s" % (self._copy(copy), _r(within), self._what))
        hay = (got["summary"] + " " + json.dumps(got["facts"], ensure_ascii=False, default=str)).lower()
        return verdict(str(text).lower() in hay, "%s said %r" % (self._copy(copy), got["summary"][:200]))

    def log(self, n=20, copy=None):
        """Print a copy's last `n` log lines into the report, as LOG lines."""
        copy = self._copy(copy)
        for ln in self._box.tail(copy, n) or ["(nothing yet)"]:
            print("LOG        %s: %s" % (copy, ln), flush=True)

    # ---- watching
    def _data(self, sel, path):
        out = mc.rcon("data get entity %s %s" % (sel, path))
        if "No entity was found" in out or "found no" in out.lower():
            return None
        return [float(v) for v in re.findall(_NUM + r"[dfbs]?(?=[,\]\s]|$)", out.split(":", 1)[-1])]

    def _health(self, who):
        got = self._data(self.mobs.get(who, who), "Health")
        return got[0] if got else None

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
            row = {"pos": tuple(pos[:3]), "health": hp[0] if hp else None, "fire": fire[0] if fire else 0}
            if who in self.players:
                row["motion"] = tuple((self._data(sel, "Motion") or [0, 0, 0])[:3])
            row["effects"] = self._effects(sel)
            s[who] = row
        self.trace.append(s)

    def _effects(self, sel):
        return re.findall(r'id: "minecraft:([a-z_]+)"', mc.rcon("data get entity %s active_effects" % sel))

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
        got = re.search(r"Count: (\d+)", mc.rcon("execute if entity @e[type=!player,x=%d,y=%d,z=%d,distance=..%d]"
                                                 % (X, FLOOR, Z, CLEAR)))
        return int(got.group(1)) if got else 0

    def _fired(self):
        """Did anything happen since the last cast, run or use: a result or a log line (past an until-stopped
        copy's first, "ready"), more things on the stage, or anyone there moved, hurt, given an effect or set
        alight?"""
        for c in self.copies:
            at = float((self._box.result(c) or {}).get("at") or 0)
            if at >= self._acted - 0.05 and at > self._ready.get(c, 0) + 0.001:
                return True
            if len(self._box.tail(c, 10 ** 6)) > self._lines.get(c, 0):
                return True
        if self._things is not None and self._count_things() > self._things:
            return True
        for who in self.players + list(self.mobs):
            p0, e0, burned = self._base.get(who) or (None, set(), False)
            h0 = (self.start.get(who) or {}).get("health")
            for r in self._rows(who, self._acted):
                if r is None or (r["health"] is not None and h0 is not None and r["health"] < h0) \
                        or (r["fire"] > 0 and not burned) or (p0 and math.dist(r["pos"], p0) > 0.5) \
                        or set(r["effects"]) - e0:
                    return True
        return False

    def _landed(self):
        """`; the copy's log last named 3000 297 2990 (10 ahead, 1 up)`: where its blast or projectile went, if
        it said, in its result or its log."""
        for c in reversed(self.copies):
            got = self._box.result(c) or {}
            said = [json.dumps(got.get("facts") or {}, default=str)] if float(got.get("at") or 0) >= self._acted - 0.05 else []
            for ln in said + list(reversed(self._box.tail(c, 60))):
                for m in reversed(list(_XYZ.finditer(ln))):
                    x, y, z = (float(v) for v in m.groups())
                    if abs(x - X) < 200 and abs(z - Z) < 200 and 250 < y < 330:
                        return "; %s last named %s %s %s (%s)" % (c, m.group(1), m.group(2), m.group(3), self._rel(x, y, z))
        return ""

    # ---- checks
    def hurt(self, who, at_least=1):
        rows, h0 = self._rows(who), (self.start.get(who) or {}).get("health")
        if any(r is None for r in rows) or any(r and r["health"] is not None and r["health"] <= 0 for r in rows):
            return verdict(True, "%s died" % who)
        low = min([r["health"] for r in rows if r and r["health"] is not None] or [h0 or 0])
        lost = (h0 or 0) - low
        why = "%s health %s -> %s (lost %s, at least %s)" % (who, _r(h0 or 0), _r(low), _r(lost), _r(at_least))
        if lost >= at_least:
            return verdict(True, why)
        first = [s["t"] for s in self.trace if s.get(who) and s[who]["health"] is not None and s[who]["health"] < (h0 or 0)]
        if first:
            why += "; it first lost health %s s after %s" % (_r(first[0] - self._acted), self._what)
        last = [r for r in rows if r]
        if last:
            why += "; it was %s" % self._rel(*last[-1]["pos"])
        return verdict(False, why + self._landed())

    def killed(self, who):
        rows = self._rows(who)
        dead = any(r is None or (r["health"] is not None and r["health"] <= 0) for r in rows)
        last = rows[-1]["health"] if rows and rows[-1] else None
        return verdict(dead, "%s %s" % (who, "died" if dead else "alive at the end, health %s%s"
                                        % (_r(last or 0), self._landed())))

    def unhurt(self, who=None):
        who = who or self.caster
        v = self.hurt(who, at_least=0.01)
        if not v[0] and not self._fired():
            return verdict(None, "nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(not v[0], v[1].split(" (")[0])

    def others_unhurt(self):
        others = self.players[1:]
        if not others:
            return verdict(None, "nobody else is on the stage: Test(player, others=(\"Alex\",))")
        hurt = [w for w in others if self.hurt(w, at_least=0.01)[0]]
        if not hurt and not self._fired():
            return verdict(None, "nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(not hurt, "%s hurt" % ", ".join(hurt) if hurt else "%s not hurt" % ", ".join(others))

    def burning(self, who):
        """`who` was set alight (its Fire ticks above 0) at some sample: fire is not an effect."""
        most = max([r["fire"] for r in self._rows(who) if r] or [0])
        return verdict(most > 0, "%s %s" % (who, "burned (Fire up to %s ticks)" % _r(most) if most > 0 else "never burned"))

    def moved(self, at_least, way="any", who=None):
        """From where `who` was at the last cast, run, use or crouch; judged on `who` alone."""
        who = who or self.caster
        far, end = self._far(who, way)
        why = ("%s moved %s %s (at least %s, since %s)" % (who, _r(far), "" if way == "any" else way, _r(at_least),
                                                           self._what)).replace("  ", " ")
        if far >= at_least:
            return verdict(True, why)
        if who in self.players and "stand_in" in self.mobs and self._far("stand_in", way)[0] >= at_least:
            why += ("; the stand-in was thrown %s but %s did not move: the ability pushes what is near him, not him"
                    % (_r(self._far("stand_in", way)[0]), who))
        if end:
            why += "; %s ended %s" % (who, self._rel(*end))
        return verdict(False, why + self._landed())

    def _far(self, who, way):
        p0 = (self._base.get(who) or (None,))[0] or (self.start.get(who) or {}).get("pos")
        best, end = 0.0, None
        for r in self._rows(who, self._acted):
            if not (r and p0):
                continue
            end = r["pos"]
            d = [r["pos"][i] - p0[i] for i in range(3)]
            best = max(best, {"up": d[1], "forward": -d[2]}.get(way, math.sqrt(sum(v * v for v in d))))
        return best, end

    def built(self, cube, share=0.02):
        """`share` of a cube is no longer air: something was built there."""
        n, total = self._count(cube, ["air"])
        n = total - n
        why = "%d of its %d blocks are built (at least %d%%)" % (n, total, round(share * 100))
        return verdict(n >= share * total, why if n >= share * total else why + "; it is %s" % self._extent(cube))

    def weather(self, kind="rain", within=8):
        """It rains (or thunders, or is clear) on the stage, within `within` s: rain sets in over a few
        seconds. The stage starts clear."""
        cond = {"rain": "raining:true", "thunder": "thundering:true", "clear": "raining:false"}.get(kind)
        if cond is None:
            raise TestError('t.weather takes "rain", "thunder" or "clear"')
        end = time.time() + within
        while True:
            ok = "passed" in mc.rcon('execute if predicate {type:"minecraft:weather_check",%s}' % cond).lower()
            if ok or time.time() > end:
                return verdict(ok, "the weather is %s%s" % ("" if ok else "not ", kind))
            time.sleep(0.5)

    def gone(self, cube, share=0.5):
        return self.turned(cube, "air", share)

    def _count(self, cube, blocks):
        """(how many of a cube's blocks are any of `blocks`, how many it has)."""
        x1, y1, z1, x2, y2, z2 = cube
        spots = [(x, y, z) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1) for z in range(z1, z2 + 1)]
        hit = [False] * len(spots)
        for b in blocks:
            for i, ok in enumerate(world.passes(["if block %d %d %d %s" % (x, y, z, b) for x, y, z in spots])):
                hit[i] = hit[i] or ok
        return sum(hit), len(spots)

    def turned(self, cube, block, share=0.1):
        """`share` of a cube is `block` now (or any of a list of blocks)."""
        blocks = [block] if isinstance(block, str) else list(block)
        n, total = self._count(cube, blocks)
        why = "%d of its %d blocks are %s (at least %d%%)" % (n, total, " or ".join(blocks), round(share * 100))
        if n >= share * total:
            return verdict(True, why)
        return verdict(False, why + "; it is %s%s" % (self._extent(cube), self._landed()))

    def block(self, right, up, forward, block):
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        said = mc.rcon("execute if block %d %d %d %s" % (x, y, z, block))
        return verdict("passed" in said.lower(), "%s at %d %d %d: %s" % (block, x, y, z, said or "no"))

    def fire_near(self, right=0, up=0, forward=0, radius=4, count=1):
        """At least `count` fire blocks within `radius` of the spot, from its height to 3 up: fire is a block."""
        x, y, z = (int(math.floor(v)) for v in self.xyz(right, up, forward))
        spots = [(a, b, c) for a in range(x - radius, x + radius + 1) for b in range(y - 1, y + 3)
                 for c in range(z - radius, z + radius + 1)]
        n = sum(world.passes(["if block %d %d %d #minecraft:fire" % s for s in spots]))
        return verdict(n >= count, "%d fire blocks within %s of %s (at least %d)" % (
            n, radius, self._rel(x + 0.5, y, z + 0.5), count))

    def near(self, kind, right=0, up=0, forward=0, radius=8, count=1, within=3):
        x, y, z = self.xyz(right, up, forward)
        sel = "@e[type=%s,x=%s,y=%s,z=%s,distance=..%s]" % (kind, _r(x), _r(y), _r(z), _r(radius))
        end, most = time.time() + within, 0
        while True:
            most = max(most, mc.count(sel))
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
        seen = seen or ('"minecraft:%s"' % name) in mc.rcon("data get entity %s active_effects" % self.mobs.get(who, who))
        return verdict(seen, "%s %s %s" % (who, "had" if seen else "never had", name))

    def holds(self, item, who=None, within=3):
        who = who or self.caster
        preds = ([items.match(item)] if re.search(r"[A-Z ]", item) else [item, items.match(item)])
        end = time.time() + within
        while True:
            has = any("passed" in mc.rcon("execute if items entity %s %s %s" % (who, slot, p)).lower()
                      for p in preds for slot in ("container.*", "weapon.offhand"))
            if has or time.time() > end:
                return verdict(has, "%s %s %s" % (who, "has" if has else "has no", item))
            time.sleep(0.25)

    def no_errors(self):
        if not self.copies:
            return verdict(None, "nothing was started")
        for c in self.copies:
            bad = [ln for ln in self._box.tail(c, 300) if _ERROR.search(ln)] + [x for x in [self._box.error(c)] if x]
            if bad:
                return verdict(False, "%s: %s" % (c, bad[0].strip()[:200]))
        if not self._fired():
            return verdict(None, "no error, but nothing fired since %s: %s" % (self._what, _QUIET))
        return verdict(True, "no error in the logs of %s" % ", ".join(self.copies))

    def check(self, item, v):
        if isinstance(v, bool):
            v = verdict(v, "the test's own %s (test.verdict(ok, why) would say why)" % v)
        if not isinstance(v, tuple) or len(v) != 2:
            raise TestError("t.check(%r, ...) takes a verdict: t.hurt(...), test.verdict(ok, why) ..." % item)
        self.checks.append((item, v[0], v[1]))
        return v[0]

    def done(self):
        words = {True: "PASS", False: "FAIL", None: "CAN'T TELL"}
        for item, ok, why in self.checks:
            print("%-10s %s — %s" % (words[ok], item, why))
        failed = [c for c in self.checks if c[1] is False]
        if failed:
            n = int(os.environ.get("VFG_TEST_TAIL") or 15)
            for c in self.copies if n else ():
                print("--- the last lines of %s's log:" % c)
                print("\n".join("  " + ln for ln in self._box.tail(c, n)) or "  (nothing)")
        print("RESULT: %d passed, %d failed, %d can't tell" % (
            sum(1 for c in self.checks if c[1] is True), len(failed), sum(1 for c in self.checks if c[1] is None)))
        sys.stdout.flush()
        self._clear()
        os._exit(2 if not self.checks else 1 if failed else 0)

    # ---- clearing
    def _wipe(self):
        """Its copies stopped, its fake players gone, every other entity and every block round the stage too."""
        for c in self.copies:
            try:
                self._box.stop(c)
            except Exception:
                pass
        for p in self.players:
            mc.rcon("player %s stop" % p)
            mc.rcon("player %s kill" % p)
        mc.rcon("kill @e[tag=vfg_test]")
        mc.rcon("execute positioned %d %d %d run kill @e[type=!player,distance=..%d]" % (X, FLOOR, Z, CLEAR + 16))
        area.fill((X - CLEAR, 280, Z - CLEAR, X + CLEAR, 319, Z + CLEAR), "air", most_mspt=0)

    def _clear(self):
        if self._cleared:
            return
        self._cleared = True
        self._wipe()
        if self.bound:
            import incantations
            for who, phrase in self.bound:
                incantations.Store().remove(who, phrase)
        world.restore()
        area.release()
