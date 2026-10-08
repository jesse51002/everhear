#!/usr/bin/env python3
"""A stand-in for the bridge's vfg_feed.py, copied to <box>/lib/vfg_feed.py.

    python3 <box>/lib/vfg_feed.py post <TYPE> "<fields>" "<text>"

Appends `TYPE#seq HH:MM:SS fields | text` to the feed, inserts the `feed` row
in the database when it exists, and prints the seq. The seq is one more than
the larger of the table's and the file's, so it is never 0. Like the real
writer, it finds its files by VFG_FEED and VFG_DB, defaulting to /opt/vfg.
"""
import fcntl
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

FEED = os.environ.get("VFG_FEED") or "/opt/vfg/feed.log"
DB = os.environ.get("VFG_DB") or "/opt/vfg/vfg.db"
SEQ_RE = re.compile(rb"^(LINE|GAME|ORCH|SYS)#(\d+) ")


def clean(text):
    text = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in text)
    return text if len(text) <= 500 else text[:499] + "…"


def last_seq():
    try:
        with open(FEED, "rb") as f:
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


def main(argv):
    if len(argv) != 5 or argv[1] != "post":
        sys.stderr.write("usage: vfg_feed.py post <TYPE> <fields> <text>\n")
        return 2
    typ, fields, text = argv[2], argv[3], argv[4]
    if typ not in ("LINE", "GAME", "ORCH", "SYS"):
        sys.stderr.write("vfg_feed: bad TYPE %r\n" % typ)
        return 1
    fields = " ".join(clean(fields).replace("|", "/").split())
    if typ == "ORCH" and not re.match(r"^T\d+ (progress|done|failed)$", fields):
        sys.stderr.write("vfg_feed: ORCH fields must match ^T\\d+ (progress|done|failed)$\n")
        return 1
    if typ == "SYS" and fields:
        sys.stderr.write("vfg_feed: SYS takes no fields\n")
        return 1
    now = time.time()
    at = datetime.fromtimestamp(now, timezone.utc)
    stamp = at.strftime("%H:%M:%S")
    iso = at.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (at.microsecond // 1000)
    with open(FEED + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        seq = last_seq()
        con = None
        if os.path.exists(DB):
            con = sqlite3.connect(DB, timeout=5)
            row = con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'feed'").fetchone()
            seq = max(seq, row[0] if row else 0)
        seq += 1
        line = "%s#%d %s %s| %s" % (typ, seq, stamp, fields + " " if fields else "", clean(text))
        if con is not None:
            with con:
                con.execute("INSERT INTO feed (seq, at, line) VALUES (?, ?, ?)", (seq, iso, line))
            con.close()
        fd = os.open(FEED, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.close(fd)
    print(seq)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
