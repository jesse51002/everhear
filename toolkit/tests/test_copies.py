"""vfg action run and the copies it starts: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

harness.py's box, with the real scripts/_lib and the stand-in actions of standins.py: command,
edit, stop and save_place, and beam (until stopped, with live values), ping and slow (once).
vfg runs here; each copy is a real process on the real kit, speaking RCON to the class's fake.
"""
import json
import os
import re
import shutil
import signal
import sys
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ISO_RE, BoxCase, alive, vfg_module, write  # noqa: E402
import standins  # noqa: E402

RETIRED = "T%d" % 7   # a task as it was named before named tasks: refused now

CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area
from actions._types import Field, Literal, Live, Values


class Crater(Values):
    crater_radius: Live[int] = Field(8, ge=2, le=64)
    """how wide the crater is, in blocks"""
    crater_leave: Live[Literal["air", "lava", "fire"]] = "air"
    """what is left in it"""


def crater(at, v):
    area.sphere(at, v["crater_radius"], block=v["crater_leave"], spare_protected=True)
'''


class ActionCase(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        standins.install(self.box.repo)
        self.box.predeploy()

    def run_action(self, *args, **env):
        return self.vfg("action", "run", *args, **env)

    def line(self, r, code=0):
        """The one JSON line on stdout, and the exit code."""
        self.assertEqual(r.returncode, code, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)
        got = json.loads(r.stdout)
        self.assertEqual(set(got) - {"error"}, {"ok", "copy", "action", "running", "summary", "facts", "notes"})
        return got

    def run_dir(self, *p):
        return os.path.join(self.box.rvfg, "run", *p)

    def box_json(self, name):
        with open(self.run_dir(name)) as f:
            return json.load(f)

    def pid(self, copy):
        with open(self.run_dir(copy + ".pid")) as f:
            return int(f.read())

    def running(self):
        with open(os.path.join(self.box.vhome, "running.json")) as f:
            return json.load(f)

    def kills(self):
        return [c for c in self.box.rcon_commands() if c.startswith("kill @e[tag=")]


class TestAnswer(unittest.TestCase):
    """The one JSON line vfg action run prints, from the box's answer (answer_of)."""

    def test_what_it_says_ran_runs_or_failed(self):
        mod = vfg_module()

        def answer(**got):
            return mod.answer_of("ping", dict({"copy": "ping", "running": False, "code": 0, "result": None,
                                               "runs": "once"}, **got), ["a note"])
        self.assertEqual(answer(result={"summary": "hi: 1 online", "facts": {"n": 1}}),
                         {"ok": True, "copy": "ping", "action": "ping", "running": False, "summary": "hi: 1 online",
                          "facts": {"n": 1}, "notes": ["a note"]})
        self.assertEqual(answer()["summary"], "ping ran and ended")
        self.assertEqual(answer(running=True, runs="until_stopped")["summary"], "running as ping")
        # A once action past its wait: what it said so far (a countdown started), and that it goes on.
        self.assertEqual(answer(running=True, result={"summary": "hello: started"})["summary"],
                         "hello: started; still running as ping")
        self.assertEqual(answer(running=True)["summary"], "still running as ping")
        # Failed: the error it crashed on, else the result() it refused with, else the last line it printed.
        for got, error in (({"error": "NameError: x", "result": {"summary": "refused"}, "tail": ["last"]},
                            "ping exited with code 1: NameError: x"),
                           ({"result": {"summary": "hello: refused"}, "tail": ["last"]}, "ping exited with code 1: hello: refused"),
                           ({"tail": ["plain: the last words", " "]}, "ping exited with code 1: plain: the last words"),
                           ({}, "ping exited with code 1")):
            line = answer(code=1, **got)
            self.assertEqual((line["ok"], line["error"]), (False, error))


class TestRun(ActionCase):
    def test_a_once_action_answers_with_its_result_and_its_copy_is_named_and_cleaned(self):
        got = self.line(self.run_action("ping", "say=hi", "--task", "hello_steve"))
        self.assertEqual(got, {"ok": True, "copy": "ping", "action": "ping", "running": False,
                               "summary": "hi: 1 online", "facts": {"online": ["Steve"], "task": "hello_steve",
                                                                    "copy": "ping"},
                               "notes": []})
        args = self.box.programs("run")
        self.assertEqual(len(args), 1)                      # the box had it all: one call, stamps only
        self.assertEqual({k: args[0][k] for k in ("action", "script", "source", "runs", "task", "wait", "on_result")},
                         {"action": "ping", "script": "ping", "source": "library", "runs": "once", "task": "hello_steve",
                          "wait": 20, "on_result": False})
        self.assertIsNone(args[0]["base"])
        self.assertNotIn("tars", args[0])
        meta = self.box_json("ping.json")
        self.assertEqual({k: meta[k] for k in ("copy", "values", "live", "runs", "task")},
                         {"copy": "ping", "values": {"say": "hi", "nap": 0, "fail": "", "early": False}, "live": [],
                          "runs": "once", "task": "hello_steve"})
        self.assertRegex(meta["started"], ISO_RE)
        self.assertFalse(os.path.exists(self.run_dir("ping.pid")))
        self.assertNotIn("kill @e[tag=vfg_ping]", self.box.rcon_commands())  # it ended by itself: what it made stays
        self.assertEqual(self.running()["copies"], {})                      # it ended: not running

    def test_an_until_stopped_action_answers_with_its_first_result_and_runs_on(self):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_OWNER=Sunny\n" % self.box.key)
        got = self.line(self.run_action("beam", "player=Steve", "--task", "laser_steve"))
        self.assertEqual((got["copy"], got["running"], got["summary"], got["facts"]),
                         ("beam", True, "Steve has a beam", {"player": "Steve"}))
        args = self.box.programs("run")[-1]
        self.assertEqual((args["wait"], args["on_result"], args["owner"], args["poll"]),
                         (vfg_module().CHECK_S, True, "Sunny", 0.5))
        pid = self.pid("beam")
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        for want in (b"VFG_SCRIPT=beam", b"VFG_TASK=laser_steve", b"VFG_OWNER=Sunny", b"VFG_LIVE_POLL_S=0.5"):
            self.assertIn(want, env)
        self.assertFalse(any(e.startswith(b"VFG_HOME=") for e in env))   # nothing of the PC's
        self.assertEqual(self.running()["copies"]["beam"]["values"]["player"], "Steve")
        r = self.run_action("beam", "as=beam")
        got = self.line(r, 1)
        self.assertRegex(got["error"], r"^beam is running \(pid %d\): stop it first" % pid)
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])

    def test_for_a_player_the_copy_is_action_player_then_2_and_has_no_task(self):
        # The brain's own copy of an action for a player (Prototype_Named_Tasks): <action>_<player>,
        # the name lowercased in full, picked on the box under copies.lock; no --task, no task.
        got = self.line(self.run_action("beam", "player=Steve", "--for", "Steve"))
        self.assertEqual((got["copy"], got["running"]), ("beam_steve", True))
        args = self.box.programs("run")[-1]
        self.assertEqual((args["base"], args["as"], args["task"]), ("beam_steve", None, None))
        meta = self.box_json("beam_steve.json")
        self.assertEqual((meta["copy"], meta["action"], meta["task"]), ("beam_steve", "beam", None))
        with open("/proc/%d/environ" % self.pid("beam_steve"), "rb") as f:
            self.assertIn(b"VFG_TASK=", f.read().split(b"\0"))
        self.assertEqual(self.line(self.run_action("--for=Steve", "beam", "cooldown=2"))["copy"], "beam_steve_2")
        self.assertEqual(self.line(self.run_action("beam", "--for", "Styrkaeopteryx"))["copy"], "beam_styrkaeopteryx")
        self.assertEqual(sorted(self.running()["copies"]), ["beam_steve", "beam_steve_2", "beam_styrkaeopteryx"])
        self.assertIsNone(self.running()["copies"]["beam_steve"]["task"])
        # edit and stop name the copy they act on: --for changes nothing there.
        got = self.line(self.run_action("edit", "copy=beam_steve_2", "cooldown=1", "--for", "Steve"))
        self.assertEqual(got["facts"]["copy"], "beam_steve_2")
        self.assertIsNone(self.box.programs("run")[-1]["base"])
        self.assertEqual(self.line(self.run_action("stop", "copy=beam_steve", "--for", "Steve"))["summary"],
                         "stopped beam_steve")
        self.assertIsNone(self.box.programs("run")[-1]["base"])
        self.assertEqual(self.line(self.run_action("beam", "--for", "Steve"))["copy"], "beam_steve")  # free again

    def test_a_crash_is_not_ok_with_what_it_crashed_on(self):
        # The last line of the error, which mc.py wrote to <copy>.error as it cleaned up; the box
        # sends it, and it wins over a result() from before the crash.
        r = self.run_action("ping", "fail=crash", "early=true")
        got = self.line(r, 1)
        self.assertEqual((got["ok"], got["running"], got["summary"]), (False, False, "hello: started"))
        self.assertEqual(got["error"], "ping exited with code 1: NameError: name 'no_such_name_here' is not defined")
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])

    def test_values_are_checked_and_refused_before_anything_is_sent(self):
        write(os.path.join(self.box.vhome, "places.json"), json.dumps({"fetched_at": time.time(), "places": {}}))
        for args, pattern in ((["nosuch"], r"^no action nosuch in actions/ or made/main/actions/"),
                              (["beam", "nope=1"], r"^beam has no value nope; its values: player, length"),
                              (["beam", "colour=blue"], r"^beam: colour is fixed at red$"),
                              (["beam", "length=far"], r"length"),
                              (["beam", "length=1", "length=2"], r"^length is given twice$"),
                              (["beam", "as=Bad-Name"], r"^as=Bad-Name is not a copy name"),
                              (["beam", "--task", "7"], r"^--task 7 is not a task's name"),
                              (["beam", "--task", RETIRED], r"^--task %s is not a task's name" % RETIRED),
                              (["beam", "--task=cast"], r"^cast is the bridge's task for an incantation's copies"),
                              (["beam", "--for", "not a name"], r"^--for not a name is not a player's name"),
                              (["beam", "--for="], r"^--for  is not a player's name"),
                              (["beam", "--for"], r"^--for takes the player"),
                              (["beam", "as=mine", "--for", "Steve"], r"^as= and --for both name the copy$"),
                              (["beam", "Steve"], r"^'Steve' is not key=value$"),
                              ([], r"^which action\?")):
            r = self.run_action(*args)
            self.assertIn(r.returncode, (1, 2), r.stderr)
            got = json.loads(r.stdout)
            self.assertFalse(got["ok"])
            self.assertRegex(got["error"], pattern)
            self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])
        self.assertEqual(self.box.calls, [])
        got = self.line(self.run_action("beam", "length=1000", "cooldown=-4"))
        self.assertEqual(sorted(got["notes"]), ["cooldown=-4 is under its min, clamped to 0",
                                                "length=1000 is over its max, clamped to 200"])
        self.assertEqual(self.box_json("beam.json")["values"]["length"], 200)

    def test_a_run_sends_only_stamps_while_the_box_has_what_it_would_deploy(self):
        shutil.rmtree(os.path.join(self.box.rvfg, "scripts"))    # a new box
        lib = os.path.join(self.box.rvfg, "scripts", "_lib")
        mine = os.path.join(self.box.repo, "scripts", "_lib", "kit", "extra.py")

        def calls(*args):
            """The SSH calls one run made: 1 when the box had it all, 2 when it asked for the tars."""
            before = len(self.box.calls)
            self.line(self.run_action(*args))
            return len(self.box.calls) - before

        def same_lib():
            with open(mine) as a, open(os.path.join(lib, "kit", "extra.py")) as b:
                return a.read() == b.read()

        self.assertEqual(calls("ping"), 2)                  # no stamps yet: the tars, then the run
        with open(os.path.join(lib, ".vfg-stamp")) as f:
            self.assertRegex(f.read(), r"^[0-9a-f]{64}\n$")
        ino = os.stat(lib).st_ino
        self.assertEqual(calls("ping", "say=again"), 1)     # nothing changed: no tars, no deploy
        self.assertEqual(os.stat(lib).st_ino, ino)
        standins.write(mine, "# v2\n")                      # another worktree's _lib: the last run wins
        self.assertEqual(calls("ping"), 2)
        self.assertTrue(same_lib())
        self.assertNotEqual(os.stat(lib).st_ino, ino)       # a change is deployed whole
        # The script alone changed: it is deployed, the unchanged _lib left as it is.
        ino = os.stat(lib).st_ino
        standins.write(os.path.join(self.box.repo, "scripts", "ping", "main.py"),
                       standins.SCRIPTS["ping"].replace('"%s: %d online"', '"%s: %d are online"'))
        self.assertEqual(calls("ping", "say=hey"), 2)
        self.assertEqual(self.box_json("ping.result.json")["summary"], "hey: 1 are online")
        self.assertEqual(os.stat(lib).st_ino, ino)

    def test_out_of_time_minecraft_down_and_ec2_unreachable(self):
        # An action's own timeout_s is its wait; past the call's own (that + 30 s) it is 124.
        self.box.slow = True
        r = self.run_action("slow")
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertEqual(json.loads(r.stdout)["error"], "timed out: slow did not answer within 31 s; it may still "
                                                        "have happened")
        self.assertEqual(self.box.programs("run")[-1]["wait"], 1)
        self.box.slow = False
        self.box.rcon_down()
        r = self.run_action("ping")
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertRegex(json.loads(r.stdout)["error"], "^minecraft is not running")
        self.assertFalse(os.path.exists(self.run_dir("ping.json")))
        self.box.down = True
        r = self.run_action("ping")
        self.assertEqual(r.returncode, 4)
        self.assertRegex(json.loads(r.stdout)["error"], "^cannot reach EC2")


class TestEditStop(ActionCase):
    def test_an_edit_is_checked_here_then_lands_live_or_restarts_and_a_stop_takes_one(self):
        """vfg's side: an edit checked against the copy's action and values, as the shortcut or as
        the edit action the brain runs, and running.json written through. The box's side (a live
        value read again, a restart keeping the rest, a stop taking one copy's things) is
        scripts/_test/test_instances.py's; the stop shortcut is the next test's."""
        a = self.line(self.run_action("beam", "player=Steve", "cooldown=5", "--task", "laser_steve"))["copy"]
        b = self.line(self.run_action("beam", "player=Alex"))["copy"]
        pa = self.pid(a)
        os.remove(os.path.join(self.box.vhome, "running.json"))   # a copy this PC does not know: the box is asked
        r = self.vfg("script", "set", a, "cooldown=0")
        self.assertEqual((r.returncode, r.stdout), (0, "beam: cooldown=0.0, live\n"), r.stderr)
        self.assertEqual(self.box_json("beam.values.json"), {"cooldown": 0.0})
        self.assertEqual((self.pid(a), self.running()["copies"][a]["values"]["cooldown"]), (pa, 0))
        for args, code, pattern in ((["beam", "nope=1"], 2, r"beam \(beam\): .*nope"),
                                    (["beam", "cooldown"], 2, r"'cooldown' is not key=value"),
                                    (["nosuch", "cooldown=1"], 1, r"nosuch is not running")):
            r = self.vfg("script", "set", *args)
            self.assertEqual(r.returncode, code, r.stderr)
            self.assertRegex(r.stderr.splitlines()[-1], pattern)
        self.assertEqual(self.vfg("script", "set", "beam", "cooldown=0").stdout, "beam: nothing changed\n")
        # The edit action itself, as the brain writes it: its values as one JSON object.
        got = self.line(self.run_action("edit", "copy=beam", 'values={"length": 1000}', "--task", "laser_fix"))
        self.assertEqual(got["notes"], ["length=1000 is over its max, clamped to 200"])
        self.assertRegex(got["summary"], r"^beam restarted with length=200, pid \d+$")
        self.assertEqual((got["facts"]["changed"], got["facts"]["live"]), (["length"], False))
        self.assertNotEqual(self.pid(a), pa)
        meta = self.box_json("beam.json")
        self.assertEqual((meta["values"]["length"], meta["values"]["cooldown"], meta["task"]), (200, 0, "laser_steve"))
        self.assertEqual(self.running()["copies"][a]["values"]["length"], 200)
        # The stop action: that copy and what it made go, here and in running.json; the other stays.
        self.assertEqual(self.line(self.run_action("stop", "copy=beam"))["summary"], "stopped beam")
        self.assertIn("kill @e[tag=vfg_beam]", self.kills())
        self.assertEqual(self.running()["copies"], {})       # beam_2 it never knew, since running.json went
        self.assertTrue(alive(self.pid(b)))                   # and beam_2 runs on

    def test_a_stop_that_failed_leaves_running_json_unless_nothing_runs(self):
        self.line(self.run_action("beam"))
        stop = os.path.join(self.box.repo, "scripts", "stop", "main.py")
        said = ('"""Stop: answers as the stop script does when it did not stop the copy."""\nimport sys\n\n'
                'from kit import result, values\n\nv = values()\n'
                'result(SAID % v["copy"], copy=v["copy"]FACTS)\nsys.exit(1)\n')
        standins.write(stop, said.replace("SAID", '"cannot stop %s: [Errno 1] Operation not permitted"')
                       .replace("FACTS", ""))
        r = self.vfg("script", "stop", "beam")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("Operation not permitted", r.stderr)
        self.assertIn("beam", self.running()["copies"])      # it may run still: kept
        standins.write(stop, said.replace("SAID", '"nothing runs as %s"').replace("FACTS", ", was_running=False"))
        self.assertEqual(self.vfg("script", "stop", "beam").returncode, 1)
        self.assertNotIn("beam", self.running()["copies"])   # the box says nothing runs as beam: gone


MADE_PIT = '''from pydantic import Field

from actions._types import Action, Live, Players, preset


class GlowPit(Action, preset("crater")):
    """A beam made on this server (a test's)."""

    script = "beam"
    runs = "until_stopped"
    takes = ["player"]
    fixed = {"colour": "blue"}
    descriptions = {"crater_radius": "how wide the pit is"}

    player: Players = "@a"
    """who has it"""
    length: int = Field(60, ge=5, le=200)
    """how far it reaches, in blocks"""
    cooldown: Live[float] = Field(10, ge=0, le=60)
    """seconds between shots"""
    colour: Live[str] = "red"
    """its colour"""
    report: bool = True
    """whether it says it is ready"""
'''


class TestScriptSave(ActionCase):
    def made(self, *p):
        return os.path.join(self.box.repo, "made", "main", *p)

    def read(self, *p):
        with open(self.made(*p)) as f:
            return f.read()

    def fill_line(self, *p):
        write(self.made(*p), self.read(*p).replace("TODO: what it does, in players' words: its line on the menu.",
                                                   "Steve's beam."))

    def test_a_copys_values_become_a_variant_of_its_library_action(self):
        self.line(self.run_action("beam", "player=Steve", "cooldown=2"))
        r = self.vfg("script", "save", "beam", "beam_steve")
        self.assertEqual(r.stdout.splitlines()[0], "saved made/main/actions/beam_steve.py from beam (beam), "
                                                   "with player=Steve cooldown=2 as its defaults")
        self.assertIn("its line on the menu is still a TODO", r.stdout)
        self.assertEqual(self.read("actions", "beam_steve.py"),
                         'from actions.beam import Beam\n\n\nclass BeamSteve(Beam):\n'
                         '    """TODO: what it does, in players\' words: its line on the menu."""\n\n'
                         '    defaults = {"player": "Steve", "cooldown": 2.0}\n')
        self.fill_line("actions", "beam_steve.py")
        self.assertEqual(self.vfg("action", "check", "beam_steve").stdout, "ok: made/main/actions/beam_steve.py\n")
        got = self.line(self.run_action("beam_steve"))
        self.assertEqual(self.box_json(got["copy"] + ".json")["values"]["player"], "Steve")
        self.assertOneLine(self.vfg("script", "save", "beam", "beam_steve"), 1,
                           "beam_steve is taken: made/main/actions/beam_steve.py")

    def test_a_made_actions_copy_is_written_out_with_its_values_as_defaults(self):
        standins.write(os.path.join(self.box.repo, "scripts", "_presets", "crater.py"), CRATER)
        standins.write(self.made("actions", "glow_pit.py"), MADE_PIT)
        self.line(self.run_action("glow_pit", "cooldown=5"))
        self.assertOk(self.vfg("script", "set", "glow_pit", "colour=green"))
        r = self.vfg("script", "save", "glow_pit", "deep_pit")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read("actions", "deep_pit.py")
        self.assertIn('class DeepPit(Action, preset("crater")):\n', text)
        self.assertIn('    script = "beam"\n', text)
        self.assertIn('    defaults = {"cooldown": 5.0, "colour": "green"}\n', text)
        self.assertNotIn("fixed", text)                     # the colour it fixed is a default now
        self.assertIn('    descriptions = {"crater_radius": "how wide the pit is"}\n', text)
        self.assertNotIn("crater_radius:", text)            # inherited, never written again
        self.assertIn('    cooldown: Live[float] = Field(10, ge=0, le=60)\n    """seconds between shots"""\n', text)
        self.fill_line("actions", "deep_pit.py")
        self.assertEqual(self.vfg("action", "check", "deep_pit").stdout, "ok: made/main/actions/deep_pit.py\n")


class TestRunning(ActionCase):
    def test_running_json_is_written_through_and_a_fresh_list_or_state_drops_an_ended_copy(self):
        self.rcon.world = {"players": {"Steve": {"pos": [120.7, 64.0, -40.2]}}, "daytime": 18000, "day": 3}
        self.line(self.run_action("beam", "player=Steve", "--task", "laser_steve"))
        self.line(self.run_action("beam", "player=Alex", "cooldown=2"))
        data = self.running()
        self.assertEqual(data["fetched_at"], 0)             # written through, never fetched yet
        beam = data["copies"]["beam"]
        self.assertEqual({k: beam[k] for k in ("action", "script", "task", "runs", "live")},
                         {"action": "beam", "script": "beam", "task": "laser_steve", "runs": "until_stopped",
                          "live": ["cooldown", "colour"]})
        self.assertEqual(beam["values"]["player"], "Steve")
        self.assertRegex(beam["started"], ISO_RE)
        r = self.vfg("script", "list")                      # never fetched: it fetches once
        self.assertEqual(r.stdout, "beam — laser_steve, player=Steve\nbeam_2 — beam, player=Alex cooldown=2\n")
        self.assertEqual(self.vfg("script", "list").stdout, r.stdout)
        self.assertEqual(len(self.box.programs("copies")), 1)   # then from running.json
        # beam_2 ends without vfg: the file still has it; --fresh drops it, and so does vfg state.
        os.killpg(self.pid("beam_2"), signal.SIGKILL)
        self.assertIn("beam_2", self.running()["copies"])
        got = json.loads(self.vfg("script", "list", "--json", "--fresh").stdout)
        self.assertEqual(sorted(got["copies"]), ["beam"])
        self.assertGreater(got["fetched_at"], time.time() - 30)
        os.remove(os.path.join(self.box.vhome, "running.json"))
        r = self.vfg("state")
        self.assertEqual(r.stdout.splitlines()[-1], "running   beam — laser_steve, player=Steve; touches Steve")
        self.assertEqual(sorted(self.running()["copies"]), ["beam"])
        beam = json.loads(self.vfg("state", "--json").stdout)["instances"][0]
        self.assertEqual({k: beam[k] for k in ("instance", "copy", "action", "script", "task", "changed", "touches")},
                         {"instance": "beam", "copy": "beam", "action": "beam", "script": "beam", "task": "laser_steve",
                          "changed": {"player": "Steve"}, "touches": ["Steve"]})
        self.assertIn("scripts     running: beam", self.vfg("up").stdout)

    def test_a_task_shows_on_a_copy_line_only_when_it_is_not_the_copys_own_name(self):
        self.line(self.run_action("beam", "as=laser_steve", "player=Steve", "--task", "laser_steve"))
        self.line(self.run_action("beam", "player=Alex", "--for", "Alex"))
        self.line(self.run_action("beam", "as=laser_steve_2", "player=Sam", "--task", "laser_steve"))
        self.assertEqual(self.vfg("script", "list").stdout.splitlines(),
                         ["beam_alex — beam, player=Alex", "laser_steve — beam, player=Steve",
                          "laser_steve_2 — beam, laser_steve, player=Sam"])
        self.assertEqual([l[10:] for l in self.vfg("state").stdout.splitlines() if l.startswith("running")],
                         ["beam_alex — beam, player=Alex; touches Alex",
                          "laser_steve — beam, player=Steve; touches Steve",
                          "laser_steve_2 — beam, laser_steve, player=Sam; touches Sam"])

    def test_a_fetch_keeps_a_copy_written_through_after_it_was_asked_for(self):
        with self.box.environ() as mod:
            cfg = mod.Config()
            mod.note_copy(cfg, "old", {"action": "beam", "started": "2026-10-09T10:00:00.000Z"})
            mod.note_copy(cfg, "new", {"action": "beam", "started": "2026-10-09T12:00:00.000Z"})
            got = mod.store_fetched(cfg, [{"copy": "box", "action": "ping", "values": {"a": 1}}],
                                    "2026-10-09T11:00:00.000Z")
        self.assertEqual(sorted(got), ["box", "new"])
        self.assertEqual(got["box"]["values"], {"a": 1})


class TestCatalog(ActionCase):
    def test_list_and_check(self):
        r = self.vfg("action", "list")
        self.assertIn("beam — A beam that logs its values every 0.1 s (a test's stand-in).\n", r.stdout)
        got = json.loads(self.vfg("action", "list", "--json").stdout)
        self.assertEqual(got["actions"]["beam"]["script"], "beam")
        self.assertTrue(got["actions"]["beam"]["fields"]["cooldown"]["live"])
        self.assertEqual(self.vfg("action", "check", "beam").stdout, "ok: actions/beam.py\n")
        zap = os.path.join(self.box.repo, "made", "main", "actions", "zap.py")
        standins.write(zap, standins.ACTIONS["ping"].replace("class Ping", "class Zap")
                       .replace('script = "ping"', 'script = "zap"').replace('takes = ["say"]', 'takes = ["sya"]'))
        r = self.vfg("action", "check", "zap")             # left out of the catalog: why, from the loader
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "zap: script zap: there is no made/main/scripts/zap/main.py or "
                                   "scripts/zap/main.py; takes names sya, which is not one of its fields\n")
        r = self.vfg("action", "list")
        self.assertNotIn("zap", r.stdout)
        self.assertIn("vfg: zap did not load, left out: script zap", r.stderr)
        standins.write(os.path.join(self.box.repo, "made", "main", "scripts", "zap", "main.py"), "# zap\n")
        standins.write(zap, standins.ACTIONS["ping"].replace("class Ping", "class Zap").replace('script = "ping"', 'script = "zap"'))
        self.assertEqual(self.vfg("action", "check", "zap").stdout, "ok: made/main/actions/zap.py\n")
        self.assertIn("zap — Ask who is online, once (a test's stand-in). (made)", self.vfg("action", "list").stdout)
        self.assertEqual(self.vfg("action", "check", "nosuch").returncode, 1)


class TestMade(ActionCase):
    def test_every_save_is_a_new_folder_and_a_restore_takes_a_name_or_a_days_latest(self):
        mod = vfg_module()

        class Clock(datetime):     # a second later at every save: no save waits for the next second
            at = [datetime(2026, 10, 10, 14, 23, 1)]

            @classmethod
            def now(cls, tz=None):
                cls.at[0] += timedelta(seconds=1)
                return cls.at[0]
        mod.datetime, real = Clock, mod.datetime
        self.addCleanup(setattr, mod, "datetime", real)
        archive = os.path.join(self.box.root, "archive")
        env = {"VFG_MADE_ARCHIVE": archive}
        self.assertOneLine(self.vfg("made", "save", **env), 1, "nothing to save")
        made = os.path.join(self.box.repo, "made", "main")
        main_py = os.path.join(made, "scripts", "zap", "main.py")
        standins.write(os.path.join(made, "actions", "zap.py"), "# zap\n")
        standins.write(os.path.join(made, "scripts", "zap", "__pycache__", "x.pyc"), "junk")
        standins.write(os.path.join(archive, "2026-10-09", "scripts", "zap", "main.py"), "# zap v0\n")  # an older vfg's
        names = []
        for v in ("v1", "v2"):
            standins.write(main_py, "# zap %s\n" % v)
            r = self.vfg("made", "save", **env)
            m = re.match(r"^saved made/main/ to .*/(2026-10-10-\d{6}): 2 files\n$", r.stdout)
            self.assertTrue(m, r)
            names.append(m.group(1))
        self.assertEqual(names, ["2026-10-10-142302", "2026-10-10-142303"])
        self.assertEqual(sorted(os.listdir(archive)), ["2026-10-09"] + names)   # nothing deleted
        self.assertFalse(os.path.exists(os.path.join(archive, names[0], "scripts", "zap", "__pycache__")))

        def restored():
            with open(main_py) as f:
                return f.read()
        standins.write(main_py, "# zap v3\n")
        r = self.vfg("made", "restore", "2026-10-10", **env)                # a date: its latest
        self.assertRegex(r.stdout, r"^restored made/main/ from .*/%s: 2 files; the one it replaced is in "
                                   r"made/\.replaced/main-\d{8}-\d{6}\n$" % names[1])
        self.assertEqual(restored(), "# zap v2\n")
        self.assertOk(self.vfg("made", "restore", names[0], **env))         # a full name
        self.assertEqual(restored(), "# zap v1\n")
        self.assertOk(self.vfg("made", "restore", "2026-10-09", **env))
        self.assertEqual(restored(), "# zap v0\n")
        r = self.vfg("made", "restore", "1999-01-01", **env)
        self.assertOneLine(r, 1, "no save 1999-01-01 in .*saved: 2026-10-09, %s, %s" % tuple(names))


if __name__ == "__main__":
    unittest.main()
