"""`jev_filter.py` itself: as a process, against the stand-in for Jev's API, a
temporary database and the stand-in feed writer, and once with a stand-in for the
bridge's cast path; and its `--judge`, `--cast` and key reading, called in-process.
Every rule of the filter is in test_filter.py and test_cast_check.py, on a fake clock."""
import contextlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fake_jev
from support import FAKE_LIB, ROOT, Lines, make_db, tempdir

from filter import Config
from jev_filter import cast_once, judge_once, read_key

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
        lines.add(now - 0.01, "Steve", "voice", "the big one, by the river", peak=-20.0)
        castle = lines.add(now, "Steve", "voice", "build me a castle", peak=-8.2)
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
        while len(self.server.calls) < 5 and time.monotonic() < end:   # the big one, castle, its gather, lol, cave
            time.sleep(0.01)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        # the ask went at once, with the line before it that belongs to it; the context line waited its
        # batch and went on the way out
        self.assertEqual([l.split(" ", 2)[2] for l in fed()], [
            "Steve voice action_oriented 0.90 -8dB | the big one, by the river … build me a castle",
            "Alex chat context 0.70 - | that cave by spawn"])
        rows = {r[0]: r[1:] for r in conn.execute(
            "SELECT text, jev_class, jev_confidence, fed_at IS NOT NULL FROM lines")}
        self.assertEqual(rows, {"the big one, by the river": ("not_relevant", 0.8, 0),
                                "build me a castle": ("action_oriented", 0.9, 1), "lol": ("not_relevant", 0.8, 0),
                                "Where should it go?": (None, None, 0), "that cave by spawn": ("context", 0.7, 1)})
        usage = conn.execute("SELECT component, output_tokens, input_tokens, cost_usd, ref FROM usage").fetchall()
        self.assertEqual(len(usage), 5)
        self.assertIn("gather:" + castle, [u[4] for u in usage])
        for component, out_tokens, tokens, cost, _ in usage:
            self.assertEqual((component, out_tokens, cost), ("jev", 0, tokens * 0.000000042))
        self.assertEqual(self.server.calls[0]["headers"]["Authorization"], "Bearer " + KEY)
        with open(journal, encoding="utf-8") as f:
            self.assertNotIn(KEY, f.read())


class Bridge(BaseHTTPRequestHandler):
    """The bridge's `POST /v1/jev/casts`, standing in: drops the line in the database, as the
    bridge does, and answers that the cast fired."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        self.server.got.append((self.path, self.headers.get("Authorization"), body))
        conn = sqlite3.connect(self.server.db)
        if body.get("keep"):   # a long line's cast: the line is kept, and carries the cast
            conn.execute("UPDATE lines SET cast_id = 'C2' WHERE id = ?", (body["line_id"],))
        else:
            conn.execute("UPDATE lines SET dropped = 'cast', cast_id = 'C1' WHERE id = ?", (body["line_id"],))
        conn.commit()
        conn.close()
        raw = json.dumps({"id": "C1", "status": "fired", "copy": "hot_fireball",
                          "line": "kept" if body.get("keep") else "dropped"}).encode()
        self.send_response(202)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestTheCastCheck(WithJev):
    def test_a_cast_goes_to_the_bridge_and_is_never_fed(self):
        db = os.path.join(self.dir, "vfg.db")
        conn = make_db(db)
        self.addCleanup(conn.close)
        conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        conn.execute("ALTER TABLE lines ADD COLUMN cast_id TEXT")
        lines = Lines(conn)
        bridge = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        bridge.db, bridge.got = db, []
        threading.Thread(target=bridge.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(bridge.server_close)
        self.addCleanup(bridge.shutdown)
        store = os.path.join(self.dir, "incantations.json")
        with open(store, "w") as f:
            json.dump({"version": 1, "incantations": [{"id": "steve.hot_fireball", "player": "Steve",
                                                       "phrase": "hot fireball", "aliases": [],
                                                       "ability": "hot_fireball", "cooldown_s": 3}]}, f)
        env_file = os.path.join(self.dir, "vfg.env")
        with open(env_file, "w") as f:
            f.write("VFG_CLIENT_TOKEN=bridge-token-not-a-secret\n")
        feed = os.path.join(self.dir, "feed.log")
        journal = os.path.join(self.dir, "journal.log")
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY" and not k.startswith("VFG_")}
        env.update(NO_PROXY)
        env.update({"VFG_DB": db, "VFG_LIB": FAKE_LIB, "FAKE_FEED_LOG": feed, "VFG_JEV_URL": self.server.url,
                    "VFG_LOOKOUT": os.path.join(self.dir, "lookout.md"), "VFG_MENU": os.path.join(self.dir, "menu.md"),
                    "VFG_ENV_FILE": env_file, "TYPESAFE_API_KEY": KEY, "VFG_ENGINE_EXPORT": store,
                    "VFG_BRIDGE_PORT": str(bridge.server_address[1])})
        now = time.time()
        cast = lines.add(now, "Steve", "voice", "Hot fire ball.", peak=-8.0)
        castle = lines.add(now + 0.01, "Steve", "voice", "build me a castle", peak=-8.2)
        inside = lines.add(now + 0.02, "Steve", "voice", "ok stand back and build me a wall, hot fireball")
        with open(journal, "w") as out:
            p = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "jev_filter.py")], env=env,
                                 stdout=out, stderr=subprocess.STDOUT, cwd=ROOT)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        end = time.monotonic() + 5
        def fed():
            try:
                with open(feed, encoding="utf-8") as f:
                    return len(f.read().splitlines())
            except FileNotFoundError:
                return 0

        while (len(self.server.calls) < 5 or fed() < 2) and time.monotonic() < end:   # 3 checks, 2 routing calls
            time.sleep(0.01)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertEqual(bridge.got, [("/v1/jev/casts", "Bearer bridge-token-not-a-secret",
                                       {"line_id": cast, "phrase": "hot fireball"}),
                                      ("/v1/jev/casts", "Bearer bridge-token-not-a-secret",
                                       {"line_id": inside, "phrase": "hot fireball", "keep": True})])
        self.assertGreaterEqual(len(self.server.calls), 5)
        with open(feed, encoding="utf-8") as f:
            self.assertEqual([l.split(" | ", 1)[1] for l in f.read().splitlines()][:2],
                             ["build me a castle", "ok stand back and build me a wall, hot fireball"])
        self.assertEqual(conn.execute("SELECT dropped, cast_id FROM lines WHERE id = ?", (inside,)).fetchone(),
                         (None, "C2"))
        self.assertEqual(conn.execute("SELECT dropped, jev_class FROM lines WHERE id = ?", (cast,)).fetchone(),
                         ("cast", None))
        refs = [r[0] for r in conn.execute("SELECT ref FROM usage")]
        self.assertEqual(sorted(r for r in refs if not r.startswith("gather:")),
                         sorted(["cast:" + cast, "cast:" + castle, castle, "cast:" + inside, inside]))
        with open(journal, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("cast check on · at 0.50 or over, dropped at 0.70, a long line at 0.70 · the bridge on "
                      "127.0.0.1:%d"
                      % bridge.server_address[1], text)
        self.assertIn("cast-check %s Steve · long · hot fireball p 0.90 → fired, the line kept (routed first) · "
                      % inside,
                      text)
        self.assertIn("cast-check %s Steve · hot fireball p 0.90 → fired · " % cast, text)
        self.assertIn("cast-check %s Steve · no p 0.90 → routing · " % castle, text)
        self.assertNotIn("bridge-token-not-a-secret", text)
        self.assertNotIn(KEY, text)


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
        self.assertRegex(out, r"^action_oriented 0\.90 · act 0\.90, let down 0\.60 · \d+ms · \d+tok\n$")
        code, out, _ = self.judge("lol nice", raw=True)
        self.assertIn('"choice": "ignore_it"', out)
        self.assertNotIn(KEY, out)
        self.server.mode = "fail"
        code, _, err = self.judge("x")
        self.assertEqual(code, 1)
        self.assertIn("500", err)
        self.assertEqual(os.listdir(self.dir), [])

    def test_cast_prints_the_answer_and_writes_nothing(self):
        store = os.path.join(self.dir, "incantations.json")
        with open(store, "w") as f:
            json.dump({"version": 1, "incantations": [{"player": "Steve", "phrase": "ice beam", "aliases": [],
                                                       "ability": "ice_beam"}]}, f)
        cfg = Config(jev_url=self.server.url, menu=os.path.join(self.dir, "menu.md"), engine_export=store)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cast_once(cfg, KEY, "Ice beam!", "Steve"), 0)
            self.assertEqual(cast_once(cfg, KEY, "nice one", "Steve"), 0)
        got = out.getvalue().splitlines()
        self.assertRegex(got[0], r"^ice beam p 0\.90 → a cast · \d+ms · \d+tok$")
        self.assertRegex(got[1], r"^no p 0\.90 · \d+ms · \d+tok$")
        calls = len(self.server.calls)
        os.remove(store)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cast_once(cfg, KEY, "Ice beam!", "Steve"), 0)
        self.assertIn("Steve has no incantations", out.getvalue())
        self.assertEqual(len(self.server.calls), calls)
        self.assertEqual(os.listdir(self.dir), ["menu.md"] if os.path.exists(cfg.menu) else [])

    def test_the_key_from_the_environment_else_the_env_file(self):
        path = os.path.join(self.dir, "vfg.env")
        with open(path, "w") as f:
            f.write("VFG_CLIENT_TOKEN=abc\nexport TYPESAFE_API_KEY='%s'\n" % KEY)
        self.assertEqual(read_key(path, {}), (KEY, path))
        self.assertEqual(read_key(path, {"TYPESAFE_API_KEY": "other"}), ("other", "the environment"))
        self.assertEqual(read_key(path, {}, name="VFG_CLIENT_TOKEN"), ("abc", path))
        key, where = read_key(os.path.join(self.dir, "missing.env"), {})
        self.assertEqual(key, "")
        self.assertIn("unreadable", where)


if __name__ == "__main__":
    unittest.main()
