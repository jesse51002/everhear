#!/usr/bin/env python3
"""realmc: real Minecraft Java 26.3 servers on this PC, each with a stand-in for the box around
it, so vfg, its remote programs and every script can be tried against the real thing, no EC2.
Several run side by side, one per name, each with its own world, ports and stand-in.

    integration/realmc/up [NAME] [--fresh | --regen] [--vanilla] [--test-server]
                                                                    start NAME (default main)
    integration/realmc/down [NAME | --all] [--purge-key]            stop it, its test server and scripts
    eval "$(integration/realmc/env [NAME])"                         point vfg (and `import mc`) at it
    integration/realmc/realmc.py list                               every server, running or not
    integration/realmc/realmc.py status [NAME] [--json]             one server; exit 0 if it runs
    integration/realmc/realmc.py cmd [--name NAME] <command…>       one RCON command, straight to it
    integration/realmc/realmc.py testserver [NAME] [--fresh|--stop] NAME's test server, as the box's
    integration/realmc/realmc.py carpet                             fetch Carpet 26.3 (official release)
    integration/realmc/realmc.py key                                copy the TypeSafe key in, unprinted

NAME defaults to $REALMC_NAME (which env exports), else main. Everything is under
integration/realmc/.server/ (gitignored; REALMC_ROOT puts it elsewhere):

    shared/server.jar     the box's own jar, out of mc-backup.tgz; shared/libraries/ and
                          shared/versions/ are what it unpacks, plus Fabric Loader's libraries
    shared/mods/          Carpet, when fetched; every server loads it
    shared/env            the box's .env (mode 600): names, and the TypeSafe key once `key` ran
    template/world/       a world made once from SEED, with the arena, the area around it
                          generated; each server's world starts as a copy of it
    <NAME>/mc/            one server's folder, standing in for /opt/mc: server.properties (its
                          own ports and password), world/, console.log; links to shared/
    <NAME>/vfg/           standing in for /opt/vfg: lib/vfg_feed.py, vfg.db, scripts/, made/, run/,
                          .env (a link to shared/env), python/bin/python3 (the box's Python 3.12)
    <NAME>/home/          HOME for vfg; home/.vfg is its VFG_HOME (config and a fake key)
    <NAME>/box/           the box's PATH (bin/python3 is Python 3.9, Amazon Linux's own) and
                          FAKE_UNITS (units/: minecraft's state; minecraft-test, a unit backed by
                          the real test server, run by `realmc.py unit`)
    <NAME>/mc-test/       its test server (testserver NAME, up --test-server), standing in for
    <NAME>/vfg-test/      /opt/mc-test and /opt/vfg-test, laid out by bridge/deploy/testserver.py

Python 3.9+, standard library only, like toolkit/vfg. Nothing here prints the RCON password or
the key, or writes to ~/.vfg or ~/.ssh. From the owner's ~/.vfg/config it reads two keys and
nothing else: VFG_ACCEPT_EULA (their acceptance of Minecraft's EULA) and VFG_BACKUP_DIR.
"""
import argparse
import fcntl
import glob
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir  # never $HOME: under vfg it is the stand-in's
ROOT = os.path.realpath(os.path.expanduser(os.environ.get("REALMC_ROOT") or os.path.join(HERE, ".server")))
SHARED = os.path.join(ROOT, "shared")
TEMPLATE = os.path.join(ROOT, "template")
TEMPLATE_INFO = os.path.join(ROOT, "template.json")
SHARED_ENV = os.path.join(SHARED, "env")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
RESERVED = ("shared", "template", "lock")

VERSION = "26.3"
# One fixed seed: a world made again from it has the same terrain (same version, same seed).
SEED = "20261009"
ARENA_AT = [(0, 0), (32, 0), (0, 32), (-32, 0), (0, -32), (32, 32), (-32, -32), (32, -32), (-32, 32)]
ARENA_HALF = 16   # the arena: a grass floor 32 x 32 at the first dry spot above, cleared 32 up
ARENA_AIR = 32
PREGEN = 176      # the template has every chunk this far from the arena: view-distance 10 and more
KEEP_LOADED = 32  # forceloaded for good around the arena, so commands work with nobody on
MARKER = "REALMC_SERVER="  # in a server's environment: which folder its pid belongs to

# The box's server.properties (out of mc-backup.tgz) is where a server's starts; these keys are
# then set for test servers on one PC. Everything else stays as the box has it.
OVERRIDES = [
    ("server-ip", "127.0.0.1"),            # the game port and RCON answer on loopback only
    ("online-mode", "false"),              # test names and Carpet's fake players; nobody else can reach it
    ("enforce-secure-profile", "false"),
    ("white-list", "false"),
    ("enforce-whitelist", "false"),
    ("enable-rcon", "true"),
    ("broadcast-rcon-to-ops", "false"),
    ("broadcast-console-to-ops", "false"),
    ("enable-query", "false"),
    ("management-server-enabled", "false"),
    ("log-ips", "false"),
    ("spawn-protection", "0"),
    ("view-distance", "10"),               # the box's: a script reaching 160 blocks from a player finds them loaded
    ("simulation-distance", "6"),          # below the box's 10: less ticking per server, several side by side
    ("max-tick-time", "60000"),            # the watchdog's default: a minute-long tick is a hang
    ("pause-when-empty-seconds", "0"),     # the box pauses after 60 s with nobody on; tests run with nobody
    ("sync-chunk-writes", "false"),        # faster saves; up --fresh mends a world a crash left
    ("level-name", "world"),
    ("level-type", "minecraft\\:normal"),
    ("level-seed", SEED),
    ("generate-structures", "true"),
    ("motd", "realmc test server"),
]
# Never carried over from the box's file: its secrets, and what each server sets for itself.
NOT_FROM_BOX = ("rcon.password", "management-server-secret", "rcon.port", "server-port", "level-seed")
# What the box sets, for when mc-backup.tgz's server.properties is not at hand.
BOX_SETTINGS = [("difficulty", "hard"), ("gamemode", "survival"), ("allow-flight", "true"),
                ("max-players", "20"), ("function-permission-level", "2"), ("op-permission-level", "4"),
                ("player-idle-timeout", "0"), ("rate-limit", "0")]

# The test server beside a realmc server (testserver NAME, up --test-server) is laid out by the
# box's own setup, bridge/deploy/testserver.py, which is also the one source of the Fabric and
# Carpet pins every realmc server runs on.
sys.path.insert(0, os.path.join(REPO, "bridge", "deploy"))
import testserver  # noqa: E402

# Carpet for 26.3, for fake players: gnembon/fabric-carpet's release v26.3, as GitHub lists it.
CARPET_JAR, CARPET_URL = testserver.CARPET_JAR, testserver.CARPET_URL
CARPET_SIZE, CARPET_SHA256 = testserver.CARPET_SIZE, testserver.CARPET_SHA256
# Fabric Loader 0.19.5, what meta.fabricmc.net lists for 26.3 (no intermediary: 26.x ships
# unobfuscated), and its libraries, with Maven's sha1s. Copied from where they already are on
# this PC (a Minecraft launcher, another server, the Gradle cache), never fetched.
FABRIC_MAIN, FABRIC_LIBS, MAVEN = testserver.FABRIC_MAIN, testserver.FABRIC_LIBS, testserver.MAVEN

EULA_TEXT = (
    "# The owner accepted Minecraft's EULA (https://aka.ms/MinecraftEULA): VFG_ACCEPT_EULA=true in\n"
    "# ~/.vfg/config, the line deploy.sh provision needs for the box. These local test servers run\n"
    "# the same server; integration/realmc/up writes this file only while that line is there.\n"
    "eula=true\n")


class Fail(Exception):
    """Exit with `code` and `msg`: 1 failed, 2 bad arguments, 3 not set up."""

    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg


def say(msg):
    """Progress and status, on stdout (stderr while `env` prints what eval reads)."""
    sys.stdout.write("realmc: " + msg + "\n")
    sys.stdout.flush()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path, algo="sha1"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_file(path, data, mode=0o644):
    """Write path.tmp, then replace path with it."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def tail(path, offset=0):
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def link(target, path):
    """`path` is a symlink to `target`, whatever was there."""
    if os.path.islink(path) and os.readlink(path) == target:
        return
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)
    os.symlink(target, path)


def copy_tree(src, dest):
    """A world copy: a reflink on btrfs (instant), a plain copy elsewhere."""
    shutil.rmtree(dest, ignore_errors=True)
    if subprocess.run(["cp", "-a", "--reflink=auto", src, dest], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest, symlinks=True)


# ------------------------------------------------------------- properties

def read_props(path):
    """KEY=value lines, as the box's toolkit reads them (no unescaping)."""
    props = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#!" or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    except OSError:
        pass
    return props


def line_key(line):
    if "=" not in line or line.lstrip().startswith(("#", "!")):
        return None
    return line.split("=", 1)[0].strip()


def merge_lines(lines, updates):
    """Set each key in `lines` (KEY=value text lines): replaced where it is, added where not."""
    lines = list(lines)
    for key, value in updates:
        line = "%s=%s" % (key, value)
        for i, old in enumerate(lines):
            if line_key(old) == key:
                lines[i] = line
                break
        else:
            lines.append(line)
    return lines


def owner_config(key):
    """One key from the owner's own ~/.vfg/config, read and nothing more."""
    try:
        with open(os.path.join(REAL_HOME, ".vfg", "config"), encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    return v
    except OSError:
        pass
    return ""


def backup_file(name):
    folder = owner_config("VFG_BACKUP_DIR") or os.path.join(REAL_HOME, "Documents", "MinecraftServers", "aws-backup")
    if folder.startswith("~"):
        folder = REAL_HOME + folder[1:]
    return os.path.join(folder, name)


# ------------------------------------------------------------------ RCON

def rcon_lock_path(port):
    """The lock file every RCON client of the server on `port` holds from send to reply, as
    scripts/_lib/mc.py and toolkit/remote/common.py do: VFG_RCON_LOCK, else
    /tmp/vfg-rcon-<port>.lock (never $TMPDIR: the stand-in's scripts do not see this shell's)."""
    return os.environ.get("VFG_RCON_LOCK") or "/tmp/vfg-rcon-%d.lock" % port


def open_lock(path):
    """A descriptor flock can hold on `path`, made if missing; read-only, and without O_CREAT
    when it exists (flock needs neither write access nor ownership)."""
    for _ in range(3):
        try:
            return os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            pass
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            os.fchmod(fd, 0o666)
        except OSError:
            pass
        return fd
    return os.open(path, os.O_RDONLY)


def held_locks():
    """This process's RCON locks, {path: [thread ident, depth, the fd that holds the flock]},
    shared through one entry in sys.modules with every other RCON client loaded in the process
    (scripts/_lib/mc.py's is the same), so a thread that already holds the lock takes it again
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
    """A vanilla RCON client, like toolkit/remote/common.py's: one packet per write (vanilla
    reads one packet per read), and a reply over 4096 characters read in parts. Vanilla answers
    every connection from one shared buffer, so each command holds rcon_lock_path(port) from
    its send to its reply, as scripts and vfg do; otherwise replies get each other's output."""

    def __init__(self, props, timeout=10.0):
        p = read_props(props)
        try:
            port = int(p.get("rcon.port") or 25575)
        except ValueError:
            port = 25575
        self.port = port
        self.lock_path = rcon_lock_path(port)
        self.lock_fd = open_lock(self.lock_path)
        try:
            self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        except OSError:
            os.close(self.lock_fd)   # `up` retries while the server starts: no fd per try
            raise
        self._send(1, 3, p.get("rcon.password", ""))
        del p
        rid, _typ, _body = self._read()
        if rid == -1:
            self.close()
            raise OSError("RCON refused the password")

    def _send(self, rid, typ, body):
        b = body.encode("utf-8")
        self.sock.sendall(struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\x00\x00")

    def _exact(self, n):
        chunks = []
        while n > 0:
            c = self.sock.recv(n)
            if not c:
                raise OSError("the server closed the RCON connection")
            chunks.append(c)
            n -= len(c)
        return b"".join(chunks)

    def _read(self):
        (length,) = struct.unpack("<i", self._exact(4))
        if length < 10 or length > (1 << 20):
            raise OSError("a bad RCON packet")
        data = self._exact(length)
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2]

    def command(self, cmd, timeout=None):
        """Run one command; its output as text. `timeout`: seconds for this one's answer (default
        10), for a command the server takes long over, like forceload generating chunks."""
        take_lock(self.lock_path, self.lock_fd)
        try:
            self._send(2, 2, cmd)
            if timeout:
                self.sock.settimeout(timeout)
            try:
                _rid, _typ, body = self._read()
            finally:
                if timeout:
                    self.sock.settimeout(10.0)
            parts = [body]
            while len(body) >= 4000:
                self.sock.settimeout(1.0)
                try:
                    (length,) = struct.unpack("<i", self._exact(4))
                    body = self._exact(length)[8:-2]
                    parts.append(body)
                except socket.timeout:
                    break
                finally:
                    self.sock.settimeout(10.0)
        finally:
            drop_lock(self.lock_path, self.lock_fd)
        return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            os.close(self.lock_fd)
        except OSError:
            pass


# ------------------------------------------------------------- processes

def pid_has(pid, marker):
    try:
        with open("/proc/%d/environ" % pid, "rb") as f:
            return marker.encode() in f.read().split(b"\0")
    except (OSError, ValueError):
        return False


def gone(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] in ("Z", "X")
    except (OSError, IndexError):
        return True


def read_pid(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def end(pid, wait_term):
    """SIGTERM the group, wait, then SIGKILL it; True once it is gone."""
    for sig, wait in ((signal.SIGTERM, wait_term), (signal.SIGKILL, 5)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not gone(pid):
            time.sleep(0.1)
        if gone(pid):
            return True
    return gone(pid)


class Locked(object):
    """A lock file held for the block; says so when it has to wait."""

    def __init__(self, path, what):
        self.path = path
        self.what = what

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say("waiting for another up or down (%s)" % self.what)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def shared_lock():
    return Locked(os.path.join(ROOT, "lock"), "the shared jar and template world")


# ------------------------------------------------------------ shared parts

def jar_version(jar):
    with zipfile.ZipFile(jar) as z:
        return json.loads(z.read("version.json").decode("utf-8")).get("id")


def ensure_shared():
    """shared/: the box's server.jar (out of mc-backup.tgz, or REALMC_SERVER_JAR), the box's
    settings with its secrets left out, and the box's .env with names only."""
    for d in (SHARED, os.path.join(SHARED, "libraries"), os.path.join(SHARED, "versions"),
              os.path.join(SHARED, "mods")):
        os.makedirs(d, exist_ok=True)
    jar = os.path.join(SHARED, "server.jar")
    box_props = os.path.join(SHARED, "box.properties")
    src = os.environ.get("REALMC_SERVER_JAR")
    if src and not os.path.exists(jar):
        shutil.copy2(os.path.expanduser(src), jar + ".part")
        os.replace(jar + ".part", jar)
    want = [m for m, path in (("mc/server.jar", jar), ("mc/server.properties", box_props))
            if not os.path.exists(path) and not (src and m == "mc/server.properties")]
    if want:
        backup = os.environ.get("REALMC_BACKUP") or backup_file("mc-backup.tgz")
        if not os.path.isfile(backup):
            if "mc/server.jar" in want:
                raise Fail(3, "no server jar: %s is missing (set REALMC_BACKUP or REALMC_SERVER_JAR)" % backup)
            want = []
    if want:
        say("taking %s out of %s" % (" and ".join(want), backup))
        tmp = tempfile.mkdtemp(prefix=".extract-", dir=SHARED)
        try:
            r = subprocess.run(["tar", "-xzf", backup, "-C", tmp] + want,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            if "mc/server.jar" in want:
                if not os.path.isfile(os.path.join(tmp, "mc", "server.jar")):
                    raise Fail(1, "could not take server.jar out of %s: %s" % (backup, r.stdout.strip()[-500:]))
                os.replace(os.path.join(tmp, "mc", "server.jar"), jar)
            try:
                with open(os.path.join(tmp, "mc", "server.properties"), encoding="utf-8") as f:
                    lines = [l for l in f.read().splitlines() if line_key(l) not in NOT_FROM_BOX]
                write_file(box_props, "\n".join(lines) + "\n")
            except OSError:
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # the box's own file, password and all, goes here
    got = jar_version(jar)
    if got != VERSION:
        raise Fail(1, "shared/server.jar is Minecraft %s, not %s" % (got, VERSION))
    if not os.path.exists(SHARED_ENV):
        with open(os.path.join(REPO, "bridge", "deploy", "env.example"), encoding="utf-8") as f:
            write_file(SHARED_ENV, f.read(), 0o600)
    os.chmod(SHARED_ENV, 0o600)


def carpet_jar():
    jars = sorted(glob.glob(os.path.join(SHARED, "mods", "fabric-carpet-*.jar")))
    if not jars:
        return None
    jar = jars[-1]
    if os.path.basename(jar) == CARPET_JAR and sha(jar, "sha256") != CARPET_SHA256:
        raise Fail(1, "%s does not match the v26.3 release's sha256; delete it and run: realmc.py carpet" % jar)
    return jar


def lib_sources(rel, sha1):
    """Where a library may already be on this PC: launchers, other servers, the Gradle cache."""
    roots = [p for p in os.environ.get("REALMC_LIB_ROOTS", "").split(os.pathsep) if p]
    roots += [os.path.join(REAL_HOME, ".var", "app", "com.mojang.Minecraft", ".minecraft", "libraries"),
              os.path.join(REAL_HOME, ".minecraft", "libraries")]
    roots += sorted(glob.glob(os.path.join(REAL_HOME, "Documents", "MinecraftServers", "*", "*", "libraries")))
    paths = [os.path.join(r, rel) for r in roots]
    parts = rel.split("/")
    group, artifact, version, fname = ".".join(parts[:-3]), parts[-3], parts[-2], parts[-1]
    paths.append(os.path.join(REAL_HOME, ".gradle", "caches", "modules-2", "files-2.1", group, artifact,
                              version, sha1, fname))
    return paths


def ensure_fabric_libs():
    """Fabric Loader and its libraries in shared/libraries/, each checked against Maven's sha1."""
    have, missing = [], []
    for rel, sha1 in FABRIC_LIBS:
        dest = os.path.join(SHARED, "libraries", rel)
        if not (os.path.isfile(dest) and sha(dest) == sha1):
            src = next((p for p in lib_sources(rel, sha1) if os.path.isfile(p) and sha(p) == sha1), None)
            if src is None:
                missing.append(rel)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest + ".part")
            os.replace(dest + ".part", dest)
        have.append(dest)
    if missing:
        urls = [next(base for pre, base in MAVEN.items() if rel.startswith(pre)) + rel for rel in missing]
        raise Fail(3, "Carpet needs Fabric Loader 0.19.5, and these are not on this PC: %s — put them under "
                      "%s/libraries/ (same paths), or start without Carpet: up --vanilla" % (" ".join(urls), SHARED))
    return have


def find_java():
    """VFG_JAVA, else the newest ~/.local/share/jdk/*/bin/java, else java on the PATH: 25 or newer."""
    cands = [os.environ.get("VFG_JAVA")]
    cands += sorted(glob.glob(os.path.join(REAL_HOME, ".local", "share", "jdk", "*", "bin", "java")), reverse=True)
    cands.append(shutil.which("java"))
    for c in cands:
        if not (c and os.access(c, os.X_OK)):
            continue
        try:
            out = subprocess.run([c, "-Xlog:disable", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 universal_newlines=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r'version "(\d+)', out)
        if m and int(m.group(1)) >= 25:
            return c
    raise Fail(3, "no Java 25 or newer (26.3 needs it): set VFG_JAVA")


def uv_find(version):
    """The Python `uv python find VERSION` names, or None."""
    uv = shutil.which("uv") or os.path.join(REAL_HOME, ".local", "bin", "uv")
    if os.access(uv, os.X_OK):
        try:
            path = subprocess.run([uv, "python", "find", version], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        if path and os.access(path, os.X_OK):
            return path
    return None


def find_box_python():
    """The box's Python, /opt/vfg/python/bin/python3 there: VFG_EC2_PYTHON, else the 3.12 uv finds."""
    py = os.environ.get("VFG_EC2_PYTHON")
    if py and os.access(py, os.X_OK):
        return py
    py = uv_find("3.12")
    if py:
        return py
    raise Fail(3, "no Python 3.12 for the box's side: `uv python install 3.12`, or set VFG_EC2_PYTHON")


def find_host_python():
    """Amazon Linux's own python3, 3.9, which the box keeps for the host; the box's 3.12 when there
    is no 3.9 here, since nothing of vfg's needs it."""
    return uv_find("3.9") or find_box_python()


def eula_ok():
    return owner_config("VFG_ACCEPT_EULA") == "true"


# --------------------------------------------------------- one server

class Server(object):
    """One named server and its stand-in box, under ROOT/<name>/."""

    def __init__(self, name):
        if not NAME_RE.match(name or "") or name in RESERVED:
            raise Fail(2, "%r is not a server name (a-z, 0-9, _ and -, at most 24; not shared, template or lock)" % name)
        self.name = name
        self.dir = os.path.join(ROOT, name)
        j = lambda *p: os.path.join(self.dir, *p)  # noqa: E731
        self.mc, self.vfg, self.home, self.vhome, self.box = j("mc"), j("vfg"), j("home"), j("home", ".vfg"), j("box")
        self.info, self.pidfile, self.lock = j("realmc.json"), j("server.pid"), j("lock")
        self.props, self.console, self.world = j("mc", "server.properties"), j("mc", "console.log"), j("mc", "world")
        # Its test server, laid out as the box's (the contract's "-test" folders): testserver NAME.
        self.mc_test, self.vfg_test = j("mc" + testserver.SUFFIX), j("vfg" + testserver.SUFFIX)
        self.test_props = j("mc" + testserver.SUFFIX, "server.properties")

    def locked(self):
        return Locked(self.lock, self.name)

    def exists(self):
        return os.path.isfile(self.props)

    # -- the stand-in
    def prepare(self):
        """The folders vfg reaches over the fake ssh, laid out as deploy.sh lays /opt/vfg out."""
        for d in (self.mc, self.vfg, os.path.join(self.vfg, "lib"), os.path.join(self.vfg, "scripts"),
                  os.path.join(self.vfg, "run"), os.path.join(self.vfg, "state"), os.path.join(self.vfg, "made"),
                  self.home, os.path.join(self.box, "bin"), os.path.join(self.box, "units")):
            os.makedirs(d, exist_ok=True)
        os.makedirs(self.vhome, mode=0o700, exist_ok=True)
        os.chmod(self.vhome, 0o700)
        for name in ("server.jar", "libraries", "versions", "mods"):
            link(os.path.join(SHARED, name), os.path.join(self.mc, name))
        host = find_host_python()
        for name in ("python3", "python3.9"):
            path = os.path.join(self.box, "bin", name)
            if os.path.realpath(path) != os.path.realpath(host):
                link(host, path)
        # The box's Python at the box's path: <vfg>/python/bin/python3, as python.sh lays it out.
        py = find_box_python()
        os.makedirs(os.path.join(self.vfg, "python", "bin"), exist_ok=True)
        for name in ("python3", "python3.12"):
            path = os.path.join(self.vfg, "python", "bin", name)
            if os.path.realpath(path) != os.path.realpath(py):
                link(py, path)
        # vfg feed post runs <vfg>/lib/vfg_feed.py; vfg say, usage add and history need vfg.db.
        shutil.copy2(os.path.join(REPO, "bridge", "vfg_feed.py"), os.path.join(self.vfg, "lib", "vfg_feed.py"))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1",
               "VFG_DB": os.path.join(self.vfg, "vfg.db"), "PYTHONPATH": os.path.join(REPO, "bridge")}
        r = subprocess.run([py, "-m", "vfg_bridge", "--init-db"], env=env, cwd=self.vfg,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if r.returncode != 0:
            raise Fail(1, "the bridge could not make vfg.db:\n" + r.stdout.strip()[-1500:])
        # The box's .env is shared/env (mode 600) for every server; a file put here instead is kept.
        env_file = os.path.join(self.vfg, ".env")
        if not os.path.exists(env_file) or os.path.islink(env_file):
            link(SHARED_ENV, env_file)
        key = os.path.join(self.vhome, "fake_key")
        if not os.path.exists(key):
            write_file(key, "not a key: fake_ssh never reads it\n", 0o600)
        # vfg's config: ours set every time, any other line a builder added kept.
        cfg = os.path.join(self.vhome, "config")
        try:
            with open(cfg, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = ["# realmc %s: vfg against a real 26.3 test server. integration/realmc/realmc.py sets"
                     % self.name, "# the keys below every time; add your own lines under them."]
        lines = merge_lines(lines, [
            ("VFG_SSH_HOST", "realmc-" + self.name), ("VFG_SSH_USER", "ec2-user"), ("VFG_SSH_KEY", key),
            ("VFG_SSH", os.path.join(HERE, "bin", "ssh")), ("VFG_REMOTE_VFG", self.vfg),
            ("VFG_REMOTE_MC", self.mc), ("VFG_REPO", REPO), ("VFG_OWNER", "Steve")])
        write_file(cfg, "\n".join(lines) + "\n", 0o600)
        self.set_unit("active" if self.pid() else "inactive")

    def set_unit(self, state):
        """The fake systemctl's minecraft unit (toolkit/tests/bin/systemctl) says what is true."""
        os.makedirs(os.path.join(self.box, "units"), exist_ok=True)
        write_file(os.path.join(self.box, "units", "minecraft"), state + "\n")

    # -- server.properties
    def taken_ports(self):
        """Every other server's ports, test servers' too (this one's own test server's included)."""
        ports = {25565, 25575, testserver.PORT, testserver.RCON_PORT}
        for path in glob.glob(os.path.join(ROOT, "*", "mc", "server.properties")) + \
                glob.glob(os.path.join(ROOT, "*", "mc" + testserver.SUFFIX, "server.properties")):
            if os.path.realpath(path) != os.path.realpath(self.props):
                for k in ("rcon.port", "server-port"):
                    try:
                        ports.add(int(read_props(path).get(k, "")))
                    except ValueError:
                        pass
        return ports

    def write_props(self):
        """The box's settings, OVERRIDES, free ports, this server's own password. Only while it is
        stopped: the server reads the file when it starts."""
        if os.path.exists(self.props):
            with open(self.props, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            mine = read_props(self.props)
        else:
            try:
                with open(os.path.join(SHARED, "box.properties"), encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = merge_lines(["#Minecraft server properties"], BOX_SETTINGS)
            mine = {}
        avoid = self.taken_ports()
        ports = {}
        for key in ("rcon.port", "server-port"):
            try:
                port = int(mine.get(key, ""))
            except ValueError:
                port = 0
            if not (1024 < port < 65536) or port in avoid or not port_free(port):
                port = pick_port(avoid)
            avoid.add(port)
            ports[key] = str(port)
        password = mine.get("rcon.password") or secrets.token_urlsafe(24)
        lines = merge_lines(lines, OVERRIDES + [
            ("rcon.port", ports["rcon.port"]), ("server-port", ports["server-port"]), ("rcon.password", password),
            ("management-server-secret", mine.get("management-server-secret") or secrets.token_urlsafe(24))])
        password = None
        write_file(self.props, "\n".join(lines) + "\n", 0o600)

    # -- running
    def pid(self):
        """The server's pid while it runs; a stale pid file is removed."""
        pid = read_pid(self.pidfile)
        if pid and pid_has(pid, MARKER + self.dir):
            return pid
        if pid:
            try:
                os.remove(self.pidfile)
            except OSError:
                pass
        return None

    def rcon(self):
        return Rcon(self.props)

    def command(self, cmd):
        r = self.rcon()
        try:
            return r.command(cmd)
        finally:
            r.close()

    def start(self, vanilla, timeout):
        """Start it detached, its console appended to console.log as the box's unit does;
        return (mode, seconds) once RCON answers."""
        java = find_java()
        carpet = None if vanilla else carpet_jar()
        heap = os.environ.get("REALMC_HEAP", "2G")
        # The -X/-- flags past the heap only keep the JVM's own warnings out of console.log.
        argv = [java, "-Xms512M", "-Xmx" + heap, "-Xlog:os+container=off", "--enable-native-access=ALL-UNNAMED",
                "--sun-misc-unsafe-memory-access=allow"]
        if carpet:
            argv += ["-Dfabric.gameJarPath=server.jar", "-cp", os.pathsep.join(ensure_fabric_libs()),
                     FABRIC_MAIN, "nogui"]
            mode = "carpet"
        else:
            argv += ["-jar", "server.jar", "nogui"]
            mode = "vanilla"
        write_file(os.path.join(self.mc, "eula.txt"), EULA_TEXT)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", "TYPESAFE_", "CLAUDE", "ANTHROPIC_"))}
        env.update({"TZ": "UTC", "REALMC_SERVER": self.dir})
        port = int(read_props(self.props)["rcon.port"])
        offset = os.path.getsize(self.console) if os.path.exists(self.console) else 0
        log = open(self.console, "ab")
        try:
            proc = subprocess.Popen(argv, cwd=self.mc, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, env=env, start_new_session=True)
        finally:
            log.close()
        write_file(self.pidfile, "%d\n" % proc.pid)
        t0 = time.monotonic()
        ready = re.compile(r"RCON running on 127\.0\.0\.1:%d" % port)
        while True:
            text = tail(self.console, offset)
            if proc.poll() is not None:
                self.set_unit("failed")
                raise Fail(1, "%s exited (code %d) while starting; the end of %s:\n%s"
                           % (self.name, proc.returncode, self.console, "\n".join(text.splitlines()[-25:])))
            if ready.search(text):
                try:
                    self.command("list")
                    break
                except OSError:
                    pass
            if time.monotonic() - t0 > timeout:
                end(proc.pid, 30)
                self.set_unit("failed")
                raise Fail(1, "%s: RCON did not answer within %d s; the end of %s:\n%s"
                           % (self.name, timeout, self.console, "\n".join(text.splitlines()[-25:])))
            time.sleep(0.2)
        self.set_unit("active")
        return mode, time.monotonic() - t0

    def stop_scripts(self):
        """Every script its stand-in runs: SIGTERM the group (its cleanup still has RCON), SIGKILL after 5 s."""
        n = 0
        for path in glob.glob(os.path.join(self.vfg, "run", "*.pid")):
            name = os.path.basename(path)[:-4]
            pid = read_pid(path)
            if pid and pid_has(pid, "VFG_SCRIPT=" + name):
                end(pid, 5)
                n += 1
            try:
                os.remove(path)
            except OSError:
                pass
        return n

    def stop(self):
        pid = self.pid()
        if not pid:
            self.set_unit("inactive")
            return False
        try:
            self.command("stop")
        except OSError:
            pass
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not gone(pid):
            time.sleep(0.2)
        if not gone(pid):
            end(pid, 30)
        # The box's unit writes this line when the server stops (ExecStopPost); the bridge reads it.
        with open(self.console, "a", encoding="utf-8") as f:
            f.write("[%s] [vfg/INFO]: Server stopped\n" % datetime.now(timezone.utc).strftime("%H:%M:%S"))
        try:
            os.remove(self.pidfile)
        except OSError:
            pass
        self.set_unit("inactive")
        return True

    def status(self):
        info = read_json(self.info, {})
        pid = self.pid()
        p = read_props(self.props)
        return {"name": self.name, "running": bool(pid), "pid": pid, "dir": self.dir, "version": VERSION,
                "mode": info.get("mode"), "carpet": bool(carpet_jar_present()),
                "rcon": "127.0.0.1:%s" % p.get("rcon.port", "?"), "game": "127.0.0.1:%s" % p.get("server-port", "?"),
                "seed": info.get("seed"), "arena": info.get("arena"), "world_from": info.get("world_from"),
                "ready_s": info.get("ready_s"), "env": os.path.join(self.vfg, ".env"),
                "console": self.console, "props": self.props, "test": test_status(self)}


# ------------------------------------------------------------ the test server

UNIT_FILE = os.path.join(REPO, "bridge", "deploy", "systemd", "minecraft-test.service")
TEST_UNIT = "minecraft-test"


def unit_settings(path=UNIT_FILE):
    """minecraft-test.service's settings, {key: value}, its Environment= pairs among them: the
    fake unit runs what the box's runs (MC_TEST_HEAP, Nice, RestartSec, TimeoutStopSec...)."""
    got = {}
    for line in testserver.read_lines(path):
        line = line.strip()
        if not line or line[0] in "#;[" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "Environment":
            for pair in v.split():
                if "=" in pair:
                    ek, ev = pair.split("=", 1)
                    got[ek] = ev
        else:
            got[k] = v
    return got


class TestUnit(object):
    """The fake systemctl's minecraft-test for one realmc server (toolkit/tests/bin/systemctl runs
    <box>/units/minecraft-test.exec, which runs `realmc.py unit NAME minecraft-test VERB`). It
    really does what the box's unit does: a supervisor process runs <mc-test>/reset.sh, then java
    -Xms256M -Xmx<MC_TEST_HEAP> @launch.args at nice 10, its console appended to
    <mc-test>/console.log, the "Server stopped" line after it; and starts it again RestartSec after
    it ends unless it was stopped (Restart=always), giving up after StartLimitBurst starts within
    StartLimitIntervalSec. The state file says active, activating, inactive or failed."""

    def __init__(self, srv):
        self.srv = srv
        self.units = os.path.join(srv.box, "units")
        j = lambda *p: os.path.join(self.units, TEST_UNIT + "".join(p))  # noqa: E731
        self.state_file, self.exec_file, self.journal = j(), j(".exec"), j(".journal")
        self.sup_pidfile, self.java_pidfile, self.lock = j(".pid"), j(".java.pid"), j(".lock")
        self.stopped_file = j(".stopped")   # when it was last stopped: a restart asked for before is dropped
        self.sup_marker = "REALMC_UNIT=%s/%s" % (srv.dir, TEST_UNIT)
        self.java_marker = MARKER + srv.mc_test

    def installed(self):
        return os.path.exists(self.exec_file)

    def install(self, java, heap=None):
        """The .exec the fake systemctl runs, with this PC's paths baked in (fake_ssh passes only
        PATH, HOME, USER, LOGNAME and FAKE_UNITS through)."""
        os.makedirs(self.units, exist_ok=True)
        q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
        env = "REALMC_ROOT=%s VFG_JAVA=%s" % (q(ROOT), q(java))
        if heap:
            env += " REALMC_TEST_HEAP=%s" % q(heap)
        write_file(self.exec_file, "#!/bin/sh\n# realmc %s: the fake systemctl's %s, backed by the real server "
                   "(realmc.py unit).\n%s exec %s %s unit %s %s \"$@\"\n"
                   % (self.srv.name, TEST_UNIT, env, q(sys.executable), q(os.path.join(HERE, "realmc.py")),
                      q(self.srv.name), TEST_UNIT), 0o755)
        if not os.path.exists(self.state_file):
            self.set_state("inactive")

    def set_state(self, state):
        write_file(self.state_file, state + "\n")

    def note(self, msg):
        """A line in the unit's journal (the fake journalctl prints the last one)."""
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write("%s %s: %s\n" % (now_iso(), TEST_UNIT, msg))

    def sup_pid(self):
        pid = read_pid(self.sup_pidfile)
        return pid if pid and pid_has(pid, self.sup_marker) and not gone(pid) else None

    def java_pid(self):
        pid = read_pid(self.java_pidfile)
        return pid if pid and pid_has(pid, self.java_marker) and not gone(pid) else None

    def state(self):
        """What is-active prints: the state file, unless nothing runs behind it."""
        try:
            with open(self.state_file) as f:
                s = f.read().strip() or "inactive"
        except OSError:
            return "inactive"
        if s in ("active", "activating", "deactivating") and not self.sup_pid():
            return "inactive" if s == "deactivating" else "failed"   # its supervisor is gone
        return s

    def locked(self):
        return Locked(self.lock, "%s %s" % (self.srv.name, TEST_UNIT))

    def job(self):
        """One start, stop or restart at a time, as systemd runs a unit's jobs: the unit's lock,
        held for the whole job (a restart's stop and start both), waited for quietly."""
        unit = self

        class Job(object):
            def __enter__(self):
                os.makedirs(unit.units, exist_ok=True)
                self.fd = os.open(unit.lock, os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
                return self

            def __exit__(self, *exc):
                os.close(self.fd)
        return Job()

    def stopped_at(self):
        try:
            with open(self.stopped_file) as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def start(self, block=True):
        """systemctl start: the supervisor detached; with `block`, back once reset.sh ran and java
        started (Type=simple), as systemd's start returns."""
        if self.sup_pid():
            return 0
        if not os.path.exists(os.path.join(self.srv.mc_test, testserver.MARKER)):
            self.set_state("failed")
            self.note("no %s: lay the test server out first (realmc.py testserver %s)"
                      % (os.path.join(self.srv.mc_test, testserver.MARKER), self.srv.name))
            sys.stderr.write("Job for %s.service failed.\n" % TEST_UNIT)
            return 1
        self.set_state("activating")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": REAL_HOME, "LANG": "C.UTF-8",
               "REALMC_ROOT": ROOT, "REALMC_UNIT": "%s/%s" % (self.srv.dir, TEST_UNIT)}
        for k in ("VFG_JAVA", "REALMC_TEST_HEAP"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        with open(self.journal, "ab") as log:
            proc = subprocess.Popen([sys.executable, os.path.join(HERE, "realmc.py"), "unit", self.srv.name, TEST_UNIT,
                                     "run"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                                    start_new_session=True)
        write_file(self.sup_pidfile, "%d\n" % proc.pid)
        if not block:
            return 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            s = self.state()
            if s == "active" and self.java_pid():
                return 0
            if s == "failed" or proc.poll() is not None:
                break
            time.sleep(0.1)
        sys.stderr.write("Job for %s.service failed; see %s\n" % (TEST_UNIT, self.journal))
        return 1

    def stop(self):
        """systemctl stop: SIGTERM to the supervisor, which stops java (SIGTERM, then SIGKILL past
        TimeoutStopSec) and writes "Server stopped"; back once both are gone."""
        sup = self.sup_pid()
        if sup:
            self.set_state("deactivating")
            try:
                os.kill(sup, signal.SIGTERM)
            except ProcessLookupError:
                pass
            limit = float(unit_settings().get("TimeoutStopSec", "60")) + 15
            deadline = time.monotonic() + limit
            while time.monotonic() < deadline and not gone(sup):
                time.sleep(0.1)
            if not gone(sup):
                end(sup, 2)
        java = self.java_pid()
        if java:  # a supervisor killed hard left it
            end(java, 30)
            self.server_stopped()
        for path in (self.sup_pidfile, self.java_pidfile):
            try:
                os.remove(path)
            except OSError:
                pass
        if self.state() != "failed":
            self.set_state("inactive")
        write_file(self.stopped_file, "%.6f\n" % time.time())
        return 0

    def restart(self, block=True):
        """systemctl restart: stop, then start, under the unit's lock (the caller holds it). With
        --no-block it goes on in the background (`unit … restart --asked T`) and returns at once;
        that restart is dropped when the unit was stopped after it was asked for, as systemd's stop
        job cancels a queued restart, so `down` right after a test leaves it down."""
        if not block:
            subprocess.Popen([sys.executable, os.path.join(HERE, "realmc.py"), "unit", self.srv.name, TEST_UNIT,
                              "restart", "--asked", "%.6f" % time.time()], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             env=dict(os.environ, REALMC_ROOT=ROOT))
            return 0
        self.stop()
        return self.start()

    def server_stopped(self):
        # ExecStopPost: 26.3 logs nothing when SIGTERM stops it; the bridge reads this line.
        with open(os.path.join(self.srv.mc_test, "console.log"), "a", encoding="utf-8") as f:
            f.write("[%s] [vfg/INFO]: Server stopped\n" % datetime.now(timezone.utc).strftime("%H:%M:%S"))

    def run(self):
        """The supervisor: reset.sh, java, again after RestartSec until stopped."""
        cfg = unit_settings()
        heap = os.environ.get("REALMC_TEST_HEAP") or cfg.get("MC_TEST_HEAP", testserver.HEAP)
        nice = int(cfg.get("Nice", "10"))
        restart_s = float(cfg.get("RestartSec", "5"))
        stop_s = float(cfg.get("TimeoutStopSec", "60"))
        burst, window = int(cfg.get("StartLimitBurst", "30")), float(cfg.get("StartLimitIntervalSec", "300"))
        write_file(self.sup_pidfile, "%d\n" % os.getpid())
        stopping, child = [False], [None]

        def on_term(signum, frame):
            stopping[0] = True
            if child[0] is not None:
                try:
                    os.killpg(child[0].pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        java = find_java()
        console = os.path.join(self.srv.mc_test, "console.log")
        scope = user_scope(cfg)   # the unit's CPUQuota, CPUWeight and MemoryMax, as on the box
        starts = []
        while not stopping[0]:
            now = time.monotonic()
            starts = [t for t in starts if now - t < window] + [now]
            if len(starts) > burst:
                self.note("start request repeated too quickly (%d starts in %d s): failed" % (len(starts), window))
                self.set_state("failed")
                return 1
            self.set_state("activating")
            with open(console, "ab") as log:
                r = subprocess.run([os.path.join(self.srv.mc_test, "reset.sh")], cwd=self.srv.mc_test,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            if r.returncode != 0:
                self.note("reset.sh failed (exit %d); see %s" % (r.returncode, console))
            elif not stopping[0]:
                env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": REAL_HOME, "LANG": "C.UTF-8",
                       "TZ": "UTC", "REALMC_SERVER": self.srv.mc_test}
                env.update(scope_env())
                with open(console, "ab") as log:
                    child[0] = subprocess.Popen(scope + testserver.java_argv(java, heap), cwd=self.srv.mc_test,
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                                env=env, start_new_session=True, preexec_fn=lambda: os.nice(nice))
                write_file(self.java_pidfile, "%d\n" % child[0].pid)
                self.set_state("active")
                self.note("started java pid %d (-Xmx%s%s), the world fresh from reset.sh"
                          % (child[0].pid, heap, ", in a user scope: " + " ".join(scope[scope.index("--scope") + 1:-1])
                             if scope else ""))
                code, stop_at = None, None
                while code is None:
                    try:
                        code = child[0].wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        if stopping[0]:
                            stop_at = stop_at or time.monotonic()
                            if time.monotonic() - stop_at > stop_s:
                                self.note("java did not stop within %g s: SIGKILL" % stop_s)
                                try:
                                    os.killpg(child[0].pid, signal.SIGKILL)
                                except (ProcessLookupError, PermissionError):
                                    pass
                child[0] = None
                try:
                    os.remove(self.java_pidfile)
                except OSError:
                    pass
                self.server_stopped()
                self.note("java exited (code %s)%s" % (code, "" if stopping[0] else
                                                         "; again in %g s (Restart=always)" % restart_s))
            if stopping[0]:
                break
            self.set_state("activating")
            deadline = time.monotonic() + restart_s
            while time.monotonic() < deadline and not stopping[0]:
                time.sleep(0.1)
        self.set_state("inactive")
        try:
            os.remove(self.sup_pidfile)
        except OSError:
            pass
        return 0


def scope_env():
    """What systemd-run --user needs to find this user's manager, which fake_ssh's environment lacks."""
    run_dir = "/run/user/%d" % os.getuid()
    if not os.environ.get("XDG_RUNTIME_DIR") and os.path.isdir(run_dir):
        return {"XDG_RUNTIME_DIR": run_dir}
    return {k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if os.environ.get(k)}


def user_scope(cfg):
    """`systemd-run --user --scope` with the unit's CPUQuota, CPUWeight and MemoryMax, the java
    started in it (systemd-run execs it: the same pid), when this PC's user manager makes one;
    else [] and the java runs without them."""
    sdrun = shutil.which("systemd-run")
    if not sdrun:
        return []
    env = dict(os.environ, **scope_env())
    try:
        ok = subprocess.run([sdrun, "--user", "--quiet", "--scope", "--", "true"], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        return []
    props = []
    for key in ("CPUQuota", "CPUWeight", "MemoryMax"):
        if cfg.get(key):
            props += ["-p", "%s=%s" % (key, cfg[key])]
    return [sdrun, "--user", "--quiet", "--scope"] + props + ["--"]


def test_ports(srv):
    """The test server's game and RCON ports: its own while they are still free (or it holds
    them), else new free ones on 127.0.0.1, never another server's."""
    mine, live = read_props(srv.test_props), read_props(srv.props)
    avoid = set(srv.taken_ports())   # every other server's, this test server's own among them
    for k in ("rcon.port", "server-port"):
        for props, keep in ((mine, False), (live, True)):
            try:
                (avoid.add if keep else avoid.discard)(int(props.get(k, "")))
            except ValueError:
                pass
    running = TestUnit(srv).sup_pid() is not None
    ports = {}
    for key in ("server-port", "rcon.port"):
        try:
            port = int(mine.get(key, ""))
        except ValueError:
            port = 0
        if not (1024 < port < 65536) or port in avoid or not (running or port_free(port)):
            port = pick_port(avoid)
        avoid.add(port)
        ports[key] = port
    return ports["server-port"], ports["rcon.port"]


def test_status(srv):
    if not os.path.isdir(srv.mc_test):
        return None
    unit = TestUnit(srv)
    p = read_props(srv.test_props)
    t = read_json(os.path.join(srv.mc_test, "template.json"), {})
    return {"state": unit.state(), "running": unit.state() == "active", "pid": unit.java_pid(),
            "rcon": "127.0.0.1:%s" % p.get("rcon.port", "?"), "game": "127.0.0.1:%s" % p.get("server-port", "?"),
            "mc": srv.mc_test, "vfg": srv.vfg_test, "marker": os.path.exists(os.path.join(srv.mc_test, testserver.MARKER)),
            "template": t.get("made"), "unit": unit.exec_file, "console": os.path.join(srv.mc_test, "console.log")}


def ensure_test_server(srv, fresh=False):
    """Lay out srv's test server with bridge/deploy/testserver.py (the box's own setup, on the
    box's Python 3.12), install its fake unit, (re)start it when something it reads changed, and
    wait for its RCON: what testserver.sh does on the box."""
    if not srv.exists():
        raise Fail(3, "%s has no server yet: integration/realmc/up %s" % (srv.name, srv.name))
    java = find_java()
    unit = TestUnit(srv)
    unit.install(java, os.environ.get("REALMC_TEST_HEAP"))
    port, rcon_port = test_ports(srv)
    heap = os.environ.get("REALMC_TEST_HEAP") or unit_settings().get("MC_TEST_HEAP", testserver.HEAP)
    argv = [os.path.join(srv.vfg, "python", "bin", "python3"), os.path.join(REPO, "bridge", "deploy", "testserver.py"),
            "--live-mc", srv.mc, "--live-vfg", srv.vfg, "--mc", srv.mc_test, "--vfg", srv.vfg_test,
            "--port", str(port), "--rcon-port", str(rcon_port), "--heap", heap, "--java", java, "--from", SHARED]
    if fresh:
        argv.append("--fresh")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", "TYPESAFE_", "CLAUDE", "ANTHROPIC_"))}
    env["HOME"] = REAL_HOME
    rc = subprocess.run(argv, env=env).returncode
    if rc == testserver.EXIT_STOP_FIRST:
        say("%s: stopping its test server to make its template or AOT cache" % srv.name)
        with unit.job():
            unit.stop()
        rc = subprocess.run(argv, env=env).returncode
    if rc not in (testserver.EXIT_OK, testserver.EXIT_RESTART):
        raise Fail(rc if rc in (2, 3) else 1, "%s: testserver.py failed (exit %d)" % (srv.name, rc))
    t0 = time.monotonic()
    if rc == testserver.EXIT_RESTART or unit.state() != "active":
        say("%s: (re)starting its test server (%s): a fresh world from its template" % (srv.name, TEST_UNIT))
        with unit.job():
            ok = unit.restart() == 0
        if not ok:
            raise Fail(1, "%s: its test server did not start; see %s and %s"
                       % (srv.name, unit.journal, os.path.join(srv.mc_test, "console.log")))
    testserver.wait_rcon(srv.test_props, 180)
    say("%s: test server ready in %.1f s: RCON 127.0.0.1:%d, game 127.0.0.1:%d, %s and %s"
        % (srv.name, time.monotonic() - t0, rcon_port, port, srv.mc_test, srv.vfg_test))


def stop_test_server(srv):
    """down: its test scripts (vfg-test/run), then the test server; True when it ran."""
    unit = TestUnit(srv)
    if not unit.installed() and not unit.sup_pid():
        return None
    for path in glob.glob(os.path.join(srv.vfg_test, "run", "*.pid")):
        pid = read_pid(path)
        if pid and pid_has(pid, "VFG_SCRIPT=" + os.path.basename(path)[:-4]):
            end(pid, 5)
    with unit.job():   # after any start or restart under way; a restart asked for before is dropped
        was = unit.sup_pid() is not None
        unit.stop()
    return was


def carpet_jar_present():
    return glob.glob(os.path.join(SHARED, "mods", "fabric-carpet-*.jar"))


def port_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as Java's server sockets do
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(avoid):
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port not in avoid:
            return port


# ------------------------------------------------------------- the world

def test_passed(r, cmd):
    return r.command(cmd).startswith("Test passed")


def entity_pos(r, selector):
    out = r.command("data get entity %s Pos" % selector)
    nums = re.findall(r"(-?\d+(?:\.\d+)?(?:E-?\d+)?)d", out)
    return tuple(float(x) for x in nums[:3]) if len(nums) >= 3 else None


def forceload_all(r, x0, z0, x1, z1, op):
    """forceload add|remove over any area, 8 x 8 chunks at a time. 26.3 answers `forceload add`
    only once its chunks are generated (256 new ones took 11 s here), hence the long timeout."""
    step = 16 * 8
    for bx in range(x0, x1 + 1, step):
        for bz in range(z0, z1 + 1, step):
            out = r.command("forceload %s %d %d %d %d" % (op, bx, bz, min(bx + step - 1, x1), min(bz + step - 1, z1)),
                            timeout=300)
            if "<--[HERE]" in out or out.startswith(("Too many", "Unknown")):
                raise Fail(1, "forceload %s answered: %s" % (op, out))


def wait_loaded(r, x0, z0, x1, z1, timeout):
    left = [(x, z) for x in range(x0 >> 4, (x1 >> 4) + 1) for z in range(z0 >> 4, (z1 >> 4) + 1)]
    deadline = time.monotonic() + timeout
    while left:
        left = [c for c in left if not test_passed(r, "execute if loaded %d 0 %d" % (c[0] * 16, c[1] * 16))]
        if not left:
            return
        if time.monotonic() > deadline:
            raise Fail(1, "%d chunks were still not loaded after %d s" % (len(left), timeout))
        time.sleep(0.5)


def make_template(r):
    """In a new world: clock and weather held; every chunk within PREGEN generated; a flat grass
    arena at the first dry spot of ARENA_AT, cleared above, the world spawn on it; the area
    around the arena forceloaded for good, so commands work there with nobody on."""
    for c in ("gamerule advance_time false", "time set noon", "gamerule advance_weather false",
              "weather clear", "gamerule respawn_radius 0", "gamerule log_admin_commands false"):
        r.command(c)
    big = (-PREGEN - 48, -PREGEN - 48, PREGEN + 47, PREGEN + 47)
    say("generating %d x %d blocks around the origin (once, for the template)" % ((big[2] - big[0] + 1,) * 2))
    t0 = time.monotonic()
    forceload_all(r, big[0], big[1], big[2], big[3], "add")
    wait_loaded(r, big[0], big[1], big[2], big[3], 600)
    say("generated in %.0f s" % (time.monotonic() - t0))
    at = None
    for x, z in ARENA_AT:
        r.command('summon minecraft:marker %d 300 %d {Tags:["realmc_probe"]}' % (x, z))
        r.command("spreadplayers %d %d 0 1 false @e[tag=realmc_probe]" % (x, z))
        p = entity_pos(r, "@e[tag=realmc_probe,limit=1]")
        r.command("kill @e[tag=realmc_probe]")
        if not p or p[1] >= 300:
            continue  # nowhere dry to stand there
        y = int(p[1])
        below = "%d %d %d" % (x, y - 1, z)
        if any(test_passed(r, "execute if block %s %s" % (below, t))
               for t in ("#minecraft:leaves", "#minecraft:logs", "minecraft:water", "minecraft:lava")):
            continue  # a tree top, or wet
        at = (x, y, z)
        break
    if at is None:
        at = (0, 64, 0)  # all water or trees: a platform at sea level, still the same every time
    x, y, z = at
    h = ARENA_HALF
    box = (x - h, z - h, x + h - 1, z + h - 1)
    for cmd in ("fill %d %d %d %d %d %d minecraft:air" % (box[0], y, box[1], box[2], y + ARENA_AIR - 1, box[3]),
                "fill %d %d %d %d %d %d minecraft:dirt" % (box[0], y - 4, box[1], box[2], y - 2, box[3]),
                "fill %d %d %d %d %d %d minecraft:grass_block" % (box[0], y - 1, box[1], box[2], y - 1, box[3])):
        out = r.command(cmd)
        if not (out.startswith("Successfully filled") or out.startswith("No blocks were filled")):
            raise Fail(1, "making the arena: `%s` answered: %s" % (cmd, out))
    r.command("kill @e[type=!minecraft:player,x=%d,y=%d,z=%d,dx=%d,dy=%d,dz=%d]"
              % (box[0], y - 4, box[1], 2 * h - 1, ARENA_AIR + 3, 2 * h - 1))
    r.command("setworldspawn %d %d %d" % at)
    keep = (x - KEEP_LOADED, z - KEEP_LOADED, x + KEEP_LOADED - 1, z + KEEP_LOADED - 1)
    r.command("forceload remove all")
    forceload_all(r, keep[0], keep[1], keep[2], keep[3], "add")
    r.command("save-all flush")
    return {"x": x, "y": y, "z": z, "half": h, "air": ARENA_AIR,
            "floor": [box[0], y - 1, box[1], box[2], y - 1, box[3]], "loaded": list(keep), "generated": list(big)}


def save_template(srv, r, arena):
    """Copy the saved world to template/ while saving is off."""
    r.command("save-off")
    try:
        r.command("save-all flush")
        tmp = TEMPLATE + ".part"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        copy_tree(srv.world, os.path.join(tmp, "world"))
        try:
            os.remove(os.path.join(tmp, "world", "session.lock"))
        except OSError:
            pass
        shutil.rmtree(TEMPLATE, ignore_errors=True)
        os.replace(tmp, TEMPLATE)
    finally:
        r.command("save-on")
    write_file(TEMPLATE_INFO, json.dumps({"seed": SEED, "version": VERSION, "made": now_iso(),
                                          "arena": arena}, indent=2) + "\n")


def template_ok():
    t = read_json(TEMPLATE_INFO, {})
    return os.path.isdir(os.path.join(TEMPLATE, "world")) and t.get("seed") == SEED and t.get("version") == VERSION


# --------------------------------------------------------------- commands

def default_name():
    return os.environ.get("REALMC_NAME") or "main"


def servers():
    return [Server(os.path.basename(d)) for d in sorted(glob.glob(os.path.join(ROOT, "*")))
            if os.path.basename(d) not in RESERVED and NAME_RE.match(os.path.basename(d))
            and os.path.isdir(os.path.join(d, "mc"))]


def print_status(s):
    say("%s: %s — Minecraft %s, %s; RCON %s, game %s"
        % (s["name"], "running pid %d" % s["pid"] if s["running"] else "stopped",
           s["version"], s["mode"] or "not started yet", s["rcon"], s["game"]))
    a = s.get("arena")
    if a:
        say("%s: arena floor y=%d, %d x %d around %d %d %d; players stand at y=%d"
            % (s["name"], a["y"] - 1, 2 * a["half"], 2 * a["half"], a["x"], a["y"], a["z"], a["y"]))
    if s["mode"] != "carpet":
        say("%s: no fake players (%s)" % (s["name"], "Carpet is fetched: start it again to load it" if s["carpet"]
                                          else "fetch Carpet first: integration/realmc/realmc.py carpet"))
    t = s.get("test")
    if t:
        say("%s: test server %s — RCON %s, game %s, %s (unit %s)" % (
            s["name"], t["state"] + (" pid %d" % t["pid"] if t["pid"] else ""), t["rcon"], t["game"], t["mc"],
            TEST_UNIT))


def cmd_up(a):
    srv = Server(a.name or default_name())
    with srv.locked():
        with shared_lock():
            ensure_shared()
        srv.prepare()
        if srv.pid():
            if not (a.fresh or a.regen):
                if a.test_server:
                    ensure_test_server(srv)
                print_status(srv.status())
                return 0
            say("%s: stopping it first" % srv.name)
            srv.stop_scripts()
            srv.stop()
        srv.write_props()
        if not eula_ok():
            raise Fail(3, "Minecraft's EULA (https://aka.ms/MinecraftEULA) is not accepted here: the owner sets "
                          "VFG_ACCEPT_EULA=true in ~/.vfg/config (as for deploy.sh provision); realmc never "
                          "writes eula=true without it. Everything else is ready in %s" % srv.dir)
        if a.fresh or a.regen:
            shutil.rmtree(srv.world, ignore_errors=True)
        with shared_lock():
            # No template means none was ever made, or making it failed: a world without one is
            # what a failed attempt left, so it is made (again) here.
            build = a.regen or not template_ok()
            if not os.path.isdir(srv.world) and not build:
                copy_tree(os.path.join(TEMPLATE, "world"), srv.world)
                origin = "template"
            elif build:
                shutil.rmtree(srv.world, ignore_errors=True)
                origin = "seed"
                say("%s: making the template world from seed %s (once; a minute or two)" % (srv.name, SEED))
            else:
                origin = read_json(srv.info, {}).get("world_from", "template")
            if build:  # the template is made here, so it keeps the shared lock until it is saved
                mode, secs = srv.start(a.vanilla, 300)
                try:
                    r = srv.rcon()
                    try:
                        arena = make_template(r)
                        save_template(srv, r, arena)
                    finally:
                        r.close()
                except BaseException:
                    say("%s: making the template failed; stopping the server and dropping its world" % srv.name)
                    srv.stop()
                    shutil.rmtree(srv.world, ignore_errors=True)
                    raise
        if not build:
            mode, secs = srv.start(a.vanilla, 120)
        t = read_json(TEMPLATE_INFO, {})
        write_file(srv.info, json.dumps({"name": srv.name, "seed": t.get("seed"), "arena": t.get("arena"),
                                         "world_from": origin, "mode": mode, "started": now_iso(),
                                         "ready_s": round(secs, 1)}, indent=2) + "\n")
        say("%s: ready in %.1f s" % (srv.name, secs))
        if a.test_server:
            ensure_test_server(srv)
        print_status(srv.status())
        say('point vfg at it: eval "$(integration/realmc/env%s)"' % ("" if srv.name == "main" else " " + srv.name))
    return 0


def cmd_down(a):
    targets = servers() if a.all else [Server(a.name or default_name())]
    for srv in targets:
        if not os.path.isdir(srv.mc):
            say("%s: no such server" % srv.name)
            continue
        with srv.locked():
            was = stop_test_server(srv)
            if was is not None:
                say("%s: test server %s" % (srv.name, "stopped" if was else "was not running"))
            n = srv.stop_scripts()
            if n:
                say("%s: stopped %d script%s" % (srv.name, n, "" if n == 1 else "s"))
            say("%s: %s" % (srv.name, "stopped" if srv.stop() else "was not running"))
    if a.purge_key:
        purge_key()
    return 0


def cmd_env(a):
    srv = Server(a.name or default_name())
    out, sys.stdout = sys.stdout, sys.stderr  # eval reads stdout: progress goes to stderr
    try:
        with srv.locked():
            with shared_lock():
                ensure_shared()
            srv.prepare()
            if not srv.exists():
                srv.write_props()  # its ports, for list; up checks them again when it starts
    finally:
        sys.stdout = out
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
    print("# realmc %s: vfg (and `import mc`) against a real %s test server in %s" % (srv.name, VERSION, srv.dir))
    print("unset VFG_SSH VFG_SSH_HOST VFG_SSH_USER VFG_SSH_KEY VFG_REMOTE_VFG VFG_REMOTE_MC VFG_REPO")
    print("export REALMC_NAME=%s" % q(srv.name))
    print("export REALMC_DIR=%s" % q(srv.dir))
    print("export VFG_HOME=%s" % q(srv.vhome))
    print("export VFG_MC_PROPERTIES=%s" % q(srv.props))
    print("export VFG_PROTECTED=%s" % q(os.path.join(srv.vfg, "protected.json")))
    print("export PATH=%s:\"$PATH\"" % q(os.path.join(HERE, "bin")))
    return 0


def cmd_status(a):
    s = Server(a.name or default_name()).status()
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        print_status(s)
    return 0 if s["running"] else 1


def cmd_list(a):
    rows = [s.status() for s in servers()]
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        say("no servers yet: integration/realmc/up [NAME]")
    for s in rows:
        print("%-12s %-8s %-8s pid %-8s rcon %-16s game %-16s %s" % (
            s["name"], "running" if s["running"] else "stopped", s["mode"] or "-", s["pid"] or "-",
            s["rcon"], s["game"], s["dir"]))
        t = s.get("test")
        if t:
            print("%-12s %-8s %-8s pid %-8s rcon %-16s game %-16s %s" % (
                " + test", "running" if t["running"] else t["state"], "carpet", t["pid"] or "-", t["rcon"], t["game"],
                t["mc"]))
    return 0


def cmd_cmd(a):
    srv = Server(a.name or default_name())
    command = " ".join(a.words).lstrip("/")
    if not command.strip():
        raise Fail(2, "which command?")
    if not srv.pid():
        raise Fail(1, "%s is not running: integration/realmc/up %s" % (srv.name, srv.name))
    out = srv.command(command)
    if out:
        print(out)
    return 0


def cmd_carpet(a):
    """Carpet 26.3 from its official GitHub release, checked against the release's sha256."""
    with shared_lock():
        os.makedirs(os.path.join(SHARED, "mods"), exist_ok=True)
        dest = os.path.join(SHARED, "mods", CARPET_JAR)
        if os.path.isfile(dest) and sha(dest, "sha256") == CARPET_SHA256:
            say("Carpet is already here: %s" % dest)
            return 0
        say("fetching %s (%d bytes)" % (CARPET_URL, CARPET_SIZE))
        req = urllib.request.Request(CARPET_URL, headers={"User-Agent": "realmc (everhear test server)"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read(CARPET_SIZE + 1)
        if len(data) != CARPET_SIZE or hashlib.sha256(data).hexdigest() != CARPET_SHA256:
            raise Fail(1, "the download does not match the release (%d bytes, sha256 %s); nothing kept"
                       % (len(data), hashlib.sha256(data).hexdigest()))
        write_file(dest + ".part", data)
        os.replace(dest + ".part", dest)
    say("Carpet in %s (sha256 checked); a running server loads it when it starts again: up NAME --fresh, "
        "or down then up" % dest)
    return 0


def cmd_key(a):
    """TYPESAFE_API_KEY from the box's backup (vfg-backup.tgz, member vfg/.env) into shared/env,
    in memory, never printed; nothing else from that file."""
    src = os.path.expanduser(a.src) if a.src else backup_file("vfg-backup.tgz")
    try:
        with tarfile.open(src, "r:gz") as tf:
            f = tf.extractfile(tf.getmember("vfg/.env"))
            data = f.read().decode("utf-8", "replace")
    except (OSError, KeyError, tarfile.TarError) as e:
        raise Fail(1, "could not read vfg/.env from %s: %s" % (src, e.__class__.__name__))
    value = None
    for line in data.splitlines():
        if line_key(line) == "TYPESAFE_API_KEY":
            value = line.split("=", 1)[1].strip()
    data = None
    if not value:
        raise Fail(1, "vfg/.env in %s has no TYPESAFE_API_KEY value" % src)
    with shared_lock():
        ensure_shared()
        with open(SHARED_ENV, encoding="utf-8") as f:
            lines = f.read().splitlines()
        write_file(SHARED_ENV, "\n".join(merge_lines(lines, [("TYPESAFE_API_KEY", value)])) + "\n", 0o600)
    value = None
    say("TYPESAFE_API_KEY copied into %s (mode 600); every server's vfg/.env links to it. "
        "down --purge-key empties it." % SHARED_ENV)
    return 0


def purge_key():
    with shared_lock():
        if not os.path.exists(SHARED_ENV):
            return
        with open(os.path.join(REPO, "bridge", "deploy", "env.example"), encoding="utf-8") as f:
            write_file(SHARED_ENV, f.read(), 0o600)
    say("emptied %s: it holds the names and no values again" % SHARED_ENV)


def cmd_testserver(a):
    """The test server beside NAME, laid out by the box's own setup: testserver.sh's work on the PC."""
    srv = Server(a.name or default_name())
    with srv.locked():
        if a.stop:
            was = stop_test_server(srv)
            say("%s: test server %s" % (srv.name, "stopped" if was else "was not running"))
            return 0
        ensure_test_server(srv, fresh=a.fresh)
        print_status(srv.status())
    return 0


def cmd_unit(a):
    """What the fake systemctl runs for a unit backed by a real program (<box>/units/<unit>.exec)."""
    if a.unit != TEST_UNIT:
        raise Fail(2, "realmc backs only %s" % TEST_UNIT)
    unit = TestUnit(Server(a.name))
    if a.verb == "is-active":
        s = unit.state()
        print(s)
        return 0 if s == "active" else 3
    if a.verb == "run":
        return unit.run()
    if a.verb == "reset-failed":
        if unit.state() == "failed":
            unit.set_state("inactive")
        return 0
    if a.no_block and a.verb == "restart":
        return unit.restart(block=False)
    with unit.job():
        if a.verb == "start":
            return unit.start(block=not a.no_block)
        if a.verb == "stop":
            return unit.stop()
        if a.asked and unit.stopped_at() > a.asked:
            unit.note("a restart asked for at %s dropped: the unit was stopped since"
                      % datetime.fromtimestamp(a.asked, timezone.utc).strftime("%H:%M:%S"))
            return 0
        return unit.restart()


def main(argv=None):
    p = argparse.ArgumentParser(prog="realmc", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", metavar="up|down|env|list|status|cmd|testserver|carpet|key")
    sub.required = True

    def named(s):
        s.add_argument("name", nargs="?", metavar="NAME", help="the server (default $REALMC_NAME, else main)")
        s.add_argument("--name", dest="name_opt", metavar="NAME", help="the same, as an option")

    s = sub.add_parser("up", help="start a server (prepare first); does nothing if it runs")
    named(s)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--fresh", action="store_true", help="its world copied from the template again first")
    g.add_argument("--regen", action="store_true", help="the template made again from the seed first")
    s.add_argument("--vanilla", action="store_true", help="without Fabric and Carpet, even when Carpet is here")
    s.add_argument("--test-server", action="store_true",
                   help="and its test server, as the box has one (testserver NAME)")
    s = sub.add_parser("down", help="stop a server's scripts, then the server")
    named(s)
    s.add_argument("--all", action="store_true", help="every server")
    s.add_argument("--purge-key", action="store_true", help="and empty shared/env (the TypeSafe key)")
    s = sub.add_parser("env", help="print the exports that point vfg at a server's stand-in")
    named(s)
    s = sub.add_parser("list", help="every server")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("status", help="one server; exit 0 when it runs")
    named(s)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("cmd", help="one RCON command, straight to a server")
    s.add_argument("--name", dest="name_opt", metavar="NAME")
    s.add_argument("words", nargs=argparse.REMAINDER)
    s = sub.add_parser("testserver", help="lay out NAME's test server (mc-test, vfg-test) with the box's "
                                          "testserver.py, and (re)start it")
    named(s)
    s.add_argument("--fresh", action="store_true", help="make its template world again")
    s.add_argument("--stop", action="store_true", help="stop it (and its tests' scripts) instead")
    s = sub.add_parser("unit", help=argparse.SUPPRESS)
    s.add_argument("name", metavar="NAME")
    s.add_argument("unit", metavar="UNIT")
    s.add_argument("verb", choices=["start", "stop", "restart", "is-active", "reset-failed", "run"])
    s.add_argument("--no-block", action="store_true")
    s.add_argument("--asked", type=float, default=None, help=argparse.SUPPRESS)
    sub.add_parser("carpet", help="fetch Carpet 26.3 from its GitHub release into shared/mods/")
    s = sub.add_parser("key", help="copy TYPESAFE_API_KEY from vfg-backup.tgz into shared/env, unprinted")
    s.add_argument("--from", dest="src", metavar="TGZ", help="default: VFG_BACKUP_DIR/vfg-backup.tgz")
    a = p.parse_args(argv)
    a.name = getattr(a, "name_opt", None) or getattr(a, "name", None)
    try:
        return {"up": cmd_up, "down": cmd_down, "env": cmd_env, "list": cmd_list, "status": cmd_status,
                "cmd": cmd_cmd, "testserver": cmd_testserver, "unit": cmd_unit, "carpet": cmd_carpet,
                "key": cmd_key}[a.cmd](a)
    except Fail as f:
        sys.stderr.write("realmc: %s\n" % f.msg)
        return f.code
    except OSError as e:  # RCON: refused, dropped or timed out
        sys.stderr.write("realmc: %s\n" % (e.strerror or e.__class__.__name__ if not str(e) else e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
