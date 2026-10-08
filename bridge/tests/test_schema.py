"""The schema: section 4's DDL, in WAL mode, applied by --init-db as often as it is run."""

import sqlite3
import unittest

from vfg_bridge import db

from helpers import Box, run, subprocess_env

# Contracts section 4, copied from the page.
CONTRACT_DDL = """\
CREATE TABLE lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('direct_ask','context','not_useful')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE INDEX lines_player_at ON lines(player_name, at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX events_at ON events(at);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""


class TestSchema(Box):
    def test_ddl_is_the_contract_with_if_not_exists(self):
        self.assertEqual(db.SCHEMA.replace(" IF NOT EXISTS", ""), CONTRACT_DDL)

    def test_init_db_twice(self):
        for _ in range(2):
            result = run(["-m", "vfg_bridge", "--init-db"])
            self.assertEqual(result.returncode, 0, result.stderr)
        names = {(t, n) for t, n in self.query(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        self.assertEqual(names, {
            ("table", "lines"), ("table", "events"), ("table", "feed"), ("table", "usage"),
            ("index", "lines_at"), ("index", "lines_player_at"), ("index", "events_at"),
        })
        self.assertEqual(self.query("PRAGMA journal_mode"), [("wal",)])
        self.assertEqual(self.query("PRAGMA user_version"), [(1,)])

    def test_init_db_needs_no_token(self):
        result = run(["-m", "vfg_bridge", "--init-db"], env=subprocess_env({"VFG_CLIENT_TOKEN": ""}))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sources(self):
        self.make_db()
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO lines (id, at, player_name, source, text)"
                     " VALUES ('a', '2026-10-07T18:00:00.000Z', 'AI', 'ai', '(to Steve) hi')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO lines (id, at, player_name, source, text)"
                         " VALUES ('b', '2026-10-07T18:00:00.000Z', 'Steve', 'radio', 'x')")

    def test_connection_settings(self):
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone(), (10000,))
        self.assertEqual(conn.execute("PRAGMA synchronous").fetchone(), (1,))  # NORMAL

    def test_serving_without_a_token_exits_78(self):
        result = run(["-m", "vfg_bridge"], env=subprocess_env({"VFG_CLIENT_TOKEN": ""}))
        self.assertEqual(result.returncode, 78)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertIn("VFG_CLIENT_TOKEN", result.stderr)


if __name__ == "__main__":
    unittest.main()
