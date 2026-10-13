"""vfg test: a task's test (or a library script's) run on the test server, never the live one;
its skeleton; and the box's side, remote/test.py. No EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Two of harness.py's boxes: the live one, and a second one whose settings VFG_TEST_BOX names (its
own VFG_REMOTE_VFG and VFG_REMOTE_MC). The test files here stand in for the kit's test helpers
(scripts/_lib/kit/test.py, tested in scripts/_test/test_kit_test.py and live on realmc): they read
what vfg test gave the test server and print a report as the helpers print it.
"""
import json
import os
import secrets
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REAL_LIB, Box, BoxCase, write  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
import standins  # noqa: E402

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

    def run_test(self, *args, **env):
        return self.vfg("test", *args, VFG_TEST_BOX=self.settings, **env)

    def on_test_box(self, *rel):
        return os.path.join(self.tbox.rvfg, *rel)


class TestRun(TestCase):
    def test_the_tasks_things_go_to_the_test_server_then_its_test_runs_there(self):
        # only what it runs: beam by t.run; stop and edit, named in words, are not its
        self.box.write("made/main/tests/zap_steve.py", REPORTS + "# it may stop or edit a beam\nif False:\n"
                       "    t.run('beam', player='Steve')\n")
        r = self.run_test("zap_steve")
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        self.assertEqual(r.stdout.splitlines(), [
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
        with open(self.on_test_box("incantations.json")) as f:
            got = json.load(f)["incantations"]
        self.assertEqual([(e["player"], e["phrase"], e["by"]) for e in got], [("Steve", "zap now", "zap_steve")])
        with open(self.on_test_box("tests", "zap_steve.actions.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["beam"])
        # a second run sends nothing it has
        r = self.run_test("zap_steve")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deployed", r.stdout)
        self.assertNotIn("deploy", [c["args"].get("op") for c in self.box.calls[-3:] if "args" in c])

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
        self.assertEqual(r.stdout.splitlines()[:6], [
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


class TestNoTestServer(TestCase):
    def test_none_configured_or_the_live_box_itself(self):
        self.box.write("made/main/tests/zap_steve.py", REPORTS)
        self.assertOneLine(self.vfg("test", "zap_steve"), 3, r"^vfg: no test server: VFG_TEST_BOX names none")
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
        self.assertOneLine(self.vfg("test", "zap_steve", VFG_TEST_BOX=other), 3,
                           r"^vfg: this test server is the live one \(the same Minecraft folder\)")
        self.tbox.write_props(port=self.box.port)
        self.assertOneLine(self.run_test("zap_steve"), 3,
                           r"^vfg: this test server is the live one \(the same RCON port %d\)" % self.box.port)
        self.assertFalse(os.path.exists(self.on_test_box("tests", "zap_steve.py")))   # nothing ran

    def test_bad_arguments(self):
        for args in (["Zap!"], ["zap_steve", "-n", "600"], ["zap_steve", "--timeout", "1"]):
            self.assertEqual(self.run_test(*args).returncode, 2, args)


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
