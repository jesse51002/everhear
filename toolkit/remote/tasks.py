# tasks.py: vfg task set and vfg task list, and the reads of vfg brain status and vfg route --task:
# the brain's tasks table in vfg.db (Prototype_Named_Tasks), one row per implementer task, keyed by
# its name. The bridge creates the table; only the brain writes it, through `vfg task set`. ARGS:
#   {"vfg": "/opt/vfg", "op": "set", "task": {"name": "galick_gun_styrk", ...the keys given...},
#    "library_action": false}
#   {"vfg": "/opt/vfg", "op": "list", "open": false, "name": null|"galick_gun_styrk", "json": false}
# vfg checked every key on the PC: the name, the status, the times (ISO with milliseconds and a Z),
# made an object of lists and variables a list. set, in one BEGIN IMMEDIATE:
#   - no row by that name: an INSERT, which needs a description, and never takes a library action's
#     name (library_action, which the PC read from its actions/); status open, and created_at and
#     updated_at now, when not given, and closed_at now for a row given done or failed without one.
#     Prints `added <name>`.
#   - a row: an UPDATE of the keys given; name and created_at never change. A given updated_at older
#     than the stored one writes nothing and prints `kept <name>: a newer write is there`; otherwise
#     updated_at is the given one, else now. closed_at, when not given, becomes now as the status
#     goes from open to done or failed, and NULL as it becomes open. Prints `updated <name>`.
# made and variables are stored as compact JSON. list prints one line per task, oldest created
# first, `<name> <status> <player|->: <description, cut to 120>`, or `none`; with json one JSON list
# of the rows, made and variables decoded. Exit 1, saying why, when vfg.db or its tasks table is
# missing, or for a refused write.
import sqlite3

DESCRIPTION_MAX = 120   # characters of the description on a plain list's line
JSON_KEYS = ("made", "variables")


def stored(key, value):
    """A value as its column holds it: made and variables as compact JSON text."""
    if key in JSON_KEYS and value is not None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def insert(con, task):
    name = task["name"]
    if not str(task.get("description") or "").strip():
        die(1, "a new task needs a description")
    if ARGS.get("library_action"):
        die(1, "%s is a library action's name" % name)
    now = now_iso()
    row = dict(task)
    row["status"] = row.get("status") or "open"
    row["created_at"] = row.get("created_at") or now
    row["updated_at"] = row.get("updated_at") or now
    if row["status"] != "open" and "closed_at" not in task:
        row["closed_at"] = now
    keys = [k for k in TASK_COLUMNS if k in row]
    con.execute("INSERT INTO tasks (%s) VALUES (%s)" % (", ".join(keys), ", ".join("?" * len(keys))),
                [stored(k, row[k]) for k in keys])
    return "added %s" % name


def update(con, task, have):
    name = task["name"]
    given = task.get("updated_at")
    if given and have["updated_at"] and given < have["updated_at"]:
        return "kept %s: a newer write is there" % name
    sets = dict((k, v) for k, v in task.items() if k not in ("name", "created_at", "updated_at"))
    sets["updated_at"] = given or now_iso()
    if "closed_at" not in task:
        if task.get("status") in ("done", "failed") and have["status"] == "open":
            sets["closed_at"] = now_iso()
        elif task.get("status") == "open":
            sets["closed_at"] = None
    keys = [k for k in TASK_COLUMNS if k in sets]
    con.execute("UPDATE tasks SET %s WHERE name = ?" % ", ".join("%s = ?" % k for k in keys),
                [stored(k, sets[k]) for k in keys] + [name])
    return "updated %s" % name


def op_set(con):
    task = ARGS["task"]
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute("SELECT status, updated_at FROM tasks WHERE name = ?", (task["name"],))
        found = cur.fetchone()
        said = update(con, task, {"status": found[0], "updated_at": found[1]}) if found else insert(con, task)
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    print(said)


def one_line(task):
    text = " ".join(str(task.get("description") or "").split())
    if len(text) > DESCRIPTION_MAX:
        text = text[:DESCRIPTION_MAX - 1] + "…"
    return "%s %s %s: %s" % (task["name"], task["status"], task.get("player") or "-", text)


def op_list(con):
    rows = task_rows(con, open_only=bool(ARGS.get("open")), name=ARGS.get("name"))
    if ARGS.get("json"):
        print(json.dumps(rows, ensure_ascii=False))
    else:
        print("\n".join(one_line(t) for t in rows) if rows else "none")


def main():
    if ARGS.get("op") not in ("set", "list"):
        die(2, "unknown tasks op %r" % ARGS.get("op"))
    db = os.path.join(ARGS["vfg"], "vfg.db")
    if not os.path.exists(db):
        die(1, "no transcript history yet (%s missing)" % db)
    write = ARGS["op"] == "set"
    con = sqlite3.connect(db_uri(db, "rw" if write else "ro"), uri=True, timeout=10, isolation_level=None)
    try:
        if not has_tasks(con):
            die(1, NO_TASKS)
        (op_set if write else op_list)(con)
    except sqlite3.Error as e:
        die(1, "cannot %s %s: %s" % ("write the task to" if write else "read the tasks in", db, e))
    finally:
        con.close()


main()
