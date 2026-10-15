# common.py: prepended to every program vfg sends to EC2.
# Python 3.9+, standard library only (the box runs it on its 3.12). The PC appends `ARGS = json.loads(...)`
# and then the program itself; the program answers with stdout, one line on
# stderr and its exit code.
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime, timezone


def die(code, msg):
    """Print one line on stderr and exit with `code`."""
    try:
        sys.stdout.flush()
    except Exception:
        pass
    sys.stderr.write("vfg: " + msg + "\n")
    sys.stderr.flush()
    sys.exit(code)


def warn(msg):
    sys.stderr.write("vfg: " + msg + "\n")
    sys.stderr.flush()


def now_iso(t=None):
    """ISO-8601 UTC with milliseconds: 2026-10-07T18:22:31.412Z."""
    if t is None:
        t = time.time()
    d = datetime.fromtimestamp(t, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def hms(t=None):
    if t is None:
        t = time.time()
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S")


def atomic_write(path, data, mode=0o644):
    """Write `path.tmp`, then os.replace it over `path`."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(t=None):
    """48 bits of milliseconds and 80 random bits, Crockford base32."""
    ms = int((time.time() if t is None else t) * 1000)
    n = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def db_uri(path, mode):
    from urllib.parse import quote
    return "file:" + quote(path) + "?mode=" + mode


def last_feed_seq(path):
    """The highest seq in the last 4 KiB of feed.log, or None without a feed."""
    import re
    rx = re.compile(rb"^([A-Z]+)#(\d+) ")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read()
    except OSError:
        return None
    best = 0
    for line in tail.split(b"\n"):
        m = rx.match(line)
        if m:
            best = max(best, int(m.group(2)))
    return best


def read_props(path):
    props = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in "#!" or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
    return props


# ---------------------------------------------------------------- tasks

# The brain's tasks in vfg.db (Prototype_Named_Tasks): one row per implementer task, keyed by its
# name. The bridge creates the table, `vfg task set` (tasks.py) alone writes it, and tasks.py's list
# and context.py's --json read it here.
TASK_COLUMNS = ("name", "description", "player", "status", "line", "brief", "made", "variables",
                "session", "result", "created_at", "updated_at", "closed_at")
NO_TASKS = "vfg.db has no tasks table yet — restart vfg-bridge, which adds it"


def has_tasks(con):
    """True when vfg.db has the tasks table, which the bridge adds as it starts."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone() is not None


def json_or_none(text):
    """A column's JSON text decoded; None when it is NULL or not JSON."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def task_rows(con, open_only=False, name=None):
    """Every task (only the open ones; only `name`), oldest created first, made and variables
    decoded (None when NULL or not JSON)."""
    where, params = [], []
    if open_only:
        where.append("status = 'open'")
    if name is not None:
        where.append("name = ?")
        params.append(name)
    sql = "SELECT %s FROM tasks%s ORDER BY created_at, name" % (
        ", ".join(TASK_COLUMNS), " WHERE " + " AND ".join(where) if where else "")
    out = []
    for row in con.execute(sql, params):
        task = dict(zip(TASK_COLUMNS, row))
        task["made"] = json_or_none(task["made"])
        task["variables"] = json_or_none(task["variables"])
        out.append(task)
    return out


# ---------------------------------------------------------------- RCON

class RconDown(Exception):
    """Minecraft is not answering RCON: exit 5."""


def mc_refused(out):
    """Minecraft's own refusals of a command."""
    return (out.startswith("Unknown or incomplete command")
            or out.startswith("Incorrect argument for command")
            or "<--[HERE]" in out)


RCON_LOCK_WAIT = 15.0


def rcon_lock_path(port):
    """The lock file every RCON client of the server on `port` holds from send to reply:
    VFG_RCON_LOCK, else /tmp/vfg-rcon-<port>.lock, as the engine's (engine/vfg_engine/mc/rcon.py)
    lock_path_for()."""
    return os.environ.get("VFG_RCON_LOCK") or "/tmp/vfg-rcon-%d.lock" % port


def open_lock(path):
    """A descriptor flock can hold on `path`, made if missing. Read-only and without O_CREAT
    when it exists: flock needs neither write access nor ownership, and a sticky /tmp
    refuses O_CREAT on another user's file (fs.protected_regular). Never through a link, and only
    when it is a plain file (OSError otherwise): /tmp is everyone's to write."""
    import stat
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def plain(fd):
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("%s is not a plain file: not used as a lock" % path)
        return fd
    for _ in range(3):
        try:
            return plain(os.open(path, flags))
        except FileNotFoundError:
            pass
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            os.fchmod(fd, 0o666)
        except OSError:
            pass
        return fd
    return plain(os.open(path, flags))


def held_locks():
    """This process's RCON locks, {path: [thread ident, depth, the fd that holds the flock]},
    shared through one entry in sys.modules with every other RCON client loaded in the process
    (the engine's and realmc's are the same), so a thread that already holds the lock takes it again
    without waiting on itself; other threads and processes still wait."""
    import threading
    h = sys.modules.get("_vfg_rcon_held")
    if h is None:
        h = type(sys)("_vfg_rcon_held")
        h.locks, h.guard = {}, threading.RLock()
        h = sys.modules.setdefault("_vfg_rcon_held", h)
    return h


def take_lock(path, fd, deadline=None):
    """Hold the RCON lock `path` through `fd` (re-entrant per thread); False past `deadline`."""
    import fcntl
    import threading
    held, me = held_locks(), threading.get_ident()
    with held.guard:
        got = held.locks.get(path)
        if got and got[0] == me:
            got[1] += 1
            return True
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if deadline is not None else 0))
            break
        except OSError:
            if deadline is None or time.time() >= deadline:
                return False
            time.sleep(0.002)
    with held.guard:
        held.locks[path] = [me, 1, fd]
    return True


def drop_lock(path, fd):
    """Let go of take_lock's hold: count down, or unlock the fd that took it."""
    import fcntl
    import threading
    held, me = held_locks(), threading.get_ident()
    with held.guard:
        got = held.locks.get(path)
        if got and got[0] == me:
            if got[1] > 1:
                got[1] -= 1
                return
            del held.locks[path]
            fd = got[2]
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, TypeError):
        pass


class Rcon(object):
    """A vanilla RCON client: one packet per write, a reply read by size.

    Vanilla reads each request into a 1460-byte buffer and expects exactly
    one packet per read, so nothing is sent before the last packet was
    answered (no empty sentinel packet). A reply longer than 4096
    characters arrives as several packets: while the last body was 4000
    bytes or more, one more packet is read with a 1 s timeout.

    Vanilla also answers every RCON connection from one shared buffer, so two
    clients with a command out at once get each other's output glued into
    their replies. Each command therefore holds rcon_lock_path(port) (flock)
    from its send to its last reply packet, as every script's mc.rcon does,
    and lets it go between commands so nobody waits long."""

    def __init__(self, mc_dir):
        path = os.path.join(mc_dir, "server.properties")
        try:
            props = read_props(path)
        except OSError:
            raise RconDown("minecraft is not set up (%s missing)" % path)
        try:
            self.port = int(props.get("rcon.port") or 25575)
        except ValueError:
            self.port = 25575
        self.lock_path = rcon_lock_path(self.port)
        self._lock_fd = None
        refused = "minecraft is not running (RCON on 127.0.0.1:%d refused)" % self.port
        if props.get("enable-rcon", "false").lower() != "true":
            raise RconDown(refused)
        password = props.get("rcon.password", "")
        try:
            self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        except socket.timeout:
            raise RconDown("minecraft did not answer RCON within 10 s")
        except OSError:
            raise RconDown(refused)
        del props
        self._send(1, 3, password)
        rid, _typ, _body = self._read()
        password = None
        if rid == -1:
            raise RconDown("RCON refused the password")

    def _send(self, rid, typ, body):
        b = body.encode("utf-8")
        self.sock.sendall(struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\x00\x00")

    def _exact(self, n):
        chunks = []
        while n > 0:
            try:
                c = self.sock.recv(n)
            except socket.timeout:
                raise
            except OSError as e:
                raise RconDown("minecraft dropped the RCON connection (%s)" % e)
            if not c:
                raise RconDown("minecraft closed the RCON connection")
            chunks.append(c)
            n -= len(c)
        return b"".join(chunks)

    def _read(self):
        try:
            (length,) = struct.unpack("<i", self._exact(4))
            if length < 10 or length > (1 << 20):
                raise RconDown("minecraft sent a bad RCON packet")
            data = self._exact(length)
        except socket.timeout:
            raise RconDown("minecraft did not answer RCON within 10 s")
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2]

    def _lock(self):
        """Hold the RCON lock, waiting at most RCON_LOCK_WAIT seconds."""
        if self._lock_fd is None:
            try:
                self._lock_fd = open_lock(self.lock_path)
            except OSError as e:
                raise RconDown("cannot open the RCON lock %s (%s)" % (self.lock_path, e.strerror or e))
        if not take_lock(self.lock_path, self._lock_fd, time.time() + RCON_LOCK_WAIT):
            raise RconDown("another RCON client held %s for %d s" % (self.lock_path, RCON_LOCK_WAIT))

    def _unlock(self):
        drop_lock(self.lock_path, self._lock_fd)

    def command(self, cmd):
        self._lock()
        try:
            self._send(2, 2, cmd)
            rid, _typ, body = self._read()
            parts = [body]
            while len(body) >= 4000:
                self.sock.settimeout(1.0)
                try:
                    (length,) = struct.unpack("<i", self._exact(4))
                    data = self._exact(length)
                    body = data[8:-2]
                    parts.append(body)
                except socket.timeout:
                    break
                finally:
                    self.sock.settimeout(10)
        finally:
            self._unlock()
        return b"".join(parts).decode("utf-8", errors="replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
