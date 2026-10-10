# usage.py: vfg usage add. ARGS:
#   {"vfg": "/opt/vfg", "rows": [{"at", "component", "model", "input_tokens",
#    "output_tokens", "cost_usd", "ref", "cache_read_tokens", "cache_write_tokens"}, ...]}
# Inserts every row in one transaction (rows queued on the PC come first) and
# prints the id of the last one, the row this call was made for. The two cache
# columns are the bridge's to add (it migrates usage on start); until it has,
# rows go in without them and one line on stderr says the counts were dropped.
import sqlite3

CACHE = ("cache_read_tokens", "cache_write_tokens")


def main():
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    con = sqlite3.connect(db_uri(db, "rw"), uri=True, timeout=10)
    last = None
    dropped = 0
    try:
        have = {row[1] for row in con.execute("PRAGMA table_info(usage)")}
        cache = [c for c in CACHE if c in have]
        columns = ["at", "component", "model", "input_tokens", "output_tokens", "cost_usd", "ref"] + cache
        sql = "INSERT INTO usage (%s) VALUES (%s)" % (", ".join(columns), ", ".join("?" * len(columns)))
        with con:
            for r in ARGS["rows"]:
                if any(r.get(c) is not None for c in CACHE if c not in cache):
                    dropped += 1
                cur = con.execute(sql, [r.get(c) for c in columns])
                last = cur.lastrowid
    except sqlite3.Error as e:
        die(1, "usage not stored: %s" % e)
    finally:
        con.close()
    print(last)
    if dropped:
        warn("cache counts of %d row(s) not stored: usage has no cache columns yet "
             "— restart vfg-bridge, which adds them" % dropped)


main()
