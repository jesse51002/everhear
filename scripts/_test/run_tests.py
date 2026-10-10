"""Tests for the scripts folder, run under the Python EC2 has, with pydantic for the actions:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/run_tests.py [-j N]
    uv run --no-project --python 3.12 --with pydantic python scripts/_test/run_tests.py ScriptTests

With no test named, every test runs in N worker processes at once (default twice the CPUs, at
most 24, or VFG_TEST_JOBS), each test on its own, and a failing test's output is shown with it.
With tests named, they run in this process one after the other, as unittest runs them.

They start fake_rcon.py on a free port with a temp server.properties and check _lib/mc.py. Then
every script (every folder without a leading "_") runs, all at once, each against its own fake
world with Steve and Alex online, the way EC2 runs it: `python3 -u main.py` in its folder with
PYTHONPATH=scripts/_lib and VFG_INSTANCE set, as a copy of its first action with nothing given
(the meta file `vfg action run` writes, lib/py/vfg_actions.py, which values() reads). Steve tries
every item it gives him (a click, a 1.5 s hold) and crouches; after 4 s it gets SIGTERM. It must
send commands, raise nothing, exit 0 within 2 s of the stop, and leave nothing tagged
vfg_<name>: no entity, bossbar, forceload or objective. A seed (README.md beside main.py) has its
README checked too, and must end as its README says; one that ends by itself runs to its end,
its sleeps ten times as fast (fast_sleep/). The scripts under the six core actions (CORE) need
values their defaults leave empty, so test_core.py runs them instead. The kit's tests
(test_kit.py), instances.py's (test_instances.py), test_core.py and every test_presets_*.py and
test_examples_*.py beside this file run with them. A script that ends by itself cleans up at
once, without mc's 0.4 s grace for a last blast (VFG_EXIT_GRACE_S=0).
"""
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
if __name__ == "__main__":
    sys.modules.setdefault("run_tests", sys.modules[__name__])   # `import run_tests` is this module, not a second one
# A script that ends by itself cleans up at once, not after mc's 0.4 s grace for a last blast.
os.environ.setdefault("VFG_EXIT_GRACE_S", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LIB = os.path.join(SCRIPTS, "_lib")
sys.path.insert(0, HERE)

from fake_rcon import FakeRcon, ONLINE  # noqa: E402
from fake_world import World  # noqa: E402

TMP = tempfile.mkdtemp(prefix="vfg-scripts-test-")


def properties(password, port):
    path = os.path.join(TMP, "server-%d.properties" % port)
    with open(path, "w") as f:
        f.write("#Minecraft server properties\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n" % (port, password))
    return path


def script_env(props):
    env = dict(os.environ)
    env.update(PYTHONPATH=LIB, VFG_MC_PROPERTIES=props, VFG_RCON_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1")
    return env


# ---- mc.py, in this process ------------------------------------------------------------

PASSWORD = secrets.token_hex(8)
FAKE = FakeRcon(PASSWORD).start()
os.environ["VFG_MC_PROPERTIES"] = properties(PASSWORD, FAKE.port)
os.environ["VFG_RCON_HOST"] = "127.0.0.1"
sys.path.insert(0, LIB)
import mc  # noqa: E402

# mc installs SIGTERM/SIGINT handlers that exit; the test runner keeps the defaults.
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)


class McTests(unittest.TestCase):
    def setUp(self):
        FAKE.clear()

    def test_auth_and_list(self):
        self.assertEqual(mc.rcon("list"), ONLINE)
        self.assertGreaterEqual(FAKE.logins, 1)
        self.assertEqual(FAKE.cmds(), ["list"])

    def test_wrong_password_returns_empty(self):
        good = mc._settings
        mc._drop()
        mc._settings = ("not-the-password", FAKE.port)
        try:
            before = FAKE.failed_logins
            self.assertEqual(mc.rcon("list"), "")
            self.assertGreater(FAKE.failed_logins, before)
            self.assertEqual(FAKE.cmds(), [])
        finally:
            mc._drop()
            mc._settings = good

    def test_reconnects_after_drop(self):
        self.assertEqual(mc.rcon("list"), ONLINE)
        logins = FAKE.logins
        FAKE.drop_all()
        time.sleep(0.1)
        self.assertEqual(mc.rcon("list"), ONLINE)
        self.assertEqual(FAKE.logins, logins + 1)

    def test_server_down_returns_empty(self):
        good = mc._settings
        mc._drop()
        mc._settings = (PASSWORD, 1)  # nothing listens on port 1
        try:
            t = time.time()
            self.assertEqual(mc.rcon("list"), "")
            self.assertLess(time.time() - t, 5)
        finally:
            mc._drop()
            mc._settings = good

    def test_a_stopped_fake_refuses_connections(self):
        """FakeRcon.stop() is a server going down: no new connection gets in."""
        fake = FakeRcon(secrets.token_hex(8)).start()
        fake.stop()
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", fake.port), timeout=2).close()

    def test_refuses_the_owners_commands(self):
        for cmd in ("op Steve", "/OP Steve", "deop Steve", "ban Steve", "ban-ip 1.2.3.4", "pardon Steve",
                    "whitelist add Steve", "kick Steve", "save-off", "stop",
                    "execute as @a run stop", "execute as @a run /kick Steve", "execute at Steve run OP Steve"):
            self.assertEqual(mc.rcon(cmd), "", cmd)
        self.assertEqual(FAKE.cmds(), [])
        mc.rcon("/time set day")
        mc.rcon("execute as @a run say stopping by")
        self.assertEqual(FAKE.cmds(), ["time set day", "execute as @a run say stopping by"])

    def test_default_player_is_the_owner_else_the_first_online(self):
        saved = mc.OWNER
        try:
            mc.OWNER = "Sunny"
            self.assertEqual(mc.default_player(), "Sunny")
            self.assertEqual(mc.owner_near(9), "@a[name=Sunny,distance=..9]")
            self.assertEqual(mc.not_owner(), ",name=!Sunny")
            mc.OWNER = ""
            self.assertEqual(mc.default_player(), "Steve")  # the fake has Steve online
            self.assertEqual(mc.owner_near(9), "@a[tag=vfg_no_owner,distance=..9]")
            self.assertEqual(mc.not_owner(), "")
        finally:
            mc.OWNER = saved

    def test_owner_comes_from_the_environment(self):
        code = "import mc; print(repr(mc.OWNER))"
        for value, want in (("Sunny", "'Sunny'"), ("not a name", "''"), ("", "''")):
            env = dict(os.environ, PYTHONPATH=LIB, VFG_OWNER=value)
            out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                                 universal_newlines=True).stdout.strip()
            self.assertEqual(out, want, value)

    def test_refused_word(self):
        self.assertEqual(mc.refused_word("/Ban-IP x"), "ban-ip")
        self.assertEqual(mc.refused_word("execute if entity @a run pardon x"), "pardon")
        self.assertIsNone(mc.refused_word("time set day"))
        self.assertIsNone(mc.refused_word("say stop it"))

    def test_say_quoting(self):
        text = 'He said "run" \\ then {left} \u00e9'
        mc.say(text)
        mc.say("psst", to="Steve", who="Demon Lord", color="dark_red")
        a, b = FAKE.cmds()
        self.assertTrue(a.startswith("tellraw @a "))
        parts = json.loads(a[len("tellraw @a "):])
        self.assertEqual(parts[0]["text"], "[AI] ")
        self.assertEqual(parts[0]["color"], "aqua")
        self.assertEqual(parts[1]["text"], text)
        self.assertTrue(b.startswith("tellraw Steve "))
        self.assertEqual(json.loads(b[len("tellraw Steve "):])[0]["text"], "[Demon Lord] ")

    def test_players_and_pos(self):
        self.assertEqual(mc.players(), ["Steve"])
        self.assertEqual(mc.players(alive=True), ["Steve"])
        self.assertIn("data get entity Steve Health", FAKE.cmds())
        self.assertEqual(mc.pos("Steve"), (10.5, 64.0, -3.5))
        self.assertIsInstance(mc.pos("Steve"), tuple)

    def test_pos_none(self):
        # The fake answers nothing for an unknown kind of query.
        self.assertIsNone(mc.pos("Steve Rotation"))

    def test_count_and_score(self):
        self.assertEqual(mc.score("Steve", "x"), 0)
        self.assertEqual(mc.count("@e[tag=x]"), 0)

    def test_spawn_tags(self):
        self.assertTrue(mc.spawn("Steve", "zombie", "IsBaby:1b"))
        summons = [c for c in FAKE.cmds() if " summon " in c]
        self.assertEqual(len(summons), 1)
        self.assertIn('Tags:["vfg","%s"]' % mc.TAG, summons[0])
        self.assertIn("PersistenceRequired:1b", summons[0])
        self.assertIn("IsBaby:1b", summons[0])
        self.assertTrue(summons[0].startswith("execute at Steve run summon zombie "))

    def test_name_and_tag(self):
        self.assertEqual(mc.NAME, "_test")
        self.assertEqual(mc.TAG, "vfg__test")

    def test_log_format(self):
        r, w = os.pipe()
        old = sys.stdout
        sys.stdout = os.fdopen(w, "w")
        try:
            mc.log("hello", 3)
            sys.stdout.flush()
        finally:
            sys.stdout.close()
            sys.stdout = old
        with os.fdopen(r) as f:
            line = f.read().strip()
        self.assertRegex(line, r"^\d\d:\d\d:\d\d _test hello 3$")

    def test_cleanup_interrupts_rcon_call(self):
        """SIGTERM while a slow rcon call is in flight: the cleanup runs on a fresh
        connection, mc kills @e[tag=TAG], and the script exits 0 at once."""
        password = secrets.token_hex(8)
        fake = FakeRcon(password, delay_on="slow", delay_s=5).start()
        try:
            folder = os.path.join(TMP, "probe")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "main.py"), "w") as f:
                f.write("from mc import rcon, on_stop, log\n"
                        "on_stop(lambda: rcon('say first registered'))\n"
                        "on_stop(lambda: rcon('say cleanup ran'))\n"
                        "rcon('slow command')\n"
                        "log('not reached')\n")
            proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder,
                                    env=script_env(properties(password, fake.port)),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            deadline = time.time() + 5
            while "slow command" not in fake.cmds() and time.time() < deadline:
                time.sleep(0.05)
            self.assertIn("slow command", fake.cmds())
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            out, _ = proc.communicate(timeout=4)
            self.assertLess(time.time() - t, 2)
            self.assertEqual(proc.returncode, 0, out)
            self.assertEqual(fake.cmds(), ["slow command", "say cleanup ran", "say first registered",
                                           "kill @e[tag=vfg_probe]"])
            self.assertNotIn(b"Traceback", out)
            self.assertNotIn(b"not reached", out)
        finally:
            fake.stop()

    def test_password_never_logged(self):
        """The password appears in nothing a script prints, even on errors."""
        password = secrets.token_hex(8)
        folder = os.path.join(TMP, "leak")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write("from mc import rcon, log\nrcon('list')\nrcon('op x')\nlog('end')\n")
        # Nothing listens on port 1: rcon fails and logs.
        out = subprocess.run([sys.executable, "-u", "main.py"], cwd=folder,
                             env=script_env(properties(password, 1)), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=30).stdout.decode()
        self.assertIn("rcon error", out)
        self.assertIn("refused", out)
        self.assertNotIn(password, out)


# ---- every script, against its own fake, all at once -------------------------------------

# The scripts under the dispatcher's core actions: each needs values its defaults leave empty (a
# command, a place, a running copy), so test_core.py runs them, with those values, on a fake world.
CORE = ("command", "edit", "save_place", "stop", "summon_around", "teleport")
FAST_SLEEP = os.path.join(HERE, "fast_sleep")


def script_names():
    return sorted(d for d in os.listdir(SCRIPTS) if not d.startswith("_") and not d.startswith(".")
                  and d not in CORE and os.path.isdir(os.path.join(SCRIPTS, d)))


def readme(name):
    """A seed's README.md as lines (the 16 seed scripts keep one beside main.py), else None."""
    path = os.path.join(SCRIPTS, name, "README.md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def ends_by_itself(name):
    """A seed whose README says it ends by itself."""
    lines = readme(name) or []
    return len(lines) > 2 and lines[2].strip() == "Ends: by itself"


def play(world, until, done):
    """What a player does to a running example, so its body runs too: Steve holds each item
    the script gave him in turn, clicks, holds the button down for 1.5 s, and crouches; until
    `until`, or `done` is set (the script ended)."""
    tried = set()
    while time.time() < until and not done.is_set():
        with world.lock:
            ids = [m.group(1) for p, i in world.gives if p == "Steve"
                   for m in [re.search(r'vfg_item:"([^"]*)"', i)] if m]
        new = [i for i in ids if i not in tried]
        if not new:
            world.sneak("Steve", 4)
            done.wait(0.2)
            continue
        item = new[0]
        tried.add(item)
        world.hold("Steve", main=item)
        done.wait(0.3)
        world.click("Steve")
        done.wait(0.5)
        for _ in range(8):
            world.click("Steve")
            done.wait(0.2)


_CATALOG = []
_CATALOG_LOCK = threading.Lock()


def catalog():
    """(vfg_actions, its Catalog of the actions), loaded once."""
    with _CATALOG_LOCK:   # the scripts run in threads: the actions load once
        if not _CATALOG:
            sys.path.insert(0, os.path.join(os.path.dirname(SCRIPTS), "lib", "py"))
            import vfg_actions
            _CATALOG.append((vfg_actions, vfg_actions.Catalog(os.path.dirname(SCRIPTS), None)))
        return _CATALOG[0]


def copy_meta(action, given, copy=None):
    """The meta file vfg action run writes for a copy of an action run with these values."""
    va, cat = catalog()
    cls = cat.get(action)
    with _CATALOG_LOCK:
        checked = va.check_values(cls, given)
    return {"copy": copy or action, "action": action, "script": cls.script, "source": "library",
            "values": checked.values, "live": checked.live, "runs": cls.runs, "task": None, "started": None}


def action_meta(name):
    """The meta file of a copy of a script's first action (the one named as the script, else the
    first by name) run with nothing given, as vfg action run writes it."""
    va, cat = catalog()
    over = sorted(n for n in cat.names() if cat.get(n).script == name)
    if not over:
        why = cat.errors().get(name)
        raise AssertionError("no action runs %s%s" % (name, " (%s)" % why if why else ""))
    return copy_meta(name if name in over else over[0], {}, copy=name)


def run_example(name, seconds=4.0, folder=None, meta=None, sleep_scale=None):
    """An example started as on EC2, as a copy of its action with nothing given (a library
    script; else `meta`, the meta file's dict), against a fake world with Steve and Alex
    online: it plays a little, then SIGTERM if it is still running. sleep_scale: its
    time.sleep runs that much faster (fast_sleep/sitecustomize.py)."""
    folder = folder or os.path.join(SCRIPTS, name)
    res = {"name": name, "example": True}
    with open(os.path.join(folder, "main.py"), encoding="utf-8") as f:
        source = f.read()
    res["source"] = source
    try:
        compile(source, os.path.join(folder, "main.py"), "exec")
        res["compiles"] = None
    except SyntaxError as e:
        res["compiles"] = "%s" % e
        return res
    world = World()
    world.add_player("Steve", (0.5, 64.0, 0.5), rot=(-90.0, 0.0), prefix="[Owner] ")
    world.add_player("Alex", (8.5, 64.0, 3.5), rot=(90.0, 0.0))
    password = secrets.token_hex(8)
    fake = FakeRcon(password, responder=world.respond).start()
    out_path = os.path.join(TMP, name + ".out")
    env = script_env(properties(password, fake.port))
    env.update(VFG_INSTANCE=name, VFG_SCRIPT=name, VFG_RUN_DIR=tempfile.mkdtemp(dir=TMP), VFG_LIVE_POLL_S="0.5",
               VFG_PROTECTED=os.path.join(TMP, "no-protected.json"))
    if sleep_scale:
        env.update(PYTHONPATH=os.pathsep.join([LIB, FAST_SLEEP]), VFG_TEST_SLEEP_SCALE=str(sleep_scale))
    if meta is None:
        meta = action_meta(name)
    with open(os.path.join(env["VFG_RUN_DIR"], name + ".json"), "w") as f:
        json.dump(meta, f)
    res["action"] = meta.get("action")
    tag = "vfg_" + name
    try:
        with open(out_path, "wb") as out:
            proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env, stdout=out, stderr=out)
            started = time.time() + 15        # a busy machine can take a while to start Python
            while not world.commands and proc.poll() is None and time.time() < started:
                time.sleep(0.05)
            until = time.time() + seconds
            done = threading.Event()
            player = threading.Thread(target=play, args=(world, until, done), daemon=True)
            player.start()
            while time.time() < until and proc.poll() is None:
                time.sleep(0.05)
            res["ended_itself"] = proc.poll() is not None
            done.set()
            res["before"] = list(world.commands)
            stop_at = time.time()
            if not res["ended_itself"]:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                    res["stop_took"] = time.time() - stop_at
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    res["stop_took"] = None
            player.join(5)
            res["code"] = proc.returncode
            with world.lock:
                res["cmds"] = list(world.commands)
                res["after"] = res["cmds"][len(res["before"]):]
                res["left"] = {
                    "entities": [e["type"] for e in world.entities if tag in e["tags"] or "vfg_left_" + name in e["tags"]
                                 or any(t.startswith(tag + "_") for t in e["tags"])],
                    "bossbars": [b for b in world.bossbars if b.startswith("vfg:%s" % name)],
                    "forceloads": sorted(world.forced),
                    "objectives": [o for o in world.objectives if o == tag or o.startswith(tag + "_")],
                }
    finally:
        fake.stop()
    with open(out_path, encoding="utf-8", errors="replace") as f:
        res["output"] = f.read()
    return res


RESULTS = {}


class ScriptTests(unittest.TestCase):
    """Every script once, all at once, each against its own fake: an example (main.py with
    ASKS) as a copy of its first action with nothing given, a seed (README.md beside it) too,
    and a seed that ends by itself run to its end, its sleeps ten times as fast."""

    run_whole = True     # the runner gives one worker the whole class: setUpClass runs every script

    @classmethod
    def setUpClass(cls):
        threads = []
        for name in script_names():
            def go(n=name):
                try:
                    RESULTS[n] = run_example(n, seconds=10, sleep_scale=0.1) if ends_by_itself(n) else run_example(n)
                except Exception as e:  # reported by the test itself
                    RESULTS[n] = {"name": n, "error": repr(e)}
            t = threading.Thread(target=go)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def test_there_are_sixteen_seeds(self):
        self.assertGreaterEqual(len([n for n in script_names() if readme(n) is not None]), 16)

    def check_example(self, name, res):
        """The spec's test of an example: it runs, sends commands, raises nothing, and stops
        within 2 s of SIGTERM with nothing tagged left behind."""
        out = res["output"]
        first = res["source"].lstrip().split("\n", 1)[0]
        self.assertRegex(first, r'^"""[^:"]+: .+"""$', "line 1 is not a one-line docstring '<Title>: what it does'")
        self.assertRegex(res["source"].lstrip().split("\n")[1], r"^ASKS = \[", "line 2 is not ASKS = [...]")
        self.assertNotIn("Traceback", out, out[-2000:])
        self.assertGreaterEqual(len(res["cmds"]), 1, "sent no command; output:\n" + out[-1000:])
        self.assertEqual(res["code"], 0, out[-1000:])
        left = dict(res["left"])
        if not res["ended_itself"]:
            self.assertIsNotNone(res["stop_took"], "did not exit within 2 s of SIGTERM")
            self.assertIn("kill @e[tag=vfg_%s]" % name, res["after"], "no cleanup sent after SIGTERM")
        else:
            # Ended by itself: the mobs it made stay (under vfg_left_<name>); a helper (a marker) never does.
            left["entities"] = [t for t in left["entities"] if t == "marker"]
        self.assertEqual(left, {"entities": [], "bossbars": [], "forceloads": [], "objectives": []},
                         "left behind after it %s" % ("ended" if res["ended_itself"] else "stopped"))

    def check_readme(self, name, lines, res):
        """A seed's README: one sentence, its Usage (only [key=value]s its action declares), and
        how it ends, which is what it did."""
        self.assertGreaterEqual(len(lines), 3, "README needs at least three lines")
        first = lines[0].strip()
        self.assertTrue(first and first[-1] in ".!?", "README line 1 is not a sentence: %r" % first)
        self.assertIsNone(re.search(r"[.!?]\s+[A-Z]", first[:-1]), "README line 1 is more than one sentence")
        self.assertFalse(first.startswith("#"), "README line 1 is a heading, not a sentence")
        usage = lines[1]
        self.assertTrue(usage.startswith("Usage: %s" % name), "README line 2: %r" % usage)
        self.assertIn(lines[2].strip(), ("Ends: by itself", "Ends: when stopped"), "README line 3: %r" % lines[2])
        self.assertNotIn("<", usage, "a seed's values all have defaults: [key=value], no <positional>")
        values = action_meta(name)["values"]
        keys = re.findall(r"\[([a-z][a-z0-9_]*)=", usage)
        self.assertTrue(keys or not values, "Usage names none of its values: " + usage)
        for k in keys:
            self.assertIn(k, values, "Usage names %s, which its action does not declare" % k)
        if ends_by_itself(name):
            self.assertTrue(res["ended_itself"], "its README says it ends by itself, but it ran on")
        else:
            self.assertFalse(res["ended_itself"], "ended before it was stopped, but its README says it runs until "
                             "stopped:\n" + res["output"][-1000:])

    def check(self, name):
        res = RESULTS[name]
        self.assertNotIn("error", res, res.get("error"))
        self.assertIsNone(res.get("compiles"), "main.py does not compile under Python %s" % sys.version.split()[0])
        self.check_example(name, res)
        lines = readme(name)
        if lines is not None:
            self.check_readme(name, lines, res)


DEMO = '''"""Demo Wand: a click fires a beam that hurts what it hits and leaves a small crater."""
ASKS = ["a demo wand"]
from kit import values, items, aim, area, mobs, loop

v = values()
mobs.Boss("Demo Boss", "zombie", health=40).spawn(near="Steve")
area.forceload((100, 0, 100, 130, 0, 130), wait=0)
for p in loop(items.clicks(v["player"], "Demo Wand", cooldown=v["cooldown"])):
    r = aim.ray(p, 60)
    if r:
        for h in aim.near_line(r, 1.5, shooter=p):
            aim.run_on(h, "damage @s 5 minecraft:magic")
        area.sphere(r.end, v["crater_radius"], "air")%s
'''


class ExampleHarnessTests(unittest.TestCase):
    """The example path itself, on a demo example and on one with a bug in its body."""

    check_example = ScriptTests.check_example

    def demo(self, name, extra=""):
        folder = os.path.join(TMP, "examples", name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(DEMO % extra)
        return run_example(name, folder=folder, meta={"values": {"player": "@a", "cooldown": 0, "crater_radius": 3},
                                                     "live": ["cooldown", "crater_radius"]})

    def test_a_good_example_passes(self):
        res = self.demo("demo_wand")
        self.check_example("demo_wand", res)
        self.assertTrue(any(c.startswith("fill ") for c in res["cmds"]), "Steve's click never reached the body")

    def test_a_bug_in_the_body_is_caught(self):
        res = self.demo("demo_broken", "\n        oops()")
        with self.assertRaises(AssertionError):
            self.check_example("demo_broken", res)
        self.assertIn("NameError", res["output"])



def _add_script_tests():
    for name in script_names():
        def test(self, n=name):
            self.check(n)
        setattr(ScriptTests, "test_script_" + name, test)


_add_script_tests()

# ---- the kit (scripts/_lib/kit): its tests are in test_kit.py -------------------------------

# ---- the kit, the presets and the examples: test_kit.py, every test_presets_*.py and test_examples_*.py

def _add_kit_and_preset_tests():
    import glob
    import importlib
    for path in [os.path.join(HERE, "test_kit.py"), os.path.join(HERE, "test_instances.py"),
                 os.path.join(HERE, "test_core.py")] \
            + sorted(glob.glob(os.path.join(HERE, "test_presets_*.py"))) \
            + sorted(glob.glob(os.path.join(HERE, "test_examples_*.py"))):
        mod = importlib.import_module(os.path.basename(path)[:-3])
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj.__module__ == mod.__name__:
                globals()[name if mod.__name__ in ("test_kit", "test_instances", "test_core")
                          else "%s__%s" % (mod.__name__, name)] = obj


_add_kit_and_preset_tests()

# ---- the runner: every test at once, in worker processes ------------------------------------

def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flat(t)
        else:
            yield t


def _units():
    """The tests as the workers take them: a class whose setUpClass runs every script at once
    (ScriptTests) whole and first, every other test on its own."""
    tests = list(_flat(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])))
    whole = {}
    units = []
    for t in tests:
        if getattr(type(t), "run_whole", False):
            whole.setdefault(type(t), []).append(t.id())
        else:
            units.append([t.id()])
    return list(whole.values()) + units, len(tests)


class _Rows(unittest.TestResult):
    """Each test's outcome, as (id, outcome, traceback)."""

    def __init__(self):
        unittest.TestResult.__init__(self)
        self.rows = []

    def addSuccess(self, test):
        unittest.TestResult.addSuccess(self, test)
        self.rows.append((test.id(), "ok", ""))

    def addFailure(self, test, err):
        unittest.TestResult.addFailure(self, test, err)
        self.rows.append((test.id(), "FAIL", self.failures[-1][1]))

    def addError(self, test, err):
        unittest.TestResult.addError(self, test, err)
        self.rows.append((test.id(), "ERROR", self.errors[-1][1]))

    def addSkip(self, test, reason):
        unittest.TestResult.addSkip(self, test, reason)
        self.rows.append((test.id(), "skipped %r" % reason, ""))

    def addExpectedFailure(self, test, err):
        unittest.TestResult.addExpectedFailure(self, test, err)
        self.rows.append((test.id(), "expected failure", ""))

    def addUnexpectedSuccess(self, test):
        unittest.TestResult.addUnexpectedSuccess(self, test)
        self.rows.append((test.id(), "FAIL", "unexpected success"))

    def addSubTest(self, test, subtest, err):
        unittest.TestResult.addSubTest(self, test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self.rows.append((subtest.id(), "FAIL" if failed else "ERROR",
                              (self.failures if failed else self.errors)[-1][1]))


def _worker():
    """Run the units the runner sends on stdin, one JSON list of test ids a line, answering
    each with a JSON line: the outcomes, how many ran, how long it took, and what the tests
    printed. Its own stdout is the runner's alone: whatever a test prints (or a thread a test
    left, between tests) goes to that test's log, or nowhere."""
    channel = os.fdopen(os.dup(1), "w", buffering=1)
    nowhere = os.open(os.devnull, os.O_WRONLY)
    os.dup2(nowhere, 1)
    os.dup2(nowhere, 2)
    tests = {t.id(): t for t in _flat(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))}
    for line in sys.stdin:
        ids = json.loads(line)
        result = _Rows()
        t0 = time.time()
        with tempfile.TemporaryFile() as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            try:
                unittest.TestSuite([tests[i] for i in ids]).run(result)
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(nowhere, 1)
                os.dup2(nowhere, 2)
            log.seek(0)
            output = log.read().decode("utf-8", "replace")
        channel.write(json.dumps({"rows": result.rows, "ran": result.testsRun, "output": output,
                                  "took": time.time() - t0}) + "\n")


def _run_all(jobs):
    """Every unit on the first free worker; each test's outcome as it comes, then the failures
    with their output, and unittest's summary. True when all passed."""
    t0 = time.time()
    units, total = _units()
    expected = [i for unit in units for i in unit]
    todo = list(reversed(units))
    lock = threading.Lock()
    rows, ran, outputs, took = [], [0], {}, []
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def work():
        proc = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "--worker"], env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True)
        try:
            while True:
                with lock:
                    if not todo:
                        return
                    unit = todo.pop()
                proc.stdin.write(json.dumps(unit) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                got = json.loads(line) if line else {"rows": [(i, "ERROR", "the worker died") for i in unit],
                                                     "ran": len(unit), "output": "", "took": 0.0}
                with lock:
                    ran[0] += got["ran"]
                    took.append((got["took"], unit[0].rsplit(".", 1)[0] if len(unit) > 1 else unit[0]))
                    for test_id, outcome, tb in got["rows"]:
                        rows.append((test_id, outcome, tb))
                        if outcome in ("FAIL", "ERROR"):
                            outputs[test_id] = got["output"]
                        print("%s ... %s" % (test_id, outcome), flush=True)
                if not line:
                    return
        finally:
            proc.stdin.close()
            proc.wait()

    threads = [threading.Thread(target=work) for _ in range(min(jobs, len(units)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    bad = [r for r in rows if r[1] in ("FAIL", "ERROR")]
    for test_id, outcome, tb in bad:
        print("=" * 70)
        print("%s: %s" % (outcome, test_id))
        print("-" * 70)
        print(tb.rstrip())
        if outputs.get(test_id, "").strip():
            print("-" * 30 + " its output " + "-" * 28)
            print(outputs[test_id].rstrip()[-4000:])
    print("-" * 70)
    print("Slowest: " + ", ".join("%s %.1f s" % (name.split(".", 1)[-1], t) for t, name in sorted(took)[::-1][:5]))
    print("Ran %d tests in %.3fs (%d workers)" % (ran[0], time.time() - t0, len(threads)))
    print()
    missing = sorted(set(expected) - set(r[0] for r in rows))
    if missing:
        print("Not run: " + ", ".join(missing))
    if bad or ran[0] < total:
        print("FAILED (failures=%d, errors=%d%s)" % (len([r for r in bad if r[1] == "FAIL"]),
                                                     len([r for r in bad if r[1] == "ERROR"]),
                                                     ", not run=%d" % (total - ran[0]) if ran[0] < total else ""))
        return False
    print("OK")
    return True


def _jobs(argv):
    """-j N from argv (taken out of it), else VFG_TEST_JOBS, else twice the CPUs, at most 24."""
    jobs = os.environ.get("VFG_TEST_JOBS") or min(24, 2 * (os.cpu_count() or 4))
    if "-j" in argv:
        i = argv.index("-j")
        jobs = argv[i + 1]
        del argv[i:i + 2]
    return max(1, int(jobs))


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--worker"]:
            try:
                _worker()
            finally:
                for d in {TMP, getattr(sys.modules.get("test_kit"), "TMP", TMP),
                          getattr(sys.modules.get("test_core"), "TMP", TMP),
                          getattr(sys.modules.get("test_examples_rays"), "TMP", TMP)}:
                    shutil.rmtree(d, ignore_errors=True)
        else:
            args = sys.argv[1:]
            jobs = _jobs(args)
            if args:
                unittest.main(argv=sys.argv[:1] + args, verbosity=2)
            else:
                sys.exit(0 if _run_all(jobs) else 1)
    finally:
        FAKE.stop()
