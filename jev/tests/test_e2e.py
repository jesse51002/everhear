"""End to end: `jev_filter.py` as a process, against `fake_jev`, a temporary
database and the stand-in feed writer, with VFG_BATCH_SECONDS=1."""
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.request

from support import FAKE_LIB, ROOT, TESTS, Lines, make_db, tempdir

KEY = "test-key-e2e-not-a-secret"
NO_PROXY = {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}


def start_fake_jev(test):
    p = subprocess.Popen([sys.executable, os.path.join(TESTS, "fake_jev.py")], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    test.addCleanup(_kill, p)
    port = int(p.stdout.readline())
    return "http://127.0.0.1:%d" % port


def _kill(p):
    if p.poll() is None:
        p.kill()
    p.wait()
    for f in (p.stdout, p.stderr):
        if f:
            f.close()


def post(url, body):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    with opener.open(req, timeout=5) as r:
        return r.read()


class E2E(unittest.TestCase):
    def setUp(self):
        d = tempdir(self)
        self.dir = d
        self.db = os.path.join(d, "vfg.db")
        self.conn = make_db(self.db)
        self.addCleanup(self.conn.close)
        self.lines = Lines(self.conn)
        self.feed = os.path.join(d, "feed.log")
        self.journal = os.path.join(d, "journal.log")
        self.jev = start_fake_jev(self)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY" and not k.startswith("VFG_")}
        env.update(NO_PROXY)
        env.update({
            "VFG_DB": self.db, "VFG_LIB": FAKE_LIB, "FAKE_FEED_LOG": self.feed,
            "VFG_JEV_URL": self.jev + "/v1/systemone", "VFG_LOOKOUT": os.path.join(self.dir, "lookout.md"),
            "VFG_ENV_FILE": os.path.join(self.dir, "no.env"), "VFG_BATCH_SECONDS": "1", "VFG_JEV_TIMEOUT": "1",
            "TYPESAFE_API_KEY": KEY,
        })
        env.update(extra)
        return env

    def start(self, **extra):
        out = open(self.journal, "w")
        self.addCleanup(out.close)
        p = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "jev_filter.py")], env=self.env(**extra),
                             stdout=out, stderr=subprocess.STDOUT, cwd=ROOT)
        self.addCleanup(_kill, p)
        return p

    def feed_lines(self):
        if not os.path.exists(self.feed):
            return []
        with open(self.feed, encoding="utf-8") as f:
            return f.read().splitlines()

    def wait_for(self, check, within):
        end = time.monotonic() + within
        while time.monotonic() < end:
            if check(self.feed_lines()):
                return True
            time.sleep(0.05)
        return check(self.feed_lines())

    def journal_text(self):
        with open(self.journal, encoding="utf-8") as f:
            return f.read()

    def test_the_whole_way(self):
        p = self.start()
        now = time.time()
        self.lines.add(now, "Steve", "voice", "build me a castle", peak=-8.2)
        self.lines.add(now + 0.01, "AI", "ai", "Where should it go?")
        self.lines.add(now + 0.02, "Alex", "chat", "lol")
        self.lines.add(now + 0.03, "Alex", "chat", "that cave by spawn")
        t0 = time.monotonic()

        def first_and_last(lines):
            text = "\n".join(lines)
            return "| build me a castle" in text and "| that cave by spawn" in text
        self.assertTrue(self.wait_for(first_and_last, 3.0), self.feed_lines())
        took = time.monotonic() - t0
        text = "\n".join(self.feed_lines())
        self.assertNotIn("lol", text)
        self.assertNotIn("Where should it go?", text)
        self.assertRegex(text, r"LINE#\d+ \d\d:\d\d:\d\d Steve voice action_oriented 0\.90 -8dB \| build me a castle")
        self.assertRegex(text, r"LINE#\d+ \d\d:\d\d:\d\d Alex chat context 0\.70 - \| that cave by spawn")
        self.assertLess(took, 3.0)

        # Jev starts failing: the next line goes through as context within 15 s, after one SYS line
        post(self.jev + "/mode", "fail")
        self.lines.add(time.time(), "Alex", "voice", "hello everyone", peak=-21.4)

        def open_and_passed(lines):
            text = "\n".join(lines)
            return "| jev unavailable — passing every line as context" in text and \
                "Alex voice context - -21dB | hello everyone" in text
        self.assertTrue(self.wait_for(open_and_passed, 15.0), self.feed_lines())
        lines = self.feed_lines()
        sys_open = [i for i, l in enumerate(lines) if l.startswith("SYS#")]
        passed = [i for i, l in enumerate(lines) if l.endswith("| hello everyone")]
        self.assertEqual(len(sys_open), 1)
        self.assertLess(sys_open[0], passed[0])

        # Jev comes back: one SYS line within 7 s
        post(self.jev + "/mode", "ok")
        self.assertTrue(self.wait_for(lambda ls: any(l.endswith("| jev back — filtering again") for l in ls), 7.0),
                        self.feed_lines())
        self.lines.add(time.time(), "Steve", "chat", "make it taller")
        self.assertTrue(self.wait_for(lambda ls: any("Steve chat action_oriented 0.90 - | make it taller" in l
                                                     for l in ls), 3.0), self.feed_lines())
        self.assertEqual(sum(l.startswith("SYS#") for l in self.feed_lines()), 2)

        # SIGTERM: exit 0 within 5 s
        p.send_signal(signal.SIGTERM)
        t = time.monotonic()
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertLess(time.monotonic() - t, 5)

        # the database: verdicts written, the AI's line untouched, one usage row per judged line
        rows = {r[0]: r[1:] for r in self.conn.execute(
            "SELECT text, jev_class, jev_confidence, fed_at IS NOT NULL FROM lines")}
        self.assertEqual(rows["build me a castle"], ("action_oriented", 0.9, 1))
        self.assertEqual(rows["lol"], ("not_relevant", 0.8, 0))
        self.assertEqual(rows["Where should it go?"], (None, None, 0))
        self.assertEqual(rows["hello everyone"], ("context", None, 1))
        usage = self.conn.execute("SELECT ref, input_tokens, cost_usd, component, output_tokens FROM usage "
                                  "WHERE ref != 'probe'").fetchall()
        self.assertEqual(len(usage), 4)   # castle, lol, cave, taller
        for ref, tokens, cost, component, out in usage:
            self.assertEqual((component, out), ("jev", 0))
            self.assertEqual(cost, tokens * 0.000000042)
        self.assertNotIn(KEY, self.journal_text())

    def test_killed_between_feed_and_mark_feeds_once(self):
        flag = os.path.join(self.dir, "crash.flag")
        open(flag, "w").close()
        p = self.start(VFG_LIB=os.path.join(TESTS, "crash_lib"), CRASH_FLAG=flag, FAKE_FEED_DB=self.db)
        self.lines.add(time.time(), "Steve", "chat", "build me a castle")
        self.assertEqual(p.wait(timeout=10), -signal.SIGKILL)
        self.assertFalse(os.path.exists(flag))
        fed_at = self.conn.execute("SELECT fed_at FROM lines WHERE text = 'build me a castle'").fetchone()[0]
        self.assertIsNone(fed_at)                       # in the feed, but not marked
        self.assertEqual(len(self.feed_lines()), 1)
        p = self.start(FAKE_FEED_DB=self.db)            # systemd brings it back
        self.lines.add(time.time(), "Alex", "chat", "make it red")
        self.assertTrue(self.wait_for(lambda ls: any(l.endswith("| make it red") for l in ls), 5.0), self.feed_lines())
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertEqual(sum(l.endswith("| build me a castle") for l in self.feed_lines()), 1)
        self.assertIsNotNone(self.conn.execute(
            "SELECT fed_at FROM lines WHERE text = 'build me a castle'").fetchone()[0])
        self.assertIn("already fed as LINE#1", self.journal_text())

    def test_no_key_fails_open_and_says_so(self):
        p = self.start(TYPESAFE_API_KEY="")
        self.lines.add(time.time(), "Steve", "chat", "build me a castle")
        self.assertTrue(self.wait_for(lambda ls: any("Steve chat context - - | build me a castle" in l for l in ls),
                                      14.0), self.feed_lines())
        self.assertTrue(self.feed_lines()[0].endswith("| jev unavailable — passing every line as context"))
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        journal = self.journal_text()
        self.assertEqual(journal.count("no TYPESAFE_API_KEY"), 1)
        calls = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.jev + "/calls").read()
        self.assertEqual(calls, b"[]")     # nothing was sent without a key

    def test_key_from_the_env_file(self):
        with open(os.path.join(self.dir, "vfg.env"), "w") as f:
            f.write("VFG_CLIENT_TOKEN=abc\nTYPESAFE_API_KEY='%s'\n" % KEY)
        os.chmod(os.path.join(self.dir, "vfg.env"), 0o600)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "jev_filter.py"), "--judge", "lol nice"],
                           env=self.env(TYPESAFE_API_KEY="", VFG_ENV_FILE=os.path.join(self.dir, "vfg.env")),
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("not_relevant 0.80"), r.stdout)
        calls = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.jev + "/calls").read()
        self.assertIn(("Bearer " + KEY).encode(), calls)

    def test_judge_by_hand_writes_nothing(self):
        os.remove(self.db)
        for text, want in (("AI, build me a small tower here", "action_oriented 0.90"), ("lol nice", "not_relevant 0.80")):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "jev_filter.py"), "--judge", text],
                               env=self.env(), capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stdout, r"^%s · \d+ms · \d+tok$" % want)
            self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.db))
        self.assertFalse(os.path.exists(self.feed))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "jev_filter.py"), "--judge", "lol nice", "--raw"],
                           env=self.env(), capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"answers"', r.stdout)
        self.assertIn('"choice": "not_relevant"', r.stdout)
        post(self.jev + "/mode", "fail")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "jev_filter.py"), "--judge", "x"],
                           env=self.env(), capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 1)
        self.assertIn("500", r.stderr)


if __name__ == "__main__":
    unittest.main()
