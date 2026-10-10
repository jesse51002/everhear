"""Stand-in for the bridge's `vfg_feed` (docs/CONTRACTS.md §7), for the tests.

Same API as the real writer:

    append(type, fields, text, at=None) -> seq
    append_batch(entries) -> [seq]        each entry a (type, fields, text, at) tuple
    python3 vfg_feed.py post <TYPE> "<fields>" "<text>"   prints the seq

It cleans as the real one does (newlines and other control characters to
spaces; text at most 500 characters, the last an ellipsis; `|` in fields to
`/`), takes a file lock, and appends `TYPE#seq HH:MM:SS fields | text` —
`HH:MM:SS` in UTC from `at`, the moment the entry describes (an ISO-8601 string
with a zone, or an aware datetime; default now) — to `$FAKE_FEED_LOG`, keeping
its own seq (one more than the last in the file). When `$FAKE_FEED_DB` is set
it also inserts the `feed` row there, as the real one does into `vfg.db`.

It is STRICTER than the real writer in one place: LINE fields that do not
match §7's grammar raise ValueError here (the real one warns and writes), so a
slip in the filter's fields fails a test instead of passing quietly.
"""
import datetime as dt
import fcntl
import os
import re
import sqlite3
import sys

TYPES = ("LINE", "GAME", "AI", "SYS")
TEXT_MAX = 500
UTC = dt.timezone.utc

LINE_RE = re.compile(r"^[A-Za-z0-9_]{1,16} (voice|chat) (action_oriented|conversational|context) "
                     r"(\d\.\d\d|-) (-?\d+dB|-)( was=not_relevant)?$")
GAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31} ([A-Za-z0-9_]{1,16}|-)$")
AI_RE = re.compile(r"^(say|action|task|result|note) (T\d+|-)$")
TIME_RE = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$")
_SEQ = re.compile(r"^[A-Z]+#(\d+) ")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _plain(value):
    value = "" if value is None else str(value)
    return _CONTROL.sub(" ", value.replace("\r\n", " "))


def clean_text(text):
    text = _plain(text)
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1] + "…"


def clean_fields(fields):
    return re.sub(" {2,}", " ", _plain(fields).replace("|", "/")).strip()


def _when(at):
    if at is None:
        return dt.datetime.now(UTC)
    if isinstance(at, dt.datetime):
        if at.tzinfo is None:
            raise ValueError("at: a naive datetime")
        return at.astimezone(UTC)
    if not isinstance(at, str):
        raise ValueError("at: must be None, an ISO-8601 string or an aware datetime")
    m = TIME_RE.match(at)
    if not m:
        raise ValueError("at: not an ISO-8601 time with a zone: %r" % at[:40])
    zone = "+00:00" if m.group(4) == "Z" else m.group(4)
    frac = ((m.group(3) or "0") + "000000")[:6]
    return dt.datetime.fromisoformat("%sT%s.%s%s" % (m.group(1), m.group(2), frac, zone)).astimezone(UTC)


def _prepare(entry):
    entry = tuple(entry)
    if len(entry) == 3:
        entry += (None,)
    if len(entry) != 4:
        raise ValueError("an entry is (type, fields, text, at)")
    kind, fields, text, at = entry
    if kind not in TYPES:
        raise ValueError("unknown feed type %r" % (kind,))
    fields = clean_fields(fields)
    rule = {"LINE": LINE_RE, "GAME": GAME_RE, "AI": AI_RE}.get(kind)
    if rule is not None and not rule.match(fields):
        raise ValueError("%s fields %r do not match §7" % (kind, fields))
    if kind == "SYS" and fields:
        raise ValueError("SYS takes no fields")
    return kind, fields, clean_text(text), _when(at)


def _last_seq(f):
    f.seek(0)
    last = 0
    for raw in f:
        m = _SEQ.match(raw)
        if m:
            last = int(m.group(1))
    return last


def append_batch(entries):
    prepared = [_prepare(e) for e in entries]
    if not prepared:
        return []
    path = os.environ.get("FAKE_FEED_LOG")
    if not path:
        raise RuntimeError("FAKE_FEED_LOG is not set")
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            seq = _last_seq(f)
            out, seqs, rows = [], [], []
            for kind, fields, text, when in prepared:
                seq += 1
                head = "%s#%d %s" % (kind, seq, when.strftime("%H:%M:%S"))
                line = "%s %s | %s" % (head, fields, text) if fields else "%s | %s" % (head, text)
                out.append(line + "\n")
                seqs.append(seq)
                rows.append((seq, when.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (when.microsecond // 1000), line))
            f.seek(0, os.SEEK_END)
            f.write("".join(out))
            f.flush()
            db = os.environ.get("FAKE_FEED_DB")
            if db:
                conn = sqlite3.connect(db, timeout=5)
                with conn:
                    conn.executemany("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", rows)
                conn.close()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return seqs


def append(type, fields, text, at=None):  # noqa: A002 -- the contract names it `type`
    return append_batch([(type, fields, text, at)])[0]


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "post":
        print('usage: vfg_feed.py post <TYPE> "<fields>" "<text>"', file=sys.stderr)
        sys.exit(2)
    print(append(sys.argv[2], sys.argv[3], sys.argv[4]))
