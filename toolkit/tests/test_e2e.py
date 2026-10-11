"""vfg as a process, end to end: python3 vfg, then fake_ssh (ssh's stand-in, which runs the remote
command here with bash), the box's Python and the remote program. A handful, each a path no
in-process test takes: the process and its SSH, closed descriptors, and watches that follow.

    python3 -m unittest discover -s toolkit/tests
"""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import FEED_RE, VFG, BoxCase, wait_for  # noqa: E402


class Watch(object):
    """A running `vfg watch`, its stdout read line by line on a thread."""

    def __init__(self, box, *args):
        self.p = box.popen("watch", *args)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.p.stdout.readline, b""):
            self.q.put(raw.decode("utf-8").rstrip("\n"))

    def expect(self, n, timeout=5):
        got, end = [], time.time() + timeout
        while len(got) < n:
            try:
                got.append(self.q.get(timeout=max(0.01, end - time.time())))
            except queue.Empty:
                break
        return got

    def texts(self, n):
        got = self.expect(n)
        for line in got:
            assert FEED_RE.match(line), line
        return [line.split(" | ")[1] for line in got]

    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        return self.p.wait(5)


class TestEndToEnd(BoxCase):
    def ssh_log(self):
        with open(os.path.join(self.box.root, "ssh.jsonl")) as f:
            return [json.loads(l) for l in f]

    def test_a_command_through_ssh_and_the_box(self):
        log = os.path.join(self.box.root, "ssh.jsonl")
        r = self.box.process("history", "--feed", "--limit", "2", FAKE_SSH_LOG=log)
        self.assertEqual((r.returncode, r.stdout), (0, "".join(l + "\n" for _, l in self.box.feed_rows[1:])), r.stderr)
        check, master, call = self.ssh_log()
        self.assertEqual(check[-3:], ["-O", "check", "ec2-user@fakebox"])
        for opt in ("ControlMaster=yes", "ControlPersist=10m", "-N", "-f"):
            self.assertIn(opt, master)
        self.assertEqual(call[-2:], ["ec2-user@fakebox", self.box.rvfg + "/python/bin/python3 -u -"])
        for opt in ("BatchMode=yes", "ConnectTimeout=10", "ServerAliveInterval=15", "ServerAliveCountMax=3",
                    "LogLevel=ERROR", "StrictHostKeyChecking=accept-new", "ControlMaster=no",
                    "UserKnownHostsFile=%s/known_hosts" % self.box.vhome, "ControlPath=%s/ssh/%%C" % self.box.vhome):
            self.assertIn(opt, call)
        self.assertEqual(call[call.index("-i") + 1], self.box.key)
        self.assertEqual(oct(os.stat(os.path.join(self.box.vhome, "ssh")).st_mode & 0o777), "0o700")
        r = self.box.process("say", "hi", FAKE_SSH_DOWN="1")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (4, "", "vfg: cannot reach EC2 (ec2-user@fakebox): "
                                                                     "ssh: connect to host fakebox port 22: Connection "
                                                                     "timed out\n"))
        with open(os.path.join(self.box.vhome, "vfg.log")) as f:
            self.assertRegex(f.read(), r'"history", "--feed", "--limit", "2"\] exit=0 \d+ms\n.*"say", "hi"\] exit=4 ')

    def test_usage_add_with_stdout_and_stderr_closed(self):
        cmd = "exec %s %s usage add --component brain --model sol --in 1 --out 2 >&- 2>&-" % (sys.executable, VFG)
        self.assertEqual(subprocess.run(["bash", "-c", cmd], env=self.box.env(), timeout=60).returncode, 0)
        self.assertEqual(self.box.query("SELECT component, input_tokens FROM usage"), [("brain", 1)])

    def test_a_persons_watch_and_the_brains_follow_the_feed(self):
        cursor = os.path.join(self.box.vhome, "cursor")

        def at():
            try:
                with open(cursor) as f:
                    return int(f.read())
            except (OSError, ValueError):
                return None
        person = Watch(self.box)
        self.assertTrue(wait_for(lambda: at() == 3))              # no cursor: nothing old, from seq 3 on
        self.box.post("AI", "say T7", "on it")
        self.assertEqual(person.texts(1), ["on it"])
        brain = Watch(self.box, "--after", "2")                   # no lock: the person's watch runs on
        self.assertEqual(brain.texts(2), ["seeded line 3", "on it"])
        self.assertEqual(person.stop(), 0)
        self.box.post("SYS", "", "while the person is away")
        self.assertEqual(brain.texts(1), ["while the person is away"])
        self.assertEqual(at(), 4)                                  # --after moves no cursor
        again = Watch(self.box)                                    # resumes from the cursor
        self.assertEqual(again.texts(1), ["while the person is away"])
        self.assertEqual((again.stop(), brain.stop()), (0, 0))

if __name__ == "__main__":
    unittest.main()
