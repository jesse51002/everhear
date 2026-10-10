"""The transcript history, /opt/vfg/vfg.db: section 4's schema, verbatim, and the bridge's inserts.

The bridge writes ``voice`` and ``chat`` rows of ``lines`` (never the jev_*
or fed_at columns), ``events``, and ``feed`` only through vfg_feed.  Every
write in the process goes through one connection behind one lock.
"""

import os
import sqlite3
import threading

from . import timeutil, ulid

# Section 4's DDL character for character, with IF NOT EXISTS added to each statement.
SCHEMA = """\
CREATE TABLE IF NOT EXISTS lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('direct_ask','context','not_useful')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX IF NOT EXISTS lines_at ON lines(at);
CREATE INDEX IF NOT EXISTS lines_player_at ON lines(player_name, at);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""

# Columns added since section 4's DDL, as (table, column, type): a fresh history and
# one made before them both get each, once, by ALTER TABLE on start.
COLUMNS = [
    ("usage", "cache_read_tokens", "INTEGER"),   # vfg usage add --cache-read
    ("usage", "cache_write_tokens", "INTEGER"),  # vfg usage add --cache-write
]

USER_VERSION = 2
BUSY_TIMEOUT_MS = 10000  # tests patch it low


def connect(path, busy_timeout_ms=None):
    """A connection in autocommit mode with the box's settings."""
    busy = BUSY_TIMEOUT_MS if busy_timeout_ms is None else busy_timeout_ms
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=busy / 1000.0, isolation_level=None,
                           check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=%d" % busy)
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def apply_schema(conn):
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.execute("PRAGMA journal_mode=WAL").fetchone()
    conn.execute("PRAGMA user_version=%d" % USER_VERSION)


def migrate(conn):
    """Add each of COLUMNS the history does not have yet; run on every start."""
    for table, column, kind in COLUMNS:
        have = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if column in have:
            continue
        try:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))
        except sqlite3.OperationalError:
            # another process added it between the look and the ALTER
            have = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
            if column not in have:
                raise


class DB:
    """The bridge's one connection, behind one lock."""

    def __init__(self, path, busy_timeout_ms=None):
        self.path = path
        self.lock = threading.Lock()
        self.conn = connect(path, busy_timeout_ms)

    def init(self):
        with self.lock:
            apply_schema(self.conn)

    def close(self):
        with self.lock:
            self.conn.close()

    def _in_transaction(self, fn):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(self.conn)
                self.conn.execute("COMMIT")
                return result
            except BaseException:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def insert_voice(self, at, ended_at, player_uuid, player_name, text, peak_dbfs, mean_dbfs,
                     client_line_id):
        """Store a spoken line once per client_line_id; return the id stored for it."""
        new_id = ulid.new()

        def run(conn):
            conn.execute(
                "INSERT INTO lines (id, at, ended_at, player_uuid, player_name, source, text,"
                " peak_dbfs, mean_dbfs, client_line_id)"
                " VALUES (?, ?, ?, ?, ?, 'voice', ?, ?, ?, ?)"
                " ON CONFLICT(client_line_id) DO NOTHING",
                (new_id, at, ended_at, player_uuid, player_name, text, peak_dbfs, mean_dbfs,
                 client_line_id))
            row = conn.execute("SELECT id FROM lines WHERE client_line_id = ?",
                               (client_line_id,)).fetchone()
            return row[0]

        return self._in_transaction(run)

    def last_console_at(self):
        """The latest ``at`` stored from the console (events and chat lines), or None."""
        with self.lock:
            row = self.conn.execute(
                "SELECT max(at) FROM (SELECT max(at) AS at FROM events"
                " UNION ALL SELECT max(at) FROM lines WHERE source = 'chat')").fetchone()
        return timeutil.parse_time(row[0]) if row and row[0] else None

    def known_players(self):
        """Every player name the history has seen, from events and spoken or typed lines."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT player_name FROM events WHERE player_name IS NOT NULL"
                " UNION SELECT player_name FROM lines WHERE source IN ('voice', 'chat')").fetchall()
        return [row[0] for row in rows]

    def insert_chat(self, at, player_name, text):
        new_id = ulid.new()
        with self.lock:
            self.conn.execute(
                "INSERT INTO lines (id, at, player_name, source, text) VALUES (?, ?, ?, 'chat', ?)",
                (new_id, at, player_name, text))
        return new_id

    def insert_event(self, at, kind, player_name, text):
        new_id = ulid.new()
        with self.lock:
            self.conn.execute(
                "INSERT INTO events (id, at, kind, player_name, text) VALUES (?, ?, ?, ?, ?)",
                (new_id, at, kind, player_name, text))
        return new_id
