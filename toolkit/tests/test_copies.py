"""vfg action run and the copies it starts: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

Each test gets test_vfg's fake box (fake_ssh runs the remote side here, the box's Python being
the one running the tests) with the real scripts/_lib and the stand-in actions of standins.py:
command, edit, stop and save_place as the dispatcher's will be, and beam (until stopped, with
live values), ping and slow (once). vfg runs under uv, with pydantic, as it does on the PC.
"""
import fcntl
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
                          "values": {"say": "hi", "nap": 0, "fail": "", "early": False}, "live": [], "runs": "once", "task": "T3"})
        self.assertRegex(meta["started"], ISO_RE)
        self.assertEqual(self.box_json("ping.result.json")["summary"], "hi: 1 online")
        self.assertFalse(os.path.exists(self.run_dir("ping.pid")))
        self.assertNotIn("kill @e[tag=vfg_ping]", self.box.rcon_commands())  # it ended by itself: what it made stays
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
        # What it said before the timeout (a countdown started) is kept in the answer.
        got = self.line(self.run_action("ping", "nap=3", "early=true", VFG_DISPATCH_ACTION_TIMEOUT_S="1"))
        self.assertEqual(got["summary"], "hello: started; still running as %s" % got["copy"])

    def test_a_once_actions_wait_on_the_box_counts_its_deploy(self):
        # The op's clock starts before the deploy: one held up past the whole timeout (another
        # deploy holds the lock) leaves the copy the 0.5 s it always gets, not the 2 s again.
        os.makedirs(self.run_dir(), exist_ok=True)
        fd = os.open(self.run_dir("deploy.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            before = len(self.box.ssh_calls())
            p = self.box.popen("action", "run", "ping", "nap=5", VFG_DISPATCH_ACTION_TIMEOUT_S="2")
            self.assertTrue(wait_for(lambda: self.remote_calls(before), 30))
            time.sleep(2.5)                                 # the op began, and waits for the lock
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        released = time.time()
        out = p.communicate(timeout=30)[0].decode()
        took = time.time() - released
        got = json.loads(out)
        self.assertEqual((got["ok"], got["running"], got["summary"]), (True, True, "still running as ping"))
        self.assertLess(took, 1.6)

    def test_a_failed_script_is_not_ok_with_what_it_failed_with(self):
        # A script that refuses says why with result() and exits non-zero: its summary.
        r = self.run_action("ping", "fail=refuse")
        got = self.line(r, 1)
        self.assertEqual((got["ok"], got["running"], got["copy"]), (False, False, "ping"))
        self.assertEqual((got["error"], got["summary"]), ("ping exited with code 1: hello: refused", "hello: refused"))
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: " + got["error"])
        # A crash: the last line of the error, which mc.py wrote to <copy>.error as it cleaned up;
        # it wins over a result() from before the crash.
        for early in ("false", "true"):
            got = self.line(self.run_action("ping", "fail=crash", "early=" + early), 1)
            self.assertEqual(got["error"], "ping exited with code 1: NameError: name 'no_such_name_here' is not defined")
        self.assertEqual(got["summary"], "hello: started")
        with open(self.run_dir("ping.error")) as f:
            self.assertEqual(f.read(), "NameError: name 'no_such_name_here' is not defined\n")
        # The next run of the copy starts with none.
        self.line(self.run_action("ping"))
        self.assertFalse(os.path.exists(self.run_dir("ping.error")))
        # Neither (a script without mc): the last line it printed, as it is.
        standins.write(os.path.join(self.box.repo, "scripts", "plain", "main.py"),
                       '"""Plain: prints, then exits 4."""\nimport sys\nprint("plain: the last words")\nsys.exit(4)\n')
        standins.write(os.path.join(self.box.repo, "actions", "plain.py"),
                       standins.ACTIONS["ping"].replace("class Ping", "class Plain").replace('"ping"', '"plain"'))
        got = self.line(self.run_action("plain"), 1)
        self.assertEqual(got["error"], "plain exited with code 4: plain: the last words")

    def test_values_are_checked_and_refused_before_anything_is_sent(self):
        with open(os.path.join(self.box.vhome, "places.json"), "w") as f:
            json.dump({"fetched_at": time.time(), "places": {}}, f)   # fresh: nothing to fetch
        before = len(self.box.ssh_calls())
        for args, pattern in ((["nosuch"], r"^no action nosuch in actions/ or made/main/actions/"),
                              (["beam", "nope=1"], r"^beam has no value nope; its values: player, length"),
                              (["beam", "colour=blue"], r"^beam: colour is fixed at red$"),
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

    def test_a_call_out_of_time_is_124_with_its_json_line(self):
        r = self.box.vfg("--timeout", "1", "action", "run", "ping", FAKE_SSH_DELAY="4")
        self.assertEqual(r.returncode, 124, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual((got["ok"], got["action"]), (False, "ping"))
        self.assertRegex(got["error"], r"^timed out: ping did not answer within 1 s; it may still have happened$")

    def test_an_unchanged_lib_is_not_swapped_and_runs_at_once_share_it(self):
        self.line(self.run_action("ping"))
        lib = os.path.join(self.box.rvfg, "scripts", "_lib")
        ino = os.stat(lib).st_ino
        self.line(self.run_action("ping"))
        self.assertEqual(os.stat(lib).st_ino, ino)          # the same folder: nothing swapped
        with open(os.path.join(self.box.repo, "scripts", "_lib", "kit", "extra.py"), "w") as f:
            f.write("# changed\n")
        self.line(self.run_action("ping"))
        self.assertNotEqual(os.stat(lib).st_ino, ino)       # a change is deployed whole
        self.assertTrue(os.path.isfile(os.path.join(lib, "kit", "extra.py")))

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

    def test_the_edit_and_stop_actions_write_through_as_their_shortcuts_do(self):
        self.line(self.run_action("beam", "player=Steve"))
        self.line(self.run_action("beam", "player=Alex"))
        self.line(self.run_action("edit", "copy=beam", "cooldown=3", "--task", "T8"))
        self.assertEqual(self.running()["copies"]["beam"]["values"]["cooldown"], 3)
        got = self.line(self.run_action("stop", "copy=beam"))
        self.assertEqual(got["summary"], "stopped beam")
        self.assertEqual(sorted(self.running()["copies"]), ["beam_2"])
        # A copy the box no longer runs leaves running.json too.
        self.box.vfg("script", "list", "--fresh")
        os.killpg(self.pid("beam_2"), signal.SIGKILL)
        self.assertIn("beam_2", self.running()["copies"])
        self.line(self.run_action("stop", "copy=beam_2"))
        self.assertEqual(self.running()["copies"], {})

    def test_a_stop_that_failed_on_the_box_leaves_running_json_unless_nothing_runs(self):
        self.line(self.run_action("beam"))
        stop = os.path.join(self.box.repo, "scripts", "stop", "main.py")
        said = '''"""Stop: answers as the stop script does when it did not stop the copy."""
import sys

from kit import result, values

v = values()
result(SAID % v["copy"], copy=v["copy"]FACTS)
sys.exit(1)
'''
        standins.write(stop, said.replace("SAID", '"cannot stop %s: [Errno 1] Operation not permitted"')
                       .replace("FACTS", ""))
        r = self.box.vfg("script", "stop", "beam")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("Operation not permitted", r.stderr)
        self.assertIn("beam", self.running()["copies"])      # it may run still: kept
        standins.write(stop, said.replace("SAID", '"nothing runs as %s"').replace("FACTS", ", was_running=False"))
        r = self.box.vfg("script", "stop", "beam")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("beam", self.running()["copies"])   # the box says nothing runs as beam: gone

    def test_an_edit_of_a_copy_this_pc_does_not_know_asks_the_box(self):
        self.line(self.run_action("beam"))
        os.remove(os.path.join(self.box.vhome, "running.json"))
        r = self.box.vfg("script", "set", "beam", "cooldown=1")
        self.assertEqual(r.stdout, "beam: cooldown=1.0, live\n", r.stderr)


MADE_BEAM = '''from pydantic import Field

from actions._types import Action, Live, Players


class GlowBeam(Action):
    """A beam made on this server (a test's)."""

    script = "beam"
    runs = "until_stopped"
    takes = ["player"]
    fixed = {"colour": "blue"}

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
        text = self.read(*p).replace("TODO: what it does, in players' words: its line on the menu.", "Steve's beam.")
        with open(self.made(*p), "w") as f:
            f.write(text)

    def test_a_copys_values_become_a_variant_of_its_library_action(self):
        self.line(self.run_action("beam", "player=Steve", "cooldown=2"))
        r = self.box.vfg("script", "save", "beam", "beam_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "saved made/main/actions/beam_steve.py from beam (beam), "
                                                   "with player=Steve cooldown=2 as its defaults")
        self.assertIn("its line on the menu is still a TODO", r.stdout)
        self.assertEqual(self.read("actions", "beam_steve.py"),
                         'from actions.beam import Beam\n\n\nclass BeamSteve(Beam):\n'
                         '    """TODO: what it does, in players\' words: its line on the menu."""\n\n'
                         '    defaults = {"player": "Steve", "cooldown": 2.0}\n')
        self.fill_line("actions", "beam_steve.py")
        r = self.box.vfg("action", "check", "beam_steve")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/beam_steve.py\n"), r.stderr)
        got = self.line(self.run_action("beam_steve"))
        self.assertEqual(self.box_json(got["copy"] + ".json")["values"]["player"], "Steve")
        r = self.box.vfg("script", "save", "beam", "beam_steve")
        self.assertEqual(r.returncode, 1)
        self.assertIn("beam_steve is taken: made/main/actions/beam_steve.py", r.stderr)

    def test_a_made_actions_copy_is_written_out_with_its_values_as_defaults(self):
        os.makedirs(self.made("actions"))
        with open(self.made("actions", "glow_beam.py"), "w") as f:
            f.write(MADE_BEAM)
        self.line(self.run_action("glow_beam", "cooldown=5"))
        self.assertEqual(self.box.vfg("script", "set", "glow_beam", "colour=green").returncode, 0)
        r = self.box.vfg("script", "save", "glow_beam", "green_beam")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read("actions", "green_beam.py")
        self.assertIn("class GreenBeam(Action):\n", text)
        self.assertIn('    script = "beam"\n', text)
        self.assertIn('    defaults = {"cooldown": 5.0, "colour": "green"}\n', text)
        self.assertNotIn("fixed", text)                 # the colour it fixed is a default now
        self.assertIn('    cooldown: Live[float] = Field(10, ge=0, le=60)\n    """seconds between shots"""\n', text)
        self.fill_line("actions", "green_beam.py")
        r = self.box.vfg("action", "check", "green_beam")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/green_beam.py\n"), r.stderr)


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
        self.assertEqual(r.stderr.splitlines()[-1], "vfg: minecraft rejected the command")
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
        r = self.box.vfg("action", "check", "zap")       # left out of the catalog: why, from the loader
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "zap: script zap: there is no made/main/scripts/zap/main.py or "
                                   "scripts/zap/main.py; takes names sya, which is not one of its fields\n")
        r = self.box.vfg("action", "list")
        self.assertNotIn("zap", r.stdout)
        self.assertIn("vfg: zap did not load, left out: script zap", r.stderr)
        standins.write(os.path.join(self.box.repo, "made", "main", "scripts", "zap", "main.py"), "# zap\n")
        standins.write(os.path.join(self.box.repo, "made", "main", "actions", "zap.py"),
                       standins.ACTIONS["ping"].replace("class Ping", "class Zap").replace('script = "ping"', 'script = "zap"'))
        r = self.box.vfg("action", "check", "zap")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/zap.py\n"), r.stderr)
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
    def test_every_save_is_a_new_folder_and_a_restore_takes_a_name_or_a_days_latest(self):
        archive = tempfile.mkdtemp(dir=self.box.root)
        env = {"VFG_MADE_ARCHIVE": archive}
        r = self.box.vfg("made", "save", **env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("nothing to save", r.stderr)
        made = os.path.join(self.box.repo, "made", "main")
        main_py = os.path.join(made, "scripts", "zap", "main.py")
        standins.write(os.path.join(made, "actions", "zap.py"), "# zap\n")
        standins.write(main_py, "# zap v1\n")
        standins.write(os.path.join(made, "scripts", "zap", "__pycache__", "x.pyc"), "junk")
        os.makedirs(os.path.join(archive, "2026-10-09"))                # a day's save, as an older vfg made it
        standins.write(os.path.join(archive, "2026-10-09", "scripts", "zap", "main.py"), "# zap v0\n")
        names = []
        for v in ("v1", "v2"):
            standins.write(main_py, "# zap %s\n" % v)
            r = self.box.vfg("made", "save", **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = re.match(r"^saved made/main/ to .*/(\d{4}-\d\d-\d\d-\d{6}): 2 files\n$", r.stdout)
            self.assertTrue(m, r.stdout)
            names.append(m.group(1))
        day = time.strftime("%Y-%m-%d")
        self.assertEqual(len(set(names)), 2)                             # never the same folder twice
        self.assertTrue(all(n.startswith(day + "-") for n in names))
        self.assertEqual(sorted(os.listdir(archive)), ["2026-10-09"] + sorted(names))   # nothing deleted
        self.assertFalse(os.path.exists(os.path.join(archive, names[0], "scripts", "zap", "__pycache__")))

        def restored():
            with open(main_py) as f:
                return f.read()

        standins.write(main_py, "# zap v3\n")
        r = self.box.vfg("made", "restore", day, **env)                  # a date: its latest
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^restored made/main/ from .*/%s: 2 files; the one it replaced is in "
                                   r"made/\.replaced/main-\d{8}-\d{6}\n$" % names[1])
        self.assertEqual(restored(), "# zap v2\n")
        r = self.box.vfg("made", "restore", names[0], **env)               # a full name
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(restored(), "# zap v1\n")
        self.assertEqual(self.box.vfg("made", "restore", "2026-10-09", **env).returncode, 0)
        self.assertEqual(restored(), "# zap v0\n")
        r = self.box.vfg("made", "restore", "1999-01-01", **env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no save 1999-01-01 in ", r.stderr)
        self.assertIn("saved: 2026-10-09, %s, %s" % tuple(names), r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
