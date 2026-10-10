"""vfg action run and the copies it starts: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Each test gets test_vfg's fake box (fake_ssh runs the remote side here, the box's Python being
the one running the tests) with the real scripts/_lib and the stand-in actions of standins.py:
command, edit, stop and save_place as the dispatcher's will be, and beam (until stopped, with
live values), ping and slow (once). vfg runs under uv, with pydantic, as it does on the PC.
"""
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)

import standins  # noqa: E402
from test_vfg import Box, ISO_RE, alive, scripts_test, vfg_module, wait_for, HAVE_WORLD  # noqa: E402


class ActionCase(unittest.TestCase):
    def setUp(self):
        self.box = Box()
        standins.install(self.box.repo)

    def tearDown(self):
        self.box.close()

    def assertOk(self, r):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))

    def run_action(self, *args, **env):
        return self.box.vfg("action", "run", *args, **env)

    def line(self, r, code=0):
        """The one JSON line on stdout, and the exit code."""
        self.assertEqual(r.returncode, code, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertTrue(r.stdout.endswith("\n"), r.stdout)
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

    def log_values(self, copy):
        """The values the beam printed last, from its log."""
        try:
            with open(self.run_dir(copy + ".log")) as f:
                rows = [json.loads(l) for l in f.read().splitlines() if l.startswith("{")]
        except OSError:
            return None
        return rows[-1] if rows else None

    def running(self):
        with open(os.path.join(self.box.vhome, "running.json")) as f:
            return json.load(f)

    def kills(self):
        return [c for c in self.box.rcon_commands() if c.startswith("kill @e[tag=")]

    def remote_calls(self, before=0):
        return [c for c in self.box.ssh_calls()[before:] if c[-1].endswith("python3 -u -")]


class TestRun(ActionCase):
    def test_a_once_action_answers_with_its_result_and_its_copy_is_named_and_cleaned(self):
        r = self.run_action("ping", "say=hi", "--task", "T3")
        got = self.line(r)
        self.assertEqual(got, {"ok": True, "copy": "ping", "action": "ping", "running": False,
                               "summary": "hi: 1 online", "facts": {"online": ["Steve"], "task": "T3", "copy": "ping"},
                               "notes": []})
        meta = self.box_json("ping.json")
        self.assertEqual({k: meta[k] for k in ("copy", "action", "script", "source", "values", "live", "runs", "task")},
                         {"copy": "ping", "action": "ping", "script": "ping", "source": "library",
                          "values": {"say": "hi", "nap": 0, "fail": False}, "live": [], "runs": "once", "task": "T3"})
        self.assertRegex(meta["started"], ISO_RE)
        self.assertEqual(self.box_json("ping.result.json")["summary"], "hi: 1 online")
        self.assertFalse(os.path.exists(self.run_dir("ping.pid")))
        self.assertIn("kill @e[tag=vfg_ping]", self.box.rcon_commands())   # it cleaned up as it ended
        self.assertEqual(self.running()["copies"], {})                      # it ended: not running
        # The deployed kit and the script, in one SSH call with the run.
        self.assertTrue(os.path.isfile(os.path.join(self.box.rvfg, "scripts", "_lib", "instances.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.box.rvfg, "scripts", "ping", "main.py")))

    def test_the_copys_process_gets_its_name_task_run_dir_and_the_box_python(self):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_OWNER=Sunny\n" % self.box.key)
        got = self.line(self.run_action("beam", "player=Steve", "--task", "T9"))
        pid = self.pid(got["copy"])
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        for want in (b"VFG_SCRIPT=beam", b"VFG_INSTANCE=beam", b"VFG_TASK=T9", b"VFG_OWNER=Sunny",
                     b"VFG_LIVE_POLL_S=0.5", b"PYTHONUNBUFFERED=1",
                     b"VFG_RUN_DIR=" + self.run_dir().encode(),
                     b"PYTHONPATH=" + os.path.join(self.box.rvfg, "scripts", "_lib").encode(),
                     b"VFG_MC_PROPERTIES=" + os.path.join(self.box.rmc, "server.properties").encode()):
            self.assertIn(want, env)
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = [x for x in f.read().split(b"\0") if x]
        self.assertEqual(argv[-2:], [b"-u", os.path.join(self.box.rvfg, "scripts", "beam", "main.py").encode()])
        self.assertEqual(os.getpgid(pid), pid)   # setsid kept the pid: a stop takes the group
        self.assertTrue(all(c[-1] == self.box.rvfg + "/python/bin/python3 -u -" for c in self.remote_calls()))

    def test_copies_are_named_after_the_action_then_2_3_or_as_given(self):
        names = [self.line(self.run_action("beam", "player=%s" % p))["copy"] for p in ("Steve", "Alex", "Sam")]
        self.assertEqual(names, ["beam", "beam_2", "beam_3"])
        pids = [self.pid(n) for n in names]
        self.assertEqual(len(set(pids)), 3)
        self.assertTrue(all(alive(p) for p in pids))
        got = self.line(self.run_action("beam", "as=steve_gun", "player=Steve"))
        self.assertEqual(got["copy"], "steve_gun")
        r = self.run_action("beam", "as=steve_gun")
        got = self.line(r, 1)
        self.assertRegex(got["error"], r"^steve_gun is running \(pid \d+\): stop it first")
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])
        # A free name is taken again: the first gap.
        self.assertOk(self.box.vfg("script", "stop", "beam_2"))
        self.assertEqual(self.line(self.run_action("beam"))["copy"], "beam_2")
        self.assertEqual(sorted(self.running()["copies"]), ["beam", "beam_2", "beam_3", "steve_gun"])

    def test_two_runs_at_once_never_take_one_name(self):
        procs = [self.box.popen("action", "run", "beam", "report=false") for _ in range(4)]
        lines = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
        self.assertEqual(sorted(l["copy"] for l in lines), ["beam", "beam_2", "beam_3", "beam_4"])

    def test_an_until_stopped_action_answers_with_its_first_result_or_after_the_check(self):
        t = time.time()
        got = self.line(self.run_action("beam", "report=no"))
        quiet = time.time() - t
        self.assertGreaterEqual(quiet, 2.0)
        self.assertEqual((got["copy"], got["running"], got["summary"], got["facts"]),
                         ("beam", True, "running as beam", {}))
        t = time.time()
        got = self.line(self.run_action("beam", "player=Steve"))
        self.assertLess(time.time() - t, quiet - 1.0)   # its result came before the 2 s check
        self.assertEqual((got["copy"], got["running"], got["summary"], got["facts"]),
                         ("beam_2", True, "Steve has a beam", {"player": "Steve"}))

    def test_a_once_action_past_its_timeout_still_runs_and_is_never_run_again(self):
        got = self.line(self.run_action("slow", "say=late"))   # timeout_s 1, a 3 s nap
        self.assertEqual((got["ok"], got["running"], got["summary"]), (True, True, "still running as slow"))
        self.assertIn("slow", self.running()["copies"])
        self.assertTrue(wait_for(lambda: os.path.exists(self.run_dir("slow.result.json")), 10))
        self.assertEqual(self.box_json("slow.result.json")["summary"], "late: 1 online")
        with open(self.run_dir("slow.log")) as f:
            self.assertEqual(f.read().count("--- "), 1)       # one run
        # The dispatcher's default for the rest.
        got = self.line(self.run_action("ping", "nap=3", VFG_DISPATCH_ACTION_TIMEOUT_S="1"))
        self.assertEqual(got["summary"], "still running as ping")

    def test_a_failed_script_is_not_ok_with_its_last_words(self):
        r = self.run_action("ping", "fail=true")
        got = self.line(r, 1)
        self.assertEqual((got["ok"], got["running"], got["copy"]), (False, False, "ping"))
        self.assertRegex(got["error"], r"^ping exited with code 3: \d\d:\d\d:\d\d ping about to fail$")
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])

    def test_values_are_checked_and_refused_before_anything_is_sent(self):
        with open(os.path.join(self.box.vhome, "places.json"), "w") as f:
            json.dump({"fetched_at": time.time(), "places": {}}, f)   # fresh: nothing to fetch
        before = len(self.box.ssh_calls())
        for args, pattern in ((["nosuch"], r"^no action nosuch in actions/ or made/main/actions/"),
                              (["beam", "nope=1"], r"^beam has no value nope: its values are player, length"),
                              (["beam", "colour=blue"], r"^beam fixes colour: "),
                              (["beam", "length=far"], r"length"),
                              (["beam", "length=1", "length=2"], r"^length is given twice$"),
                              (["beam", "as=Bad-Name"], r"^as=Bad-Name is not a copy name"),
                              (["beam", "--task", "7"], r"^--task is T<n>"),
                              (["beam", "Steve"], r"^'Steve' is not key=value$"),
                              ([], r"^which action\?")):
            r = self.run_action(*args)
            self.assertIn(r.returncode, (1, 2), r.stderr)
            got = json.loads(r.stdout)
            self.assertFalse(got["ok"])
            self.assertRegex(got["error"], pattern)
            self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])
        self.assertEqual(self.remote_calls(before), [])
        got = self.line(self.run_action("beam", "length=1000", "cooldown=-4", "report=false"))
        self.assertEqual(sorted(got["notes"]), ["cooldown=-4 is under its min, clamped to 0",
                                                "length=1000 is over its max, clamped to 200"])
        self.assertEqual(self.box_json("beam.json")["values"]["length"], 200)

    def test_minecraft_down_and_ec2_unreachable(self):
        self.box.stop_rcon()
        r = self.run_action("ping")
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertRegex(json.loads(r.stdout)["error"], "^minecraft is not running")
        self.assertFalse(os.path.exists(self.run_dir("ping.json")))
        r = self.run_action("ping", FAKE_SSH_DOWN="1")
        self.assertEqual(r.returncode, 4)
        self.assertRegex(json.loads(r.stdout)["error"], "^cannot reach EC2")


class TestEditStop(ActionCase):
    def test_an_edit_lands_live_or_restarts_with_the_rest_kept_and_a_stop_takes_only_one(self):
        a = self.line(self.run_action("beam", "player=Steve", "cooldown=5", "--task", "T5"))["copy"]
        b = self.line(self.run_action("beam", "player=Alex"))["copy"]
        pa, pb = self.pid(a), self.pid(b)
        self.assertTrue(wait_for(lambda: (self.log_values(a) or {}).get("cooldown") == 5, 5))
        # Live: the file, not the process.
        r = self.box.vfg("script", "set", a, "cooldown=0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "beam: cooldown=0.0, live\n")
        self.assertEqual(self.box_json("beam.values.json"), {"cooldown": 0.0})
        self.assertFalse(os.path.exists(self.run_dir("beam_2.values.json")))
        t = time.time()
        self.assertTrue(wait_for(lambda: (self.log_values(a) or {}).get("cooldown") == 0, 3))
        self.assertLess(time.time() - t, 1.0)           # within VFG_LIVE_POLL_S, and the beam's 0.1 s
        self.assertEqual(self.pid(a), pa)
        self.assertEqual(self.running()["copies"][a]["values"]["cooldown"], 0)
        # Not live: a restart under the same name, every other value kept (the live edit too).
        r = self.box.vfg("script", "set", a, "length=1000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "length=1000 is over its max, clamped to 200")
        self.assertRegex(r.stdout.splitlines()[1], r"^beam restarted with length=200, pid \d+$")
        pa2 = self.pid(a)
        self.assertNotEqual(pa2, pa)
        self.assertTrue(wait_for(lambda: not alive(pa), 5))
        self.assertTrue(wait_for(lambda: self.log_values(a) == {"player": "Steve", "length": 200, "cooldown": 0,
                                                                 "colour": "red", "report": True}, 5))
        meta = self.box_json("beam.json")
        self.assertEqual((meta["values"]["length"], meta["values"]["cooldown"], meta["task"]), (200, 0, "T5"))
        self.assertFalse(os.path.exists(self.run_dir("beam.values.json")))  # folded into the meta
        self.assertEqual(self.running()["copies"][a]["values"]["length"], 200)
        # A stop takes one copy and what it made, never the other.
        r = self.box.vfg("script", "stop", a)
        self.assertEqual((r.returncode, r.stdout), (0, "stopped beam\n"), r.stderr)
        self.assertTrue(wait_for(lambda: not alive(pa2), 5))
        self.assertTrue(alive(pb))
        self.assertIn("kill @e[tag=vfg_beam]", self.kills())
        self.assertNotIn("kill @e[tag=vfg_beam_2]", self.kills())
        self.assertEqual(sorted(self.running()["copies"]), [b])
        r = self.box.vfg("script", "stop", a)
        self.assertEqual(r.stdout, "beam was not running\n")

    def test_edit_refusals(self):
        self.line(self.run_action("beam"))
        for args, code, pattern in ((["beam", "nope=1"], 2, r"beam \(beam\): .*nope"),
                                    (["beam", "cooldown"], 2, r"'cooldown' is not key=value"),
                                    (["nosuch", "cooldown=1"], 1, r"nosuch is not running")):
            r = self.box.vfg("script", "set", *args)
            self.assertEqual(r.returncode, code, r.stderr)
            self.assertRegex(r.stderr.splitlines()[-1], pattern)
        r = self.box.vfg("script", "set", "beam", "cooldown=10")
        self.assertEqual(r.stdout, "beam: nothing changed\n")   # its value already: nothing is run
        # The edit action itself, as the dispatcher runs it.
        got = self.line(self.run_action("edit", "copy=beam", "cooldown=3", "--task", "T8"))
        self.assertEqual((got["copy"], got["facts"]["changed"], got["facts"]["live"]), ("edit", ["cooldown"], True))
        got = self.line(self.run_action("edit", "copy=beam", 'values={"colour": "blue"}'))
        self.assertEqual(self.box_json("beam.values.json"), {"cooldown": 3.0, "colour": "blue"})

    def test_an_edit_of_a_copy_this_pc_does_not_know_asks_the_box(self):
        self.line(self.run_action("beam"))
        os.remove(os.path.join(self.box.vhome, "running.json"))
        r = self.box.vfg("script", "set", "beam", "cooldown=1")
        self.assertEqual(r.stdout, "beam: cooldown=1.0, live\n", r.stderr)


class TestRunning(ActionCase):
    def test_running_json_is_written_through_and_a_fresh_list_drops_an_ended_copy(self):
        self.line(self.run_action("beam", "player=Steve", "--task", "T4"))
        self.line(self.run_action("beam", "player=Alex", "cooldown=2"))
        data = self.running()
        self.assertEqual(data["fetched_at"], 0)          # written through, never fetched yet
        beam = data["copies"]["beam"]
        self.assertEqual({k: beam[k] for k in ("action", "script", "task", "runs", "live")},
                         {"action": "beam", "script": "beam", "task": "T4", "runs": "until_stopped",
                          "live": ["cooldown", "colour"]})
        self.assertEqual(beam["values"]["player"], "Steve")
        self.assertRegex(beam["started"], ISO_RE)
        before = len(self.box.ssh_calls())
        r = self.box.vfg("script", "list")               # never fetched: it fetches once
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "beam — T4, player=Steve\nbeam_2 — beam, player=Alex cooldown=2\n")
        self.assertEqual(len(self.remote_calls(before)), 1)
        before = len(self.box.ssh_calls())
        self.assertEqual(self.box.vfg("script", "list").stdout, r.stdout)
        self.assertEqual(self.remote_calls(before), [])  # from running.json
        # beam_2 ends without vfg: the file still has it; --fresh drops it.
        os.killpg(self.pid("beam_2"), signal.SIGKILL)
        self.assertIn("beam_2", self.running()["copies"])
        got = json.loads(self.box.vfg("script", "list", "--json", "--fresh").stdout)
        self.assertEqual(sorted(got["copies"]), ["beam"])
        self.assertGreater(got["fetched_at"], time.time() - 30)
        self.assertEqual(sorted(self.running()["copies"]), ["beam"])

    def test_a_fetch_keeps_a_copy_written_through_after_it_was_asked_for(self):
        mod = vfg_module("vfgmod_running")
        os.environ["VFG_HOME"] = self.box.vhome
        try:
            cfg = mod.Config()
        finally:
            os.environ.pop("VFG_HOME", None)
        mod.note_copy(cfg, "old", {"action": "beam", "started": "2026-10-09T10:00:00.000Z"})
        mod.note_copy(cfg, "new", {"action": "beam", "started": "2026-10-09T12:00:00.000Z"})
        got = mod.store_fetched(cfg, [{"copy": "box", "action": "ping", "values": {"a": 1}}], "2026-10-09T11:00:00.000Z")
        self.assertEqual(sorted(got), ["box", "new"])
        self.assertEqual(got["box"]["values"], {"a": 1})

    def test_state_lists_the_copies_and_writes_running_json(self):
        self.box.write_world({"players": {"Steve": {"pos": [120.7, 64.0, -40.2]}}, "daytime": 18000, "day": 3,
                              "raining": False, "thundering": False})
        self.line(self.run_action("beam", "player=Steve", "cooldown=1", "--task", "T5"))
        os.remove(os.path.join(self.box.vhome, "running.json"))
        r = self.box.vfg("state")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["online    Steve 120 64 -41 overworld", "time      night (18000), day 3",
                                                 "weather   clear", "running   beam — T5, player=Steve cooldown=1; "
                                                                     "touches Steve"])
        self.assertEqual(sorted(self.running()["copies"]), ["beam"])
        got = json.loads(self.box.vfg("state", "--json").stdout)
        beam = got["instances"][0]
        self.assertEqual({k: beam[k] for k in ("instance", "copy", "action", "script", "task", "changed", "touches")},
                         {"instance": "beam", "copy": "beam", "action": "beam", "script": "beam", "task": "T5",
                          "changed": {"player": "Steve", "cooldown": 1}, "touches": ["Steve"]})


class TestShortcuts(ActionCase):
    def test_cmd_is_the_command_action(self):
        r = self.box.vfg("cmd", "/time", "set", "day")
        self.assertEqual((r.returncode, r.stdout), (0, "Set the time to 1000\n"), r.stderr)
        self.assertIn("time set day", self.box.rcon_commands())
        self.assertEqual(self.box_json("command.json")["values"], {"commands": ["time set day"]})
        r = self.box.vfg("cmd", "bogus")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown or incomplete command", r.stdout)
        self.assertRegex(r.stderr.splitlines()[-1], r"^vfg: command exited with code 1")
        r = self.box.vfg("cmd", "tp", "@a", self.box.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("your shell expanded a ~", r.stderr)

    def test_list_and_check(self):
        r = self.box.vfg("action", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("beam — A beam that logs its values every 0.1 s (a test's stand-in).\n", r.stdout)
        self.assertIn("slow — ", r.stdout)
        got = json.loads(self.box.vfg("action", "list", "--json").stdout)
        self.assertEqual(got["actions"]["beam"]["script"], "beam")
        self.assertTrue(got["actions"]["beam"]["fields"]["cooldown"]["live"])
        r = self.box.vfg("action", "check", "beam")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: actions/beam.py\n"), r.stderr)
        standins.write(os.path.join(self.box.repo, "made", "main", "actions", "zap.py"),
                       standins.ACTIONS["ping"].replace("class Ping", "class Zap")
                       .replace('script = "ping"', 'script = "zap"').replace('takes = ["say"]', 'takes = ["sya"]'))
        r = self.box.vfg("action", "check", "zap")
        self.assertEqual(r.returncode, 1)
        self.assertIn("made/main/actions/zap.py: takes sya, which it has no field for\n", r.stdout)
        self.assertIn("made/main/actions/zap.py: its script zap is in neither scripts/ nor made/main/scripts/\n", r.stdout)
        self.assertIn("zap — Ask who is online, once (a test's stand-in). (made)", self.box.vfg("action", "list").stdout)
        self.assertEqual(self.box.vfg("action", "check", "nosuch").returncode, 1)

    @unittest.skipUnless(HAVE_WORLD, "no scripts/_test/fake_places_world.py beside the toolkit")
    def test_place_save_is_the_save_place_action_and_writes_through(self):
        self.box.stop_rcon()
        world = scripts_test("fake_places_world").FakeWorld(players={"Steve": (197.5, 68.0, 251.5)})
        server = scripts_test("fake_rcon").FakeRcon(self.box.password, port=self.box.port,
                                                    responder=world.respond).start()
        try:
            self.assertEqual(self.box.vfg("place", "list").stdout, "none\n")
            r = self.box.vfg("place", "save", "castle", "--player", "Steve", "--by", "T23")
            self.assertEqual((r.returncode, r.stdout), (0, "saved castle at 197 68 251 in overworld\n"), r.stderr)
            self.assertEqual(world.storage["castle"]["pos"], [197, 68, 251])
            self.assertEqual(world.storage["castle"]["by"], "T23")
            r = self.box.vfg("place", "save", "nest", "--at", "1", "2", "3", "--dim", "the_end")
            self.assertEqual(r.stdout, "saved nest at 1 2 3 in the_end\n", r.stderr)
            with open(os.path.join(self.box.vhome, "places.json")) as f:
                cached = json.load(f)["places"]
            self.assertEqual(cached["castle"], {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"})
            self.assertEqual(cached["nest"]["dim"], "minecraft:the_end")
        finally:
            server.stop()


class TestMade(ActionCase):
    def test_save_then_restore_a_day(self):
        archive = tempfile.mkdtemp(dir=self.box.root)
        env = {"VFG_MADE_ARCHIVE": archive}
        r = self.box.vfg("made", "save", **env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("nothing to save", r.stderr)
        made = os.path.join(self.box.repo, "made", "main")
        standins.write(os.path.join(made, "actions", "zap.py"), "# zap\n")
        standins.write(os.path.join(made, "scripts", "zap", "main.py"), "# zap v1\n")
        standins.write(os.path.join(made, "scripts", "zap", "__pycache__", "x.pyc"), "junk")
        r = self.box.vfg("made", "save", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        day = time.strftime("%Y-%m-%d")
        self.assertRegex(r.stdout, r"^saved made/main/ to .*/%s: 2 files\n$" % day)
        self.assertFalse(os.path.exists(os.path.join(archive, day, "scripts", "zap", "__pycache__")))
        standins.write(os.path.join(made, "scripts", "zap", "main.py"), "# zap v2\n")
        self.assertIn("replacing today's earlier save", self.box.vfg("made", "save", **env).stdout)
        standins.write(os.path.join(made, "scripts", "zap", "main.py"), "# zap v3\n")
        r = self.box.vfg("made", "restore", day, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"; the one it replaced is in made/\.replaced/main-\d{8}-\d{6}\n$")
        with open(os.path.join(made, "scripts", "zap", "main.py")) as f:
            self.assertEqual(f.read(), "# zap v2\n")
        r = self.box.vfg("made", "restore", "1999-01-01", **env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("saved: %s" % day, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
