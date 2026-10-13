"""vfg test: a task's test of its engine content, run on the test server's own engine (livetest),
never the live one; its skeleton; and the box's side, remote/engine.py's test op. No EC2, no
network.

    python3 -m unittest discover -s toolkit/tests

Two of harness.py's boxes: the live one, with a fake engine (fake_engine.py) whose bindings the
task made go to the test, and a second one whose settings VFG_TEST_BOX names (its own
VFG_REMOTE_VFG and VFG_REMOTE_MC), with fake_livetest.py as its engine's Python: it notes how it
was run and plays what the test file's FAKE asks (the real runner is tested in engine/tests/box).
"""
import json
import os
import secrets
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import PY, TESTS, Box, BoxCase, write  # noqa: E402
from fake_engine import FakeEngine  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
import standins  # noqa: E402

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


class TestCase(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        standins.install(self.box.repo)
        self.box.write("made/main/content/zap_steve.py", ZAP)
        self.engine = FakeEngine(self.box.rvfg).start()
        self.addCleanup(self.engine.stop)
        # the test server: a second box on the same host, its own folders, its own fake Minecraft, and an
        # engine whose Python is fake_livetest.py
        self.trcon = FakeRcon(secrets.token_hex(8)).start()
        self.addCleanup(self.trcon.stop)
        self.tbox = Box(self.trcon)
        self.addCleanup(self.tbox.close)
        python = os.path.join(self.tbox.rvfg, "engine", ".venv", "bin", "python")
        with open(os.path.join(TESTS, "fake_livetest.py")) as f:
            write(python, "#!%s\n%s" % (PY, f.read()))
        os.chmod(python, os.stat(python).st_mode | stat.S_IXUSR)
        self.settings = write(os.path.join(self.box.root, "test-box.env"),
                              "# the test server\nexport VFG_REMOTE_VFG=%s\nVFG_REMOTE_MC='%s'\nVFG_REPO=/elsewhere\n"
                              % (self.tbox.rvfg, self.tbox.rmc))

    def run_test(self, *args, **env):
        return self.vfg("test", *args, VFG_TEST_BOX=self.settings, **env)

    def on_test_box(self, *rel):
        return os.path.join(self.tbox.rvfg, *rel)

    def call(self):
        with open(self.on_test_box("state", "engine", "livetest-call.json")) as f:
            return json.load(f)


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
        self.assertEqual(r.stdout.splitlines(), ["ENGINE     loaded zap_steve", "PASS       it hits the husk — 6 damage",
                                                 "RESULT: 1 passed, 0 failed, 0 can't tell"])  # the JSON left out
        # the test server got this server's content and the test; livetest ran there, on both content folders
        self.assertTrue(os.path.isfile(self.on_test_box("made", "main", "content", "zap_steve.py")))
        self.assertTrue(os.path.isfile(self.on_test_box("made", "main", "tests", "zap_steve.py")))
        call = self.call()
        self.assertEqual(call["content"], [self.on_test_box("engine", "library"),
                                           self.on_test_box("made", "main", "content")])
        self.assertEqual(call["options"]["properties"], os.path.join(self.tbox.rmc, "server.properties"))
        self.assertEqual(call["options"]["test"], self.on_test_box("made", "main", "tests", "zap_steve.py"))
        self.assertEqual((call["options"]["boot"], call["options"]["timeout"]), ("60.0", "60.0"))
        self.assertEqual((call["pythonpath"], call["cwd"]), (self.on_test_box("engine"), self.on_test_box("engine")))
        # the task's own binding, from the live engine, bound first; another task's is not
        self.assertEqual(call["bindings"], [{"player": "Steve", "phrase": "zap now", "ability": "fireball",
                                             "aliases": ["zapnow"], "everyday_aliases": []}])
        # the live box was read, never written
        self.assertEqual([c["args"].get("op") for c in self.box.calls if "args" in c and
                          c["args"]["vfg"] == self.box.rvfg][-2:], ["ask", "list"])
        self.assertFalse(os.path.exists(os.path.join(self.box.rvfg, "made", "main", "tests")))

    def test_a_failed_check_a_test_that_cannot_run_and_one_past_its_time(self):
        def fake(**f):
            self.box.write("made/main/tests/zap_steve.py", "def test(t):\n    pass\n\n\nFAKE = %r\n" % f)
        fake(code=1, lines=["FAIL       it zaps — 0 sparks"], checks=[{"item": "it zaps", "ok": False, "why": "0"},
                                                                    {"item": "Alex unhurt", "ok": True, "why": "-"}],
             log=["error: zap_steve on_cast: KeyError 'at'"])
        r = self.run_test("zap_steve")
        self.assertOneLine(r, 1, r"^vfg: the test failed: 1 of 2 checks: it zaps$")
        self.assertIn("FAIL       it zaps — 0 sparks\n", r.stdout)
        self.assertIn("LOG        error: zap_steve on_cast: KeyError 'at'\n", r.stdout)   # the engine's log after a fail
        fake(code=2, error="the engine refused start: no scenario horde", checks=[])
        self.assertOneLine(self.run_test("zap_steve"), 1,
                           r"^vfg: the test could not run as written: the engine refused start: no scenario horde$")
        fake(code=4, error="the engine did not load", checks=[],
             errors=[{"file": "zap_steve.py", "check": "imports", "error": "NameError: x"}])
        self.assertOneLine(self.run_test("zap_steve"), 1, r"^vfg: the test's engine did not start or load the content: "
                                                          r"zap_steve\.py: imports: NameError: x$")
        fake(code=3, error="no .vfg-test-server marker beside server.properties", checks=[])
        self.assertOneLine(self.run_test("zap_steve"), 3, r"^vfg: not a test server: no \.vfg-test-server marker")
        fake(code=5, error="stopped", checks=[])
        self.assertOneLine(self.run_test("zap_steve", "--timeout", "5"), 1, r"^vfg: the test ran past its 5 s and was "
                                                                            r"stopped")
        self.assertEqual(self.call()["options"]["timeout"], "5.0")

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
        self.assertEqual(r.stdout.splitlines()[:5], [
            "CHANGED    since the last run of this test:",
            'CHANGED    check removed: "Alex is not hurt" (t.others_unhurt())',
            'CHANGED    check added: "it burns" (t.burning(husk))',
            'CHANGED    bar lowered: "the wall breaks": t.gone(wall, 0.5) -> t.gone(wall, 0.05)',
            "CHANGED    target moved: husk: t.mob('husk', 0, 0, 10, health=40) -> t.mob('husk', 0, 0, 4, health=40)"])
        r = self.run_test("zap_steve")                       # run again as it is: nothing changed
        self.assertNotIn("CHANGED", r.stdout)
        self.box.write("made/main/tests/zap_steve.py", "# only words\n" + edited)
        self.assertNotIn("CHANGED", self.run_test("zap_steve").stdout)   # a comment is no change

    def test_a_test_server_with_no_engine_says_so(self):
        os.remove(self.on_test_box("engine", ".venv", "bin", "python"))
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        self.assertOneLine(self.run_test("zap_steve"), 3, r"^vfg: no engine to test with \(.*\.venv/bin/python")

    def test_the_live_engine_down_the_test_runs_with_no_binding(self):
        self.engine.stop()
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vfg: the live engine's bindings were not read (the engine is not running", r.stderr)
        self.assertNotIn("bindings", self.call()["options"])


class TestNoTestServer(TestCase):
    def test_none_configured_or_the_live_box_itself(self):
        self.box.write("made/main/tests/zap_steve.py", PASSING)
        # its hint names what an implementer may run (the guard refuses vfg action run)
        self.assertOneLine(self.vfg("test", "zap_steve"), 3, r"^vfg: no test server: VFG_TEST_BOX names none.*"
                                                              r"one vfg engine start, grant, give or apply instead$")
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
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=other), 3,
                           r"^vfg: this test server is the live one \(the same Minecraft folder\)")
        self.tbox.write_props(port=self.box.port)
        self.assertOneLine(self.run_test("zap_steve"), 3,
                           r"^vfg: this test server is the live one \(the same RCON port %d\)" % self.box.port)
        self.assertFalse(os.path.exists(self.on_test_box("made", "main", "tests", "zap_steve.py")))   # nothing ran

    def test_bad_arguments(self):
        for args in (["Zap!"], ["zap_steve", "-n", "600"], ["zap_steve", "--timeout", "1"]):
            self.assertEqual(self.run_test(*args).returncode, 2, args)


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
