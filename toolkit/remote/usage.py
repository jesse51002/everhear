# usage.py: vfg usage add and vfg usage flush. ARGS:
#   {"vfg": "/opt/vfg", "rows": [{"at", "component", "model", "input_tokens",
#    "output_tokens", "cost_usd", "ref", "cache_read_tokens", "cache_write_tokens",
#    "ok", "error", "ms", "largest_tokens"}, ...]}
# Inserts every row in one transaction (rows queued on the PC come first) and
# prints the id of the last one, the row this call was made for. ok, error and ms
# are a structured() call's (lib/py/vfg_llm.py); a row from `vfg usage add` has
# none of them. largest_tokens is an implementer run's largest request
# (`vfg usage add --largest`). The added columns are the bridge's to add (it
# migrates usage on start); until it has, rows go in without them and one line on
# stderr says what was dropped.
import sqlite3

BASE = ("at", "component", "model", "input_tokens", "output_tokens", "cost_usd", "ref")
ADDED = (("cache counts", ("cache_read_tokens", "cache_write_tokens"), "cache columns"),
         ("call outcomes", ("ok", "error", "ms"), "ok, error and ms columns"),
         ("largest requests", ("largest_tokens",), "largest_tokens column"))


def main():
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    con = sqlite3.connect(db_uri(db, "rw"), uri=True, timeout=10)
    last = None
    dropped = dict((what, 0) for what, _, _ in ADDED)
    try:
        have = {row[1] for row in con.execute("PRAGMA table_info(usage)")}
        columns = list(BASE) + [c for _, cols, _ in ADDED for c in cols if c in have]
        sql = "INSERT INTO usage (%s) VALUES (%s)" % (", ".join(columns), ", ".join("?" * len(columns)))
        with con:
            for r in ARGS["rows"]:
                for what, cols, _ in ADDED:
                    if any(r.get(c) is not None for c in cols if c not in have):
                        dropped[what] += 1
                cur = con.execute(sql, [r.get(c) for c in columns])
                last = cur.lastrowid
    except sqlite3.Error as e:
        die(1, "usage not stored: %s" % e)
    finally:
        con.close()
    print(last)
    for what, _, missing in ADDED:
        if dropped[what]:
            warn("%s of %d row(s) not stored: usage has no %s yet "
                 "— restart vfg-bridge, which adds them" % (what, dropped[what], missing))


main()
