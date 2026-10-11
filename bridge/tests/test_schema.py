"""The schema: section 4's DDL, in WAL mode, applied as often as the bridge starts, and
a history made before the brain brought up to date."""

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
  jev_class TEXT CHECK (jev_class IN ('action_oriented','conversational','context','not_relevant')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE INDEX lines_player_at ON lines(player_name, at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX events_at ON events(at);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer','brain','respond','planner','alias')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""

# The same tables as a box made them before the brain: Jev's three classes, four components.
OLD_DDL = (CONTRACT_DDL
           .replace("'action_oriented','conversational','context','not_relevant'",
                    "'direct_ask','context','not_useful'")
           .replace(",'brain','respond','planner','alias'", ""))

# usage as the brain's box made it, before the one log: six components, the cache columns added.
BRAIN_USAGE = ("CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,\n"
               "  component TEXT NOT NULL CHECK (component IN "
               "('jev','conversation','orchestrator','implementer','brain','respond')),\n"
               "  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT, "
               "cache_read_tokens INTEGER, cache_write_tokens INTEGER)")

AT = "2026-10-08T05:00:00.000Z"


class TestSchema(Box):
    def test_ddl_is_the_contract_with_if_not_exists(self):
        self.assertEqual(db.SCHEMA.replace(" IF NOT EXISTS", ""), CONTRACT_DDL)

    def test_init_db_needs_no_token_and_applies_again_as_a_no_op(self):
        result = run(["-m", "vfg_bridge", "--init-db"], env=subprocess_env({"VFG_CLIENT_TOKEN": ""}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.make_db()                                   # the bridge starting again
        names = {(t, n) for t, n in self.query(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        self.assertEqual(names, {
            ("table", "lines"), ("table", "events"), ("table", "feed"), ("table", "usage"),
            ("index", "lines_at"), ("index", "lines_player_at"), ("index", "events_at"),
            ("table", "casts"), ("index", "casts_player_started"),
        })
        self.assertEqual(self.query("PRAGMA journal_mode"), [("wal",)])
        self.assertEqual(self.query("PRAGMA user_version"), [(3,)])
        columns = [row[1] for row in self.query("PRAGMA table_info(usage)")]
        self.assertEqual(columns[-5:], ["cache_read_tokens", "cache_write_tokens", "ok", "error", "ms"])
        self.assertEqual([row[1] for row in self.query("PRAGMA table_info(feed)")],
                         ["seq", "at", "line", "processed_at"])
        columns = [row[1] for row in self.query("PRAGMA table_info(lines)")]
        self.assertEqual(columns[-2:], ["dropped", "cast_id"])
        columns = [row[1] for row in self.query("PRAGMA table_info(casts)")]
        self.assertEqual(columns, ["id", "at", "started_at", "ended_at", "player_name", "phrase",
                                   "heard", "layer", "incantation_id", "action", "copy", "status",
                                   "error", "line_id", "client_cast_id"])
        unique = {self.query("PRAGMA index_info(%s)" % row[1])[0][2]
                  for row in self.query("PRAGMA index_list(casts)") if row[2]}
        self.assertIn("client_cast_id", unique)

    def test_a_history_from_before_the_brain_is_rebuilt_once_keeping_every_column(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.executescript(OLD_DDL)
        conn.execute("PRAGMA user_version=2")
        # what another branch may have added since: columns, with constraints, and an index
        conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        conn.execute("ALTER TABLE lines ADD COLUMN cast_id TEXT REFERENCES casts(id)")
        conn.execute("CREATE INDEX lines_cast ON lines(cast_id)")
        for n, (cls, dropped) in enumerate((("direct_ask", None), ("not_useful", AT),
                                            ("context", None), (None, None)), 1):
            conn.execute("INSERT INTO lines (id, at, player_name, source, text, client_line_id,"
                         " jev_class, dropped, cast_id) VALUES (?, ?, 'Steve', 'voice', ?, ?, ?, ?, ?)",
                         ("L%d" % n, AT, "line %d" % n, "c%d" % n, cls, dropped, "C1" if dropped else None))
        conn.execute("INSERT INTO usage (id, at, component) VALUES (7, ?, 'orchestrator')", (AT,))
        conn.execute("UPDATE sqlite_sequence SET seq = 9 WHERE name = 'usage'")
        conn.execute("INSERT INTO feed (at, line) VALUES (?, 'SYS#1 05:00:00 | up')", (AT,))
        conn.close()

        database = self.make_db()
        for table, column, values, renamed in db.CHECKS:  # a second start changes nothing more
            self.assertFalse(db.rebuild_check(database.conn, table, column, values, renamed))
        self.assertEqual(self.query("SELECT id, jev_class, dropped, cast_id FROM lines ORDER BY id"), [
            ("L1", "action_oriented", None, None), ("L2", "not_relevant", AT, "C1"),
            ("L3", "context", None, None), ("L4", None, None, None)])
        # the old table's SQL with the new list, every added column kept (SQLite's rename quotes the name)
        self.assertEqual(self.query("SELECT sql FROM sqlite_master WHERE name = 'lines'")[0][0]
                         .replace('"lines"', "lines"),
                         CONTRACT_DDL.split(";")[0].replace(
                             "fed_at TEXT)", "fed_at TEXT, dropped TEXT, cast_id TEXT REFERENCES casts(id))"))
        self.assertEqual({n for (n,) in self.query("SELECT name FROM sqlite_master WHERE type = 'index'"
                                                   " AND tbl_name = 'lines' AND sql IS NOT NULL")},
                         {"lines_at", "lines_player_at", "lines_cast"})
        self.assertEqual(self.query("PRAGMA user_version"), [(3,)])
        self.assertEqual(self.query("SELECT seq, processed_at FROM feed"), [(1, None)])

        conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO lines (id, at, player_name, source, text, jev_class)"
                     " VALUES ('L5', ?, 'Alex', 'chat', 'thanks AI', 'conversational')", (AT,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO lines (id, at, player_name, source, text, jev_class)"
                         " VALUES ('L6', ?, 'Alex', 'chat', 'x', 'direct_ask')", (AT,))
        with self.assertRaises(sqlite3.IntegrityError):  # client_line_id is still unique
            conn.execute("INSERT INTO lines (id, at, player_name, source, text, client_line_id)"
                         " VALUES ('L7', ?, 'Alex', 'chat', 'x', 'c1')", (AT,))
        cur = conn.execute("INSERT INTO usage (at, component) VALUES (?, 'brain')", (AT,))
        self.assertEqual(cur.lastrowid, 10)              # after the old table's last id, not reused
        conn.execute("INSERT INTO usage (at, component) VALUES (?, 'respond')", (AT,))
        self.assertEqual(self.query("SELECT id, component FROM usage ORDER BY id"),
                         [(7, "orchestrator"), (10, "brain"), (11, "respond")])

    def test_usage_from_before_the_one_log_gets_the_call_columns_and_the_new_components(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.executescript(CONTRACT_DDL.replace(CONTRACT_DDL[CONTRACT_DDL.index("CREATE TABLE usage"):],
                                                BRAIN_USAGE + ";\n"))
        conn.execute("INSERT INTO usage (id, at, component, model, cost_usd, ref, cache_read_tokens)"
                     " VALUES (4, ?, 'brain', 'openai/gpt-6.1-sol', 0.0123, '#5', 9000)", (AT,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO usage (at, component) VALUES (?, 'planner')", (AT,))
        conn.close()

        database = self.make_db()
        for table, column, values, renamed in db.CHECKS:  # a second start changes nothing more
            self.assertFalse(db.rebuild_check(database.conn, table, column, values, renamed))
        self.assertEqual([row[1] for row in self.query("PRAGMA table_info(usage)")],
                         ["id", "at", "component", "model", "input_tokens", "output_tokens", "cost_usd", "ref",
                          "cache_read_tokens", "cache_write_tokens", "ok", "error", "ms"])
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(conn.close)
        for component, ok, error in (("planner", 0, "no answer from m within 20 s"), ("alias", 1, None)):
            conn.execute("INSERT INTO usage (at, component, model, cost_usd, ref, ok, error, ms)"
                         " VALUES (?, ?, 'm', 0.001, 'r', ?, ?, 840)", (AT, component, ok, error))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO usage (at, component) VALUES (?, 'dispatcher')", (AT,))
        self.assertEqual(self.query("SELECT id, component, cost_usd, cache_read_tokens, ok, error, ms FROM usage"
                                    " ORDER BY id"),
                         [(4, "brain", 0.0123, 9000, None, None, None),
                          (5, "planner", 0.001, None, 0, "no answer from m within 20 s", 840),
                          (6, "alias", 0.001, None, 1, None, 840)])

    def test_a_fresh_history_is_never_rebuilt(self):
        database = self.make_db()
        for table, column, values, renamed in db.CHECKS:
            self.assertFalse(db.rebuild_check(database.conn, table, column, values, renamed))

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
