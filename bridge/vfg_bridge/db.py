"""The transcript history, /opt/vfg/vfg.db: section 4's schema, verbatim, and the bridge's inserts.

The bridge writes ``voice`` and ``chat`` rows of ``lines`` (never the jev_*
or fed_at columns), ``events``, ``casts``, and ``feed`` only through vfg_feed. It
creates ``tasks``, the brain's, and never writes it.
Every write in the process goes through one connection behind one lock.
"""

import os
import re
import sqlite3
import threading

from . import timeutil, ulid

# Section 4's DDL character for character, with IF NOT EXISTS added to each statement.
SCHEMA = """\
CREATE TABLE IF NOT EXISTS lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('action_oriented','conversational','context','not_relevant')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX IF NOT EXISTS lines_at ON lines(at);
CREATE INDEX IF NOT EXISTS lines_player_at ON lines(player_name, at);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer','brain','respond','planner','alias')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
"""

# Tables added since section 4's DDL, each made by IF NOT EXISTS on every start, so a history made
# before one gets it then, and nothing else changes.
# casts: one row per cast an incantation got, fired or not (status fired, cooldown, duplicate or
# failed; layer spotter or transcript).
# tasks: the brain's, one row per implementer task, written only through `vfg task set`; the bridge
# creates it and never writes it.
ADDED = """\
CREATE TABLE IF NOT EXISTS casts (id TEXT PRIMARY KEY, at TEXT NOT NULL, started_at TEXT,
  ended_at TEXT, player_name TEXT NOT NULL, phrase TEXT NOT NULL, heard TEXT, layer TEXT NOT NULL,
  incantation_id TEXT, action TEXT, copy TEXT, status TEXT NOT NULL, error TEXT, line_id TEXT,
  client_cast_id TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS casts_player_started ON casts(player_name, started_at);
CREATE TABLE IF NOT EXISTS tasks (name TEXT PRIMARY KEY, description TEXT NOT NULL, player TEXT,
  status TEXT NOT NULL CHECK (status IN ('open','done','failed')), line TEXT, brief TEXT,
  made TEXT, variables TEXT, session TEXT, result TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, closed_at TEXT);
"""

# Columns added since section 4's DDL, as (table, column, type): a fresh history and
# one made before them both get each, once, by ALTER TABLE on start.
COLUMNS = [
    ("usage", "cache_read_tokens", "INTEGER"),   # vfg usage add --cache-read
    ("usage", "cache_write_tokens", "INTEGER"),  # vfg usage add --cache-write
    ("usage", "ok", "INTEGER"),                  # a structured() call's row: 1 answered, 0 failed
    ("usage", "error", "TEXT"),                  # why it failed, short; NULL when it answered
    ("usage", "ms", "INTEGER"),                  # how long the call took, its retries included
    ("lines", "dropped", "TEXT"),                # 'cast': only an incantation; Jev never sees it
    ("lines", "cast_id", "TEXT"),                # the cast the line overlaps
    ("feed", "processed_at", "TEXT"),            # the brain's mark: vfg context mark
]

# CHECK lists changed since a history was made, as (table, column, the list now,
# {an old value: its new value}).  SQLite cannot alter a CHECK, so a history whose
# table still has another list gets the table rebuilt once, on start.
CHECKS = [
    ("lines", "jev_class", ("action_oriented", "conversational", "context", "not_relevant"),
     {"direct_ask": "action_oriented", "not_useful": "not_relevant"}),
    ("usage", "component", ("jev", "conversation", "orchestrator", "implementer", "brain", "respond",
                            "planner", "alias"),
     {}),
]

USER_VERSION = 4
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
    conn.executescript(ADDED)
    migrate(conn)
    conn.execute("PRAGMA journal_mode=WAL").fetchone()
    conn.execute("PRAGMA user_version=%d" % USER_VERSION)


def migrate(conn):
    """Add each of COLUMNS the history does not have yet, then bring each of CHECKS
    up to date; run on every start."""
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
    for table, column, values, renamed in CHECKS:
        rebuild_check(conn, table, column, values, renamed)


def _table_sql(conn, table):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                       (table,)).fetchone()
    return row[0] if row else None


def rebuild_check(conn, table, column, values, renamed=None):
    """Rebuild ``table`` so the CHECK on ``column`` allows ``values``, when its stored
    SQL still has another list; old values in ``renamed`` become their new ones.

    The new table is the old one's own SQL with the list changed, so every column
    added since, by COLUMNS or by anything else, keeps its type and constraints.
    Rows are copied by column name, then the table's indexes and triggers are made
    again.  One transaction; a table already up to date, or a fresh one, is left
    alone.  Answers whether it rebuilt.
    """
    check = "CHECK (%s IN (%s))" % (column, ",".join("'%s'" % v for v in values))
    found_re = re.compile(r"CHECK\s*\(\s*%s\s+IN\s*\(([^)]*)\)\s*\)" % column, re.I)
    conn.execute("BEGIN IMMEDIATE")
    try:
        sql = _table_sql(conn, table) or ""
        found = found_re.search(sql)
        if found is None or tuple(re.findall(r"'([^']*)'", found.group(1))) == tuple(values):
            conn.execute("COMMIT")
            return False
        new = table + "_rebuilt"
        create, n = re.subn(r"^CREATE TABLE\s+\"?%s\"?\s*\(" % table, "CREATE TABLE %s (" % new,
                            sql[:found.start()] + check + sql[found.end():])
        if n != 1:
            raise sqlite3.OperationalError("cannot read the CREATE TABLE of %s: %r" % (table, sql))
        conn.execute(create)
        names = [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
        picked = list(names)
        if renamed:
            whens = " ".join("WHEN '%s' THEN '%s'" % pair for pair in sorted(renamed.items()))
            picked[names.index(column)] = "CASE %s %s ELSE %s END" % (column, whens, column)
        conn.execute("INSERT INTO %s (%s) SELECT %s FROM %s"
                     % (new, ", ".join(names), ", ".join(picked), table))
        others = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name = ?"
                              " AND type IN ('index', 'trigger') AND sql IS NOT NULL",
                              (table,)).fetchall()
        seq = None
        if _table_sql(conn, "sqlite_sequence"):
            seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
        conn.execute("DROP TABLE %s" % table)
        conn.execute("ALTER TABLE %s RENAME TO %s" % (new, table))
        if seq is not None:
            # AUTOINCREMENT never hands out an id the old table already used
            if not conn.execute("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?",
                                (seq[0], table)).rowcount:
                conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, seq[0]))
        for (statement,) in others:
            conn.execute(statement)
        conn.execute("COMMIT")
        return True
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
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
                     client_line_id, dropped=None, cast_id=None, cast_ids=()):
        """Store a spoken line once per client_line_id; return the id stored for it.

        A new line also becomes the ``line_id`` of each of ``cast_ids`` that has none."""
        new_id = ulid.new()

        def run(conn):
            cur = conn.execute(
                "INSERT INTO lines (id, at, ended_at, player_uuid, player_name, source, text,"
                " peak_dbfs, mean_dbfs, client_line_id, dropped, cast_id)"
                " VALUES (?, ?, ?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(client_line_id) DO NOTHING",
                (new_id, at, ended_at, player_uuid, player_name, text, peak_dbfs, mean_dbfs,
                 client_line_id, dropped, cast_id))
            if cur.rowcount == 1 and cast_ids:
                conn.executemany("UPDATE casts SET line_id = ? WHERE id = ? AND line_id IS NULL",
                                 [(new_id, cid) for cid in cast_ids])
            row = conn.execute("SELECT id FROM lines WHERE client_line_id = ?",
                               (client_line_id,)).fetchone()
            return row[0]

        return self._in_transaction(run)

    def line_id(self, client_line_id):
        """The id stored for a client_line_id, or None when it is new."""
        with self.lock:
            row = self.conn.execute("SELECT id FROM lines WHERE client_line_id = ?",
                                    (client_line_id,)).fetchone()
        return row[0] if row else None

    # -- casts -------------------------------------------------------------------

    CAST_COLUMNS = ("id", "at", "started_at", "ended_at", "player_name", "phrase", "heard", "layer",
                    "incantation_id", "action", "copy", "status", "error", "line_id",
                    "client_cast_id")

    def insert_cast(self, cast):
        """Store a cast, a dict of CAST_COLUMNS (line_id and any other left out are NULL)."""
        values = [cast.get(k) for k in self.CAST_COLUMNS]
        with self.lock:
            self.conn.execute("INSERT INTO casts (%s) VALUES (%s)" % (
                ", ".join(self.CAST_COLUMNS), ", ".join("?" * len(self.CAST_COLUMNS))), values)

    def cast_by_client_id(self, client_cast_id):
        """The cast stored for a client_cast_id, as a dict, or None."""
        with self.lock:
            row = self.conn.execute("SELECT %s FROM casts WHERE client_cast_id = ?"
                                    % ", ".join(self.CAST_COLUMNS), (client_cast_id,)).fetchone()
        return dict(zip(self.CAST_COLUMNS, row)) if row else None

    def overlapping_casts(self, player_name, lo, hi, since):
        """A player's casts whose window meets [lo, hi], of those started at ``since`` or later,
        as dicts, oldest first. Every time is format_time's, so text compares as time."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT %s FROM casts WHERE player_name = ? AND started_at >= ? AND started_at <= ?"
                " AND ended_at >= ? ORDER BY started_at, id" % ", ".join(self.CAST_COLUMNS),
                (player_name, since, hi, lo)).fetchall()
        return [dict(zip(self.CAST_COLUMNS, row)) for row in rows]

    def unjudged_lines(self, player_name, lo, hi, since):
        """(id, text) of a player's voice lines whose window meets [lo, hi], said at ``since``
        or later, that are neither judged by Jev nor dropped."""
        with self.lock:
            return self.conn.execute(
                "SELECT id, text FROM lines WHERE player_name = ? AND at >= ? AND at <= ?"
                " AND ended_at >= ? AND source = 'voice' AND jev_class IS NULL AND dropped IS NULL"
                " ORDER BY at, id", (player_name, since, hi, lo)).fetchall()

    def drop_line(self, line_id, cast_id):
        """Drop a line Jev has not judged, for a cast that came after it; the cast's line_id
        becomes it. False when Jev judged it first."""
        def run(conn):
            n = conn.execute("UPDATE lines SET dropped = 'cast', cast_id = ? WHERE id = ?"
                             " AND jev_class IS NULL", (cast_id, line_id)).rowcount
            if n:
                conn.execute("UPDATE casts SET line_id = ? WHERE id = ? AND line_id IS NULL",
                             (line_id, cast_id))
            return bool(n)

        return self._in_transaction(run)

    def last_fired(self, player_name, since):
        """{incantation_id: the ``at`` it last fired} of a player's casts started at ``since`` or later."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT incantation_id, max(at) FROM casts WHERE player_name = ? AND started_at >= ?"
                " AND status = 'fired' GROUP BY incantation_id", (player_name, since)).fetchall()
        return dict(rows)

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
