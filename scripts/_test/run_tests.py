"""Tests for the scripts folder, run under the Python EC2 has:

    uv run --python 3.9 python scripts/_test/run_tests.py

They start fake_rcon.py on a free port with a temp server.properties, check
_lib/mc.py, then run every script (every folder without a leading "_") the way
EC2 does: `python3 -u main.py` in its own folder with PYTHONPATH=scripts/_lib.
"""
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LIB = os.path.join(SCRIPTS, "_lib")
sys.path.insert(0, HERE)

from fake_rcon import FakeRcon, ONLINE  # noqa: E402

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

def script_names():
    return sorted(d for d in os.listdir(SCRIPTS)
                  if not d.startswith("_") and not d.startswith(".") and os.path.isdir(os.path.join(SCRIPTS, d)))


def usage_args(line):
    """The Usage line's arguments: each <...> filled with Steve, every [...] left out."""
    rest = line.split(None, 2)[2] if len(line.split()) > 2 else ""
    rest = re.sub(r"\[[^\]]*\]", " ", rest)
    return ["Steve" for _ in re.findall(r"<[^>]*>", rest)]


def run_script(name):
    folder = os.path.join(SCRIPTS, name)
    res = {"name": name}
    with open(os.path.join(folder, "README.md"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    res["lines"] = lines
    with open(os.path.join(folder, "main.py"), encoding="utf-8") as f:
        source = f.read()
    try:
        compile(source, os.path.join(folder, "main.py"), "exec")
        res["compiles"] = None
    except SyntaxError as e:
        res["compiles"] = "%s" % e
        return res
    if len(lines) < 3 or not lines[1].startswith("Usage: ") or not lines[2].startswith("Ends: "):
        return res
    ends_itself = lines[2].strip() == "Ends: by itself"
    args = usage_args(lines[1])
    res["args"] = args
    password = secrets.token_hex(8)
    fake = FakeRcon(password).start()
    out_path = os.path.join(TMP, name + ".out")
    try:
        with open(out_path, "wb") as out:
            start = time.time()
            proc = subprocess.Popen([sys.executable, "-u", "main.py"] + args, cwd=folder,
                                    env=script_env(properties(password, fake.port)), stdout=out, stderr=out)
            if ends_itself:
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    res["timeout"] = True
                res["took"] = time.time() - start
            else:
                time.sleep(3)
                res["alive_at_3s"] = proc.poll() is None
                res["before"] = fake.cmds()
                stop_at = time.time()
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                    res["stop_took"] = time.time() - stop_at
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    res["stop_took"] = None
                res["after"] = fake.cmds(since=stop_at)
            res["code"] = proc.returncode
            res["ends_itself"] = ends_itself
            res["cmds"] = fake.cmds()
    finally:
        fake.stop()
    with open(out_path, encoding="utf-8", errors="replace") as f:
        res["output"] = f.read()
    return res


RESULTS = {}


class ScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        threads = []
        for name in script_names():
            def go(n=name):
                try:
                    RESULTS[n] = run_script(n)
                except Exception as e:  # reported by the test itself
                    RESULTS[n] = {"name": n, "error": repr(e)}
            t = threading.Thread(target=go)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def test_there_are_sixteen_seeds(self):
        self.assertGreaterEqual(len(script_names()), 16)

    def check(self, name):
        res = RESULTS[name]
        self.assertNotIn("error", res, res.get("error"))
        self.assertIsNone(res.get("compiles"), "main.py does not compile under Python %s" % sys.version.split()[0])
        lines = res["lines"]
        self.assertGreaterEqual(len(lines), 3, "README needs at least three lines")
        first = lines[0].strip()
        self.assertTrue(first and first[-1] in ".!?", "README line 1 is not a sentence: %r" % first)
        self.assertIsNone(re.search(r"[.!?]\s+[A-Z]", first[:-1]), "README line 1 is more than one sentence")
        self.assertFalse(first.startswith("#"), "README line 1 is a heading, not a sentence")
        self.assertTrue(lines[1].startswith("Usage: %s" % name), "README line 2: %r" % lines[1])
        self.assertIn(lines[2].strip(), ("Ends: by itself", "Ends: when stopped"), "README line 3: %r" % lines[2])
        out = res["output"]
        self.assertNotIn("Traceback", out, out[-2000:])
        self.assertGreaterEqual(len(res["cmds"]), 1, "sent no command; output:\n" + out[-1000:])
        if res["ends_itself"]:
            self.assertFalse(res.get("timeout"), "did not end by itself within 20 s")
            self.assertEqual(res["code"], 0, out[-1000:])
        else:
            self.assertTrue(res["alive_at_3s"], "ended before 3 s but its README says it runs until stopped:\n"
                            + out[-1000:])
            self.assertGreaterEqual(len(res["before"]), 1, "sent no command in its first 3 s")
            self.assertIsNotNone(res["stop_took"], "did not exit within 2 s of SIGTERM")
            self.assertEqual(res["code"], 0, out[-1000:])
            self.assertIn("kill @e[tag=vfg_%s]" % name, res["after"], "no cleanup sent after SIGTERM")


def _add_script_tests():
    for name in script_names():
        def test(self, n=name):
            self.check(n)
        setattr(ScriptTests, "test_script_" + name, test)


_add_script_tests()

if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        FAKE.stop()
