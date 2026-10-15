"""vfg test: a task's test of its engine content, run on the test server's own engine (livetest),
never the live one; its skeleton; and the box's side, remote/test.py: the queue, the fresh world,
the sandbox. No EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Two of harness.py's boxes: the live one, with a fake engine (fake_engine.py) whose bindings the
task made go to the test, and the live box's engine for tests to run on (<vfg>/engine: its
.venv/bin/python is fake_livetest.py, which notes how it was run and plays what the test file's
FAKE asks; the real runner is tested in engine/tests/box); and a second box whose settings
VFG_TEST_BOX names (its own VFG_REMOTE_VFG and VFG_REMOTE_MC), a test server: its marker
(.vfg-test-server), its fresh world's token (world/vfg-fresh) and the unit minecraft-test, whose
restart (this file's systemctl, first on the box's PATH) writes the token again. VFG_TEST_BOX unset
is the live box's own test server, <box>/vfg-test and <box>/mc-test (TestBoxOwn). Each test runs as
vfg test runs it: in a systemd-run --user sandbox where this PC has one, else as a plain process;
TestSandbox checks the sandbox itself and is skipped where there is none.
"""
import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import PY, TESTS, Box, BoxCase, alive, wait_for, write  # noqa: E402
from fake_engine import FakeEngine  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
import standins  # noqa: E402

# The box's systemctl for these tests: minecraft-test's restart is noted ($FAKE_UNITS/<unit>.calls)
# and runs $FAKE_UNITS/<unit>.restart (what a restart does to the test server); the rest is
# toolkit/tests/bin/systemctl's.
SYSTEMCTL = """#!/usr/bin/env bash
dir="${FAKE_UNITS:?}"
if [ "${1:-}" = restart ]; then
  shift; nb=""
  if [ "${1:-}" = --no-block ]; then nb=" --no-block"; shift; fi
  u="$1"
  [ -f "$dir/$u" ] || { echo "Failed to restart $u.service: Unit $u.service not found." >&2; exit 5; }
  echo "restart$nb $u" >> "$dir/$u.calls"
  if [ -f "$dir/$u.restart" ]; then bash "$dir/$u.restart" || exit 1; fi
  echo active > "$dir/$u"
  exit 0
fi
exec %s "$@"
""" % os.path.join(TESTS, "bin", "systemctl")
SERVER_RE = (r"^SERVER     %s \(RCON \d+\): (no wait in the queue|[\d.]+ s in the queue); (%s); "
             r"(sandboxed: systemd-run --user, as this user \(weaker: the live server's processes stay visible\)"
             r"|NOT sandboxed \(.*\): a plain process)(; /tmp shared: the test server is under it)?$")
# The box's systemd-run, for the system mode this PC has none of: each call noted ($FAKE_UNITS/
# systemd-run.calls, its argv as JSON), and $FAKE_UNITS/systemd-run.mode says what it does: "down"
# (no system manager: every call fails), "probe-fails" (the probe finds the live server in reach),
# else it runs what it was given here, as this user, with its EnvironmentFile and WorkingDirectory
# (no sandbox: what is checked is what it was asked for). --user always fails: the box has none.
SYSTEMD_RUN = """#!%s
import json, os, shlex, subprocess, sys
units = os.environ["FAKE_UNITS"]
argv = sys.argv[1:]
with open(os.path.join(units, "systemd-run.calls"), "a") as f:
    f.write(json.dumps(argv) + "\\n")
try:
    mode = open(os.path.join(units, "systemd-run.mode")).read().strip()
except OSError:
    mode = ""
if "--user" in argv or mode == "down":
    sys.stderr.write("Failed to connect to bus: No medium found\\n")
    sys.exit(1)
unit = [a.split("=", 1)[1] for a in argv if a.startswith("--unit=")]
if "--" not in argv:
    sys.exit(0)                                  # sudo -n systemd-run ... true
if unit and "-probe-" in unit[0]:
    if mode == "probe-fails":
        print("reachable: /opt/mc/server.properties")
        sys.exit(1)
    sys.exit(0)
props = dict(a.split("=", 1) for a in argv[argv.index("-p") + 1::2] if "=" in a and not a.startswith("-"))
env = {}
for line in open(props["EnvironmentFile"]):
    if "=" in line:
        k, v = line.rstrip("\\n").split("=", 1)
        env[k] = shlex.split(v)[0] if v else ""
p = subprocess.run(argv[argv.index("--") + 1:], cwd=props["WorkingDirectory"], env=env, stdin=subprocess.DEVNULL)
sys.stderr.write("Running as unit: %%s.service\\nFinished with result: %%s\\n" %% (unit[0], "success" if p.returncode == 0 else "exit-code"))
sys.exit(p.returncode)
""" % sys.executable


def sandbox_here():
    """Does systemd-run --user run a unit on this PC (as test.py's probe would find it)?"""
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir("/run/user/%d" % os.getuid()):
        env["XDG_RUNTIME_DIR"] = "/run/user/%d" % os.getuid()
    try:
        return subprocess.run(["systemd-run", "--user", "--no-ask-password", "--quiet", "--wait", "--collect",
                               "--pipe", "-p", "ProtectSystem=strict", "true"], env=env, timeout=20,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


SANDBOX = sandbox_here()

ZAP = '''"""Zap: a spark at a player (a test's)."""
from vfg_engine import Ability, AbilityConfig


class ZapSteve(Ability):
    class Config(AbilityConfig):
        summary = "A spark."
'''
PASSING = '''def test(t):
    husk = t.mob("husk", 0, 0, 10)
    t.cast("zap now")
    t.check("it hits the husk", t.hurt(husk))


FAKE = {"lines": ["ENGINE     loaded zap_steve", "PASS       it hits the husk — 6 damage",
                  "RESULT: 1 passed, 0 failed, 0 can't tell"],
        "checks": [{"item": "it hits the husk", "ok": True, "why": "6 damage"}]}
'''
REPORT = ["ENGINE     loaded zap_steve", "PASS       it hits the husk — 6 damage",
          "RESULT: 1 passed, 0 failed, 0 can't tell"]


def install_engine(vfg):
    """<vfg>/engine as deploy.sh engine leaves it on the live box: its venv's python (here
    fake_livetest.py), its package and its library."""
    python = os.path.join(vfg, "engine", ".venv", "bin", "python")
    with open(os.path.join(TESTS, "fake_livetest.py")) as f:
        write(python, "#!%s\n%s" % (PY, f.read()))
    os.chmod(python, os.stat(python).st_mode | stat.S_IXUSR)
    write(os.path.join(vfg, "engine", "vfg_engine", "livetest.py"), "# a stand-in: .venv/bin/python plays it\n")
    write(os.path.join(vfg, "engine", "library", "arena.py"), '"""The library (a stand-in)."""\n')


class TestCase(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        standins.install(self.box.repo)
        self.box.write("made/main/content/zap_steve.py", ZAP)
        self.engine = FakeEngine(self.box.rvfg).start()
        self.addCleanup(self.engine.stop)
        install_engine(self.box.rvfg)
        # the test server: a second box on the same host, its own folders and its own fake Minecraft
        self.trcon = FakeRcon(secrets.token_hex(8)).start()
        self.addCleanup(self.trcon.stop)
        self.tbox = Box(self.trcon)
        self.addCleanup(self.tbox.close)
        self.make_test_server(self.tbox.rvfg, self.tbox.rmc)
        self.settings = write(os.path.join(self.box.root, "test-box.env"),
                              "# the test server\nexport VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC='%s'\nVFG_REPO=/elsewhere\n"
                              % (self.tbox.rvfg, self.tbox.rmc))

    def make_test_server(self, vfg, mc, unit=True):
        """`vfg` and `mc` a test server's, as its setup leaves them: its Python, its marker, a fresh
        world's token and, with `unit`, minecraft-test, whose restart writes the token again."""
        os.makedirs(os.path.join(vfg, "python", "bin"), exist_ok=True)
        if not os.path.lexists(os.path.join(vfg, "python", "bin", "python3")):
            os.symlink(sys.executable, os.path.join(vfg, "python", "bin", "python3"))
        if not os.path.isfile(os.path.join(mc, "server.properties")):
            write(os.path.join(mc, "server.properties"), "enable-rcon=true\nrcon.port=%d\nrcon.password=%s\n"
                  % (self.trcon.port, self.trcon.password))
        self.mc_test = mc
        write(os.path.join(mc, ".vfg-test-server"), json.dumps({"made": "2026-10-13T00:00:00Z",
                                                                  "rcon_port": self.trcon.port,
                                                                  "live_mc": self.box.rmc, "live_vfg": self.box.rvfg}))
        write(os.path.join(mc, "world", "vfg-fresh"), "")
        write(os.path.join(vfg, "protected.json"), "[]\n")
        write(os.path.join(self.box.bin, "systemctl"), SYSTEMCTL)
        os.chmod(os.path.join(self.box.bin, "systemctl"), 0o755)
        if unit:
            write(os.path.join(self.box.units, "minecraft-test"), "active\n")
            self.on_restart("")

    def on_restart(self, more):
        """What minecraft-test's restart does: a fresh world's token, and `more` (bash)."""
        write(os.path.join(self.box.units, "minecraft-test.restart"),
              "touch %s\n%s\n" % (os.path.join(self.mc_test, "world", "vfg-fresh"), more))

    def restarts(self):
        try:
            with open(os.path.join(self.box.units, "minecraft-test.calls")) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def run_test(self, *args, **env):
        env.setdefault("VFG_TEST_BOX", self.settings)
        return self.vfg("test", *args, **env)

    def on_test_box(self, *rel):
        return os.path.join(self.tbox.rvfg, *rel)

    def call(self, vfg=None):
        with open(os.path.join(vfg or self.tbox.rvfg, "state", "engine", "livetest-call.json")) as f:
            return json.load(f)

    def assertServerLine(self, line, world="a fresh world", mc=None):
        self.assertRegex(line, SERVER_RE % (re.escape(mc or self.tbox.rmc), world))


class TestRun(TestCase):
    def test_the_tasks_content_and_test_go_to_the_test_server_then_livetest_runs_there(self):
        # the live server: zap_steve bound Steve's "zap now"; another task bound Alex's
        self.assertOk(self.vfg("engine", "bind", "Steve", "zap now", "fireball"))
        self.assertOk(self.vfg("engine", "bind", "Alex", "big boom", "--scenario", "horde", "count=3"))
        self.box.add_task("zap_steve", "Zap for Steve.", made={"content": ["zap_steve"],
                                                               "bindings": ["steve.zap_now"], "grants": []})
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        r = self.run_test("zap_steve")
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        lines = r.stdout.splitlines()
        self.assertServerLine(lines.pop(0))
        self.assertEqual(lines, REPORT)                          # both JSON lines left out
        # the test server got this server's content and the test, in tests/ (this user's alone)
        self.assertTrue(os.path.isfile(self.on_test_box("tests", "content", "zap_steve.py")))
        self.assertTrue(os.path.isfile(self.on_test_box("tests", "zap_steve.py")))
        # livetest ran there on the live box's engine: the library, then this server's content
        call = self.call()
        home = os.path.realpath(os.path.join(self.box.rvfg, "engine"))
        self.assertEqual(call["content"], [os.path.join(home, "library"), self.on_test_box("tests", "content")])
        self.assertEqual(call["options"]["properties"], os.path.join(self.tbox.rmc, "server.properties"))
        self.assertEqual(call["options"]["state-dir"], self.on_test_box("state", "engine"))
        self.assertEqual(call["options"]["test"], self.on_test_box("tests", "zap_steve.py"))
        self.assertEqual((call["options"]["boot"], call["options"]["timeout"], call["options"]["tail"]),
                         ("60", "60", "15"))
        self.assertEqual((call["pythonpath"], call["cwd"]), (home, os.path.realpath(self.on_test_box("tests"))))
        env = call["env"]
        self.assertEqual((env["VFG_TEST"], env["VFG_MC_PROPERTIES"], env["VFG_PROTECTED"]),
                         ("1", os.path.join(self.tbox.rmc, "server.properties"), self.on_test_box("protected.json")))
        self.assertFalse([k for k in env if k.startswith("VFG_ENGINE_")])   # never the live engine's paths
        # the task's own binding, from the live engine, bound first; another task's is not
        self.assertEqual(call["bindings"], [{"player": "Steve", "phrase": "zap now", "ability": "fireball",
                                             "aliases": ["zapnow"], "everyday_aliases": []}])
        # the live box was read, never written; one call to the test server
        self.assertEqual([(c["args"].get("op"), c["args"]["vfg"]) for c in self.box.calls if "args" in c][-3:],
                         [("ask", self.box.rvfg), ("list", self.box.rvfg), (None, self.tbox.rvfg)])
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "tests")))
        # after it, the test server restarts for the next test, without waiting for it
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"])
        # a second run has the world that restart made
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[0])
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"] * 2)

    def test_the_two_json_lines_are_livetests_then_the_test_servers(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.box.programs()[-1]
        self.assertEqual(sorted(args["engine"]), ["bindings", "content"])
        # what the box printed, as vfg read it: livetest's JSON just before the test server's, last
        out = subprocess.run([PY, "-u", "-"], input=self.program_text(args), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.box_env(), timeout=60).stdout.decode()
        lines = out.strip().splitlines()
        self.assertEqual(sorted(json.loads(lines[-1])), ["code", "queued", "restarted", "seconds", "timed_out"])
        self.assertIn("checks", json.loads(lines[-2]))

    def program_text(self, args):
        sys.path.insert(0, os.path.join(os.path.dirname(TESTS)))
        with open(os.path.join(os.path.dirname(TESTS), "remote", "common.py")) as f:
            text = f.read()
        text += "\nARGS = json.loads(%r)\n" % json.dumps(args)
        with open(os.path.join(os.path.dirname(TESTS), "remote", "test.py")) as f:
            return (text + "\n" + f.read()).encode()

    def box_env(self):
        env = dict(self.box.base_env, PATH="%s:%s" % (self.box.bin, os.environ.get("PATH", "")),
                   FAKE_UNITS=self.box.units)
        return env

    def test_a_failed_check_a_test_that_cannot_run_and_one_past_its_time(self):
        def fake(**f):
            self.box.write("made/main/tests/zap_steve.py", "def test(t):\n    pass\n\n\nFAKE = %r\n" % f)
        fake(code=1, lines=["FAIL       it zaps — 0 sparks"], checks=[{"item": "it zaps", "ok": False, "why": "0"},
                                                                    {"item": "Alex unhurt", "ok": True, "why": "-"}],
             log=["12:00:00.000 INFO the engine started", "12:00:01.000 ERROR zap_steve on_cast: KeyError 'at'"])
        r = self.run_test("zap_steve")
        self.assertOneLine(r, 1, r"^vfg: the test failed: 1 of 2 checks: it zaps$")
        self.assertIn("FAIL       it zaps — 0 sparks\n", r.stdout)
        # the engine's log after a fail, as livetest itself prints it (--tail): the test server reads nothing
        self.assertIn("LOG        12:00:01.000 ERROR zap_steve on_cast: KeyError 'at'\n", r.stdout)
        self.run_test("zap_steve", "-n", "1")
        self.assertEqual(self.call()["options"]["tail"], "1")
        # the codes an implementer fixes are 1 and 2; 4 and 5 are never theirs (TEST_EXITS)
        fake(code=2, error="the engine refused start: no scenario horde", checks=[])
        self.assertOneLine(self.run_test("zap_steve"), 2,
                           r"^vfg: the test could not run as written: the engine refused start: no scenario horde$")
        # content that does not load is the implementer's: 2, with the engine's words, whether the
        # test then could not run (2) or its engine gave up (4)
        load = [{"file": "zap_steve.py", "check": "imports", "error": "NameError: x"}]
        fake(code=2, error="no ability zap_steve in the engine: t.grant", checks=[], errors=load)
        self.assertOneLine(self.run_test("zap_steve"), 2, r"^vfg: the test could not run as written: no ability "
                           r"zap_steve in the engine: t\.grant; the content that did not load: zap_steve\.py: "
                           r"imports: NameError: x$")
        fake(code=4, error="the engine did not load", checks=[], errors=load)
        self.assertOneLine(self.run_test("zap_steve"), 2, r"^vfg: the test's engine did not load the content: "
                                                          r"zap_steve\.py: imports: NameError: x$")
        # an engine that does not start for no reason in the content: 4, reported untested
        fake(code=4, error="the engine exited with 1 before it answered: the datapack is not loaded", checks=[])
        self.assertOneLine(self.run_test("zap_steve"), 4, r"^vfg: the test's engine did not start \(the engine exited "
                           r"with 1 before it answered: the datapack is not loaded\); report the ability untested")
        fake(code=3, error="no .vfg-test-server marker beside server.properties", checks=[])
        self.assertOneLine(self.run_test("zap_steve"), 3, r"^vfg: not a test server: no \.vfg-test-server marker")
        # past its time: livetest stops itself at its --timeout, and says so; the implementer's (2)
        self.box.write("made/main/tests/zap_steve.py", "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
        began = time.time()
        r = self.run_test("zap_steve", "--timeout", "5")
        self.assertOneLine(r, 2, r"^vfg: the test ran past its 5 s and was stopped: shorten it")
        self.assertIn("started", r.stdout)
        self.assertLess(time.time() - began, 30)
        self.assertEqual(self.call()["options"]["timeout"], "5")

    def test_what_a_test_changed_since_its_last_run_is_shown_first(self):
        body = ("def test(t):\n    husk = t.mob('husk', 0, 0, 10, health=40)\n    wall = t.blocks('stone', 0, 0, 12)\n"
                "    t.cast('zap now')\n    t.check('it hits', t.hurt(husk, at_least=5))\n"
                "    t.check('the wall breaks', t.gone(wall, 0.5))\n    t.check('Alex is not hurt', t.others_unhurt())\n")
        self.box.write("made/main/tests/zap_steve.py", body)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("CHANGED", r.stdout)                 # a first run: nothing to compare with
        self.assertTrue(os.path.isfile(os.path.join(self.box.vhome, "tests", "main", "zap_steve.py")))
        edited = (body.replace("    t.check('Alex is not hurt', t.others_unhurt())\n", "")
                  .replace("t.gone(wall, 0.5)", "t.gone(wall, 0.05)").replace("0, 0, 10, health", "0, 0, 4, health")
                  + "    t.check('it burns', t.burning(husk))\n")
        self.box.write("made/main/tests/zap_steve.py", "# a comment\n" + edited)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertServerLine(lines.pop(5))       # the box's, after what vfg showed before sending it
        self.assertEqual(lines[:5], [
            "CHANGED    since the last run of this test:",
            'CHANGED    check removed: "Alex is not hurt" (t.others_unhurt())',
            'CHANGED    check added: "it burns" (t.burning(husk))',
            'CHANGED    bar lowered: "the wall breaks": t.gone(wall, 0.5) -> t.gone(wall, 0.05)',
            "CHANGED    target moved: husk: t.mob('husk', 0, 0, 10, health=40) -> t.mob('husk', 0, 0, 4, health=40)"])
        r = self.run_test("zap_steve")                       # run again as it is: nothing changed
        self.assertNotIn("CHANGED", r.stdout)
        self.box.write("made/main/tests/zap_steve.py", "# only words\n" + edited)
        self.assertNotIn("CHANGED", self.run_test("zap_steve").stdout)   # a comment is no change

    def test_no_engine_on_the_live_box_says_so(self):
        os.remove(os.path.join(self.box.rvfg, "engine", ".venv", "bin", "python"))
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        self.assertOneLine(self.run_test("zap_steve"), 4, r"^vfg: no game engine on the live box to test with \(.*"
                                                          r"\.venv.*\): deploy\.sh engine installs it; report the "
                                                          r"ability untested$")
        self.assertFalse(os.path.exists(self.on_test_box("tests", "zap_steve.py")))   # nothing ran

    def test_the_live_engine_down_the_test_runs_with_no_binding(self):
        self.engine.stop()
        self.box.write("made/main/tests/zap_steve.py", "def test(t):\n    t.check('a', True)\n")
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vfg: the live engine's bindings were not read (the engine is not running", r.stderr)
        self.assertNotIn("bindings", self.call()["options"])

    def test_an_older_test_server_program_or_vfg_is_refused_in_words(self):
        # test.py with no "engine" (an older vfg's kit test): refused, nothing written there
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        args = dict(self.box.programs()[-1])
        del args["engine"]
        p = subprocess.run([PY, "-u", "-"], input=TestRun.program_text(self, args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=TestRun.box_env(self), timeout=60)
        self.assertEqual(p.returncode, 4, p.stderr)
        self.assertIn(b"a test is the game engine's livetest, and this one came with no engine", p.stderr)


class TestOutOfReach(TestCase):
    def test_a_cast_test_with_the_box_unreachable_reports_an_infra_code_not_a_failing_check(self):
        # the live bindings cannot be read and the test casts: it would find none, so it must not
        # exit 1 (a failing check, which would have the implementer "fix" a working ability)
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        down = write(os.path.join(self.box.root, "down"), "")
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertIn(r.returncode, (4, 5), r.stderr)          # an infra code, never 1
        self.assertRegex(r.stderr.splitlines()[-1], r"report the ability untested")

    def test_the_box_out_of_reach_is_tried_once_more_by_vfg_test_itself(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING.replace('    t.cast("zap now")\n', ""))
        down = write(os.path.join(self.box.root, "down"), "")
        threading.Timer(1.5, os.remove, [down]).start()
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stderr, r"vfg: cannot reach EC2 .*: trying once more")
        self.assertIn("RESULT: 1 passed", r.stdout)
        write(down, "")                               # down for good: exit 4, the implementer reports it
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertRegex(r.stderr.splitlines()[-1], r"^vfg: cannot reach EC2")


class TestNoTestServer(TestCase):
    def test_none_configured_or_the_live_box_itself(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        # its hint names what an implementer may run (the guard refuses vfg action run)
        self.assertOneLine(self.run_test("zap_steve", VFG_TEST_BOX="none"), 3,
                           r"^vfg: no test server: VFG_TEST_BOX is none; check it on the live server with one vfg "
                           r"engine start, grant, give or apply instead$")
        live = write(os.path.join(self.box.root, "live.env"), "VFG_REMOTE_VFG=%s\n" % self.box.rvfg)
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=live), 3, r"names the live server itself")
        # another vfg folder on the same host, but the live Minecraft: refused too
        live = write(os.path.join(self.box.root, "live-mc.env"), "VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                     % (self.tbox.rvfg, self.box.rmc))
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=live), 3, r"names the live server itself \(%s"
                           % self.box.rmc)
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX="/nowhere/at/all"), 3,
                           r"VFG_TEST_BOX's file cannot be read")
        self.assertEqual(self.box.calls, [])

    def test_the_live_minecraft_under_another_path_or_its_rcon_port(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        link = os.path.join(self.box.root, "mc-link")
        os.symlink(self.box.rmc, link)
        other = write(os.path.join(self.box.root, "link.env"), "VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                      % (self.tbox.rvfg, link))
        # the live Minecraft has no marker: no test server there
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=other), 3,
                           r"^vfg: no test server at %s: it has no \.vfg-test-server" % re.escape(link))
        # a marker put there by mistake (and naming no port): still the live one, by its folder
        write(os.path.join(self.box.rmc, ".vfg-test-server"), "{}")
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=other), 3,
                           r"^vfg: this test server is the live one \(the same Minecraft folder as the live server "
                           r"vfg named, %s\)" % re.escape(self.box.rmc))
        os.remove(os.path.join(self.box.rmc, ".vfg-test-server"))
        # the live server's RCON port: refused by its marker, which names another; or by the port itself
        self.tbox.write_props(port=self.box.port)
        self.assertOneLine(self.run_test("zap_steve"), 3, r"^vfg: the marker in %s names RCON port %d, but its "
                           r"server\.properties has %d" % (re.escape(self.tbox.rmc), self.trcon.port, self.box.port))
        write(os.path.join(self.tbox.rmc, ".vfg-test-server"), json.dumps({"rcon_port": self.box.port}))
        self.assertOneLine(self.run_test("zap_steve"), 3,
                           r"^vfg: this test server is the live one \(the same RCON port %d as the live server vfg "
                           r"named\)" % self.box.port)
        self.assertFalse(os.path.exists(self.on_test_box("tests", "zap_steve.py")))   # nothing ran

    def test_the_live_folders_with_the_pcs_check_bypassed_are_refused_on_the_box(self):
        # named as another host, so vfg's own check (same host and folder) lets it through
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        live = write(os.path.join(self.box.root, "live-as-other.env"), "VFG_SSH_HOST=elsewhere\nVFG_REMOTE_VFG=%s\n"
                     "VFG_REMOTE_MC=%s\n" % (self.box.rvfg, self.box.rmc))
        r = self.vfg("test", "zap_steve", VFG_TEST_BOX=live)
        self.assertOneLine(r, 3, r"^vfg: no test server at %s: it has no \.vfg-test-server" % re.escape(self.box.rmc))
        # the test server's marker copied onto the live one: its port is not the live server's
        shutil.copy(os.path.join(self.tbox.rmc, ".vfg-test-server"), self.box.rmc)
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=live), 3, r"names RCON port %d, but its "
                           r"server\.properties has %d" % (self.trcon.port, self.box.port))
        # and with the port right too, the marker's own live folders are the live server's
        write(os.path.join(self.box.rmc, ".vfg-test-server"), json.dumps({
            "rcon_port": self.box.port, "live_mc": self.box.rmc, "live_vfg": self.box.rvfg}))
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=live), 3,
                           r"^vfg: this test server is the live one \(the same vfg folder as the live server its "
                           r"marker names")
        # with no live folder named at all, the live vfg folder still has the box's secrets
        write(os.path.join(self.box.rmc, ".vfg-test-server"), json.dumps({"rcon_port": self.box.port}))
        write(os.path.join(self.box.rvfg, ".env"), "TYPESAFE_API_KEY=\n")
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=live), 3,
                           r"^vfg: %s has a \.env, the box's secrets: a test server's vfg folder never has one"
                           % re.escape(self.box.rvfg))
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "tests")))   # nothing ran on the live box
        self.assertEqual([c["args"].get("op") for c in self.box.calls if "args" in c][-1:], [None])

    def test_bad_arguments(self):
        for args in (["Zap!"], ["zap_steve", "-n", "600"], ["zap_steve", "--timeout", "1"]):
            self.assertEqual(self.run_test(*args).returncode, 2, args)
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        self.assertOneLine(self.run_test("zap_steve", VFG_TEST_SANDBOX="docker"), 3,
                           r"VFG_TEST_SANDBOX is one of auto, system, user, none")


class TestBoxOwn(TestCase):
    """VFG_TEST_BOX unset or box: the live box's own test server, its folders the live ones + -test."""

    def test_unset_is_the_live_boxs_own_test_server(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        vfg, mc = self.box.rvfg + "-test", self.box.rmc + "-test"
        # none made yet: the box says so, before anything is sent there (through ssh, as on the box)
        r = self.box.process("test", "zap_steve")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: no test server on the box: %s/python/bin/python3 is "
                         "missing; deploy.sh testserver makes it" % vfg)
        os.makedirs(os.path.join(vfg, "python", "bin"))
        os.symlink(sys.executable, os.path.join(vfg, "python", "bin", "python3"))
        r = self.box.process("test", "zap_steve")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: no test server at %s: it has no .vfg-test-server, which "
                         "only the test server's setup writes; deploy.sh testserver makes it" % mc)
        self.assertFalse(os.path.exists(os.path.join(vfg, "tests")))     # nothing went there
        self.make_test_server(vfg, mc)
        for name in ("", "box", "BOX"):
            r = self.run_test("zap_steve", VFG_TEST_BOX=name or None)
            self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
            self.assertServerLine([ln for ln in r.stdout.splitlines() if ln.startswith("SERVER")][0], mc=mc)
            self.assertIn("RESULT: 1 passed", r.stdout)
            self.assertEqual(self.call(vfg)["options"]["properties"], os.path.join(mc, "server.properties"))
        self.assertTrue(os.path.isfile(os.path.join(vfg, "tests", "zap_steve.py")))
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "tests")))
        args = self.box.programs()[-1]
        self.assertEqual((args["vfg"], args["mc"], args["live"]), (vfg, mc, {"vfg": self.box.rvfg,
                                                                            "mc": self.box.rmc}))
        # and through ssh, the way the box runs it
        r = self.box.process("test", "zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_file_that_leaves_the_host_out_takes_the_live_ones(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        settings = write(os.path.join(self.box.root, "folders-only.env"), "VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                         % (self.tbox.rvfg, self.tbox.rmc))
        self.box.write_config("VFG_SSH_HOST=box-of-today\nVFG_SSH_KEY=%s\n" % self.box.key)
        r = self.run_test("zap_steve", VFG_TEST_BOX=settings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["dest"] for c in self.box.calls], ["ec2-user@box-of-today"] * len(self.box.calls))
        self.assertIn("live", self.box.programs()[-1])     # the same host: the live folders go along


class TestFreshAndUp(TestCase):
    """Every test on a fresh world of an answering server: restarted when it must be, said in its line."""

    def setUp(self):
        TestCase.setUp(self)
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        self.fresh = os.path.join(self.tbox.rmc, "world", "vfg-fresh")

    def test_a_used_world_is_restarted_first_and_the_token_claimed(self):
        os.remove(self.fresh)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[0], r"a fresh world: restarted it for a fresh world, [\d.]+ s")
        self.assertEqual(self.restarts(), ["restart minecraft-test", "restart --no-block minecraft-test"])

    def test_a_stopped_server_is_restarted_and_the_test_still_runs(self):
        good = os.path.join(self.tbox.root, "server.properties.good")
        shutil.copy(os.path.join(self.tbox.rmc, "server.properties"), good)
        self.tbox.write_props(enable=False)       # RCON refused, as a stopped server's is
        self.on_restart("cp %s %s" % (good, os.path.join(self.tbox.rmc, "server.properties")))
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[0], r"a fresh world: restarted it: it was down, [\d.]+ s")
        self.assertIn("RESULT: 1 passed", r.stdout)

    def test_a_wedged_server_token_present_but_rcon_dead_is_restarted_once_and_recovers(self):
        # a recent restart is noted and the world's token is there, but RCON does not answer (the
        # java hung): ready() waits only a short grace, then restarts once, which brings it back
        good = os.path.join(self.tbox.root, "server.properties.good")
        shutil.copy(os.path.join(self.tbox.rmc, "server.properties"), good)
        write(os.path.join(self.tbox.rvfg, "tests", "restarted"), "%.3f\n" % time.time())
        self.tbox.write_props(enable=False)                    # RCON refused: wedged
        self.on_restart("cp %s %s" % (good, os.path.join(self.tbox.rmc, "server.properties")))
        began = time.time()
        r = self.run_test("zap_steve", VFG_TEST_BOOT_S="5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - began, 20)               # not two full boots
        self.assertServerLine(r.stdout.splitlines()[0], r"a fresh world: restarted it: it was down, [\d.]+ s")
        self.assertEqual(self.restarts(), ["restart minecraft-test", "restart --no-block minecraft-test"])

    def test_a_wedged_server_a_restart_cannot_fix_gives_up_once_with_guidance(self):
        write(os.path.join(self.tbox.rvfg, "tests", "restarted"), "%.3f\n" % time.time())
        self.tbox.write_props(enable=False)
        self.on_restart("true")                                # the restart does not mend RCON
        began = time.time()
        r = self.run_test("zap_steve", VFG_TEST_BOOT_S="5")
        self.assertOneLine(r, 5, r"^vfg: the test server was down and did not answer RCON within 5 s even after a "
                                 r"restart \(.*\); report the ability untested and why; never test on the live "
                                 r"server or its players$")
        self.assertLess(time.time() - began, 40)               # a short grace + one boot, not two
        self.assertEqual(self.restarts(), ["restart minecraft-test"])   # restarted once, then gave up

    def test_down_and_not_coming_back_or_no_unit(self):
        self.tbox.write_props(enable=False)
        r = self.run_test("zap_steve", VFG_TEST_BOOT_S="5")
        self.assertOneLine(r, 5, r"^vfg: the test server was down and did not answer RCON within 5 s even after a "
                                 r"restart \(minecraft is not running \(RCON on 127\.0\.0\.1:\d+ refused\)\); "
                                 r"report the ability untested and why; never test on the live server or its players$")
        os.remove(os.path.join(self.box.units, "minecraft-test"))
        self.assertOneLine(self.run_test("zap_steve"), 5, r"^vfg: the test server is down \(.*\) and has no "
                                                          r"minecraft-test unit to restart it; report the ability "
                                                          r"untested and why; never test on the live server or its "
                                                          r"players$")
        write(os.path.join(self.box.units, "minecraft-test"), "failed\n")   # its last restart: given up
        self.on_restart("exit 1")
        self.assertOneLine(self.run_test("zap_steve"), 5, r"^vfg: the test server was down and its restart failed "
                                                          r"\(sudo -n systemctl restart minecraft-test: .*\); report "
                                                          r"the ability untested")
        self.assertFalse(os.path.exists(self.on_test_box("tests", "zap_steve.py")))

    def test_with_no_unit_a_used_world_is_said_not_fresh(self):
        os.remove(os.path.join(self.box.units, "minecraft-test"))
        os.remove(self.fresh)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[0], "NOT a fresh world: a test used it, and it has no "
                                                        "minecraft-test unit to restart it")
        self.assertEqual(self.restarts(), [])

    def test_the_last_tests_restart_is_waited_for_not_done_again(self):
        os.remove(self.fresh)          # the last test's restart (--no-block) still under way
        write(os.path.join(self.tbox.rvfg, "tests", "restarted"), "%.3f\n" % time.time())
        threading.Timer(1.5, lambda: write(self.fresh, "")).start()
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[0], r"a fresh world: waited for the last test's restart, [\d.]+ s")
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"])   # only its own, after it

    def test_the_tests_own_files_each_time_and_run_emptied_and_state_never_touched(self):
        write(self.on_test_box("tests", "content", "old_ability.py"), "# an earlier test's content\n")
        write(self.on_test_box("run", "old_copy.log"), "a copy of an earlier test\n")
        # what the test user left in state/: test.py neither reads nor writes there (a link stays a link)
        os.makedirs(self.on_test_box("state", "engine"), exist_ok=True)
        os.symlink(os.path.join(self.box.rvfg, ".env"), self.on_test_box("state", "planted"))
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.on_test_box("tests", "content")), ["zap_steve.py"])
        self.assertFalse(os.path.exists(self.on_test_box("run", "old_copy.log")))
        self.assertTrue(os.path.islink(self.on_test_box("state", "planted")))
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, ".env")))
        with open(self.on_test_box("tests", "zap_steve.bindings.json")) as f:
            self.assertEqual(json.load(f), [])                       # this task bound nothing


class TestQueue(TestCase):
    def setUp(self):
        TestCase.setUp(self)
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        os.makedirs(self.on_test_box("tests"), exist_ok=True)
        self.lock = os.open(self.on_test_box("tests", "tests.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, self.lock)

    def test_it_waits_its_turn_and_says_how_long(self):
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        threading.Timer(1.2, lambda: fcntl.flock(self.lock, fcntl.LOCK_UN)).start()
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.splitlines()[0]
        self.assertServerLine(line)
        self.assertGreaterEqual(float(re.search(r"([\d.]+) s in the queue", line).group(1)), 1.0)

    def test_past_its_queue_it_gives_up(self):
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        r = self.run_test("zap_steve", VFG_TEST_QUEUE_S="1")
        self.assertOneLine(r, 5, r"^vfg: the test server was busy with other tests for all of the 1 s its queue waits "
                                 r"\(VFG_TEST_QUEUE_S\)$")

    def budget_programs(self):
        return [p for p in self.box.programs() if "queue" in p]

    def test_the_queue_gets_what_the_wall_leaves_keeps_a_minimum_and_caps_timeout(self):
        # a vfg test must end inside an implementer's 10-minute Bash call; the queue gets what the
        # test and the server's restarts leave of 590 s, down to a floor; too large a --timeout is refused
        r = self.run_test("zap_steve")                           # default: nothing to shrink
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.budget_programs()[-1]["queue"], 300)
        r = self.run_test("zap_steve", "--timeout", "350")       # 590 - (350 + 30 warm + 120 boot + 30) = 60
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.budget_programs()[-1]["queue"], 60)
        before = len(self.budget_programs())
        r = self.run_test("zap_steve", "--timeout", "500")       # no room for even the 30 s floor
        self.assertOneLine(r, 2, r"^vfg: --timeout is at most 380 s with VFG_TEST_BOOT_S=120: a test and the test "
                                 r"server's restarts must finish inside the 590 s an implementer's shell allows")
        self.assertEqual(len(self.budget_programs()), before)    # refused before anything was sent
        # a larger boot lowers the cap
        r = self.run_test("zap_steve", "--timeout", "250", VFG_TEST_BOOT_S="300")
        self.assertOneLine(r, 2, r"^vfg: --timeout is at most 200 s with VFG_TEST_BOOT_S=300")

    def q(self):
        return self.on_test_box("tests", "queue")

    def test_a_dead_waiters_ticket_is_skipped(self):
        os.makedirs(self.q(), exist_ok=True)
        write(os.path.join(self.q(), "1-2147480000"), "")   # an older ticket of a pid that is gone
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)         # reaped, not waited for forever
        self.assertIn("RESULT: 1 passed", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.q(), "1-2147480000")))

    def test_it_waits_behind_an_older_live_ticket_even_when_the_lock_is_free(self):
        os.makedirs(self.q(), exist_ok=True)
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        older = os.path.join(self.q(), "1-%d" % sleeper.pid)   # an older ticket of a live process
        write(older, "")
        p = self.box.popen("test", "zap_steve", VFG_TEST_BOX=self.settings)
        # the lock is free, but it is not the oldest ticket: it must wait
        self.assertIsNone(p.poll())
        time.sleep(2)
        self.assertIsNone(p.poll(), "ran before the older ticket was gone")
        os.remove(older)                                       # now it is the oldest: it runs
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn(b"RESULT: 1 passed", out)

    def test_three_at_once_all_finish_one_after_another(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING + "import time\ntime.sleep(1)\n")
        settings = self.settings
        procs = [self.box.popen("test", "zap_steve", VFG_TEST_BOX=settings) for _ in range(3)]
        outs = [p.communicate(timeout=90) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0], [o[1] for o in outs])
        waits = sorted(float((re.search(r"([\d.]+) s in the queue", o[0].decode()) or re.search(r"()", "")).group(1)
                             or 0) for o in outs)
        self.assertGreaterEqual(waits[2], 1.5, outs)     # the last waited for both before it
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"] * 3)


class OutsideTmp(TestCase):
    """Boxes made outside /tmp, as the box's are, so a sandboxed test gets a /tmp of its own."""

    @classmethod
    def setUpClass(cls):
        super(OutsideTmp, cls).setUpClass()
        import tempfile
        cls.saved_tempdir = tempfile.tempdir
        tempfile.tempdir = os.path.join(os.path.expanduser("~"), ".cache", "vfg-toolkit-tests")
        os.makedirs(tempfile.tempdir, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        import tempfile
        tempfile.tempdir = cls.saved_tempdir
        super(OutsideTmp, cls).tearDownClass()


class TestSystemSandbox(OutsideTmp):
    """The box's system mode (sudo -n systemd-run), with a stand-in systemd-run: the test runs as the
    marker's test_user, never as the box's own user, in a unit that hides the live services from it."""

    def setUp(self):
        TestCase.setUp(self)
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        write(os.path.join(self.box.bin, "systemd-run"), SYSTEMD_RUN)
        os.chmod(os.path.join(self.box.bin, "systemd-run"), 0o755)
        import pwd
        self.other = pwd.getpwuid(65534).pw_name if self.uid_exists(65534) else None
        self.marker = os.path.join(self.tbox.rmc, ".vfg-test-server")

    @staticmethod
    def uid_exists(uid):
        import pwd
        try:
            pwd.getpwuid(uid)
            return True
        except KeyError:
            return False

    def mark(self, **more):
        with open(self.marker) as f:
            m = json.load(f)
        m.update(more)
        write(self.marker, json.dumps(m))

    def calls(self):
        try:
            with open(os.path.join(self.box.units, "systemd-run.calls")) as f:
                return [json.loads(ln) for ln in f]
        except OSError:
            return []

    def test_on_the_box_a_marker_with_no_test_user_is_refused_never_run_as_this_user(self):
        r = self.run_test("zap_steve")
        self.assertOneLine(r, 3, r"^vfg: the test server cannot run a test as another user: its marker names no "
                                 r"test_user; re-run deploy.sh testserver$")
        self.mark(test_user="vfg_no_such_user")
        self.assertOneLine(self.run_test("zap_steve"), 3, r"its test user vfg_no_such_user does not exist")
        import pwd
        self.mark(test_user=pwd.getpwuid(os.getuid()).pw_name)
        self.assertOneLine(self.run_test("zap_steve"), 3, r"is this user, the live services' own")
        self.assertFalse(os.path.exists(self.on_test_box("tests", "zap_steve.py")))   # nothing ran
        self.assertFalse([c for c in self.calls() if "--" in c])

    def test_without_a_system_manager_the_pc_takes_its_user_mode(self):
        write(os.path.join(self.box.units, "systemd-run.mode"), "down")
        r = self.run_test("zap_steve")    # no system manager, and the stand-in has no user one
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT sandboxed (system: no system manager here; user: ", r.stdout)

    def test_it_runs_as_the_test_user_with_the_live_services_hidden(self):
        if not self.other:
            self.skipTest("no user 65534 here")
        self.mark(test_user=self.other)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("; sandboxed: systemd-run as %s\n" % self.other, r.stdout)
        self.assertIn("RESULT: 1 passed", r.stdout)
        probe, ran = [c for c in self.calls() if "--" in c]
        import pwd
        pw = pwd.getpwnam(self.other)
        for argv in (probe, ran):
            self.assertEqual(argv[0], "--uid=%d" % pw.pw_uid)
            self.assertTrue(argv[1].startswith("--gid="), argv)
            props = [argv[i + 1] for i, a in enumerate(argv) if a == "-p"]
            for want in ("ProtectProc=invisible", "PrivateTmp=yes", "IPAddressDeny=any", "IPAddressAllow=localhost",
                         "UMask=0002", "ProtectHome=yes", "NoNewPrivileges=yes", "ProtectSystem=strict",
                         "InaccessiblePaths=" + os.path.realpath(self.box.rmc),
                         "TemporaryFileSystem=%s:ro" % os.path.realpath(self.box.rvfg),
                         "ReadWritePaths=" + os.path.realpath(self.on_test_box("run")),
                         "ReadWritePaths=" + os.path.realpath(self.on_test_box("state"))):
                self.assertIn(want, props, argv)
            self.assertNotIn("ReadWritePaths=" + os.path.realpath(self.tbox.rvfg), props)   # not tests/
        # the test: livetest on the live engine, its three folders bound back read-only, never the
        # engine's own folder (its state, socket and log); the unit's limit past livetest's own
        props = [ran[i + 1] for i, a in enumerate(ran) if a == "-p"]
        engine = os.path.realpath(os.path.join(self.box.rvfg, "engine"))
        binds = [p.split("=", 1)[1] for p in props if p.startswith("BindReadOnlyPaths=")]
        for d in (".venv", "vfg_engine", "library"):
            self.assertIn(os.path.join(engine, d), binds)
        self.assertNotIn(engine, binds)
        self.assertIn("RuntimeMaxSec=75", props)
        cmd = ran[ran.index("--") + 1:]
        self.assertEqual(cmd[4:8], [os.path.join(engine, ".venv", "bin", "python"), "-u", "-m", "vfg_engine.livetest"])
        checks = json.loads(probe[-1])
        self.assertIn(os.path.join(os.path.realpath(self.box.rmc), "server.properties"), checks["read"])
        self.assertTrue(any(w.startswith(os.path.realpath(self.box.rvfg) + "/.vfg-probe") for w in checks["write"]))
        self.assertIn(os.getpid(), checks["pids"])    # vfg test's own: the live services' user
        # the test's environment is the test user's, with no secret and the test server's paths
        with open(self.on_test_box("tests", "sandbox.env")) as f:
            env = f.read()
        self.assertIn("USER='%s'" % self.other, env)
        self.assertIn("VFG_TEST='1'", env)
        self.assertIn("VFG_PROTECTED='%s'" % self.on_test_box("protected.json"), env)
        self.assertNotIn("VFG_ENGINE_", env)

    def test_a_passing_probe_is_cached_and_not_rerun(self):
        if not self.other:
            self.skipTest("no user 65534 here")
        self.mark(test_user=self.other)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue([c for c in self.calls() if any("-probe-" in a for a in c)])   # it probed once
        self.assertTrue(os.path.exists(self.on_test_box("tests", ".sandbox-probe.json")))
        os.remove(os.path.join(self.box.units, "systemd-run.calls"))
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in self.calls() if any("-probe-" in a for a in c)])   # cached: no second probe
        self.assertTrue([c for c in self.calls() if "--" in c])                         # but the test still ran

    def test_the_probe_cache_hits_although_each_run_is_a_new_process(self):
        # through ssh each vfg test's box side is a new process with a new pid: the cache key must
        # not change with it (the live units' MainPIDs only), or every test would probe again
        if not self.other:
            self.skipTest("no user 65534 here")
        self.mark(test_user=self.other)
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue([c for c in self.calls() if any("-probe-" in a for a in c)])
        os.remove(os.path.join(self.box.units, "systemd-run.calls"))
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in self.calls() if any("-probe-" in a for a in c)])   # cached across pids
        self.assertTrue([c for c in self.calls() if "--" in c])

    def test_a_sandbox_that_fails_its_probe_on_the_box_runs_nothing(self):
        if not self.other:
            self.skipTest("no user 65534 here")
        self.mark(test_user=self.other)
        write(os.path.join(self.box.units, "systemd-run.mode"), "probe-fails")
        r = self.run_test("zap_steve")
        self.assertOneLine(r, 5, r"^vfg: the test server's sandbox failed its check \(reachable: /opt/mc/server\."
                                 r"properties\): no test runs outside it$")
        self.assertFalse([c for c in self.calls() if "--" in c and "-probe-" not in "".join(c)])

    def test_the_probe_finds_a_live_file_or_process_in_reach(self):
        # the probe itself, as the sandbox runs it: 0 only when nothing of the live server is in reach
        with open(os.path.join(os.path.dirname(TESTS), "remote", "test.py")) as f:
            src = f.read()
        probe = re.search(r'^PROBE = \((.*?)\)\n', src, re.S | re.M)
        code = eval("(" + probe.group(1) + ")")

        def run_probe(read=(), write=(), pids=()):
            checks = {"read": list(read), "write": list(write), "pids": list(pids)}
            return subprocess.run([sys.executable, "-c", code, json.dumps(checks)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        gone = os.path.join(self.box.root, "nowhere", "server.properties")
        self.assertEqual(run_probe(read=[gone]).returncode, 0)        # unreadable: fine
        r = run_probe(read=[os.path.join(self.box.rmc, "server.properties")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("reachable (read): %s" % os.path.join(self.box.rmc, "server.properties"), r.stdout)
        # a writable live folder is reachable, but the probe leaves nothing behind
        probefile = os.path.join(self.box.rvfg, ".vfg-probe-test")
        r = run_probe(write=[probefile])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("reachable (write): %s" % probefile, r.stdout)
        self.assertFalse(os.path.exists(probefile))                  # created with O_EXCL, then unlinked
        gonew = os.path.join(self.box.root, "nowhere", ".vfg-probe-x")
        self.assertEqual(run_probe(write=[gonew]).returncode, 0)      # a folder it cannot write: fine
        r = run_probe(pids=[os.getpid()])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("process %d of the live services' user is visible" % os.getpid(), r.stdout)


class TestPlainFallback(TestCase):
    """The plain process fallback (VFG_TEST_SANDBOX=none): no cgroup to take what a test started with
    it, so test.py kills this test's descendants by their token afterwards, and says it cannot
    guarantee it."""

    def test_it_kills_a_detached_process_by_its_token_and_says_so(self):
        pidfile = self.on_test_box("tests", "child.pid")
        # a process in a session of its own (setsid): the test's process-group kill never reaches
        # it; it inherits VFG_TEST_RUN, so the reap by token does
        self.box.write("made/main/tests/zap_steve.py", PASSING + "import subprocess\n"
                       "p = subprocess.Popen(['setsid', 'sleep', '300'], stdout=subprocess.DEVNULL,\n"
                       "                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
                       "open(%r, 'w').write(str(p.pid))\n" % pidfile)
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT sandboxed (VFG_TEST_SANDBOX=none): a plain process, its strays killed by name "
                      "afterwards but not guaranteed gone", r.stdout)
        self.assertIn("RESULT: 1 passed", r.stdout)
        with open(pidfile) as f:
            pid = int(f.read())
        self.assertTrue(wait_for(lambda: not alive(pid), 10), "the detached process outlived the test")


@unittest.skipUnless(SANDBOX, "no systemd-run --user on this machine")
class TestSandbox(OutsideTmp):
    """A test in its unit: the live server's files, and the live engine's, out of its reach; the
    live engine's venv, package and library in; and nothing it started left."""

    def test_it_cannot_reach_the_live_server_nor_the_live_engine_and_leaves_nothing_running(self):
        pidfile = self.on_test_box("tests", "child.pid")
        host_tmp = write("/tmp/vfg-sandbox-check-%s.lock" % secrets.token_hex(4), "")   # as a live RCON lock is
        self.addCleanup(os.remove, host_tmp)
        engine = os.path.join(self.box.rvfg, "engine")
        write(os.path.join(engine, "incantations.json"), '{"version": 1, "incantations": []}')   # the live export
        self.box.write("made/main/tests/zap_steve.py", PASSING + """import subprocess
for what, path, mode in (("read", %r, "r"), ("write", %r, "w"), ("read", %r, "r"), ("read", %r, "r"),
                         ("read", %r, "r"), ("write", %r, "w")):
    try:
        open(path, mode).close()
        print("REACHED    %%s %%s" %% (what, path))
    except OSError as e:
        print("REFUSED    %%s %%s: %%s" %% (what, path, type(e).__name__))
p = subprocess.Popen(["setsid", "sleep", "300"])
open(%r, "w").write(str(p.pid))
""" % (os.path.join(self.box.rmc, "server.properties"), os.path.join(self.box.rvfg, "stolen.txt"),
       os.path.join(engine, "incantations.json"), host_tmp, os.path.join(engine, "library", "arena.py"),
       os.path.join(engine, "library", "planted.py"), pidfile))
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="user")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"sandboxed: systemd-run --user, as this user \(weaker")
        refused = [ln for ln in r.stdout.splitlines() if ln.startswith(("REACHED", "REFUSED"))]
        self.assertEqual(refused, [
            "REFUSED    read %s: PermissionError" % os.path.join(self.box.rmc, "server.properties"),
            "REFUSED    write %s: OSError" % os.path.join(self.box.rvfg, "stolen.txt"),
            "REFUSED    read %s: FileNotFoundError" % os.path.join(engine, "incantations.json"),   # the live export
            "REFUSED    read %s: FileNotFoundError" % host_tmp,                                    # a /tmp of its own
            "REACHED    read %s" % os.path.join(engine, "library", "arena.py"),                    # the library, read
            "REFUSED    write %s: OSError" % os.path.join(engine, "library", "planted.py")])       # ...only
        self.assertTrue(os.path.isfile(os.path.join(engine, "incantations.json")))   # it is there, outside
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "stolen.txt")))
        with open(pidfile) as f:
            pid = int(f.read())
        self.assertTrue(wait_for(lambda: not alive(pid), 10), "the test's background process outlived it")

    def test_auto_takes_the_sandbox_and_none_says_it_is_not(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        r = self.run_test("zap_steve")
        self.assertIn("; sandboxed: systemd-run --user, as this user", r.stdout)
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="none")
        self.assertIn("; NOT sandboxed (VFG_TEST_SANDBOX=none): a plain process, its strays killed by name "
                      "afterwards but not guaranteed gone\n", r.stdout)

    def test_past_its_time_it_stops_and_its_unit_takes_everything_in_it(self):
        pidfile = self.on_test_box("tests", "child.pid")
        self.box.write("made/main/tests/zap_steve.py", """import subprocess, time
p = subprocess.Popen(["setsid", "sleep", "300"])
open(%r, "w").write(str(p.pid))
print("started", flush=True)
time.sleep(60)
""" % pidfile)
        began = time.time()
        r = self.run_test("zap_steve", "--timeout", "5", VFG_TEST_SANDBOX="user")
        self.assertOneLine(r, 2, r"^vfg: the test ran past its 5 s and was stopped: shorten it")
        self.assertIn("started\n", r.stdout)
        self.assertLess(time.time() - began, 20)
        with open(pidfile) as f:
            self.assertTrue(wait_for(lambda: not alive(int(f.read())), 10))


class TestExitTable(unittest.TestCase):
    def test_one_table_for_the_mapping_the_help_and_the_box(self):
        out = subprocess.run([PY, os.path.join(os.path.dirname(TESTS), "vfg"), "test", "--help"],
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
        text = " ".join(out.split())
        for code, words in ((0, "every check passed"), (1, "a check failed"), (2, "the test or the content is to fix"),
                            (3, "no test server"), (4, "out of reach"), (5, "the test server down, stalled or busy")):
            self.assertIn("%d: %s" % (code, words), text)
        with open(os.path.join(os.path.dirname(TESTS), "remote", "test.py")) as f:
            box = f.read()
        self.assertEqual(sorted(set(int(c) for c in re.findall(r"\bdie\((\d)", box))), [2, 3, 4, 5])


class TestSkeleton(TestCase):
    def test_a_task_with_no_test_gets_the_skeleton(self):
        r = self.run_test("glow_alex")
        self.assertOneLine(r, 1, r"^vfg: no test for glow_alex yet: wrote the skeleton made/main/tests/glow_alex\.py; "
                                 r"fill in one check per DONE WHEN item, then vfg test glow_alex$")
        with open(os.path.join(self.box.repo, "made", "main", "tests", "glow_alex.py")) as f:
            text = f.read()
        compile(text, "glow_alex.py", "exec")
        for words in ("def test(t):", 't.grant("glow_alex")', "t.mob(", "t.check(", "t.no_errors()",
                      "engine/vfg_engine/livetest.py's docstring has every verb"):
            self.assertIn(words, text)
        self.assertEqual(self.box.calls, [])
        # a test written already is run as it is, never written over
        self.box.write("made/main/tests/glow_alex.py", PASSING)
        self.assertOk(self.run_test("glow_alex"))


if __name__ == "__main__":
    unittest.main()
