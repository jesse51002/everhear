# up.py: vfg up. ARGS: {"mc": "/opt/mc", "vfg": "/opt/vfg", "wait": 90}
# Starts minecraft, vfg-bridge, vfg-jev and vfg-engine (the game engine spec's one engine process)
# if they are down, then waits until all four are active and RCON answers `list`. The test
# server's unit, minecraft-test, where it is installed, is only reported, and started without
# waiting when it is down (reset first when it gave up): vfg up never waits for it nor fails on it,
# since vfg test restarts it after every test and brings it up itself.
import subprocess

UNITS = ["minecraft", "vfg-bridge", "vfg-jev", "vfg-engine"]
TEST_UNIT = "minecraft-test"


def run(argv, timeout=60):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout.decode("utf-8", errors="replace").strip()


def first_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[0].strip() if lines else ""


def last_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def is_active(unit):
    return first_line(run(["systemctl", "is-active", unit])[1]) or "unknown"


def journal(unit):
    return last_line(run(["sudo", "-n", "journalctl", "-u", unit, "-n", "1", "-o", "cat"])[1])


def rcon_list():
    try:
        r = Rcon(ARGS["mc"])
        r.command("list")
        r.close()
        return True, ""
    except RconDown as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def test_server():
    """minecraft-test's row, or None where it is not installed: its state, and a start asked
    (--no-block) when it is down."""
    if first_line(run(["systemctl", "show", "-p", "LoadState", "--value", TEST_UNIT], 15)[1]) in ("", "not-found"):
        return None
    state = is_active(TEST_UNIT)
    if state in ("active", "activating", "reloading", "deactivating"):
        return state
    if state == "failed":
        run(["sudo", "-n", "systemctl", "reset-failed", TEST_UNIT], 15)
    rc, text = run(["sudo", "-n", "systemctl", "start", "--no-block", TEST_UNIT], 15)
    if rc == 0:
        return "%s  starting (not waited for)" % state
    return "%s  start failed  %s" % (state, journal(TEST_UNIT) or last_line(text))


def main():
    units = {}
    for u in UNITS:
        info = {"state": "", "action": "", "note": ""}
        units[u] = info
        load = first_line(run(["systemctl", "show", "-p", "LoadState", "--value", u])[1])
        if load == "not-found":
            info["state"] = "missing"
            continue
        if is_active(u) != "active":
            rc, text = run(["sudo", "-n", "systemctl", "start", u])
            if rc == 0:
                info["action"] = "started"
            else:
                info["action"] = "start failed"
                info["note"] = journal(u) or last_line(text)

    deadline = time.time() + float(ARGS.get("wait", 90))
    while True:
        for u, info in units.items():
            if info["state"] != "missing":
                info["state"] = is_active(u)
        rcon_ok, rcon_err = rcon_list()
        live = [i for i in units.values() if i["state"] != "missing"]
        if all(i["state"] == "active" for i in units.values()) and rcon_ok:
            break
        hopeless = (any(i["state"] in ("missing", "failed") for i in units.values())
                    or any(i["action"] == "start failed" for i in live))
        if hopeless or time.time() >= deadline:
            break
        time.sleep(2)

    for u, info in units.items():
        if info["state"] not in ("active", "missing") and not info["note"]:
            info["note"] = journal(u)

    rows = []
    for u in UNITS:
        info = units[u]
        cols = [info["state"]]
        if info["action"]:
            cols.append(info["action"])
        if u == "minecraft" and info["state"] != "missing":
            cols.append("rcon ok" if rcon_ok else "rcon not answering")
        if info["note"]:
            cols.append(info["note"])
        rows.append((u, "  ".join(cols)))
    test = test_server()
    if test is not None:
        rows.append((TEST_UNIT, test))
    seq = last_feed_seq(os.path.join(ARGS["vfg"], "feed.log"))
    rows.append(("feed", "no feed.log yet" if seq is None else "last seq %d" % seq))
    for name, text in rows:
        print("%-12s%s" % (name + " " if len(name) >= 12 else name, text))
    sys.stdout.flush()

    for u in UNITS:
        info = units[u]
        if info["state"] == "missing":
            die(1, "not up: %s missing" % u)
        if info["state"] != "active":
            why = " (%s)" % info["note"] if info["note"] else ""
            die(1, "not up: %s %s%s" % (u, "failed" if info["state"] in ("failed", "inactive") else info["state"], why))
    if not rcon_ok:
        die(1, "not up: minecraft RCON not answering (%s)" % rcon_err)


main()
