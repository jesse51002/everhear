# rcon.py: vfg cmd and vfg say. ARGS:
#   {"mode": "cmd", "mc": "/opt/mc", "command": "time set day"}
#   {"mode": "say", "mc": "/opt/mc", "vfg": "/opt/vfg", "to": null|"Steve",
#    "commands": ["tellraw @a [...]", ...], "store": "<text as stored>", "task": null|"galick_gun_styrk"}
# A say is stored as an ai line, then fed as `AI say <task's name|-> | <text as stored>` through
# /opt/vfg/lib/vfg_feed.py, the feed's one writer, in this same call.
import sqlite3


def connect():
    try:
        return Rcon(ARGS["mc"])
    except RconDown as e:
        die(5, str(e))


def run_cmd():
    r = connect()
    try:
        out = r.command(ARGS["command"])
    except RconDown as e:
        die(5, str(e))
    r.close()
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if mc_refused(out):
        die(1, "minecraft rejected the command")


def run_say():
    r = connect()
    first_at = None
    for cmd in ARGS["commands"]:
        try:
            out = r.command(cmd)
        except RconDown as e:
            die(5, str(e))
        if first_at is None:
            first_at = now_iso()
        if "No player was found" in out:
            if ARGS.get("to"):
                die(1, "%s is not online" % ARGS["to"])
            die(1, "nobody is online")
        if mc_refused(out):
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
            die(1, "minecraft rejected the command")
    r.close()
    # The message has gone; storing it can only warn, never fail it.
    db = os.path.join(ARGS["vfg"], "vfg.db")
    try:
        if not os.path.exists(db):
            raise RuntimeError("%s missing" % db)
        con = sqlite3.connect(db_uri(db, "rw"), uri=True, timeout=5)
        try:
            with con:
                con.execute(
                    "INSERT INTO lines (id, at, player_name, source, text) VALUES (?, ?, 'AI', 'ai', ?)",
                    (ulid(), first_at, ARGS["store"]))
        finally:
            con.close()
    except Exception as e:
        warn("said, but not stored in history: %s" % e)
    try:
        feed(first_at)
    except Exception as e:
        warn("said, but not fed: %s" % e)


def feed(at):
    """The feed's AI say row, through vfg_feed, which finds its files by VFG_DB and VFG_FEED."""
    os.environ["VFG_DB"] = os.path.join(ARGS["vfg"], "vfg.db")
    os.environ["VFG_FEED"] = os.path.join(ARGS["vfg"], "feed.log")
    lib = os.path.join(ARGS["vfg"], "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import vfg_feed
    vfg_feed.append("AI", "say %s" % (ARGS.get("task") or "-"), ARGS["store"], at)


if ARGS["mode"] == "cmd":
    run_cmd()
else:
    run_say()
