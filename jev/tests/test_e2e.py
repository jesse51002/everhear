"""`jev_filter.py` itself: once as a process, against the stand-in for Jev's API, a
temporary database and the stand-in feed writer; and its `--judge` and key reading,
called in-process. Every rule of the filter is in test_filter.py, on a fake clock."""
import contextlib
import io
import os
import signal
import subprocess
import sys
import time
import unittest

import fake_jev
from support import FAKE_LIB, ROOT, Lines, make_db, tempdir

from filter import Config
from jev_filter import judge_once, read_key

KEY = "test-key-e2e-not-a-secret"
NO_PROXY = {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}


class WithJev(unittest.TestCase):
    """The stand-in for Jev's API, in a thread, for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls.server = fake_jev.serve()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.mode = "ok"
        self.server.calls.clear()
        self.dir = tempdir(self)


class TestTheProcess(WithJev):
    def test_judges_feeds_and_stops_on_sigterm_flushing_what_waits(self):
        db = os.path.join(self.dir, "vfg.db")
        conn = make_db(db)
        self.addCleanup(conn.close)
        lines = Lines(conn)
        feed = os.path.join(self.dir, "feed.log")
        journal = os.path.join(self.dir, "journal.log")
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY" and not k.startswith("VFG_")}
        env.update(NO_PROXY)
        env.update({"VFG_DB": db, "VFG_LIB": FAKE_LIB, "FAKE_FEED_LOG": feed, "VFG_JEV_URL": self.server.url,
                    "VFG_LOOKOUT": os.path.join(self.dir, "lookout.md"), "VFG_MENU": os.path.join(self.dir, "menu.md"),
                    "VFG_ENV_FILE": os.path.join(self.dir, "no.env"), "TYPESAFE_API_KEY": KEY})
        now = time.time()
        lines.add(now, "Steve", "voice", "build me a castle", peak=-8.2)
        lines.add(now + 0.01, "AI", "ai", "Where should it go?")
        lines.add(now + 0.02, "Alex", "chat", "lol")
        lines.add(now + 0.03, "Alex", "chat", "that cave by spawn")
        with open(journal, "w") as out:
            p = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "jev_filter.py")], env=env,
                                 stdout=out, stderr=subprocess.STDOUT, cwd=ROOT)
        self.addCleanup(lambda: p.poll() is None and p.kill())

        def fed():
            try:
                with open(feed, encoding="utf-8") as f:
                    return f.read().splitlines()
            except FileNotFoundError:
                return []

        end = time.monotonic() + 5
        while len(self.server.calls) < 3 and time.monotonic() < end:   # castle, lol, cave
            time.sleep(0.01)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        # the ask went at once; the context line waited its batch and went on the way out
        self.assertEqual([l.split(" ", 2)[2] for l in fed()], [
            "Steve voice action_oriented 0.90 -8dB | build me a castle",
            "Alex chat context 0.70 - | that cave by spawn"])
        rows = {r[0]: r[1:] for r in conn.execute(
            "SELECT text, jev_class, jev_confidence, fed_at IS NOT NULL FROM lines")}
        self.assertEqual(rows, {"build me a castle": ("action_oriented", 0.9, 1), "lol": ("not_relevant", 0.8, 0),
                                "Where should it go?": (None, None, 0), "that cave by spawn": ("context", 0.7, 1)})
        usage = conn.execute("SELECT component, output_tokens, input_tokens, cost_usd FROM usage").fetchall()
        self.assertEqual(len(usage), 3)
        for component, out_tokens, tokens, cost in usage:
            self.assertEqual((component, out_tokens, cost), ("jev", 0, tokens * 0.000000042))
        self.assertEqual(self.server.calls[0]["headers"]["Authorization"], "Bearer " + KEY)
        with open(journal, encoding="utf-8") as f:
            self.assertNotIn(KEY, f.read())


class TestByHand(WithJev):
    def judge(self, text, raw=False):
        cfg = Config(jev_url=self.server.url, lookout=os.path.join(self.dir, "lookout.md"),
                     menu=os.path.join(self.dir, "menu.md"), db=os.path.join(self.dir, "vfg.db"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = judge_once(cfg, KEY, text, raw=raw)
        return code, out.getvalue(), err.getvalue()

    def test_judge_prints_the_verdict_and_writes_nothing(self):
        code, out, _ = self.judge("AI, build me a small tower here")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^action_oriented 0\.90 · \d+ms · \d+tok\n$")
        code, out, _ = self.judge("lol nice", raw=True)
        self.assertIn('"choice": "not_relevant"', out)
        self.assertNotIn(KEY, out)
        self.server.mode = "fail"
        code, _, err = self.judge("x")
        self.assertEqual(code, 1)
        self.assertIn("500", err)
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_key_from_the_environment_else_the_env_file(self):
        path = os.path.join(self.dir, "vfg.env")
        with open(path, "w") as f:
            f.write("VFG_CLIENT_TOKEN=abc\nexport TYPESAFE_API_KEY='%s'\n" % KEY)
        self.assertEqual(read_key(path, {}), (KEY, path))
        self.assertEqual(read_key(path, {"TYPESAFE_API_KEY": "other"}), ("other", "the environment"))
        key, where = read_key(os.path.join(self.dir, "missing.env"), {})
        self.assertEqual(key, "")
        self.assertIn("unreadable", where)


if __name__ == "__main__":
    unittest.main()
