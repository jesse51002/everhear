"""Tests for scripts/_lib/instances.py: copies started, named, edited and stopped on a box made
in a temp folder, with a script that only sleeps. No RCON: a stop's cleanup is mc.py's, tested
with the kit; here, what the box does with a copy's processes and files.

    uv run --no-project --python 3.12 python scripts/_test/test_instances.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
sys.path.insert(0, LIB)

import instances  # noqa: E402

NAP = '''import json, os, sys, time
meta = json.load(open(os.path.join(os.environ["VFG_RUN_DIR"], os.environ["VFG_SCRIPT"] + ".json")))
print("values", json.dumps(meta["values"], sort_keys=True), "task", os.environ["VFG_TASK"], flush=True)
if meta["values"].get("end"):
    sys.exit(meta["values"]["end"])
time.sleep(60)
'''


class InstancesTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="vfg-instances-")
        folder = os.path.join(self.base, "scripts", "nap")
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(NAP)
        self.box = instances.Box(self.base, os.path.join(self.base, "mc"))

    def tearDown(self):
        for name in self.box.names():
            self.box.stop(name)
        shutil.rmtree(self.base, ignore_errors=True)

    def meta(self, **values):
        return {"action": "nap", "script": "nap", "source": "library", "values": dict(values),
                "live": ["cooldown"], "runs": "until_stopped", "task": "T4"}

    def start(self, as_=None, **values):
        name, pid, proc = self.box.start(self.meta(**values), as_)
        self.addCleanup(proc.wait)
        return name, pid, proc

    def test_names_the_action_then_2_3_or_as_given_never_a_running_one(self):
        self.assertEqual([self.start()[0] for _ in range(3)], ["nap", "nap_2", "nap_3"])
        self.assertEqual(self.start(as_="mine")[0], "mine")
        with self.assertRaises(instances.CopyError):
            self.start(as_="mine")
        for bad in ("Mine", "a-b", "_x", "x" * 49):
            with self.assertRaises(instances.CopyError):
                self.start(as_=bad)
        self.assertTrue(self.box.stop("nap_2"))
        self.assertEqual(self.start()[0], "nap_2")              # the first free one
        self.assertEqual(instances.copy_name("a" * 48, 2), "a" * 46 + "_2")
        self.assertEqual(sorted(c["copy"] for c in self.box.copies()), ["mine", "nap", "nap_2", "nap_3"])

    def test_a_copy_gets_its_meta_and_waits_end_or_result(self):
        name, pid, proc = self.start(cooldown=2)
        got = self.box.wait(name, proc, 0.3)
        self.assertEqual((got["running"], got["code"], got["result"]), (True, None, None))
        meta = self.box.copy(name)
        self.assertEqual((meta["pid"], meta["task"], meta["values"]), (pid, "T4", {"cooldown": 2}))
        with open(self.box.path(name, ".result.json"), "w") as f:
            json.dump({"summary": "ready", "facts": {}, "at": time.time()}, f)
        self.assertEqual(self.box.wait(name, proc, 5, on_result=True)["result"]["summary"], "ready")
        name, pid, proc = self.start(end=3)
        got = self.box.wait(name, proc, 10)
        self.assertEqual((got["running"], got["code"]), (False, 3))
        self.assertFalse(os.path.exists(self.box.path(name, ".pid")))
        self.assertIn("values {\"end\": 3} task T4", "\n".join(self.box.tail(name)))
        with self.assertRaises(instances.NotDeployed):
            self.box.start(dict(self.meta(), script="nowhere"))

    def test_an_edit_lands_live_or_restarts_with_the_rest_kept(self):
        name, pid, proc = self.start(cooldown=2, length=60)
        got = self.box.edit(name, {"cooldown": 0, "length": 60}, ["cooldown"], True)
        self.assertEqual((got["live"], got["restarted"], self.box.pid(name)), (True, None, pid))
        self.assertEqual(self.box.live(name), {"cooldown": 0})
        got = self.box.edit(name, {"cooldown": 0, "length": 9}, ["length"], False)
        self.assertFalse(got["live"])
        self.assertNotEqual(got["restarted"], pid)
        self.assertEqual(self.box.meta(name)["values"], {"cooldown": 0, "length": 9})   # the live edit kept
        self.assertEqual(self.box.live(name), {})
        with self.assertRaises(instances.CopyError):
            self.box.edit(name, {"range": 5}, ["range"], False)     # not a value of what it runs
        with self.assertRaises(instances.CopyError):
            self.box.edit("nobody", {"cooldown": 1}, ["cooldown"], True)
        # A field the copy's meta does not list as live is never written live.
        got = self.box.edit(name, {"length": 4}, None, True)
        self.assertFalse(got["live"])

    def test_two_live_edits_at_once_both_land(self):
        name, pid, proc = self.start(cooldown=2, colour="red")
        meta = self.box.meta(name)
        meta["live"] = ["cooldown", "colour"]
        instances.write_json(self.box.path(name, ".json"), meta)
        write = instances.write_json

        def slow_write(path, data):   # each edit holds its read a while before it writes
            time.sleep(0.2)
            write(path, data)

        instances.write_json = slow_write
        self.addCleanup(setattr, instances, "write_json", write)
        edits = [({"cooldown": 0}, ["cooldown"]), ({"colour": "blue"}, ["colour"])]
        threads = [threading.Thread(target=self.box.edit, args=(name, vals, changed, True)) for vals, changed in edits]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(self.box.live(name), {"cooldown": 0, "colour": "blue"})

    def test_a_stop_ends_only_that_copy(self):
        a, pa, _ = self.start()
        b, pb, _ = self.start()
        self.assertTrue(self.box.stop(a))
        self.assertTrue(instances.gone(pa))
        self.assertFalse(instances.gone(pb))
        self.assertFalse(self.box.stop(a))
        self.assertEqual([c["copy"] for c in self.box.copies()], [b])


if __name__ == "__main__":
    unittest.main(verbosity=2)
