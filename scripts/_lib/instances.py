"""Start, stop and edit a copy: one run of an action's script, on EC2.

Python 3.9+, standard library only. Deployed with the kit, so the `stop` and `edit` scripts
`import instances`; vfg ships the same file with its scripts program (toolkit/remote/scripts.py),
so `vfg action run`, `vfg script list --fresh` and `vfg state` use this code too.

    instances.stop(copy)                   # the stop script
    instances.edit(copy, values, changed, live_only)   # the edit script, with what the edit action got
    box = instances.here()                 # in a script: the box it runs on, from its environment
    box = instances.Box("/opt/vfg", "/opt/mc", owner="Steve", poll=0.5, rcon=rcon)

    name, pid, proc = box.start(meta, as_=None)   # picks the copy's name and starts it
    box.wait(name, proc, 20)               # {"running", "code", "result"}: ended, timed out or reported
    box.stop(name)                         # True when it was running; everything tagged goes
    box.edit(name, {"cooldown": 0})        # live fields into the live file, else a restart
    box.copy(name), box.copies()           # one copy as the PC reads it; every running copy

A copy's files in run/ (VFG_RUN_DIR):
    <copy>.pid          the pid of its process group, while it runs
    <copy>.log          what it printed
    <copy>.json         its meta: {"copy", "action", "script", "source", "server", "values" (every
                        field, checked on the PC), "live" (the Live fields' names), "runs", "task",
                        "started"}
    <copy>.values.json  live edits, {field: value}: the kit reads it again every VFG_LIVE_POLL_S
    <copy>.result.json  what kit.result() wrote last: {"summary", "facts", "at"}

A copy's name is `as=` when the run gives one (refused while that copy runs), else the action's
name, else the next free <action>_2, _3 ...; it is picked, and the copy started, under
run/copies.lock, so two runs at once never take one name. Everything a copy makes carries the
tag vfg_<copy>; mc.py removes it when the copy is stopped or ends by itself, and a stop that
had to SIGKILL sends that kill itself.
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

COPY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
# What run/ may hold from before copies (vfg script run's instances, beam-a): read, never made.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
SOURCES = ("library", "made")
STOP_WAIT = 5.0       # SIGTERM, then SIGKILL after this many seconds
# A copy runs on the Python this runs on: the box's 3.12, /opt/vfg/python/bin/python3.
PYTHON = sys.executable or "python3"
CHECK_S = 2.0         # an until-stopped copy is checked this long after its start


class CopyError(Exception):
    """A start, stop or edit refused; the message is one line."""


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
        """The copy's pid while it runs; a stale pid file is removed."""
        path = self.path(name, ".pid")
        try:
            with open(path) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return None
        if has_marker(pid, "VFG_SCRIPT=" + name):
            return pid
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

    def folder(self, meta):
        """Where a copy's script is deployed: scripts/<script>, or made/<server>/scripts/<script>."""
        if meta.get("source") == "made":
            return os.path.join(self.base, "made", meta["server"], "scripts", meta["script"])
        return os.path.join(self.base, "scripts", meta["script"])

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

    def lock(self):
        """run/copies.lock, held while a name is picked and its copy started."""
        os.makedirs(self.run, exist_ok=True)
        return _Flock(os.path.join(self.run, "copies.lock"))

    def pick(self, action, as_=None):
        """The name for a new copy of `action` (call under lock())."""
        if as_:
            if not COPY_RE.match(as_):
                raise CopyError("as=%s is not a copy name (a-z, 0-9 and _, at most 48)" % as_)
            pid = self.pid(as_)
            if pid:
                raise CopyError("%s is running (pid %d): stop it first, or run it as another name" % (as_, pid))
            return as_
        n = 1
        while self.pid(copy_name(action, n)):
            n += 1
        return copy_name(action, n)

    def start(self, meta, as_=None):
        """Name a copy and start it, detached: (name, pid, the Popen to wait on).

        `meta` holds action, script, source (and server for a made script), values, live,
        runs and task; its copy's earlier files (live edits, result) go."""
        for k in ("action", "script"):
            if not NAME_RE.match(meta.get(k) or ""):
                raise CopyError("%s is not a name: %r" % (k, meta.get(k)))
        if meta.get("source", "library") not in SOURCES:
            raise CopyError("source is library or made, not %r" % meta.get("source"))
        main_py = os.path.join(self.folder(meta), "main.py")
        if not os.path.isfile(main_py):
            raise CopyError("%s is not deployed (no %s)" % (meta["script"], main_py))
        with self.lock():
            name = self.pick(meta["action"], as_)
            for ext in (".values.json", ".result.json"):
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
        env = dict(os.environ)
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
        ran, the kill is sent from here when the box was given an rcon."""
        pid = self.pid(name)
        if not pid:
            return False
        try:
            os.killpg(pid, signal.SIGTERM)
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
        if killed and self.rcon is not None:
            try:
                self.rcon("kill @e[tag=vfg_%s]" % name)
            except Exception:
                pass
        return True

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
            for ext in (".values.json", ".result.json"):
                remove(self.path(name, ext))
            meta["values"] = new
            for k in ("edits", "pid", "since"):
                meta.pop(k, None)
            pid, proc = self._spawn(meta)
        out.update(live=False, values=new, restarted=pid)
        out.update(self.wait(name, proc, wait, on_result=True))
        return out


class _Flock(object):
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

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
    """The box a script runs on, from the environment its copy was started with."""
    run = os.environ.get("VFG_RUN_DIR") or "/opt/vfg/run"
    props = os.environ.get("VFG_MC_PROPERTIES") or "/opt/mc/server.properties"
    try:
        poll = float(os.environ.get("VFG_LIVE_POLL_S") or 0.5)
    except ValueError:
        poll = 0.5
    return Box(os.path.dirname(run.rstrip("/")), os.path.dirname(props), owner=os.environ.get("VFG_OWNER", ""),
               poll=poll, rcon=rcon)
