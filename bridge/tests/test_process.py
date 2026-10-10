"""The whole bridge as systemd runs it: python3 -m vfg_bridge, then SIGTERM."""

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time
import unittest

import vfg_feed

from helpers import Box, subprocess_env


class TestBridgeProcess(Box):
    def start(self):
        proc = subprocess.Popen([sys.executable, "-m", "vfg_bridge"], env=subprocess_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            out = proc.stdout.readline()
            m = re.search(r"serving on [^:]+:(\d+)", out)
            if m:
                return proc, int(m.group(1))
            if not out and proc.poll() is not None:
                break
        self.fail("the bridge did not start")

    def stop(self, proc):
        proc.send_signal(signal.SIGTERM)
        rest, _ = proc.communicate(timeout=15)
        return proc.returncode, rest

    def post(self, port, body):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/v1/lines", body=json.dumps(body),
                      headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
        resp = conn.getresponse()
        doc = json.loads(resp.read())
        conn.close()
        return resp.status, doc

    def test_serves_tails_and_stops_on_sigterm(self):
        self.console("")
        proc, port = self.start()
        self.assertTrue(os.path.exists(self.watch_path))
        now = vfg_feed.utcnow()
        body = {"client_line_id": "c0ffee00-0000-4000-8000-000000000001", "player_name": "Steve",
                "player_uuid": None, "text": "build me a castle",
                "started_at": vfg_feed.format_time(now), "ended_at": vfg_feed.format_time(now),
                "peak_dbfs": -8.2, "mean_dbfs": -23.5}
        self.assertEqual(self.post(port, body)[0], 202)
        code, rest = self.stop(proc)
        self.assertEqual(code, 0, rest)
        self.assertNotIn("build me a castle", rest)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "console.offset")))  # the tail ran
        self.assertEqual(self.query("SELECT source, text FROM lines"), [("voice", "build me a castle")])


class TestFatal(unittest.TestCase):
    def test_an_exception_escaping_a_thread_ends_the_process_with_1(self):
        from unittest import mock
        from vfg_bridge import __main__ as entry

        def boom():
            raise RuntimeError("tail died")

        with mock.patch.object(entry.os, "_exit") as exit_, \
                mock.patch.object(entry.log, "exception") as logged:
            entry._guarded("console tail", boom)()
        exit_.assert_called_once_with(1)
        self.assertEqual(logged.call_count, 1)


if __name__ == "__main__":
    unittest.main()
