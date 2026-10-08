# history.py: vfg history, read-only. ARGS:
#   {"vfg": "/opt/vfg", "what": "lines"|"events"|"feed", "since": iso|null,
#    "until": iso|null, "player": name|null, "classes": [..]|null,
#    "source": "voice"|"chat"|"ai"|null, "limit": 50}
import sqlite3


def stamp(at):
    # 2026-10-07T18:22:31.412Z -> 2026-10-07 18:22:31
    return (at or "")[:19].replace("T", " ")


def flat(text):
    return " ".join((text or "").splitlines())


def main():
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    con = sqlite3.connect(db_uri(db, "ro"), uri=True, timeout=5)
    what = ARGS["what"]
    where, params = [], []
    if ARGS.get("since"):
        where.append("at >= ?")
        params.append(ARGS["since"])
    if ARGS.get("until"):
        where.append("at <= ?")
        params.append(ARGS["until"])
    if ARGS.get("player") and what != "feed":
        where.append("player_name = ? COLLATE NOCASE")
        params.append(ARGS["player"])
    if what == "lines":
        if ARGS.get("source"):
            where.append("source = ?")
            params.append(ARGS["source"])
        classes = ARGS.get("classes") or []
        if classes:
            ors = []
            judged = [c for c in classes if c != "unjudged"]
            if judged:
                ors.append("jev_class IN (%s)" % ",".join("?" * len(judged)))
                params.extend(judged)
            if "unjudged" in classes:
                ors.append("(jev_class IS NULL AND source != 'ai')")
            where.append("(" + " OR ".join(ors) + ")")
        sql = ("SELECT at, player_name, source, jev_class, jev_confidence, text FROM lines")
        order = "at DESC, rowid DESC"
    elif what == "events":
        sql = "SELECT at, kind, player_name, text FROM events"
        order = "at DESC, rowid DESC"
    else:
        sql = "SELECT seq, line FROM feed"
        order = "seq DESC"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + order + " LIMIT ?"
    params.append(int(ARGS.get("limit") or 50))
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        die(1, "cannot read %s: %s" % (db, e))
    out = []
    for row in reversed(rows):
        if what == "lines":
            at, player, source, cls, conf, text = row
            judged = "-"
            if cls:
                judged = cls + " " + ("%.2f" % conf if conf is not None else "-")
            out.append("%s %s %s %s | %s" % (stamp(at), player, source, judged, flat(text)))
        elif what == "events":
            at, kind, player, text = row
            out.append("%s %s %s | %s" % (stamp(at), kind, player or "-", flat(text)))
        else:
            out.append(flat(row[1]))
    if out:
        sys.stdout.write("\n".join(out) + "\n")


main()
