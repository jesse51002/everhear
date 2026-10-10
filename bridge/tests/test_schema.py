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
            ("table", "casts"), ("index", "casts_player_started"),
        })
        self.assertEqual(self.query("PRAGMA journal_mode"), [("wal",)])
        self.assertEqual(self.query("PRAGMA user_version"), [(2,)])
        columns = [row[1] for row in self.query("PRAGMA table_info(usage)")]
        self.assertEqual(columns[-2:], ["cache_read_tokens", "cache_write_tokens"])
        columns = [row[1] for row in self.query("PRAGMA table_info(lines)")]
        self.assertEqual(columns[-2:], ["dropped", "cast_id"])
        columns = [row[1] for row in self.query("PRAGMA table_info(casts)")]
        self.assertEqual(columns, ["id", "at", "started_at", "ended_at", "player_name", "phrase",
                                   "heard", "layer", "incantation_id", "action", "copy", "status",
                                   "error", "line_id", "client_cast_id"])
        unique = {self.query("PRAGMA index_info(%s)" % row[1])[0][2]
                  for row in self.query("PRAGMA index_list(casts)") if row[2]}
        self.assertIn("client_cast_id", unique)

    def test_a_history_from_before_the_cache_columns_gets_them_on_start(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(CONTRACT_DDL)
        conn.execute("PRAGMA user_version=1")
        conn.execute("INSERT INTO usage (at, component, model, input_tokens, output_tokens)"
                     " VALUES ('2026-10-08T05:00:00.000Z', 'jev', 'jev-latest', 10, 0)")
        conn.commit()
        conn.close()
        for _ in range(2):
            result = run(["-m", "vfg_bridge", "--init-db"])
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.query("PRAGMA user_version"), [(2,)])
        self.assertEqual(self.query("SELECT input_tokens, cache_read_tokens, cache_write_tokens FROM usage"),
                         [(10, None, None)])
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO usage (at, component, model, input_tokens, output_tokens,"
                     " cache_read_tokens, cache_write_tokens)"
                     " VALUES ('2026-10-08T05:01:00.000Z', 'implementer', 'haiku', 5, 1, 900, 40)")
        conn.commit()
        conn.close()
        self.assertEqual(self.query("SELECT cache_read_tokens, cache_write_tokens FROM usage WHERE id = 2"),
                         [(900, 40)])

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
