"""Shared stand-ins for the tests: the database the bridge would make, rows the
bridge and `vfg say` would write, a clock the tests move by hand, and the
stand-in feed writer."""
import importlib.util
import logging
import os
import sqlite3
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from jev_clock import iso  # noqa: E402

# The journal is the service's stdout; under the tests it stays quiet unless a
# test listens for it with assertLogs.
logging.getLogger("vfg-jev").addHandler(logging.NullHandler())

SCHEMA = os.path.join(TESTS, "schema.sql")
FAKE_LIB = os.path.join(TESTS, "fake_lib")
T0 = 1791397351.0   # 2026-10-07T18:22:31.000Z


def make_db(path):
    """A database built from §4's schema, WAL on — as the bridge makes it."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


class Lines:
    """Writes `lines` rows the way the bridge (voice, chat) and `vfg say` (ai) would."""

    def __init__(self, conn):
        self.conn = conn
        self.n = 0

    def add(self, at, player, source, text, peak=None, **verdict):
        self.n += 1
        line_id = "01L%07d" % self.n
        if source == "ai":
            player = "AI"
        cols = {"id": line_id, "at": iso(at) if isinstance(at, (int, float)) else at,
                "player_name": player, "source": source, "text": text, "peak_dbfs": peak}
        if source == "voice":
            cols["ended_at"] = cols["at"]
            cols["mean_dbfs"] = None if peak is None else peak - 15
        cols.update(verdict)
        names = ", ".join(cols)
        self.conn.execute("INSERT INTO lines (%s) VALUES (%s)" % (names, ",".join("?" * len(cols))),
                          list(cols.values()))
        return line_id

    def get(self, line_id):
        self.conn.row_factory = sqlite3.Row
        try:
            return dict(self.conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone())
        finally:
            self.conn.row_factory = None

    def usage(self):
        self.conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in self.conn.execute("SELECT * FROM usage ORDER BY id")]
        finally:
            self.conn.row_factory = None


class FakeClock:
    """Moved by hand. Kept in whole milliseconds, so 9.9 s is 9.9 s and not
    9.900000095 after a few additions."""

    def __init__(self, t=T0):
        self.ms = int(round(t * 1000))

    def time(self):
        return self.ms / 1000.0

    def advance(self, seconds):
        self.ms += int(round(seconds * 1000))


def load_fake_feed():
    """The stand-in `vfg_feed` module, loaded fresh."""
    spec = importlib.util.spec_from_file_location("vfg_feed", os.path.join(FAKE_LIB, "vfg_feed.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tempdir(test):
    d = tempfile.TemporaryDirectory(prefix="vfg-jev-test-")
    test.addCleanup(d.cleanup)
    return d.name
