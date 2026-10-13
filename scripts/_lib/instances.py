"""Start, stop and edit a copy: one run of an action's script, on EC2.

Python 3.9+, standard library only. Deployed with the kit, so the `stop` and `edit` scripts
`import instances`; vfg ships the same file with its scripts program (toolkit/remote/scripts.py),
so `vfg action run`, `vfg script list --fresh` and `vfg state` use this code too.

    instances.stop(copy)                   # the stop script
    instances.edit(copy, values, changed, live_only)   # the edit script, with what the edit action got
    box = instances.here()                 # in a script: the box it runs on, from its environment
    box = instances.Box("/opt/vfg", "/opt/mc", owner="Steve", poll=0.5, rcon=rcon)

    name, pid, proc = box.start(meta, as_=None)   # picks the copy's name and starts it
    box.start(meta, base="railgun_steve")  # the brain's own copy: railgun_steve, then railgun_steve_2
    box.start(meta, wait=1.5)              # Busy (a CopyError) when copies.lock stays held 1.5 s
    box.wait(name, proc, 20)               # {"running", "code", "result"}: ended, timed out or reported
    box.stop(name)                         # True when it was running; everything tagged goes
    box.edit(name, {"cooldown": 0})        # live fields into the live file, else a restart
    box.copy(name), box.copies()           # one copy as the PC reads it; every running copy

A copy's files in run/ (VFG_RUN_DIR):
    <copy>.pid          the pid of its process group, while it runs
    <copy>.log          what it printed
    <copy>.json         its meta: {"copy", "action", "script", "source", "server", "values" (every
                        field, checked on the PC), "live" (the Live fields' names), "runs", "task"
                        (a task's name, null, or cast for an incantation's), "started"}
    <copy>.values.json  live edits, {field: value}: the kit reads it again every VFG_LIVE_POLL_S
    <copy>.result.json  what kit.result() wrote last: {"summary", "facts", "at"}
    <copy>.error        a crash's last line (NameError: name 'x' is not defined), which mc.py
                        writes as it cleans up after an error nothing caught

A deployed script folder holds action_defaults.json, which vfg writes into each deploy of it:
{action: {"values": {field: its default, fixed values over them}, "live": [...]}} for each action
over that script, as the actions are on the PC then. start() lays a copy's values over its action's
(with_defaults): a binding made before its action gained a value still runs, with that value's
default.

A copy's name is `as=` when the run gives one (refused while that copy runs), else its stem:
the base the run gives (`vfg action run … --for Steve`: the brain's own copy,
<action>_<player>), else the action's name; the stem itself, else the next free <stem>_2,
_3 ...; it is picked, and the copy started, under run/copies.lock, so two runs at once never
take one name. Everything a copy makes carries the tag vfg_<copy>: mc.py kills it when the
copy is stopped (or fails), and a stop that had to SIGKILL sends that kill itself; a copy
that ends by itself hands it over to vfg_left_<copy>, so the next copy under its name, which
is free again, never takes it with its stop.
"""
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

COPY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
# What run/ may hold from before copies (vfg script run's instances, beam-a): read, never made.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
SOURCES = ("library", "made")
DEFAULTS = "action_defaults.json"   # in a deployed script folder: what vfg wrote of its actions
STOP_WAIT = 5.0       # SIGTERM, then SIGKILL after this many seconds
# A copy runs on the Python this runs on: the box's 3.12, /opt/vfg/python/bin/python3.
PYTHON = sys.executable or "python3"
CHECK_S = 2.0         # an until-stopped copy is checked this long after its start
# The box's secrets (/opt/vfg/.env, Contracts §11): a copy started by the bridge for a cast would
# inherit them from its environment, and no script needs them.
SECRETS = ("VFG_CLIENT_TOKEN", "TYPESAFE_API_KEY")


# The launch preset (scripts/_presets/launch.py) spares what it throws fall damage with a
# fall_damage_multiplier modifier, which is saved with a player, and stamps each throw with the
# game time in an objective. launch_sweep() is the one place that takes away what a throw left:
# the preset calls it at each throw and at its own cleanup, and Box.stop after stopping (or
# killing) a copy that holds the preset, so a copy killed mid-flight leaves nobody spared for good.
LAUNCH_MOD, LAUNCH_SCORE = "vfg:launch", "vfg_launch"
LAUNCH_STAND, LAUNCH_CHARGE = "vfg_launch_stand", "vfg_launch_charge"
LAUNCH_STALE = 40     # ticks: a stamp this old on someone standing on the ground is a throw that landed
_GAME_TIME = re.compile(r"game time is (\d+)")


def launch_tags(copy):
    """(the tag on what a copy threw that is in flight, the tag once it has left the ground, the
    tag once it has landed)."""
    g = "vfg_%s_launch" % copy
    return g, g + "_air", g + "_down"


def launch_sweep(rcon, copy=None):
    """Take away what a throw of the launch preset left, with rcon (a callable: command -> reply).

    With `copy` (a copy stopped, killed, or starting again): everything it threw that still
    carries its tag gets Slow Falling for 30 s while in the air or riding, then the modifier and
    its tags come off, and its stands are killed (a rider is set down). Then, whoever threw them:
    the modifier comes off whoever has stood on the ground 2 s past their last throw, and a stand
    or a staged wind charge thrown as long ago is killed. What is in the air, or newer, is left."""
    if copy:
        g, air, down = launch_tags(copy)
        rcon("effect give @e[tag=%s,nbt={OnGround:0b}] minecraft:slow_falling 30 0 true" % g)
        rcon("execute as @e[tag=vfg_%s_lr] on passengers run effect give @s minecraft:slow_falling 30 0 true" % copy)
        for sel in ("@e", "@a"):
            rcon("execute as %s[tag=%s] run attribute @s minecraft:fall_damage_multiplier modifier remove %s"
                 % (sel, g, LAUNCH_MOD))
            for tag in (g, air, down):
                rcon("tag %s[tag=%s] remove %s" % (sel, tag, tag))
        rcon("kill @e[tag=vfg_%s_lr]" % copy)
    m = _GAME_TIME.search(rcon("time query gametime") or "")
    if not m:
        return
    old = "scores={%s=..%d}" % (LAUNCH_SCORE, int(m.group(1)) - LAUNCH_STALE)
    rcon("execute as @e[%s,nbt={OnGround:1b}] run attribute @s minecraft:fall_damage_multiplier modifier remove %s"
         % (old, LAUNCH_MOD))
    rcon("kill @e[type=armor_stand,tag=%s,%s,nbt={OnGround:1b}]" % (LAUNCH_STAND, old))
    rcon("kill @e[type=wind_charge,tag=%s,%s]" % (LAUNCH_CHARGE, old))


class CopyError(Exception):
    """A start, stop or edit refused; the message is one line."""


class NotDeployed(CopyError):
    """The copy's script is not on the box."""


class Busy(CopyError):
    """run/copies.lock stayed held (another copy starting, or one stopping for an edit) past a
    start's wait."""


def now_iso(t=None):
    d = datetime.fromtimestamp(time.time() if t is None else t, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Atomically: a temp file beside it, then os.replace."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def has_marker(pid, marker):
    """True when `pid` is alive and its environment holds `marker`: a recycled pid never does."""
    try:
        with open("/proc/%d/environ" % int(pid), "rb") as f:
            return marker.encode() in f.read().split(b"\0")
    except (OSError, ValueError):
        return False


def gone(pid):
    """True once `pid` no longer exists or is a zombie."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] in ("Z", "X")
    except (OSError, IndexError):
        return True


def copy_name(base, n):
    """`galick_gun`, then `galick_gun_2`, `galick_gun_3` ..., cut to fit 48 characters."""
    if n < 2:
        return base[:48]
    suffix = "_%d" % n
    return base[:48 - len(suffix)].rstrip("_") + suffix


class Box(object):
    """One box's copies: `base` is /opt/vfg (scripts/, made/, run/), `mc` is /opt/mc."""

    def __init__(self, base, mc="/opt/mc", owner="", poll=0.5, rcon=None):
        self.base = base
        self.mc = mc
        self.owner = owner or ""
        self.poll = poll
        self.rcon = rcon      # a callable for a stop's last resort (kill @e[tag=...]), or None
        self.run = os.path.join(base, "run")

    # ---- files

    def path(self, name, ext):
        return os.path.join(self.run, name + ext)

    def pid(self, name):
        """The copy's pid while it runs; a stale pid file is removed. A process that lives but
        does not show the marker yet (between the execs of setsid, nohup and python, its
        environment cannot be read) is asked again for half a second before it is called stale."""
        path = self.path(name, ".pid")
        try:
            with open(path) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return None
        for _ in range(10):
            if has_marker(pid, "VFG_SCRIPT=" + name):
                return pid
            if gone(pid):
                break
            time.sleep(0.05)
        remove(path)
        return None

    def meta(self, name):
        """A copy's meta, filled in for one an older vfg started (an instance of a script)."""
        meta = read_json(self.path(name, ".json"))
        if not isinstance(meta, dict):
            meta = {}
        meta["copy"] = name
        meta.setdefault("script", meta.get("instance") or name)
        meta.setdefault("action", meta["script"])
        meta.setdefault("source", "library")
        if not isinstance(meta.get("values"), dict):
            meta["values"] = {}
        if not isinstance(meta.get("live"), list):
            meta["live"] = []
        meta.setdefault("runs", "until_stopped")
        meta.setdefault("task", None)
        meta.setdefault("started", None)
        return meta

    def live(self, name):
        got = read_json(self.path(name, ".values.json"), {})
        return got if isinstance(got, dict) else {}

    def result(self, name):
        got = read_json(self.path(name, ".result.json"))
        return got if isinstance(got, dict) and isinstance(got.get("summary"), str) else None

    def error(self, name):
        """The last line of the error a copy crashed on, as mc.py wrote it; None when it did not."""
        try:
            with open(self.path(name, ".error"), encoding="utf-8", errors="replace") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def folder(self, meta):
        """Where a copy's script is deployed: scripts/<script>, or made/<server>/scripts/<script>."""
        if meta.get("source") == "made":
            return os.path.join(self.base, "made", meta["server"], "scripts", meta["script"])
        return os.path.join(self.base, "scripts", meta["script"])

    def with_defaults(self, meta):
        """`meta` with its values laid over its action's as vfg deployed them with its script
        (DEFAULTS): a value the action has and the values lack takes the action's default. With
        "asked" (a cast's, incantations.cast_meta: the values its binding asked for and the caster
        fields) only those keep the bound value; the others follow the action as it is now, as
        vfg's refresh of the binding would make them. Without "asked" (a run, an older binding)
        every value given stays. A live field the action has now is live. No file, or no entry
        for the action: the meta as it is. "asked" never reaches the meta file."""
        meta = dict(meta)
        asked = meta.pop("asked", None)
        got = read_json(os.path.join(self.folder(meta), DEFAULTS), None)
        mine = got.get(meta.get("action")) if isinstance(got, dict) else None
        if not (isinstance(mine, dict) and isinstance(mine.get("values"), dict)):
            return meta
        defaults = mine["values"]
        given = meta.get("values") if isinstance(meta.get("values"), dict) else {}
        keep = None if asked is None else set(asked)
        values = dict((k, v if keep is None or k in keep or k not in defaults else defaults[k]) for k, v in given.items())
        values.update((k, v) for k, v in defaults.items() if k not in values)
        meta["values"] = values
        live = list(meta.get("live") or [])
        meta["live"] = live + [k for k in mine.get("live") or [] if k in values and k not in live]
        return meta

    def names(self):
        """Every copy with a meta or a pid file in run/, by name."""
        try:
            files = os.listdir(self.run)
        except OSError:
            return []
        found = set()
        for fn in files:
            for ext in (".json", ".pid"):
                if fn.endswith(ext) and not fn.endswith((".values.json", ".result.json")) \
                        and NAME_RE.match(fn[:-len(ext)]):
                    found.add(fn[:-len(ext)])
        return sorted(found)

    def copy(self, name):
        """A copy as the PC reads it: its meta, its values now (the live edits on its live
        fields), the live file as it is, its pid and since when (UTC, HH:MM:SS)."""
        meta = self.meta(name)
        live = self.live(name)
        values = dict(meta["values"])
        for k, v in live.items():
            if k in meta["live"] or not meta["live"]:
                values[k] = v
        pid = self.pid(name)
        since = None
        if pid:
            try:
                since = time.strftime("%H:%M:%S", time.gmtime(os.stat(self.path(name, ".pid")).st_mtime))
            except OSError:
                since = "?"
        meta.update({"values": values, "edits": live, "pid": pid, "since": since})
        return meta

    def copies(self):
        """Every copy that is running."""
        return [c for c in (self.copy(n) for n in self.names()) if c["pid"]]

    # ---- start

    def lock(self, wait=None):
        """run/copies.lock, held while a name is picked and its copy started; with `wait`, Busy
        once it has stayed held that many seconds."""
        os.makedirs(self.run, exist_ok=True)
        return _Flock(os.path.join(self.run, "copies.lock"), wait)

    def pick(self, action, as_=None, base=None):
        """The name for a new copy of `action` (call under lock()): `as_`, else the first free of
        `base` (cut to 48 characters), `base`_2, _3 ..., else of the action's name."""
        if as_:
            if not COPY_RE.match(as_):
                raise CopyError("as=%s is not a copy name (a-z, 0-9 and _, at most 48)" % as_)
            pid = self.pid(as_)
            if pid:
                raise CopyError("%s is running (pid %d): stop it first, or run it as another name" % (as_, pid))
            return as_
        if base is not None and not COPY_RE.match(base[:48]):
            raise CopyError("%s is not a copy name (a-z, 0-9 and _)" % base)
        stem = base or action
        n = 1
        while self.pid(copy_name(stem, n)):
            n += 1
        return copy_name(stem, n)

    def start(self, meta, as_=None, wait=None, base=None):
        """Name a copy and start it, detached: (name, pid, the Popen to wait on).

        `meta` holds action, script, source (and server for a made script), values, live,
        runs and task; its copy's earlier files (live edits, result, error) go. `base` is the
        name the copy takes in place of the action's, then with _2, _3 ... (pick). With `wait`,
        it waits at most that many seconds for copies.lock, then raises Busy."""
        for k in ("action", "script"):
            if not NAME_RE.match(meta.get(k) or ""):
                raise CopyError("%s is not a name: %r" % (k, meta.get(k)))
        if meta.get("source", "library") not in SOURCES:
            raise CopyError("source is library or made, not %r" % meta.get("source"))
        main_py = os.path.join(self.folder(meta), "main.py")
        if not os.path.isfile(main_py):
            raise NotDeployed("%s is not deployed (no %s)" % (meta["script"], main_py))
        meta = self.with_defaults(meta)
        with self.lock(wait):
            name = self.pick(meta["action"], as_, base)
            for ext in (".values.json", ".result.json", ".error"):
                remove(self.path(name, ext))
            meta = dict(meta, copy=name)
            pid, proc = self._spawn(meta)
        return name, pid, proc

    def _spawn(self, meta):
        """Write the meta, start main.py with nothing after it (values() reads the meta), write the pid."""
        name = meta["copy"]
        folder = self.folder(meta)
        log = self.path(name, ".log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("--- %s run %s (%s) ---\n" % (now_iso(), name, meta["action"]))
        env = dict((k, v) for k, v in os.environ.items() if k not in SECRETS)
        # VFG_SCRIPT marks the process as this copy's (pid()); mc.py names its tag vfg_<copy> from it.
        env["VFG_SCRIPT"] = name
        env["VFG_INSTANCE"] = name
        env["VFG_TASK"] = meta.get("task") or ""
        # The kit reads <VFG_RUN_DIR>/<copy>.json and its .values.json (every VFG_LIVE_POLL_S),
        # and kit.world.protected() the places nothing may touch from VFG_PROTECTED.
        env["VFG_RUN_DIR"] = self.run
        env["VFG_PROTECTED"] = os.path.join(self.base, "protected.json")
        env["VFG_LIVE_POLL_S"] = str(self.poll or 0.5)
        env["PYTHONPATH"] = os.path.join(self.base, "scripts", "_lib")
        env["PYTHONUNBUFFERED"] = "1"
        env["VFG_MC_PROPERTIES"] = os.path.join(self.mc, "server.properties")
        if self.owner:
            env["VFG_OWNER"] = self.owner
        else:
            env.pop("VFG_OWNER", None)
        meta["started"] = now_iso()
        write_json(self.path(name, ".json"), meta)
        logf = open(log, "ab")
        try:
            # No shell, and no start_new_session: the child is then not a group leader, so
            # `setsid` keeps its pid, which becomes the copy's (and its process group's).
            proc = subprocess.Popen(["setsid", "nohup", PYTHON, "-u", os.path.join(folder, "main.py")],
                                    cwd=folder, stdin=subprocess.DEVNULL, stdout=logf,
                                    stderr=subprocess.STDOUT, env=env, close_fds=True)
        finally:
            logf.close()
        tmp = self.path(name, ".pid.tmp")
        with open(tmp, "w") as f:
            f.write("%d\n" % proc.pid)
        os.replace(tmp, self.path(name, ".pid"))
        return proc.pid, proc

    def wait(self, name, proc, seconds, on_result=False):
        """Wait for a copy just started: until it ends, `seconds` pass, or (on_result) it
        writes a result. {"running": bool, "code": its exit code once it ended, "result"}."""
        deadline = time.time() + seconds
        result_path = self.path(name, ".result.json")
        while True:
            code = proc.poll()
            if code is not None:
                remove(self.path(name, ".pid"))
                return {"running": False, "code": code, "result": self.result(name)}
            if on_result and os.path.exists(result_path):
                got = self.result(name)
                if got is not None:
                    return {"running": True, "code": None, "result": got}
            if time.time() >= deadline:
                return {"running": True, "code": None, "result": self.result(name)}
            time.sleep(0.05)

    def tail(self, name, n=20):
        """The last `n` lines a copy printed, without its log's header."""
        try:
            with open(self.path(name, ".log"), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        if lines and lines[0].startswith("--- "):
            lines = lines[1:]
        return lines[-n:]

    # ---- stop and edit

    def stop(self, name):
        """SIGTERM its process group, SIGKILL after STOP_WAIT s; False when it was not running.

        Its cleanup (mc.py) removes everything tagged vfg_<copy>; after a SIGKILL that never
        ran, the kill is sent from here when the box was given an rcon. A copy whose script holds
        the launch preset gets launch_sweep(rcon, copy) after it, killed or not: what it threw
        is set down and nobody keeps its fall guard."""
        pid = self.pid(name)
        if not pid:
            return False
        launched = self.rcon is not None and self._holds_launch(name)
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            # A stop within milliseconds of the start: setsid has not made the group yet, so
            # the process alone gets it, before it has run anything of the copy's.
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        except PermissionError as e:
            raise CopyError("cannot stop %s: %s" % (name, e))
        deadline = time.time() + STOP_WAIT
        while time.time() < deadline and not gone(pid):
            time.sleep(0.05)
        killed = not gone(pid)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        remove(self.path(name, ".pid"))
        if launched:
            try:
                launch_sweep(self.rcon, name)
            except Exception:
                pass
        if killed and self.rcon is not None:
            try:
                self.rcon("kill @e[tag=vfg_%s]" % name)
            except Exception:
                pass
        return True

    def _holds_launch(self, name):
        """True when the copy's script calls launch_sweep: it holds the launch preset."""
        try:
            with open(os.path.join(self.folder(self.meta(name)), "main.py"), encoding="utf-8", errors="replace") as f:
                return "launch_sweep(" in f.read()
        except (OSError, KeyError):
            return False

    def edit(self, name, values, changed=None, live_only=None, wait=CHECK_S):
        """Change values on a running copy; they were checked on the PC (check_edit, against
        the copy's action and its values then).

        `values` is the full new set and `changed` the fields that change, as the edit
        action gets them; or, with changed None, `values` holds only the fields to change.
        When every changed field is live (live_only; a field the copy's meta does not list as
        live never is), they go into its live file and it reads them within VFG_LIVE_POLL_S;
        otherwise it restarts under its name with the new set, live edits folded in.
        {"copy", "action", "live": bool, "changed": [...], "values": all of them now,
         "restarted": the new pid or None, "running", "code", "result"}"""
        if not isinstance(values, dict):
            raise CopyError("the values are a dict, not %r" % (values,))
        if changed is None:
            changed = list(values)
        changed = [k for k in changed if k in values]
        if not changed:
            raise CopyError("nothing to change")
        if not self.pid(name):
            raise CopyError("%s is not running" % name)
        meta = self.meta(name)
        stranger = [k for k in changed if meta["values"] and k not in meta["values"]]
        if stranger:
            raise CopyError("%s runs %s, which has no %s: it was not changed (it may have been started "
                            "again as another action)" % (name, meta["action"], ", ".join(stranger)))
        live = all(k in meta["live"] for k in changed)
        if live_only is not None:
            live = live and bool(live_only)
        out = {"copy": name, "action": meta["action"], "changed": changed, "restarted": None,
               "running": True, "code": None, "result": None}
        if live:
            # Read, change and write the live file under the copies lock: two edits at once
            # both land, neither writing over the other.
            with self.lock():
                cur = self.live(name)
                cur.update(dict((k, values[k]) for k in changed))
                write_json(self.path(name, ".values.json"), cur)
            out.update(live=True, values=self.copy(name)["values"])
            return out
        new = self.copy(name)["values"]
        new.update(values if len(values) > len(changed) else dict((k, values[k]) for k in changed))
        with self.lock():
            # The name stays this copy's: no other run can take it between the stop and the start.
            self.stop(name)
            for ext in (".values.json", ".result.json", ".error"):
                remove(self.path(name, ext))
            meta["values"] = new
            for k in ("edits", "pid", "since"):
                meta.pop(k, None)
            pid, proc = self._spawn(meta)
        out.update(live=False, values=new, restarted=pid)
        out.update(self.wait(name, proc, wait, on_result=True))
        return out


class _Flock(object):
    """A lock file in run/, held with flock. Opened read-only, never through a link, and only when it
    is a plain file: on the test server run/ is the test user's to write too (vfg test), and vfg's
    deploy opens its lock there as the box's own user."""

    def __init__(self, path, wait=None):
        self.path = path
        self.wait = wait

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o664)
        if not stat.S_ISREG(os.fstat(self.fd).st_mode):
            os.close(self.fd)
            raise CopyError("%s is not a plain file: not used as a lock" % self.path)
        if self.wait is None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    raise Busy("%s stayed held for %.1f s" % (os.path.basename(self.path), self.wait))
                time.sleep(0.005)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def stop(copy):
    """The stop script's: stop a running copy on this box, and everything it made. True when
    it was running."""
    import mc
    return here(rcon=mc.rcon).stop(copy)


def edit(copy, values, changed, live_only):
    """The edit script's: change a running copy's values as the edit action got them (`values`
    the full new set, `changed` the fields that change, `live_only` whether each is live).
    Box.edit's answer; CopyError when it was not changed."""
    import mc
    return here(rcon=mc.rcon).edit(copy, values, changed, live_only)


def here(rcon=None):
    """The box a script runs on, from the environment its copy was started with; under vfg test
    (VFG_TEST=1) never the live server's /opt/vfg or /opt/mc by default: CopyError."""
    run = os.environ.get("VFG_RUN_DIR")
    props = os.environ.get("VFG_MC_PROPERTIES")
    if os.environ.get("VFG_TEST") == "1" and not (run and props):
        raise CopyError("VFG_RUN_DIR and VFG_MC_PROPERTIES must be set under vfg test: a test never falls back "
                        "to the live server's /opt/vfg or /opt/mc")
    run = run or "/opt/vfg/run"
    props = props or "/opt/mc/server.properties"
    try:
        poll = float(os.environ.get("VFG_LIVE_POLL_S") or 0.5)
    except ValueError:
        poll = 0.5
    return Box(os.path.dirname(run.rstrip("/")), os.path.dirname(props), owner=os.environ.get("VFG_OWNER", ""),
               poll=poll, rcon=rcon)
