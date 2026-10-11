# context.py: vfg context and vfg context mark, the brain's reads of the feed table (and with
# --json, of the tasks table). ARGS:
#   {"vfg": "/opt/vfg", "op": "read", "since": iso, "json": false}
#   {"vfg": "/opt/vfg", "op": "mark", "seqs": [812, 813]}
# read prints the feed rows at or after `since`, oldest first: each line as it is, or with
# json one {"seq", "at", "line", "processed_at"} object per line, then {"tasks": [every row of the
# tasks table, oldest created first, as tasks.py's list --json prints them]}, then {"last_seq": N},
# the feed's highest seq (0 for an empty feed), read first, so every row past it is a row the
# brain's `vfg watch --after N` prints, and none is missed or read twice. mark sets processed_at to
# now on the rows named whose processed_at is NULL, in one transaction, and prints how many.
# processed_at is the bridge's column (it migrates feed on start), and so is the tasks table; until
# the column is there, both exit 1 saying so, and until the table is, a json read does (a plain one
# needs no table).
import sqlite3

MARK_CHUNK = 500   # seqs per UPDATE: well inside SQLite's limit on bound values


def columns(con):
    return {row[1] for row in con.execute("PRAGMA table_info(feed)")}


def main():
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    mark = ARGS["op"] == "mark"
    con = sqlite3.connect(db_uri(db, "rw" if mark else "ro"), uri=True, timeout=10)
    try:
        if "processed_at" not in columns(con):
            die(1, "the feed table has no processed_at column yet — restart vfg-bridge, which adds it")
        if mark:
            at, n = now_iso(), 0
            seqs = [int(s) for s in ARGS["seqs"]]
            with con:
                for i in range(0, len(seqs), MARK_CHUNK):
                    part = seqs[i:i + MARK_CHUNK]
                    cur = con.execute("UPDATE feed SET processed_at = ? WHERE processed_at IS NULL AND seq IN (%s)"
                                      % ",".join("?" * len(part)), [at] + part)
                    n += cur.rowcount
            print(n)
            return
        if ARGS.get("json") and not has_tasks(con):
            die(1, NO_TASKS)
        top = con.execute("SELECT MAX(seq) FROM feed").fetchone()[0] or 0
        rows = con.execute("SELECT seq, at, line, processed_at FROM feed WHERE at >= ? AND seq <= ? ORDER BY seq",
                           (ARGS["since"], top)).fetchall()
        tasks = task_rows(con) if ARGS.get("json") else None
    except sqlite3.Error as e:
        die(1, "cannot %s %s: %s" % ("mark" if mark else "read", db, e))
    finally:
        con.close()
    if ARGS.get("json"):
        out = [json.dumps({"seq": s, "at": a, "line": l, "processed_at": p}, ensure_ascii=False)
               for s, a, l, p in rows] + [json.dumps({"tasks": tasks}, ensure_ascii=False),
                                          json.dumps({"last_seq": top})]
    else:
        out = [" ".join((l or "").splitlines()) for _s, _a, l, _p in rows]
    if out:
        sys.stdout.write("\n".join(out) + "\n")


main()
