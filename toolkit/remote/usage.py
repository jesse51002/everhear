# usage.py: vfg usage add. ARGS:
#   {"vfg": "/opt/vfg", "rows": [{"at", "component", "model", "input_tokens",
#    "output_tokens", "cost_usd", "ref"}, ...]}
# Inserts every row in one transaction (rows queued on the PC come first) and
# prints the id of the last one, the row this call was made for.
import sqlite3


def main():
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    con = sqlite3.connect(db_uri(db, "rw"), uri=True, timeout=10)
    last = None
    try:
        with con:
            for r in ARGS["rows"]:
                cur = con.execute(
                    "INSERT INTO usage (at, component, model, input_tokens, output_tokens, cost_usd, ref)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["at"], r["component"], r.get("model"), r.get("input_tokens"),
                     r.get("output_tokens"), r.get("cost_usd"), r.get("ref")))
                last = cur.lastrowid
    except sqlite3.Error as e:
        die(1, "usage not stored: %s" % e)
    finally:
        con.close()
    print(last)


main()
