"""The whole bridge as systemd runs it: python3 -m vfg_bridge, SIGTERM, a restart with no duplicates."""

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


def stamp():
    return vfg_feed.utcnow().strftime("%H:%M:%S")


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

    def wait_rows(self, sql, count, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if os.path.exists(self.db_path) and self.query(sql)[0][0] >= count:
                return
            time.sleep(0.05)
        self.fail("%s did not reach %d" % (sql, count))

    def test_serve_stop_and_restart(self):
        self.console("[00:00:00] [Server thread/INFO]: <Steve> before the bridge ever ran")
        proc, port = self.start()
        self.assertTrue(os.path.exists(self.watch_path))
        now = vfg_feed.utcnow()
        body = {"client_line_id": "c0ffee00-0000-4000-8000-000000000001", "player_name": "Steve",
                "player_uuid": None, "text": "build me a castle",
                "started_at": vfg_feed.format_time(now), "ended_at": vfg_feed.format_time(now),
                "peak_dbfs": -8.2, "mean_dbfs": -23.5}
        status, first = self.post(port, body)
        self.assertEqual(status, 202)
        self.assertEqual(self.post(port, body), (202, first))
        self.console("[%s] [Server thread/INFO]: <Steve> smoke test" % stamp(),
                     "[%s] [Server thread/INFO]: System chat: Steve joined the game" % stamp())
        self.wait_rows("SELECT count(*) FROM events", 1)
        self.wait_rows("SELECT count(*) FROM lines WHERE source = 'chat'", 1)
        code, rest = self.stop(proc)
        self.assertEqual(code, 0, rest)
        self.assertNotIn("build me a castle", rest)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "console.offset")))

        proc, port = self.start()
        self.console("[%s] [Server thread/INFO]: System chat: Steve left the game" % stamp())
        self.wait_rows("SELECT count(*) FROM events", 2)
        code, rest = self.stop(proc)
        self.assertEqual(code, 0, rest)
        self.assertEqual(self.query("SELECT source, text FROM lines ORDER BY source"),
                         [("chat", "smoke test"), ("voice", "build me a castle")])
        self.assertEqual([k for (k,) in self.query("SELECT kind FROM events ORDER BY at, id")],
                         ["join", "leave"])
        self.assertEqual([re.sub(r"#\d+ \d\d:\d\d:\d\d", "", ln) for ln in self.feed_lines()],
                         ["GAME join Steve | Steve joined the game",
                          "GAME leave Steve | Steve left the game"])


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
