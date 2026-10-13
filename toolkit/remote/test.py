# test.py: the test server's side of vfg test, after the deploy (scripts.py). Appended after
# common.py and ARGS:
#   {"vfg": "/opt/vfg-test", "mc": "/opt/mc-test", "task": "hot_fireball_steve", "server": "main",
#    "file": "made/main/tests/hot_fireball_steve.py", "test": "<the test's text>",
#    "actions": {name: its actions.json entry}, "incantations": [store entry, ...], "timeout": 60,
#    "owner": "", "poll": 0.5, "tail": 15, "live": {"vfg", "mc"} (when the live server is on this host)}
# With "live", the test server that is the live one under another path (the same folder once
# links are followed, or the same RCON port) is refused, exit 3: a test never touches the live world.
# One test at a time on this box (<vfg>/tests/tests.lock, waited for within the timeout). The
# incantations go into this box's store (<vfg>/incantations.json, through the deployed kit's
# incantations.Store, replacing one of the same player and phrase); the test is written to
# <vfg>/tests/<task>.py, its actions beside it, and run on the box's Python as a copy runs
# (PYTHONPATH the kit, VFG_RUN_DIR, VFG_MC_PROPERTIES; VFG_SCRIPT test_<task> names its tag),
# with VFG_TEST=1, which kit/test.py asks for, and VFG_TEST_TAIL, the log lines it shows of each
# copy on a failure. Past the timeout it gets SIGTERM (its cleanup runs), then SIGKILL 5 s later. Prints what the test printed, then one JSON line, last:
# {"code": its exit code, or null when it was stopped, "seconds", "timed_out"}. Minecraft not
# answering RCON is exit 5, before anything runs.
import fcntl
import re
import signal
import subprocess

BASE = ARGS["vfg"]
MC = ARGS.get("mc") or "/opt/mc"
TASK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
SECRETS = ("VFG_CLIENT_TOKEN", "TYPESAFE_API_KEY")
KILL_AFTER = 5.0


def main():
    if not TASK_RE.match(ARGS.get("task") or ""):
        die(2, "%r is not a task's name" % ARGS.get("task"))
    live = ARGS.get("live")
    if live:
        pairs = (("vfg folder", BASE, live.get("vfg")), ("Minecraft folder", MC, live.get("mc")))
        same = [what for what, here, there in pairs if there and os.path.realpath(here) == os.path.realpath(there)]
        if not same and live.get("mc"):
            try:
                port = read_props(os.path.join(MC, "server.properties")).get("rcon.port")
                if port and port == read_props(os.path.join(live["mc"], "server.properties")).get("rcon.port"):
                    same = ["RCON port %s" % port]
            except OSError:
                pass
        if same:
            die(3, "this test server is the live one (the same %s): a test never runs there" % same[0])
    try:
        Rcon(MC).close()
    except RconDown as e:
        die(5, str(e))
    began = time.time()
    timeout = float(ARGS.get("timeout") or 60)
    tests = os.path.join(BASE, "tests")
    os.makedirs(tests, exist_ok=True)
    lock = os.open(os.path.join(tests, "tests.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        run(began, timeout, tests, lock)
    finally:
        os.close(lock)


def run(began, timeout, tests, lock):
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - began > timeout:
                die(1, "another test held this test server for all of the %g s" % timeout)
            time.sleep(0.2)
    task = ARGS["task"]
    lib = os.path.join(BASE, "scripts", "_lib")
    sys.path.insert(0, lib)
    import incantations
    store = incantations.Store(os.path.join(BASE, "incantations.json"))
    for e in ARGS.get("incantations") or []:
        try:
            store.put(e)
        except (ValueError, incantations.StoreError) as x:
            warn("incantation %r not put on the test server: %s" % (e.get("phrase"), x))
    path = os.path.join(tests, task + ".py")
    atomic_write(path, ARGS["test"].encode("utf-8"))
    acts = os.path.join(tests, task + ".actions.json")
    atomic_write(acts, json.dumps(ARGS.get("actions") or {}).encode("utf-8"))
    env = dict((k, v) for k, v in os.environ.items() if k not in SECRETS)
    env.update({"VFG_TEST": "1", "VFG_TEST_ACTIONS": acts, "VFG_TEST_TAIL": str(ARGS.get("tail", 15)),
                "VFG_SCRIPT": ("test_" + task)[:48].rstrip("_"), "VFG_TASK": "", "VFG_SERVER": ARGS.get("server") or "main", "VFG_RUN_DIR": os.path.join(BASE, "run"),
                "VFG_PROTECTED": os.path.join(BASE, "protected.json"), "VFG_LIVE_POLL_S": str(ARGS.get("poll") or 0.5),
                "PYTHONPATH": lib, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "VFG_MC_PROPERTIES": os.path.join(MC, "server.properties"),
                "VFG_INCANTATIONS": os.path.join(BASE, "incantations.json")})
    if ARGS.get("owner"):
        env["VFG_OWNER"] = ARGS["owner"]
    else:
        env.pop("VFG_OWNER", None)
    os.makedirs(os.path.join(BASE, "run"), exist_ok=True)
    left = max(1.0, timeout - (time.time() - began))
    proc = subprocess.Popen(["setsid", sys.executable, "-u", path], cwd=tests, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=left)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig, wait in ((signal.SIGTERM, KILL_AFTER), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                out, _ = proc.communicate(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            out = b""
    sys.stdout.flush()
    sys.stdout.buffer.write(out or b"")
    if out and not out.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    print(json.dumps({"code": None if timed_out else proc.returncode, "seconds": round(time.time() - began, 1),
                      "timed_out": timed_out}))


main()
