"""vfg test: a task's test (or a library script's) run on the test server, never the live one;
its skeleton; and the box's side, remote/test.py. No EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Two of harness.py's boxes: the live one, and a second one whose settings VFG_TEST_BOX names (its
own VFG_REMOTE_VFG and VFG_REMOTE_MC), a test server: its marker (.vfg-test-server), its fresh
world's token (world/vfg-fresh) and the unit minecraft-test, whose restart (this file's systemctl,
first on the box's PATH) writes the token again. VFG_TEST_BOX unset is the live box's own test
server, <box>/vfg-test and <box>/mc-test (TestBoxOwn). The test files here stand in for the kit's
test helpers (scripts/_lib/kit/test.py, tested in scripts/_test/test_kit_test.py and live on
realmc): they read what vfg test gave the test server and print a report as the helpers print it.
Each runs as vfg test runs it: in a systemd-run --user sandbox where this PC has one, else as a
plain process; TestSandbox checks the sandbox itself and is skipped where there is none.
"""
import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REAL_LIB, TESTS, Box, BoxCase, alive, wait_for, write  # noqa: E402
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

ZAP = '''"""Zap: a spark at a player."""
ASKS = []
import mc
'''

# What the kit's helpers would print, from what vfg test gave the test server.
REPORTS = '''import json, os, sys
import incantations
e = incantations.Store().lookup("Steve", "zap now")
acts = json.load(open(os.environ["VFG_TEST_ACTIONS"]))
print("12:00:00 test_zap_steve forceloaded 49 chunk(s)")
print("PASS       the incantation is here — %s" % (e and e["action"]))
print("PASS       the actions it names — %s" % ", ".join(sorted(acts)))
print("PASS       only on the test server — VFG_TEST=%s %s" % (os.environ.get("VFG_TEST"),
                                                             os.environ.get("VFG_SCRIPT")))
print("RESULT: 3 passed, 0 failed, 0 can't tell")
'''


class TestCase(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        standins.install_types(self.box.repo)
        shutil.copy(os.path.join(REAL_LIB, "incantations.py"), os.path.join(self.box.repo, "scripts", "_lib"))
        shutil.copytree(os.path.join(REAL_LIB, "kit", "templates"),
                        os.path.join(self.box.repo, "scripts", "_lib", "kit", "templates"))
        self.box.write("actions/beam.py", standins.ACTIONS["beam"])
        self.box.write("scripts/beam/main.py", standins.SCRIPTS["beam"])
        self.box.write("made/main/scripts/zap_steve/main.py", ZAP)
        # the test server: a second box on the same host, its own folders and its own fake Minecraft
        self.trcon = FakeRcon(secrets.token_hex(8)).start()
        self.addCleanup(self.trcon.stop)
        self.tbox = Box(self.trcon)
        self.addCleanup(self.tbox.close)
        self.make_test_server(self.tbox.rvfg, self.tbox.rmc)
        self.settings = write(os.path.join(self.box.root, "test-box.env"),
                              "# the test server\nexport VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC='%s'\nVFG_REPO=/elsewhere\n"
                              % (self.tbox.rvfg, self.tbox.rmc))
        # the live server's incantations: one zap_steve bound, one of another task's
        entries = [{"player": "Steve", "phrase": "zap now", "id": "steve.zap_now", "action": "zap_steve",
                    "script": "zap_steve", "source": "made", "server": "main", "values": {}, "runs": "once",
                    "by": "zap_steve"},
                   {"player": "Alex", "phrase": "big boom", "id": "alex.big_boom", "action": "beam", "script": "beam",
                    "source": "library", "values": {}, "runs": "once", "by": "boom_alex"}]
        write(os.path.join(self.box.rvfg, "incantations.json"), json.dumps({"version": 3, "incantations": entries}))

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

    def assertServerLine(self, line, world="a fresh world", mc=None):
        self.assertRegex(line, SERVER_RE % (re.escape(mc or self.tbox.rmc), world))


class TestRun(TestCase):
    def test_the_tasks_things_go_to_the_test_server_then_its_test_runs_there(self):
        # only what it runs: beam by t.run; stop and edit, named in words, are not its
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "# it may stop or edit a beam\nif False:\n"
                       "    t.run('beam', player='Steve')\n")
        r = self.run_test("zap_steve")
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        lines = r.stdout.splitlines()
        self.assertServerLine(lines.pop(1))
        self.assertEqual(lines, [
            "test server: deployed _lib, beam, zap_steve",
            "PASS       the incantation is here — zap_steve",
            "PASS       the actions it names — beam",
            "PASS       only on the test server — VFG_TEST=1 test_zap_steve",
            "RESULT: 3 passed, 0 failed, 0 can't tell"])   # the kit's own log line left out
        # the live box was read, never written; the test server got the scripts, the binding and the test
        ops = [(c["args"].get("op"), c["args"]["vfg"]) for c in self.box.calls if "args" in c]
        self.assertEqual(ops, [("list", self.box.rvfg), ("stale", self.tbox.rvfg), ("deploy", self.tbox.rvfg),
                               (None, self.tbox.rvfg)])
        self.assertTrue(os.path.isfile(self.on_test_box("made", "main", "scripts", "zap_steve", "main.py")))
        self.assertTrue(os.path.isfile(self.on_test_box("scripts", "beam", "main.py")))
        self.assertTrue(os.path.isfile(self.on_test_box("scripts", "_lib", "incantations.py")))
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "scripts", "beam")))
        with open(self.on_test_box("state", "incantations.json")) as f:
            got = json.load(f)["incantations"]
        self.assertEqual([(e["player"], e["phrase"], e["by"]) for e in got], [("Steve", "zap now", "zap_steve")])
        with open(self.on_test_box("tests", "zap_steve.actions.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["beam"])
        # after it, the test server restarts for the next test, without waiting for it
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"])
        # a second run sends nothing it has, and has the world that restart made
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deployed", r.stdout)
        self.assertServerLine(r.stdout.splitlines()[0])
        self.assertNotIn("deploy", [c["args"].get("op") for c in self.box.calls[-3:] if "args" in c])
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"] * 2)

    def test_a_failed_check_an_unfinished_test_and_one_past_its_time(self):
        self.box.write("made/main/tests/zap_steve.py",
                       'print("FAIL       it zaps — 0 sparks")\nprint("RESULT: 0 passed, 1 failed, 0 can\'t tell")\n'
                       'raise SystemExit(1)\n')
        r = self.run_test("zap_steve")
        self.assertOneLine(r, 1, r"^vfg: the test failed: 0 passed, 1 failed, 0 can't tell$")
        self.assertIn("FAIL       it zaps — 0 sparks\n", r.stdout)
        self.box.write("made/main/tests/zap_steve.py", 'raise RuntimeError("the stage fell")\n')
        self.assertOneLine(self.run_test("zap_steve"), 1,
                           r"^vfg: the test did not finish \(exit 1\): RuntimeError: the stage fell$")
        self.box.write("made/main/tests/zap_steve.py", 'print("RESULT: 0 passed, 0 failed, 0 can\'t tell")\n'
                                                       'raise SystemExit(2)\n')
        self.assertOneLine(self.run_test("zap_steve"), 1, r"^vfg: the test checked nothing")
        self.box.write("made/main/tests/zap_steve.py", "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
        r = self.run_test("zap_steve", "--timeout", "5")
        self.assertOneLine(r, 1, r"^vfg: the test ran past its 5 s and was stopped")
        self.assertIn("started", r.stdout)

    def test_what_a_test_changed_since_its_last_run_is_shown_first(self):
        # the checks are never reached (if False): only what the file says matters here
        body = ("if False:\n    husk = t.mob('husk', 0, 0, 10, health=40)\n    wall = t.blocks('stone', 0, 0, 12)\n"
                "    t.cast('zap now')\n    t.check('it hits', t.hurt(husk, at_least=5))\n"
                "    t.check('the wall breaks', t.gone(wall, 0.5))\n    t.check('Alex is not hurt', t.others_unhurt())\n")
        self.box.write("made/main/tests/zap_steve.py", REPORTS + body)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("CHANGED", r.stdout)                 # a first run: nothing to compare with
        self.assertTrue(os.path.isfile(os.path.join(self.box.vhome, "tests", "main", "zap_steve.py")))
        edited = (body.replace("    t.check('Alex is not hurt', t.others_unhurt())\n", "")
                  .replace("t.gone(wall, 0.5)", "t.gone(wall, 0.05)").replace("0, 0, 10, health", "0, 0, 4, health")
                  + "    t.check('it burns', t.burning(husk))\n")
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "# a comment\n" + edited)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertServerLine(lines.pop(5))       # the box's, after what vfg showed before sending it
        self.assertEqual(lines[:6], [
            "CHANGED    since the last run of this test:",
            'CHANGED    check removed: "Alex is not hurt" (t.others_unhurt())',
            'CHANGED    check added: "it burns" (t.burning(husk))',
            'CHANGED    bar lowered: "the wall breaks": t.gone(wall, 0.5) -> t.gone(wall, 0.05)',
            "CHANGED    target moved: husk: t.mob('husk', 0, 0, 10, health=40) -> t.mob('husk', 0, 0, 4, health=40)",
            "PASS       the incantation is here — zap_steve"])
        r = self.run_test("zap_steve")                       # run again as it is: nothing changed
        self.assertNotIn("CHANGED", r.stdout)
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "# only words\n" + edited)
        self.assertNotIn("CHANGED", self.run_test("zap_steve").stdout)   # a comment is no change

    def test_a_library_scripts_test(self):
        self.box.write("scripts/beam/test.py", 'print("PASS       it beams — ok")\nprint("RESULT: 1 passed, 0 failed, '
                                               '0 can\'t tell")\n')
        r = self.run_test("beam")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS       it beams — ok", r.stdout)
        self.assertTrue(os.path.isfile(self.on_test_box("scripts", "beam", "test.py")))
        self.box.write("scripts/zap/main.py", ZAP)
        self.assertOneLine(self.run_test("zap"), 1, r"^vfg: the library script zap has no test\.py$")


class TestOutOfReach(TestCase):
    def test_a_cast_test_with_the_box_unreachable_reports_an_infra_code_not_a_failing_check(self):
        # the live incantations cannot be read and the test casts: it would find none, so it must not
        # exit 1 (a failing check, which would have the implementer "fix" a working ability)
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "if False:\n    t.cast('zap now')\n")
        # the test casts; force the live-incantations fetch to fail by making the box unreachable
        down = write(os.path.join(self.box.root, "down"), "")
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertIn(r.returncode, (4, 5), r.stderr)          # an infra code, never 1
        self.assertRegex(r.stderr.splitlines()[-1], r"report the ability untested")

    def test_the_box_out_of_reach_is_tried_once_more_by_vfg_test_itself(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        down = write(os.path.join(self.box.root, "down"), "")
        threading.Timer(1.5, os.remove, [down]).start()
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stderr, r"vfg: cannot reach EC2 .*: trying once more")
        self.assertIn("RESULT: 3 passed", r.stdout)
        write(down, "")                               # down for good: exit 4, the implementer reports it
        r = self.box.process("test", "zap_steve", VFG_TEST_BOX=self.settings, FAKE_SSH_DOWN_FILE=down)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertRegex(r.stderr.splitlines()[-1], r"^vfg: cannot reach EC2")


class TestNoTestServer(TestCase):
    def test_none_configured_or_the_live_box_itself(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        self.assertOneLine(self.run_test("zap_steve", VFG_TEST_BOX="none"), 3,
                           r"^vfg: no test server: VFG_TEST_BOX is none; check it on the live server with one vfg "
                           r"action run instead$")
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
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
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
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
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
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        self.assertOneLine(self.run_test("zap_steve", VFG_TEST_SANDBOX="docker"), 3,
                           r"VFG_TEST_SANDBOX is one of auto, system, user, none")


class TestBoxOwn(TestCase):
    """VFG_TEST_BOX unset or box: the live box's own test server, its folders the live ones + -test."""

    def test_unset_is_the_live_boxs_own_test_server(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
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
        self.assertFalse(os.path.exists(os.path.join(vfg, "scripts")))     # not even a deploy went there
        self.make_test_server(vfg, mc)
        for name in ("", "box", "BOX"):
            r = self.run_test("zap_steve", VFG_TEST_BOX=name or None)
            self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
            self.assertServerLine([ln for ln in r.stdout.splitlines() if ln.startswith("SERVER")][0], mc=mc)
            self.assertIn("PASS       only on the test server — VFG_TEST=1 test_zap_steve", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(vfg, "tests", "zap_steve.py")))
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "tests")))
        args = self.box.programs()[-1]
        self.assertEqual((args["vfg"], args["mc"], args["live"]), (vfg, mc, {"vfg": self.box.rvfg,
                                                                            "mc": self.box.rmc}))
        # and through ssh, the way the box runs it
        r = self.box.process("test", "zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_file_that_leaves_the_host_out_takes_the_live_ones(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
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
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        self.fresh = os.path.join(self.tbox.rmc, "world", "vfg-fresh")

    def test_a_used_world_is_restarted_first_and_the_token_claimed(self):
        os.remove(self.fresh)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[1], r"a fresh world: restarted it for a fresh world, [\d.]+ s")
        self.assertEqual(self.restarts(), ["restart minecraft-test", "restart --no-block minecraft-test"])

    def test_a_stopped_server_is_restarted_and_the_test_still_runs(self):
        good = os.path.join(self.tbox.root, "server.properties.good")
        shutil.copy(os.path.join(self.tbox.rmc, "server.properties"), good)
        self.tbox.write_props(enable=False)       # RCON refused, as a stopped server's is
        self.on_restart("cp %s %s" % (good, os.path.join(self.tbox.rmc, "server.properties")))
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[1], r"a fresh world: restarted it: it was down, [\d.]+ s")
        self.assertIn("RESULT: 3 passed", r.stdout)

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
        self.assertServerLine(r.stdout.splitlines()[1], r"a fresh world: restarted it: it was down, [\d.]+ s")
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
        self.assertServerLine(r.stdout.splitlines()[1], "NOT a fresh world: a test used it, and it has no "
                                                        "minecraft-test unit to restart it")
        self.assertEqual(self.restarts(), [])

    def test_the_last_tests_restart_is_waited_for_not_done_again(self):
        os.remove(self.fresh)          # the last test's restart (--no-block) still under way
        write(os.path.join(self.tbox.rvfg, "tests", "restarted"), "%.3f\n" % time.time())
        threading.Timer(1.5, lambda: write(self.fresh, "")).start()
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertServerLine(r.stdout.splitlines()[1], r"a fresh world: waited for the last test's restart, [\d.]+ s")
        self.assertEqual(self.restarts(), ["restart --no-block minecraft-test"])   # only its own, after it

    def test_the_store_holds_only_this_tasks_incantations_and_run_is_emptied(self):
        stale = {"player": "Alex", "phrase": "old trick", "action": "beam", "script": "beam", "source": "library",
                 "values": {}, "runs": "once", "by": "another_task"}
        write(self.on_test_box("state", "incantations.json"), json.dumps({"version": 7, "incantations": [stale]}))
        write(self.on_test_box("run", "old_copy.log"), "a copy of an earlier test\n")
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.on_test_box("state", "incantations.json")) as f:
            got = json.load(f)["incantations"]
        self.assertEqual([(e["player"], e["phrase"]) for e in got], [("Steve", "zap now")])
        self.assertFalse(os.path.exists(self.on_test_box("run", "old_copy.log")))


class TestQueue(TestCase):
    def setUp(self):
        TestCase.setUp(self)
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        os.makedirs(self.on_test_box("tests"), exist_ok=True)
        self.lock = os.open(self.on_test_box("tests", "tests.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, self.lock)

    def test_it_waits_its_turn_and_says_how_long(self):
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        threading.Timer(1.2, lambda: fcntl.flock(self.lock, fcntl.LOCK_UN)).start()
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.splitlines()[1]
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
        self.assertIn("RESULT: 3 passed", r.stdout)
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
        import time as _t
        _t.sleep(2)
        self.assertIsNone(p.poll(), "ran before the older ticket was gone")
        os.remove(older)                                       # now it is the oldest: it runs
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn(b"RESULT: 3 passed", out)

    def test_three_at_once_all_finish_one_after_another(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "import time\ntime.sleep(1)\n")
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
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
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
        self.assertIn("RESULT: 3 passed", r.stdout)
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
            self.assertNotIn("ReadWritePaths=" + os.path.realpath(self.tbox.rvfg), props)   # not tests/ or scripts/
        checks = json.loads(probe[-1])
        self.assertIn(os.path.join(os.path.realpath(self.box.rmc), "server.properties"), checks["read"])
        self.assertTrue(any(w.startswith(os.path.realpath(self.box.rvfg) + "/.vfg-probe") for w in checks["write"]))
        self.assertIn(os.getpid(), checks["pids"])    # vfg test's own: the live services' user
        # the test's environment is the test user's, with no secret and the test server's store
        with open(self.on_test_box("tests", "sandbox.env")) as f:
            env = f.read()
        self.assertIn("USER='%s'" % self.other, env)
        self.assertIn("VFG_INCANTATIONS='%s'" % self.on_test_box("state", "incantations.json"), env)

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
    """The plain process fallback (VFG_TEST_SANDBOX=none): no cgroup to take a test's copies with
    it, so test.py kills this test's descendants by their token afterwards, and says it cannot
    guarantee it."""

    def test_it_kills_a_detached_copy_by_its_token_and_says_so(self):
        pidfile = self.on_test_box("tests", "child.pid")
        # a copy in a session of its own (setsid), as instances starts one: the test's process-group
        # kill never reaches it; it inherits VFG_TEST_RUN, so the reap by token does
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "import subprocess\n"
                       # a real copy writes to its own log, not the test's pipe: DEVNULL here too
                       "p = subprocess.Popen(['setsid', 'sleep', '300'], stdout=subprocess.DEVNULL,\n"
                       "                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
                       "open(%r, 'w').write(str(p.pid))\n" % pidfile)
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOT sandboxed (VFG_TEST_SANDBOX=none): a plain process, its strays killed by name "
                      "afterwards but not guaranteed gone", r.stdout)
        self.assertIn("RESULT: 3 passed", r.stdout)
        with open(pidfile) as f:
            pid = int(f.read())
        self.assertTrue(wait_for(lambda: not alive(pid), 10), "the detached copy outlived the test")


@unittest.skipUnless(SANDBOX, "no systemd-run --user on this machine")
class TestSandbox(OutsideTmp):
    """A test in its unit: the live server's files out of its reach, and nothing it started left."""

    def test_it_cannot_read_the_live_server_nor_write_its_files_and_leaves_nothing_running(self):
        pidfile = self.on_test_box("tests", "child.pid")
        host_tmp = write("/tmp/vfg-sandbox-check-%s.lock" % secrets.token_hex(4), "")   # as a live RCON lock is
        self.addCleanup(os.remove, host_tmp)
        self.box.write("made/main/tests/zap_steve.py", REPORTS + """import subprocess
for what, path, mode in (("read", %r, "r"), ("write", %r, "w"), ("write", %r, "w"), ("read", %r, "r")):
    try:
        open(path, mode).close()
        print("REACHED    %%s %%s" %% (what, path))
    except OSError as e:
        print("REFUSED    %%s %%s: %%s" %% (what, path, type(e).__name__))
p = subprocess.Popen(["setsid", "sleep", "300"])
open(%r, "w").write(str(p.pid))
""" % (os.path.join(self.box.rmc, "server.properties"), os.path.join(self.box.rvfg, "stolen.txt"),
       os.path.join(self.box.rvfg, "scripts", "x.py"), host_tmp, pidfile))
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="user")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"sandboxed: systemd-run --user, as this user \(weaker")
        refused = [ln for ln in r.stdout.splitlines() if ln.startswith(("REACHED", "REFUSED"))]
        self.assertEqual(refused, [
            "REFUSED    read %s: PermissionError" % os.path.join(self.box.rmc, "server.properties"),
            "REFUSED    write %s: OSError" % os.path.join(self.box.rvfg, "stolen.txt"),
            "REFUSED    write %s: FileNotFoundError" % os.path.join(self.box.rvfg, "scripts", "x.py"),
            "REFUSED    read %s: FileNotFoundError" % host_tmp])      # a /tmp of its own
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "stolen.txt")))
        with open(pidfile) as f:
            pid = int(f.read())
        self.assertTrue(wait_for(lambda: not alive(pid), 10), "the test's background process outlived it")

    def test_auto_takes_the_sandbox_and_none_says_it_is_not(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        r = self.run_test("zap_steve")
        self.assertIn("; sandboxed: systemd-run --user, as this user", r.stdout)
        r = self.run_test("zap_steve", VFG_TEST_SANDBOX="none")
        self.assertIn("; NOT sandboxed (VFG_TEST_SANDBOX=none): a plain process, its strays killed by name "
                      "afterwards but not guaranteed gone\n", r.stdout)

    def test_past_its_time_its_unit_is_stopped_with_everything_in_it(self):
        pidfile = self.on_test_box("tests", "child.pid")
        self.box.write("made/main/tests/zap_steve.py", """import signal, subprocess, sys, time
p = subprocess.Popen(["setsid", "sleep", "300"])
open(%r, "w").write(str(p.pid))
signal.signal(signal.SIGTERM, lambda *a: (print("cleaning up", flush=True), sys.exit(0)))
print("started", flush=True)
time.sleep(60)
""" % pidfile)
        began = time.time()
        r = self.run_test("zap_steve", "--timeout", "5", VFG_TEST_SANDBOX="user")
        self.assertOneLine(r, 1, r"^vfg: the test ran past its 5 s and was stopped")
        self.assertIn("started\ncleaning up\n", r.stdout)
        self.assertLess(time.time() - began, 20)
        with open(pidfile) as f:
            self.assertTrue(wait_for(lambda: not alive(int(f.read())), 10))


KINDS = "area-blast|build|cast-launch|cast-projectile|item-use|world-event"
ZAP_ACTION = '''from actions._types import Action, Players


class ZapSteve(Action):
    """Zap: a spark at a player, from his Zapper."""

    script = "zap_steve"
    runs = "until_stopped"
    takes = ["player", "item"]

    player: Players = "@a"
    """who has it"""
    item: str = "Zapper"
    """the item's name"""
'''


class TestSkeleton(TestCase):
    def test_a_task_with_no_test_gets_the_skeleton(self):
        r = self.run_test("glow_alex")
        self.assertOneLine(r, 1, r"^vfg: no test for glow_alex yet: wrote the skeleton made/main/tests/glow_alex\.py; "
                                 r"or the template of its kind: vfg test glow_alex --kind %s; fill in one check per "
                                 r"DONE WHEN item, then vfg test glow_alex$" % KINDS.replace("|", r"\|"))
        with open(os.path.join(self.box.repo, "made", "main", "tests", "glow_alex.py")) as f:
            text = f.read()
        compile(text, "glow_alex.py", "exec")
        for words in ("from kit import test", 't = test.Test("Steve")', "t.cast(", 't.run("glow_alex"', "t.watch(",
                      "t.check(", "t.done()", "# KIND: start from the template of its kind instead: vfg test glow_alex "
                      "--kind %s" % KINDS):
            self.assertIn(words, text)
        self.assertEqual(self.box.calls, [])

    def test_kind_writes_its_template_filled_over_none_or_the_bare_skeleton(self):
        self.run_test("glow_alex")                                       # the bare skeleton
        r = self.run_test("glow_alex", "--kind", "cast-launch")
        self.assertOneLine(r, 1, r"^vfg: wrote the cast-launch template made/main/tests/glow_alex\.py: one t\.check "
                                 r"per DONE WHEN item at its TODO")
        with open(os.path.join(self.box.repo, "made", "main", "tests", "glow_alex.py")) as f:
            text = f.read()
        compile(text, "glow_alex.py", "exec")
        self.assertIn('t = test.Test("Steve")', text)
        self.assertIn('t.cast("glow alex")', text)          # no binding known: its name in words
        # its player and phrase from the incantations it bound, as the cache has them
        self.vfg("incantation", "list")
        r = self.run_test("zap_steve", "--kind", "cast-projectile")
        self.assertEqual(r.returncode, 1, r.stderr)
        with open(os.path.join(self.box.repo, "made", "main", "tests", "zap_steve.py")) as f:
            text = f.read()
        self.assertIn('t.cast("zap now")', text)
        self.assertIn('"Steve, who said it, is not hurt"', text)
        self.assertNotIn("{", text.split('"""')[2].replace("{name}", ""))
        # a test written already is run as it is, never written over
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        r = self.run_test("zap_steve", "--kind", "item-use")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("zap_steve has a test already: --kind writes a template only where there is none", r.stderr)
        self.assertIn("RESULT: 3 passed", r.stdout)
        self.assertEqual(self.run_test("zap_steve", "--kind", "laser").returncode, 2)

    def test_script_new_writes_it_and_never_copies_a_library_test(self):
        self.box.write("scripts/beam/test.py", "# the library's own\n")
        r = self.vfg("script", "new", "beam_alex", "--from", "beam")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("made made/main/tests/beam_alex.py: its test, a skeleton (or the template of its kind: vfg test "
                      "beam_alex --kind %s)" % KINDS, r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "made", "main", "tests", "beam_alex.py")))
        self.assertFalse(os.path.exists(os.path.join(self.box.repo, "made", "main", "scripts", "beam_alex", "test.py")))

    def test_script_new_with_a_kind_and_its_item(self):
        r = self.vfg("script", "new", "beam_bob", "--from", "beam", "--kind", "item-use")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("made made/main/tests/beam_bob.py: its test, the item-use template", r.stdout)
        with open(os.path.join(self.box.repo, "made", "main", "tests", "beam_bob.py")) as f:
            text = f.read()
        compile(text, "beam_bob.py", "exec")
        self.assertIn('t.run("beam_bob", player="Steve")', text)
        self.assertIn('# t.check("Steve has the <its item>", t.holds("<its item>"))  (its action names no item)', text)
        self.assertEqual(self.vfg("script", "new", "beam_eve", "--kind", "laser").returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.box.repo, "made", "main", "scripts", "beam_eve")))
        # an action that names its item: the template checks he holds it
        self.box.write("made/main/actions/zap_steve.py", ZAP_ACTION)
        r = self.run_test("zap_steve", "--kind", "item-use")
        self.assertEqual(r.returncode, 1, r.stderr)
        with open(os.path.join(self.box.repo, "made", "main", "tests", "zap_steve.py")) as f:
            text = f.read()
        self.assertIn('t.check("Steve has the Zapper", t.holds("Zapper"))', text)


if __name__ == "__main__":
    unittest.main()
