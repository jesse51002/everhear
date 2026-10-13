# test.py: the test server's side of vfg test, after the deploy (scripts.py). Appended after
# common.py and ARGS:
#   {"vfg": "/opt/vfg-test", "mc": "/opt/mc-test", "task": "hot_fireball_steve", "server": "main",
#    "file": "made/main/tests/hot_fireball_steve.py", "test": "<the test's text>",
#    "actions": {name: its actions.json entry}, "incantations": [store entry, ...], "timeout": 60,
#    "queue": 300, "boot": 120, "sandbox": "auto", "unit": "minecraft-test", "owner": "",
#    "poll": 0.5, "tail": 15, "live": {"vfg", "mc"} (when the live server is on this host)}
#
# The test server is a second Minecraft server (Prototype_Implementer_Quality): <mc>/.vfg-test-server,
# its marker, written only by its setup ({"made", "rcon_port", "live_mc", "live_vfg"}), and the unit
# minecraft-test, whose every start puts a fresh copy of the template world in place and writes the
# freshness token <mc>/world/vfg-fresh. A test runs there and nowhere else:
#  1. No marker, a marker whose rcon_port is not the server's, or a vfg folder with a .env (the live
#     box's secrets, which a test server never has): exit 3. The live server's folders
#     (ARGS' "live", sent by vfg whenever the test server is on the live box's host; the marker's
#     live_mc and live_vfg; and /opt/vfg, /opt/mc unless /opt/mc has a marker itself) must not be
#     the test server's, nor hold it or lie inside it, nor share its RCON port: exit 3.
#  2. One test at a time (<vfg>/tests/tests.lock), in first-come order: each waiter leaves a ticket
#     in <vfg>/tests/queue/ (this user's alone) and takes the lock only as the oldest live ticket,
#     so a server that just freed up goes to the next in line, not to whoever calls again first; a
#     ticket whose process is gone is skipped. Waited for at most "queue" s: past it, exit 5.
#  3. What an earlier test left running is stopped: the sandbox's unit, and any copy run/ names.
#  4. Up, with a fresh world: when the last test's restart (tests/restarted) is under "boot" s old,
#     its fresh world is waited for; Minecraft not answering RCON is restarted (sudo -n systemctl
#     restart minecraft-test) and waited for, "boot" s at most; a world without its token (a test
#     used it) is restarted the same way. Then the token is deleted: this test has it. Down after
#     that, or down with no unit to restart: exit 5, saying what was tried.
#  5. The test is written to <vfg>/tests/<task>.py, its actions and the task's incantations beside
#     it. tests/ is this user's alone; run/ and state/, which the test writes, are the test user's
#     too, so nothing here writes there by name: inside the sandbox, as the test's own first step
#     (tests/_vfg_runner.py), run/ is emptied and the store (<vfg>/state/incantations.json) made to
#     hold exactly the task's incantations; after it, run/ is emptied again by unlinking.
#  6. It runs on the box's Python as a copy runs (PYTHONPATH the kit, VFG_RUN_DIR, VFG_MC_PROPERTIES,
#     VFG_INCANTATIONS, VFG_PROTECTED, all the test server's; VFG_SCRIPT test_<task> names its tag;
#     VFG_TEST=1, which kit/test.py asks for and under which the kit talks to no server without the
#     marker; VFG_TEST_TAIL, the log lines it shows of each copy on a failure), in a sandbox: a
#     transient systemd unit in which the live Minecraft folder cannot be read, the live vfg folder
#     is an empty read-only one (its Python bound back in, read-only), nothing but run/, state/ and
#     a /tmp of its own can be written, sudo cannot raise it, and every process it starts dies with
#     it. On the box (system mode: sudo -n systemd-run) it runs as the marker's test_user (vfgtest),
#     never as this user, the live services' own: other users' processes are hidden from it
#     (ProtectProc=invisible), so no /proc/<pid> of the live server's leads anywhere, and it has
#     no network but loopback; a marker with no test_user, or one that does not exist, is exit 3
#     (re-run deploy.sh testserver), and a sandbox that fails its probe is exit 5, never a run
#     outside it. On the PC (user mode, systemd-run --user) it runs as this user, said in the
#     output: weaker, the live server's processes stay visible. "sandbox" "auto" takes system,
#     else user, else the plain process today's boxes had (setsid), said in the output; "system",
#     "user" or "none" only that one. Past "timeout" s it gets SIGTERM (its cleanup runs), then
#     SIGKILL 5 s later.
#  7. Then sudo -n systemctl restart --no-block minecraft-test: the next test's world warms up.
# Prints a SERVER line (the queue's wait, what was restarted, the sandbox), what the test printed,
# then one JSON line, last: {"code": its exit code, or null when it was stopped, "seconds" (the
# test's own), "timed_out", "queued", "restarted"}.
import fcntl
import hashlib
import math
import re
import shutil
import signal
import subprocess

BASE = ARGS["vfg"]
MC = ARGS["mc"]
TESTS = os.path.join(BASE, "tests")
RUN = os.path.join(BASE, "run")
MARKER = ".vfg-test-server"
FRESH = os.path.join(MC, "world", "vfg-fresh")
RESTARTED = os.path.join(TESTS, "restarted")
STATE = os.path.join(BASE, "state")
STORE = os.path.join(STATE, "incantations.json")
RUNNER = os.path.join(TESTS, "_vfg_runner.py")
QUEUE = os.path.join(TESTS, "queue")
TICKET_RE = re.compile(r"^(\d+)-(\d+)$")
# A token unique to this `vfg test`, put in the test's environment (and so in every copy it starts,
# which inherit the environment): in the plain fallback it is how this test's descendants, even those
# in sessions of their own (setsid copies), are found and killed afterwards. ulid() is time-ordered.
TEST_RUN = ulid()
UNIT = ARGS.get("unit") or "minecraft-test"
TASK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
KILL_AFTER = 5
MEMORY_MAX = "1G"
# What of this login's environment a test gets, besides what is set for it below: never a secret.
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "TZ")
SECRET = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY")
# Run in the sandbox before the test: 0 only when the live server is out of its reach. Its one
# argument, JSON: files it must not read or write, and pids of the live services' user it must not see.
PROBE = ("import json, os, sys\n"
         "c = json.loads(sys.argv[1])\n"
         "for p in c['read']:\n"            # the live Minecraft folder: must not be readable
         "    try:\n"
         "        open(p, 'rb').close()\n"
         "    except OSError:\n"
         "        continue\n"
         "    sys.exit('reachable (read): ' + p)\n"
         "for p in c['write']:\n"           # the live vfg folder: must not be writable; leaves nothing
         "    try:\n"
         "        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n"
         "    except OSError:\n"
         "        continue\n"
         "    os.close(fd)\n"
         "    try:\n"
         "        os.remove(p)\n"
         "    except OSError:\n"
         "        pass\n"
         "    sys.exit('reachable (write): ' + p)\n"
         "for pid in c['pids']:\n"
         "    if os.path.exists('/proc/%d' % pid):\n"
         "        sys.exit('process %d of the live services\\' user is visible' % pid)\n")
# The test's first step, inside the sandbox as the test's own user: run/ emptied, the store holding
# exactly the task's incantations (argv[1], a JSON list), then the test itself (argv[2]) in its place.
RUNNER_TEXT = '''import json, os, shutil, sys
run = os.environ["VFG_RUN_DIR"]
for name in os.listdir(run):
    path = os.path.join(run, name)
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError as e:
        print("SERVER     run/%s was not cleared: %s" % (name, e.strerror or e), flush=True)
import incantations
store = incantations.Store()
os.makedirs(os.path.dirname(store.path), exist_ok=True)
with open(sys.argv[1], encoding="utf-8") as f:
    entries = json.load(f)
good = []
for e in entries:
    try:
        store._prepared(e)   # the same check replace_all makes, per entry, so one bad one is dropped, not all
        good.append(e)
    except ValueError as x:
        print("SERVER     incantation %r not put on the test server: %s" % (e.get("phrase"), x), flush=True)
store.replace_all(good)
sys.stdout.flush()
os.execv(sys.executable, [sys.executable, "-u", sys.argv[2]])
'''


def real(path):
    return os.path.realpath(path)


def within(a, b):
    """Is path `a` path `b`, or inside it?"""
    return a == b or a.startswith(b.rstrip("/") + "/")


def run(argv, timeout=30, env=None):
    """(exit code, its output, stdout and stderr together); 127 when it cannot run."""
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, p.stdout.decode("utf-8", "replace").strip()


def last_line(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"


# ------------------------------------------------------------------ 1. which server

def marker():
    """The test server's marker, as JSON ({} when it is not); exit 3 when there is none."""
    path = os.path.join(MC, MARKER)
    if not os.path.isfile(path):
        die(3, "no test server at %s: it has no %s, which only the test server's setup writes (deploy.sh "
               "testserver); a test never runs on a server without it" % (MC, MARKER))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def lives(mark):
    """The live servers this test server must not be: (vfg folder, Minecraft folder, whose)."""
    out = []
    sent = ARGS.get("live") or {}
    if sent.get("vfg") or sent.get("mc"):
        out.append((sent.get("vfg"), sent.get("mc"), "the live server vfg named"))
    if mark.get("live_vfg") or mark.get("live_mc"):
        out.append((mark.get("live_vfg"), mark.get("live_mc"), "the live server its marker names"))
    if not os.path.isfile(os.path.join("/opt/mc", MARKER)):   # the box's own, unless it is a test server
        out.append(("/opt/vfg", "/opt/mc", "the box's live server"))
    return out


def rcon_port(mc_dir):
    """A server's RCON port as server.properties gives it (Rcon's default when it gives none);
    None when it cannot be read."""
    try:
        return read_props(os.path.join(mc_dir, "server.properties")).get("rcon.port") or "25575"
    except OSError:
        return None


def check_server(mark):
    """Exit 3 unless this is a test server and none of the live ones: its RCON port and the live
    servers' existing folders, to keep out of the sandbox's reach."""
    port = rcon_port(MC)
    if port is None:
        die(3, "no test server at %s: it has no server.properties" % MC)
    if mark.get("rcon_port") is not None and str(mark.get("rcon_port")) != port:
        die(3, "the marker in %s names RCON port %s, but its server.properties has %s: it is not this server's "
               "marker; a test never runs there" % (MC, mark.get("rcon_port"), port))
    if os.path.lexists(os.path.join(BASE, ".env")):
        die(3, "%s has a .env, the box's secrets: a test server's vfg folder never has one, so this is the live "
               "server's; a test never runs there" % BASE)
    hide = []
    for vfg, mc, whose in lives(mark):
        for what, here, there in (("vfg folder", BASE, vfg), ("Minecraft folder", MC, mc)):
            if not there:
                continue
            a, b = real(here), real(there)
            if within(a, b) or within(b, a):
                die(3, "this test server is the live one (the same %s as %s, %s): a test never runs there"
                    % (what, whose, there))
        if mc and rcon_port(mc) == port:
            die(3, "this test server is the live one (the same RCON port %s as %s): a test never runs there"
                % (port, whose))
        for kind, path in (("vfg", vfg), ("mc", mc)):
            if path and os.path.isdir(path) and (kind, real(path)) not in hide:
                hide.append((kind, real(path)))
    return port, hide


# ------------------------------------------------------------------ 3, 4. clean and up

def kill_strays():
    """Anything an earlier test of this server left running outside a sandbox: its copies (run/'s
    pid files) and the test itself (tests/running.pid), each only if it is this server's."""
    pids = [os.path.join(RUN, n) for n in (os.listdir(RUN) if os.path.isdir(RUN) else []) if n.endswith(".pid")]
    killed = 0
    for path in pids + [os.path.join(TESTS, "running.pid")]:
        pid = read_pid(path)
        if pid and pid != os.getpid() and pid_with_marker(pid, "VFG_RUN_DIR=" + RUN):
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                    killed += 1
                    break
                except (ProcessLookupError, PermissionError):
                    continue
    return killed


def answers():
    """"" when Minecraft answers `list` over RCON, else why not."""
    try:
        r = Rcon(MC)
        try:
            r.command("list")
        finally:
            r.close()
        return ""
    except RconDown as e:
        return str(e)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)


def wait_up(until):
    why = answers()
    while why and time.time() < until:
        time.sleep(0.5)
        why = answers()
    return why


def unit_exists():
    rc, out = run(["systemctl", "show", "-p", "LoadState", "--value", UNIT], 15)
    return rc == 0 and out.strip() not in ("", "not-found")


def unit_state():
    return (run(["systemctl", "is-active", UNIT], 15)[1].splitlines() or ["unknown"])[0].strip()


def unit_job_pending():
    """True when a start/restart job for the unit is still queued (`systemctl show -p Job`): the
    gap a --no-block restart passes through between its stop and start, when the unit is briefly
    inactive but on its way up."""
    rc, out = run(["systemctl", "show", "-p", "Job", "--value", UNIT], 15)
    return rc == 0 and out.strip() not in ("", "0")


def unit_dead():
    """True only when the unit has really stopped, not the momentary inactive of a --no-block
    restart: failed, or inactive with no job pending and still inactive a moment later."""
    st = unit_state()
    if st == "failed":
        return True
    if st == "inactive" and not unit_job_pending():
        time.sleep(1.0)
        return unit_state() == "inactive" and not unit_job_pending()
    return False


def restart(block, timeout=30):
    """sudo -n systemctl restart [--no-block] minecraft-test: (ok, its output). A restart asked
    is noted (tests/restarted), so the next test waits for its world rather than restarting it. A
    unit that gave up (failed: its start limit, StartLimitBurst) is reset first."""
    if unit_state() == "failed":
        run(["sudo", "-n", "systemctl", "reset-failed", UNIT], 15)
    rc, out = run(["sudo", "-n", "systemctl", "restart"] + ([] if block else ["--no-block"]) + [UNIT],
                  timeout)
    if rc == 0:
        try:
            atomic_write(RESTARTED, "%.3f\n" % time.time())
        except OSError:
            pass
    else:
        try:
            os.remove(RESTARTED)
        except OSError:
            pass
    return rc == 0, out


def restarted_at():
    try:
        with open(RESTARTED) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


# A fresh-world restart answers RCON well within this; a server still dead after it is wedged, and
# waiting a second full `boot` on it only doubles the cost, so after it we restart once and give up.
# vfg sends `warm` (its TEST_WARM_GRACE), so this bound and the PC's budget cannot drift.
WARM_GRACE = float(ARGS.get("warm") or 30)
GIVE_UP = ("report the ability untested and why; never test on the live server or its players")


def ready(boot, has_unit):
    """The test server up with a fresh world, its token claimed: (seconds it took, what happened,
    a warning or ""). At most one restart of our own, and at most one `boot`-long wait, so a wedged
    server (RCON there but not answering) costs ~WARM_GRACE + boot, not 2 x boot."""
    began = time.time()
    did, warning = [], ""
    up = not answers()
    fresh = os.path.exists(FRESH)
    last = restarted_at() if has_unit else None
    if last is not None and began - last < boot and not (up and fresh):
        # a restart the last test kicked off is warming a fresh world: a short grace for it to
        # answer, rather than a restart of our own; a wedged server does not answer within it.
        # A momentary inactive is the --no-block restart's own stop/start gap, not a dead unit:
        # only a unit that has really stopped (unit_dead) ends the wait early, else a redundant
        # blocking restart would pile on top of the one already under way.
        while time.time() < began + min(boot, WARM_GRACE):
            up, fresh = not answers(), os.path.exists(FRESH)
            if up and fresh:
                break
            if unit_dead():
                break
            time.sleep(0.5)
        if up and fresh and time.time() - began >= 1:
            did.append("waited for the last test's restart")

    if not (up and fresh):
        if not has_unit:
            if not up:
                die(5, "the test server is down (%s) and has no %s unit to restart it; %s"
                    % (answers() or "no RCON", UNIT, GIVE_UP))
            warning = "NOT a fresh world: a test used it, and it has no %s unit to restart it" % UNIT
        else:
            why = "was down" if not up else "had a used world"
            until = time.time() + boot
            ok, out = restart(True, boot)   # kills a wedged java and starts a fresh world
            if not ok:
                die(5, "the test server %s and its restart failed (sudo -n systemctl restart %s: %s); %s"
                    % (why, UNIT, last_line(out), GIVE_UP))
            still = wait_up(until)          # one boot-long wait, then give up
            if still:
                die(5, "the test server %s and did not answer RCON within %g s even after a restart (%s); %s"
                    % (why, boot, still, GIVE_UP))
            did.append("restarted it: it was down" if why == "was down" else "restarted it for a fresh world")
            if not os.path.exists(FRESH):
                warning = "NOT a fresh world: its restart wrote no world/vfg-fresh"
    try:
        os.remove(FRESH)   # this test's now
    except OSError:
        pass
    return time.time() - began, did, warning


# ------------------------------------------------------------------ 5. the task's things

def empty_run():
    """run/ emptied after a test, by unlinking only: it is the test user's to write too, so
    nothing here opens a file there by name (a link it left is removed, never followed)."""
    try:
        names = os.listdir(RUN)
    except OSError:
        return
    for name in names:
        path = os.path.join(RUN, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)   # fd-based: it follows no link inside
            else:
                os.remove(path)
        except OSError:
            pass


def test_env(lib, acts, user=None):
    task = ARGS["task"]
    env = dict((k, v) for k, v in os.environ.items()
               if (k in ENV_KEEP or k.startswith(("VFG_", "PYTHON", "LC_"))) and not SECRET.search(k))
    if user:   # the test user's own: no home of this user's
        env.update(USER=user[0], LOGNAME=user[0], HOME=STATE)
    env.update({"VFG_TEST": "1", "VFG_TEST_ACTIONS": acts, "VFG_TEST_TAIL": str(ARGS.get("tail", 15)),
                "VFG_SCRIPT": ("test_" + task)[:48].rstrip("_"), "VFG_TASK": "",
                "VFG_SERVER": ARGS.get("server") or "main", "VFG_RUN_DIR": RUN,
                "VFG_PROTECTED": os.path.join(BASE, "protected.json"),
                "VFG_LIVE_POLL_S": str(ARGS.get("poll") or 0.5), "PYTHONPATH": lib, "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "VFG_MC_PROPERTIES": os.path.join(MC, "server.properties"),
                "VFG_INCANTATIONS": STORE, "VFG_TEST_RUN": TEST_RUN})
    for k in ("VFG_RCON_LOCK", "VFG_INSTANCE"):   # a test's RCON lock is its server's port's
        env.pop(k, None)
    if ARGS.get("owner"):
        env["VFG_OWNER"] = ARGS["owner"]
    else:
        env.pop("VFG_OWNER", None)
    return env


# ------------------------------------------------------------------ 6. the sandbox

def systemd_run():
    path = shutil.which("systemd-run")
    if path:
        return path
    for d in ("/usr/bin", "/bin"):
        path = os.path.join(d, "systemd-run")
        if os.access(path, os.X_OK):
            return path
    return None


def manager_env():
    """systemd-run's own environment: a user manager is found through XDG_RUNTIME_DIR."""
    env = dict(os.environ)
    run_dir = "/run/user/%d" % os.getuid()
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(run_dir):
        env["XDG_RUNTIME_DIR"] = run_dir
    return env


def ctl(mode, sdrun):
    """The systemctl that speaks to the sandbox's manager (beside systemd-run)."""
    path = os.path.join(os.path.dirname(sdrun), "systemctl")
    path = path if os.access(path, os.X_OK) else "systemctl"
    return ["sudo", "-n", path] if mode == "system" else [path, "--user"]


def test_user(mark):
    """(name, uid, gid) of the marker's test_user, the user a test runs as on the box; or why
    there is none to run as. Never this user: the live services run as it."""
    import grp
    import pwd
    name = mark.get("test_user")
    if not name:
        return "its marker names no test_user"
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        return "its test user %s does not exist" % name
    if pw.pw_uid == os.getuid() or pw.pw_uid == 0:
        return "its test user %s is %s" % (name, "root" if pw.pw_uid == 0 else "this user, the live services' own")
    try:
        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        gid = pw.pw_gid
    return (name, pw.pw_uid, gid)


def unit_pids():
    """The live units' MainPIDs (minecraft, vfg-bridge, vfg-jev) that are running."""
    pids = []
    for unit in ("minecraft", "vfg-bridge", "vfg-jev"):
        rc, out = run(["systemctl", "show", "-p", "MainPID", "--value", unit], 15)
        try:
            pid = int(out.strip())
        except ValueError:
            continue
        if rc == 0 and pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def live_pids():
    """Pids of the live services' user a sandboxed test must not see: this one, and the live units'."""
    return [os.getpid()] + [p for p in unit_pids() if p != os.getpid()]


def env_file(env):
    """The test's environment as a file systemd reads (EnvironmentFile=), so no value is on a
    command line (sudo would log it and drop the environment)."""
    lines = []
    for k, v in sorted(env.items()):
        if "\n" in v or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            continue
        if "'" not in v:
            lines.append("%s='%s'" % (k, v))
        else:
            lines.append('%s="%s"' % (k, re.sub(r'([\\"$`])', r"\\\1", v)))
    path = os.path.join(TESTS, "sandbox.env")
    atomic_write(path, "\n".join(lines) + "\n", 0o600)
    return path


def sandbox_argv(mode, sdrun, unit, timeout, hide, user=None, envfile=None):
    py_home = os.path.dirname(os.path.dirname(real(sys.executable)))
    props = ["RuntimeMaxSec=%d" % int(math.ceil(timeout)), "TimeoutStopSec=%d" % KILL_AFTER, "Nice=10",
             "CPUWeight=20", "IOWeight=20", "MemoryMax=" + MEMORY_MAX, "TasksMax=512", "NoNewPrivileges=yes",
             "ProtectSystem=strict", "ReadWritePaths=" + real(RUN), "ReadWritePaths=" + real(STATE),
             "WorkingDirectory=" + real(TESTS)]
    # a /tmp of its own: no RCON lock of the live server's (/tmp/vfg-rcon-<port>.lock) to hold; only a
    # test server laid out under /tmp itself (the toolkit's tests) shares this user's
    props.append("ReadWritePaths=/tmp" if shared_tmp() else "PrivateTmp=yes")
    if envfile:
        props.append("EnvironmentFile=" + envfile)
    for kind, path in hide:
        if kind == "mc":   # its RCON password, its world: not even read
            props.append("InaccessiblePaths=" + path)
        else:              # an empty folder, read-only: its .env, vfg.db, scripts and store out of reach
            props.append("TemporaryFileSystem=%s:ro" % path)
            if within(py_home, path):   # the box's Python lives in it: read-only, back in
                props.append("BindReadOnlyPaths=" + py_home)
    if mode == "system":
        # another user: the live services' processes (theirs) hidden, their homes too, and no
        # network but loopback, where the test server is
        props += ["ProtectProc=invisible", "ProtectHome=yes", "UMask=0002", "IPAddressDeny=any",
                  "IPAddressAllow=localhost", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"]
        argv = ["sudo", "-n", sdrun, "--uid=%d" % user[1], "--gid=%d" % user[2]]
    else:
        argv = [sdrun, "--user"]
    argv += ["--no-ask-password", "--pipe", "--wait", "--collect", "--unit=" + unit]
    for p in props:
        argv += ["-p", p]
    return argv


def shared_tmp():
    """Is the test server under /tmp, where a /tmp of the test's own would hide it?"""
    return any(within(real(p), "/tmp") for p in (BASE, MC))


PROBE_TTL = 600.0   # a passing sandbox verdict is trusted this long, keyed by what the sandbox depends on
PROBE_CACHE = os.path.join(TESTS, ".sandbox-probe.json")


def probe_key(mode, hide, user):
    """What a passing probe depends on: the mode, the folders hidden, the test user, and the live
    services' units (their MainPIDs; never this process's own pid, new every run). A change in any
    re-probes; so does PROBE_TTL passing."""
    h = hashlib.sha256()
    h.update(("%s|%s|%s|%s" % (mode, sorted(hide), user, sorted(unit_pids()))).encode("utf-8"))
    return h.hexdigest()


def probe_cached(key):
    """True when a passing probe for this key is still fresh. Only passes are ever stored."""
    try:
        with open(PROBE_CACHE) as f:
            c = json.load(f)
        return c.get("key") == key and (time.time() - float(c.get("at") or 0)) < PROBE_TTL
    except (OSError, ValueError, TypeError):
        return False


def probe_remember(key):
    try:
        atomic_write(PROBE_CACHE, json.dumps({"key": key, "at": time.time()}))
    except OSError:
        pass


def probe(mode, sdrun, unit, hide, user=None):
    """"" when this mode's sandbox runs Python with the live server out of reach, else why not: the
    live Minecraft folder unread, the live vfg folder unwritten (the probe leaves nothing: it
    creates a unique name with O_EXCL and unlinks it), and, as another user, the live services'
    processes unseen. A passing verdict is cached (probe_remember) and skipped while fresh."""
    key = probe_key(mode, hide, user)
    if probe_cached(key):
        return ""
    checks = {"read": [os.path.join(path, "server.properties") for kind, path in hide if kind == "mc"],
              "write": [os.path.join(path, ".vfg-probe-%d" % os.getpid()) for kind, path in hide if kind != "mc"],
              "pids": live_pids() if mode == "system" else []}
    argv = sandbox_argv(mode, sdrun, "%s-probe-%d" % (unit, os.getpid()), 30, hide, user)
    rc, out = run(argv + ["--quiet", "--", real(sys.executable), "-c", PROBE, json.dumps(checks)], 40, manager_env())
    if rc == 0:
        probe_remember(key)
        return ""
    return last_line(out)


def root_systemd(sdrun):
    """Does sudo -n systemd-run reach a system manager here (the box), whatever the test user?"""
    rc, _ = run(["sudo", "-n", sdrun, "--no-ask-password", "--quiet", "--wait", "--collect", "--pipe", "true"],
                30, manager_env())
    return rc == 0


def stop_unit(mode, sdrun, unit):
    """A unit of that name left by an earlier test (its vfg gone before it ended) is stopped."""
    run(ctl(mode, sdrun) + ["stop", unit + ".service"], 30, manager_env())


def pick_sandbox(hide, mark):
    """(mode, systemd-run, unit, the test user or None, why the others were not taken). Where the
    system manager answers (the box), system mode or nothing: never this user's own sandbox."""
    want = (ARGS.get("sandbox") or "auto").lower()
    unit = "vfg-test-" + hashlib.sha1(real(BASE).encode("utf-8")).hexdigest()[:10]
    if want == "none":
        return "none", None, unit, None, "VFG_TEST_SANDBOX=none"
    sdrun = systemd_run()
    if not sdrun:
        if want != "auto":
            die(5, "VFG_TEST_SANDBOX=%s, and this box has no systemd-run" % want)
        return "none", None, unit, None, "no systemd-run here"
    whys = []
    if want in ("auto", "system"):
        user = test_user(mark)
        if isinstance(user, tuple):
            why = probe("system", sdrun, unit, hide, user)
            if not why:
                return "system", sdrun, unit, user, ""
            if want == "system" or root_systemd(sdrun):
                die(5, "the test server's sandbox failed its check (%s): no test runs outside it" % why)
            whys.append("system: %s" % why)
        else:
            if want == "system" or root_systemd(sdrun):
                die(3, "the test server cannot run a test as another user: %s; re-run deploy.sh testserver"
                    % user)
            whys.append("system: no system manager here")
    if want in ("auto", "user"):
        why = probe("user", sdrun, unit, hide)
        if not why:
            return "user", sdrun, unit, None, ""
        whys.append("user: %s" % why)
    if want != "auto":
        die(5, "VFG_TEST_SANDBOX=%s is not usable here (%s)" % (want, "; ".join(whys)))
    return "none", sdrun, unit, None, "; ".join(whys)


def run_sandboxed(mode, sdrun, unit, timeout, hide, user, envfile, argv_test):
    """The test in its unit: (output, exit code or None, timed out, a note or "")."""
    argv = sandbox_argv(mode, sdrun, unit, timeout, hide, user, envfile)
    # its stderr into its stdout inside the unit: systemd-run's own stderr is then its status alone
    argv += ["--", "/bin/sh", "-c", 'exec 2>&1; exec "$@"', "sh", real(sys.executable), "-u"] + argv_test
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=manager_env())
    try:
        out, err = p.communicate(timeout=timeout + KILL_AFTER + 15)
    except subprocess.TimeoutExpired:   # the unit outlived its RuntimeMaxSec: stopped hard
        run(ctl(mode, sdrun) + ["kill", "--signal=SIGKILL", unit + ".service"], 15, manager_env())
        stop_unit(mode, sdrun, unit)
        p.kill()
        out, err = p.communicate()
        return out or b"", None, True, ""
    err = err.decode("utf-8", "replace")
    m = re.search(r"Finished with result: (\S+)", err)
    if not m:   # systemd-run never ran it
        return out or b"", p.returncode, False, "the sandbox did not start it: %s" % last_line(err)
    result = m.group(1)
    if result == "timeout":
        return out or b"", None, True, ""
    note = "" if result in ("success", "exit-code") else "the test's unit ended with %s%s" % (
        result, " (over MemoryMax=%s)" % MEMORY_MAX if result == "oom-kill" else "")
    return out or b"", p.returncode, False, note


def descendants():
    """Every live pid of this test (its environment holds VFG_TEST_RUN=TEST_RUN): the test and the
    copies it started, which run in sessions of their own (setsid) and so escape its process group.
    This process is never among them (it has no VFG_TEST_RUN)."""
    marker = "VFG_TEST_RUN=" + TEST_RUN
    found = []
    try:
        pids = [int(n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return found
    for pid in pids:
        if pid != os.getpid() and pid_with_marker(pid, marker):
            found.append(pid)
    return found


def reap_descendants():
    """Everything this test started, after it (plain mode only): SIGTERM so each copy's cleanup
    runs, then SIGKILL the survivors; the test server's lock stays held until they are gone, so no
    next test overlaps them. The count left running (0 when all are gone)."""
    left = descendants()
    if not left:
        return 0
    for sig, wait in ((signal.SIGTERM, KILL_AFTER), (signal.SIGKILL, 2.0)):
        for pid in left:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        end = time.time() + wait
        while time.time() < end:
            if not descendants():
                return 0
            time.sleep(0.1)
    return len(descendants())


def run_plain(timeout, env, argv_test):
    """The test as a plain process (no sandbox): (output, exit code or None, timed out). Its own
    process group is killed on a timeout; reap_descendants (the caller) then catches the copies it
    started in sessions of their own, which the group kill does not reach."""
    proc = subprocess.Popen(["setsid", sys.executable, "-u"] + argv_test, cwd=TESTS, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    try:
        atomic_write(os.path.join(TESTS, "running.pid"), "%d\n" % proc.pid)
    except OSError:
        pass
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
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
    try:
        os.remove(os.path.join(TESTS, "running.pid"))
    except OSError:
        pass
    return out or b"", None if timed_out else proc.returncode, timed_out


# ------------------------------------------------------------------ the queue

def queue_entries():
    """The tickets in tests/queue/, oldest first: [(nanoseconds, pid, name), ...]."""
    try:
        names = os.listdir(QUEUE)
    except OSError:
        return []
    out = []
    for n in names:
        m = TICKET_RE.match(n)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), n))
    out.sort()
    return out


def make_ticket():
    """This waiter's ticket, <nanoseconds>-<pid>: its name and path. tests/queue/ is this user's."""
    os.makedirs(QUEUE, exist_ok=True)
    name = "%d-%d" % (time.time_ns(), os.getpid())
    path = os.path.join(QUEUE, name)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except FileExistsError:
        name = "%d-%d" % (time.time_ns(), os.getpid())
        path = os.path.join(QUEUE, name)
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o644))
    return name, path


def reap_queue(stale_after):
    """Tickets of a gone process, or older than stale_after s (a waiter SIGKILLed before it could
    remove its own), dropped by whoever sees them."""
    now = time.time()
    for ns, pid, n in queue_entries():
        path = os.path.join(QUEUE, n)
        try:
            dead = not os.path.exists("/proc/%d" % pid)
            old = now - os.stat(path).st_mtime > stale_after
        except OSError:
            continue
        if dead or old:
            try:
                os.remove(path)
            except OSError:
                pass


def wait_in_line(lock, name, began, queue, stale_after):
    """Take the lock, in first-come order: this waiter's ticket must be the oldest live one, and the
    lock free. True when taken; exit 5 past the queue's budget."""
    while True:
        reap_queue(stale_after)
        entries = queue_entries()
        if entries and entries[0][2] == name:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                pass
        if time.time() - began >= queue:
            die(5, "the test server was busy with other tests for all of the %g s its queue waits "
                   "(VFG_TEST_QUEUE_S)" % queue)
        time.sleep(0.2)


# ------------------------------------------------------------------ the run

def main():
    if not TASK_RE.match(ARGS.get("task") or ""):
        die(2, "%r is not a task's name" % ARGS.get("task"))
    mark = marker()
    port, hide = check_server(mark)
    timeout = float(ARGS.get("timeout") or 60)
    queue = float(ARGS.get("queue") if ARGS.get("queue") is not None else 300)
    boot = float(ARGS.get("boot") or 120)
    os.makedirs(TESTS, exist_ok=True)
    began = time.time()
    lock = os.open(os.path.join(TESTS, "tests.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    name, ticket = make_ticket()
    try:
        wait_in_line(lock, name, began, queue, queue + 2 * boot + timeout + 120)
        # holding the lock now: drop the ticket, so a server that frees up hands to the next waiter,
        # and whoever calls again queues behind it rather than racing for the lock
        try:
            os.remove(ticket)
        except OSError:
            pass
        held(time.time() - began, timeout, boot, port, hide, mark)
    finally:
        os.close(lock)
        try:
            os.remove(ticket)
        except OSError:
            pass


def held(queued, timeout, boot, port, hide, mark):
    task = ARGS["task"]
    lib = os.path.join(BASE, "scripts", "_lib")
    has_unit = unit_exists()
    for d in (RUN, STATE):   # the box's setup made them, the test user's to write too; elsewhere, here
        os.makedirs(d, exist_ok=True)
    mode, sdrun, unit, user, why_not = pick_sandbox(hide, mark)
    if mode != "none":
        stop_unit(mode, sdrun, unit)
    strays = kill_strays() if mode == "none" else 0
    took, did, warning = ready(boot, has_unit)
    path = os.path.join(TESTS, task + ".py")
    entries = os.path.join(TESTS, task + ".incantations.json")
    env = test_env(lib, os.path.join(TESTS, task + ".actions.json"), user)
    atomic_write(path, ARGS["test"].encode("utf-8"))
    atomic_write(env["VFG_TEST_ACTIONS"], json.dumps(ARGS.get("actions") or {}).encode("utf-8"))
    atomic_write(entries, json.dumps(ARGS.get("incantations") or []).encode("utf-8"))
    atomic_write(RUNNER, RUNNER_TEXT.encode("utf-8"))
    said = ["no wait in the queue" if queued < 0.5 else "%.1f s in the queue" % queued,
            warning or ("a fresh world" if not did else "a fresh world: %s, %.1f s" % ("; ".join(did), took)),
            {"system": "sandboxed: systemd-run as %s" % (user[0] if user else "?"),
             "user": "sandboxed: systemd-run --user, as this user (weaker: the live server's processes stay "
                     "visible)"}.get(mode) or "NOT sandboxed (%s): a plain process, its strays killed by "
                     "name afterwards but not guaranteed gone" % why_not]
    if mode != "none" and shared_tmp():
        said.append("/tmp shared: the test server is under it")
    if strays:
        said.append("stopped %d process(es) an earlier test left" % strays)
    print("%-10s %s (RCON %s): %s" % ("SERVER", MC, port, "; ".join(said)))
    sys.stdout.flush()
    began = time.time()
    note = ""
    argv_test = [RUNNER, entries, path]
    if mode == "none":
        out, code, timed_out = run_plain(timeout, env, argv_test)
    else:
        out, code, timed_out, note = run_sandboxed(mode, sdrun, unit, timeout, hide, user, env_file(env),
                                                   argv_test)
    seconds = time.time() - began
    if mode == "none":
        # no cgroup to take the copies with it: kill this test's descendants by their token, holding
        # the lock until they are gone so no next test overlaps them
        leftover = reap_descendants()
        if leftover:
            note = (note + "; " if note else "") + "%d process(es) this test started could not be killed" % leftover
    empty_run()
    if has_unit:
        ok, said = restart(False)   # the next test's fresh world, warming up now
        if not ok:
            note = (note + "; " if note else "") + "not restarted for the next test: %s" % last_line(said)
    sys.stdout.buffer.write(out)
    if out and not out.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    if note:
        sys.stdout.write("%-10s %s\n" % ("SERVER", note))
    sys.stdout.flush()
    print(json.dumps({"code": code, "seconds": round(seconds, 1), "timed_out": timed_out,
                      "queued": round(queued, 1), "restarted": bool(did)}))

main()
