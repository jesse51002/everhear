#!/usr/bin/env python3
"""A stand-in for the bridge's vfg_feed.py, copied to <box>/lib/vfg_feed.py.

    python3 <box>/lib/vfg_feed.py post <TYPE> "<fields>" "<text>"     prints the seq
    import vfg_feed; vfg_feed.append(type, fields, text, at=None) -> seq

Appends `TYPE#seq HH:MM:SS fields | text` to the feed, inserts the `feed` row in the database
when it exists, and returns the seq: one more than the larger of the table's and the file's,
so it is never 0. Like the real writer, it finds its files by VFG_FEED and VFG_DB, read on
every call, defaulting to /opt/vfg; and it takes LINE, GAME, AI and SYS, AI's fields
`<say|action|task|result|note> <task|->`.
"""
import fcntl
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

TYPES = ("LINE", "GAME", "AI", "SYS")
AI_RE = re.compile(r"^(say|action|task|result|note) (T\d+|-)$")
SEQ_RE = re.compile(rb"^([A-Z]+)#(\d+) ")


def clean(text):
    text = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in text)
    return text if len(text) <= 500 else text[:499] + "…"


def last_seq(feed):
    try:
        with open(feed, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read()
    except OSError:
        return 0
    best = 0
    for line in tail.split(b"\n"):
        m = SEQ_RE.match(line)
        if m:
            best = max(best, int(m.group(2)))
    return best


def append(type, fields, text, at=None):  # noqa: A002 -- the contract names it `type`
    if type not in TYPES:
        raise ValueError("type must be one of %s, got %r" % (", ".join(TYPES), type))
    fields = " ".join(clean(fields).replace("|", "/").split())
    if type == "AI" and not AI_RE.match(fields):
        raise ValueError("AI fields must match %s, got %r" % (AI_RE.pattern, fields))
    if type == "SYS" and fields:
        raise ValueError("SYS takes no fields, got %r" % fields)
    feed = os.environ.get("VFG_FEED") or "/opt/vfg/feed.log"
    db = os.environ.get("VFG_DB") or "/opt/vfg/vfg.db"
    if at is None:
        moment = datetime.now(timezone.utc)
    else:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)
    iso = moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)
    with open(feed + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        seq = last_seq(feed)
        con = None
        if os.path.exists(db):
            con = sqlite3.connect(db, timeout=5)
            row = con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'feed'").fetchone()
            seq = max(seq, row[0] if row else 0)
        seq += 1
        line = "%s#%d %s %s| %s" % (type, seq, moment.strftime("%H:%M:%S"), fields + " " if fields else "",
                                    clean(text))
        if con is not None:
            with con:
                con.execute("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", (seq, iso, line))
            con.close()
        fd = os.open(feed, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.close(fd)
    return seq


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 4 or argv[0] != "post":
        sys.stderr.write("usage: vfg_feed.py post <TYPE> <fields> <text>\n")
        return 2
    try:
        seq = append(argv[1], argv[2], argv[3])
    except ValueError as e:
        sys.stderr.write("vfg_feed: %s\n" % e)
        return 1
    print(seq)
    return 0


if __name__ == "__main__":
    sys.exit(main())
