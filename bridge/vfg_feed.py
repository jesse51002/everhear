#!/usr/bin/env python3
"""vfg_feed -- the one locked writer of the feed (contracts section 7).

Module (import it once /opt/vfg/lib is on sys.path):

    append(type, fields, text, at=None) -> seq
    append_batch(entries) -> [seq]          entries: (type, fields, text, at) tuples

Command line:

    python3 /opt/vfg/lib/vfg_feed.py post <TYPE> "<fields>" "<text>"     prints the seq

Every line goes to /opt/vfg/feed.log as ``TYPE#seq HH:MM:SS fields | text`` and
to the ``feed`` table of /opt/vfg/vfg.db, under an exclusive flock on
feed.log.lock.  Paths come from VFG_DB and VFG_FEED, read on every call.

Python 3.9, standard library only; imports nothing from vfg_bridge.
"""

import datetime as _dt
import fcntl
import os
import re
import sqlite3
import sys
import urllib.parse

__all__ = [
    "append", "append_batch", "TYPES",
    "parse_time", "format_time", "utcnow", "clean_text", "clean_fields",
]

TYPES = ("LINE", "GAME", "AI", "SYS")

DEFAULT_DB = "/opt/vfg/vfg.db"
DEFAULT_FEED = "/opt/vfg/feed.log"

TEXT_MAX = 500          # characters, the last one an ellipsis when cut
TAIL_BYTES = 4096       # how much of feed.log is read for its last seq
WRITE_ATTEMPTS = 3      # one write, then two more tries
BUSY_TIMEOUT_MS = 10000

UTC = _dt.timezone.utc

# Section 7's field grammar, per type.  GAME takes any kind: the bridge's, and
# what else posts one.  AI's second field is a task's name (galick_gun_styrk), or -.
GAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31} ([A-Za-z0-9_]{1,16}|-)$")
AI_RE = re.compile(r"^(say|action|task|result|note) ([a-z][a-z0-9_]{1,31}|-)$")
LINE_RE = re.compile(
    r"^[A-Za-z0-9_]{1,16} (voice|chat) (to_ai|context) (\d\.\d\d|-) (-?\d+dB|-)"
    r"( was=not_relevant)?$"
)

# An incoming time: what a Java Instant prints (0-9 fraction digits), or any offset.
TIME_RE = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$"
)

_SEQ_RE = re.compile(rb"^[A-Z]+#(\d+) ")  # any type, a retired one too
# C0, DEL, C1, and the two Unicode line separators.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_SPACES_RE = re.compile(" {2,}")

_warned_line = False


# --- times ------------------------------------------------------------------

def utcnow():
    """Now, as an aware UTC datetime."""
    return _dt.datetime.now(UTC)


def parse_time(value):
    """Read an ISO-8601 time with a zone into an aware UTC datetime.

    Accepts ``YYYY-MM-DDTHH:MM:SS[.f{1,9}](Z|+HH:MM|-HH:MM)``; anything else
    raises ValueError.  Fractions past microseconds are dropped.
    """
    m = TIME_RE.fullmatch(value) if isinstance(value, str) else None
    if m is None:
        raise ValueError("not an ISO-8601 time with a zone: %r" % (value,))
    year, month, day, hour, minute, second, frac, zone = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if zone == "Z":
        tz = UTC
    else:
        hh, mm = int(zone[1:3]), int(zone[4:6])
        if hh > 23 or mm > 59:
            raise ValueError("not a valid zone offset: %r" % (value,))
        delta = _dt.timedelta(hours=hh, minutes=mm)
        tz = _dt.timezone(-delta if zone[0] == "-" else delta)
    try:
        moment = _dt.datetime(int(year), int(month), int(day), int(hour), int(minute),
                              int(second), micro, tzinfo=tz)
    except ValueError:
        raise ValueError("not a valid time: %r" % (value,)) from None
    return moment.astimezone(UTC)


def format_time(moment):
    """Write an aware datetime as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` in UTC."""
    moment = moment.astimezone(UTC)
    return "%s.%03dZ" % (moment.strftime("%Y-%m-%dT%H:%M:%S"), moment.microsecond // 1000)


def _resolve_at(at, now):
    if at is None:
        return now
    if isinstance(at, str):
        return parse_time(at)
    if isinstance(at, _dt.datetime):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at: a naive datetime; give it a timezone")
        return at.astimezone(UTC)
    raise ValueError("at: must be None, an ISO-8601 string or an aware datetime, not %s"
                     % type(at).__name__)


# --- cleaning and checking --------------------------------------------------

def _plain(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    # Lone surrogates (from undecodable argv bytes) cannot be stored or written.
    value = value.encode("utf-8", "replace").decode("utf-8")
    value = value.replace("\r\n", " ")
    return _CONTROL_RE.sub(" ", value)


def clean_text(text):
    """Newlines, tabs and other control characters to spaces; 500 characters at most."""
    text = _plain(text)
    if len(text) > TEXT_MAX:
        text = text[:TEXT_MAX - 1] + "\u2026"
    return text


def clean_fields(fields):
    """As clean_text, plus ``|`` to ``/``, runs of spaces to one, ends trimmed."""
    fields = _plain(fields).replace("|", "/")
    return _SPACES_RE.sub(" ", fields).strip()


def _check(type_, fields):
    if type_ == "GAME":
        if not GAME_RE.fullmatch(fields):
            raise ValueError("GAME fields must match %s, got %r" % (GAME_RE.pattern, fields))
    elif type_ == "AI":
        if not AI_RE.fullmatch(fields):
            raise ValueError("AI fields must match %s, got %r" % (AI_RE.pattern, fields))
    elif type_ == "SYS":
        if fields:
            raise ValueError("SYS takes no fields, got %r" % (fields,))
    elif not LINE_RE.fullmatch(fields):
        # The Jev filter formats LINE: warn, but never silence speech over a slip.
        global _warned_line
        if not _warned_line:
            _warned_line = True
            sys.stderr.write("vfg_feed: warning: LINE fields %r do not match %s; written anyway\n"
                             % (fields, LINE_RE.pattern))
            sys.stderr.flush()


def _prepare(entry, now):
    entry = tuple(entry)
    if len(entry) == 3:
        type_, fields, text = entry
        at = None
    elif len(entry) == 4:
        type_, fields, text, at = entry
    else:
        raise ValueError("an entry is (type, fields, text, at), got %d items" % len(entry))
    if type_ not in TYPES or not isinstance(type_, str):
        raise ValueError("type must be one of %s, got %r" % (", ".join(TYPES), type_))
    fields = clean_fields(fields)
    _check(type_, fields)
    return type_, fields, clean_text(text), _resolve_at(at, now)


def _render(type_, seq, at, fields, text):
    hms = at.strftime("%H:%M:%S")
    if fields:
        return "%s#%d %s %s | %s" % (type_, seq, hms, fields, text)
    return "%s#%d %s | %s" % (type_, seq, hms, text)


# --- writing ----------------------------------------------------------------

def _paths():
    return (os.environ.get("VFG_DB") or DEFAULT_DB,
            os.environ.get("VFG_FEED") or DEFAULT_FEED)


def _feed_tail(path):
    """(last seq in the file's last 4 KiB, whether the file ends in a newline)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return 0, True
    try:
        size = os.fstat(fd).st_size
        start = max(0, size - TAIL_BYTES)
        data = os.pread(fd, size - start, start) if size else b""
    finally:
        os.close(fd)
    pieces = data.split(b"\n")
    if start > 0:
        pieces = pieces[1:]  # the first piece may be the end of a longer line
    best = 0
    for piece in pieces:
        m = _SEQ_RE.match(piece)
        if m:
            best = max(best, int(m.group(1)))
    return best, (not data or data.endswith(b"\n"))


def _db_last_seq(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'feed'").fetchone()
    if row is None:
        raise sqlite3.OperationalError(
            "vfg.db has no feed table; start vfg-bridge or run python3 -m vfg_bridge --init-db")
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'feed'").fetchone()
    if row is None:
        row = conn.execute("SELECT max(seq) FROM feed").fetchone()
    return int(row[0] or 0)


def _write_all(fd, data):
    view = memoryview(data)
    failures = 0
    while view:
        try:
            n = os.write(fd, view)
        except OSError:
            failures += 1
            if failures >= WRITE_ATTEMPTS:
                raise
            continue
        if n <= 0:
            failures += 1
            if failures >= WRITE_ATTEMPTS:
                raise OSError("feed.log: write made no progress")
            continue
        view = view[n:]


def _write(prepared):
    db_path, feed_path = _paths()
    cloexec = getattr(os, "O_CLOEXEC", 0)
    lock_fd = os.open(feed_path + ".lock", os.O_RDWR | os.O_CREAT | cloexec, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        uri = "file:%s?mode=rw" % urllib.parse.quote(os.path.abspath(db_path))
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000.0,
                               isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                file_seq, ends_clean = _feed_tail(feed_path)
                first = max(_db_last_seq(conn), file_seq) + 1
                rows, lines = [], []
                for i, (type_, fields, text, at) in enumerate(prepared):
                    seq = first + i
                    line = _render(type_, seq, at, fields, text)
                    rows.append((seq, format_time(at), line))
                    lines.append(line)
                conn.executemany("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", rows)
                # The brain's own records are processed as they are written: only it writes them.
                ai = [(format_time(utcnow()), row[0]) for row, entry in zip(rows, prepared) if entry[0] == "AI"]
                if ai:
                    try:
                        conn.executemany("UPDATE feed SET processed_at = ? WHERE seq = ?", ai)
                    except sqlite3.OperationalError:
                        pass  # a history from before processed_at, which the bridge adds on start
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()
        data = "".join(line + "\n" for line in lines)
        if not ends_clean:
            data = "\n" + data  # a torn last line never swallows ours
        fd = os.open(feed_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | cloexec, 0o644)
        try:
            _write_all(fd, data.encode("utf-8"))
        finally:
            os.close(fd)
        return [first + i for i in range(len(prepared))]
    finally:
        os.close(lock_fd)  # releases the flock


def append_batch(entries):
    """Append (type, fields, text, at) entries in one go; return their seqs, consecutive."""
    entries = list(entries)
    if not entries:
        return []
    now = utcnow()
    prepared = [_prepare(entry, now) for entry in entries]
    return _write(prepared)


def append(type, fields, text, at=None):  # noqa: A002 -- the contract names it `type`
    """Append one line; return its seq."""
    return append_batch([(type, fields, text, at)])[0]


# --- command line -----------------------------------------------------------

USAGE = 'usage: vfg_feed.py post <TYPE> "<fields>" "<text>"'


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 4 or argv[0] != "post":
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        seq = append(argv[1], argv[2], argv[3])
    except Exception as exc:  # one line on stderr, whatever it was
        message = " ".join(str(exc).split()) or type(exc).__name__
        sys.stderr.write("vfg_feed: %s\n" % message)
        return 1
    sys.stdout.write("%d\n" % seq)
    return 0


if __name__ == "__main__":
    sys.exit(main())
