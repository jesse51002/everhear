#!/usr/bin/env python3
"""testserver.py: the test server beside a live Minecraft server, for implementers' tests (vfg
test). The same program lays it out on the box (testserver.sh runs it as ec2-user, on
/opt/vfg/python/bin/python3) and on the owner's PC (integration/realmc, beside a realmc server).

    testserver.py [--live-mc /opt/mc] [--live-vfg /opt/vfg] [--mc /opt/mc-test] [--vfg /opt/vfg-test]
                  [--port 25566] [--rcon-port 25576] [--heap 1536M] [--user ec2-user]
                  [--test-user vfgtest] [--java /usr/bin/java] [--from DIR]... [--fresh] [--no-template]
    testserver.py --wait SECONDS [--mc /opt/mc-test]    until its RCON answers `list`

The test folders default to the live ones + "-test". What it makes, reading the live server's
files and never writing them (it never starts, stops or restarts the live server):

    <mc>/.vfg-test-server  the marker, JSON {"made", "rcon_port", "port", "live_mc", "live_vfg",
                           "vfg", and "test_user" with --test-user}: anything that runs a test
                           refuses a folder without it, and the live folder never has it (this
                           refuses one that does)
    <mc>/server.jar        a copy of the live server.jar, sha256-equal, Minecraft 26.3
    <mc>/libraries/        Fabric Loader 0.19.5 and its libraries (Maven's sha1s); the server
                           unpacks its own libraries beside them at its first start
    <mc>/mods/             Carpet for 26.3 (the release's sha256), for fake players; nothing else
    <mc>/launch.args       the java argfile: java -Xms256M -Xmx<heap> @launch.args (the unit's line);
                           two CPUs' threads, C1 only, and the AOT cache once there is one
    <mc>/test-server.aot   Java 25's AOT cache of a start (aot.json: for which Java and classpath),
                           made after the template and again when Java changes; --no-aot: none
    <mc>/server.properties the live one's settings, minus its secrets and ports, with
                           TEST_OVERRIDES, its own ports and its own random RCON password; 600,
                           or 640 in the test user's group (Minecraft rewrites it in place)
    <mc>/eula.txt          only when the live server's says eula=true (the owner accepted it)
    <mc>/reset.sh          world/ <- world.template/, the live world's datapacks copied in,
                           world/vfg-fresh; minecraft-test runs it before every start
    <mc>/world.template/   made once (and with --fresh, or when the jar changes): seed SEED,
                           the sky stage's chunks generated and forceloaded, spawn on the stage;
                           template.json says from what
    <vfg>/                 python -> <live vfg>/python; scripts/ tests/ made/ (written by this
                           user only); run/ and state/ (the test's too); state/incantations.json
                           (an empty store) and its lock; protected.json ([]); no .env

--test-user (the box: vfgtest, a system user testserver.sh makes) is the user a sandboxed test runs
as, so that it is not the live services' uid. Its group then owns <vfg> (2755: read, not written),
run/ and state/ (2775: written, setgid, the store's two files 664), and reads server.properties
(640) and the marker. Nothing this user writes into by name is writable by the test user: python,
scripts/, tests/ and made/ stay 755. Without it (realmc), the test runs as this user, and the
modes are plain.

Pins: the one source of truth for the Fabric and Carpet jars the test servers run;
integration/realmc/realmc.py imports them from here. --from DIR (realmc's shared/) is looked in
first, DIR/libraries/<path> and DIR/mods/<jar>, each checked against its pin; otherwise each is
fetched from its official URL and checked the same way.

Exit: 0 done, nothing a running test server reads changed; 10 done, start or restart the test
server to apply it; 11 the template or the AOT cache must be made and the test server is running:
stop it and run this again (testserver.sh and realmc do); 1 failed; 2 bad arguments; 3 refused (it would touch
the live server, or a folder it did not make; or the live EULA is not accepted).

Python 3.9+, standard library only. It never prints the RCON password.
"""
import argparse
import fcntl
import hashlib
import json
import grp
import os
import pwd
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

# ------------------------------------------------------------------- pins
MC_VERSION = "26.3"
# One fixed seed: a world made again from it has the same terrain (same version, same seed).
SEED = "20261009"
# Carpet for 26.3, for fake players: gnembon/fabric-carpet's release v26.3, as GitHub lists it.
CARPET_JAR = "fabric-carpet-26.3+v260915.jar"
CARPET_URL = "https://github.com/gnembon/fabric-carpet/releases/download/v26.3/fabric-carpet-26.3%2Bv260915.jar"
CARPET_SIZE = 1541420
CARPET_SHA256 = "7c40b93e807ed914064176898037117097446c6cc76e6a80a856c8bf7f7659f9"
# Fabric Loader 0.19.5, what meta.fabricmc.net lists for 26.3 (no intermediary: 26.x ships
# unobfuscated), and its libraries, with Maven's sha1s.
FABRIC_LOADER = "0.19.5"
FABRIC_MAIN = "net.fabricmc.loader.impl.launch.server.FabricServerLauncher"
FABRIC_LIBS = [
    ("net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar", "ff9e65cffca4a67f31523e1807fe0855940fcbfa"),
    ("net/fabricmc/sponge-mixin/0.17.4+mixin.0.8.7/sponge-mixin-0.17.4+mixin.0.8.7.jar",
     "5f66cc9f59b8efaa942155a3d5a30599bf6640dd"),
    ("org/ow2/asm/asm/9.10.1/asm-9.10.1.jar", "ada2141c0cc52ee8f5c48cd5fa4ce0e794f22236"),
    ("org/ow2/asm/asm-analysis/9.10.1/asm-analysis-9.10.1.jar", "8d49f14d51f632cb1d87c88d1ceaf50db0d8af1b"),
    ("org/ow2/asm/asm-commons/9.10.1/asm-commons-9.10.1.jar", "4229e4c55fd8e01c23f9fe9884075cc628aacc50"),
    ("org/ow2/asm/asm-tree/9.10.1/asm-tree-9.10.1.jar", "e244332a17564c1d1572449399a842de35881be2"),
    ("org/ow2/asm/asm-util/9.10.1/asm-util-9.10.1.jar", "7bb9d450e8d4cbf9f9e04096c44bbfe7fba80b15"),
]
MAVEN = {"net/fabricmc/": "https://maven.fabricmc.net/", "org/ow2/": "https://repo1.maven.org/maven2/"}

# ------------------------------------------------------- the layout contract
LIVE_MC, LIVE_VFG = "/opt/mc", "/opt/vfg"
SUFFIX = "-test"
PORT, RCON_PORT = 25566, 25576
LIVE_PORT, LIVE_RCON_PORT = 25565, 25575
HEAP = "1536M"            # minecraft-test.service's MC_TEST_HEAP; testserver.sh passes the unit's
XMS = "256M"
USER = "ec2-user"
MARKER = ".vfg-test-server"
FRESH = "vfg-fresh"
LOCK = ".testserver.lock"
# The JVM's own warnings kept out of console.log; java -Xms -Xmx @launch.args.
JVM_FLAGS = ["-Xlog:os+container=off", "--enable-native-access=ALL-UNNAMED", "--sun-misc-unsafe-memory-access=allow"]
# A start is most of a test server's CPU: one after every test. Two CPUs' worth of GC and compiler
# threads, the C1 compiler only (tests run a minute; C1's code kept the stage's tick at 10 ms under a
# test's load, C2's at 6, both far from 50), and an AOT cache of what a start loads took a restart
# from about 32 to 13 CPU-seconds on 4 CPUs (2026-10-13, integration/realmc/README.md).
JVM_CPU_FLAGS = ["-XX:ActiveProcessorCount=2", "-XX:TieredStopAtLevel=1"]
AOT, AOT_INFO = "test-server.aot", "aot.json"

# The sky stage the kit's tests stand on: scripts/_lib/kit/_stage/helpers.py's X, Z, FLOOR and
# CLEAR (a test checks they agree). The template has every chunk within PREGEN chunks of its
# middle generated (view-distance 10 around a player on it, and the border), the CLEAR area
# forceloaded for good (a test's own forceload finds it loaded), and the world spawn on it.
STAGE_X, STAGE_Z, STAGE_FLOOR, STAGE_CLEAR = 3000, 3000, 295, 48
PREGEN = 12
TEMPLATE_FORMAT = 1       # bump when the template's making changes: every test server makes it again
GAMERULES = [("advance_time", "false"), ("advance_weather", "false"), ("respawn_radius", "0"),
             ("log_admin_commands", "false"), ("spawn_mobs", "false"), ("spawn_phantoms", "false")]

# Never taken from the live server.properties: its secrets, and what the test server sets itself.
NOT_FROM_LIVE = ("rcon.password", "rcon.port", "server-port", "server-ip", "query.port", "level-seed",
                 "management-server-secret", "management-server-port", "management-server-tls-keystore",
                 "management-server-tls-keystore-password", "text-filtering-config")
# Then set for the test server. Everything else stays as the live server has it (difficulty,
# game mode, allow-flight, the packs a new world starts with...).
TEST_OVERRIDES = [
    ("server-ip", "127.0.0.1"),            # nobody joins: loopback only (the firewall drops it off lo too)
    ("online-mode", "false"),              # Carpet's fake players; nobody else can reach it
    ("enforce-secure-profile", "false"),
    ("white-list", "false"),
    ("enforce-whitelist", "false"),
    ("enable-rcon", "true"),
    ("broadcast-rcon-to-ops", "false"),
    ("broadcast-console-to-ops", "false"),
    ("enable-query", "false"),
    ("management-server-enabled", "false"),
    ("enable-jmx-monitoring", "false"),
    ("log-ips", "false"),
    ("spawn-protection", "0"),
    ("view-distance", "10"),               # the live server's: a script reaching 160 blocks finds them loaded
    ("simulation-distance", "6"),          # below the live 10: less ticking beside the live server
    ("pause-when-empty-seconds", "0"),     # a test starts with nobody on: it must keep ticking
    ("sync-chunk-writes", "false"),        # its world is thrown away at every start
    ("max-tick-time", "60000"),
    ("level-name", "world"),
    ("level-type", "minecraft\\:normal"),
    ("level-seed", SEED),
    ("generate-structures", "true"),
    ("motd", "EverHear test server"),
]

EXIT_OK, EXIT_RESTART, EXIT_STOP_FIRST = 0, 10, 11
HEAP_RE = re.compile(r"^[1-9][0-9]*[MG]$")


class Fail(Exception):
    """Exit with `code` and `msg`: 1 failed, 2 bad arguments, 3 refused."""

    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg


def say(msg):
    sys.stdout.write("testserver: %s\n" % msg)
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
    """Write path.tmp, then replace path with it; False when it already held exactly this."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        with open(path, "rb") as f:
            if f.read() == data and (os.stat(path).st_mode & 0o777) == mode:
                return False
    except OSError:
        pass
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return True


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def copy_file(src, dest, mode=0o644):
    """dest <- src: a reflink where the disk has them, else a copy; through dest.part."""
    part = dest + ".part"
    if subprocess.run(["cp", "--reflink=auto", src, part], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        shutil.copyfile(src, part)
    os.chmod(part, mode)
    os.replace(part, dest)


def copy_tree(src, dest):
    """A world copy: a reflink where the disk has them (instant), a plain copy elsewhere."""
    shutil.rmtree(dest, ignore_errors=True)
    if subprocess.run(["cp", "-R", "--preserve=mode,timestamps", "--reflink=auto", src, dest],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest, symlinks=True)


def jar_version(jar):
    with zipfile.ZipFile(jar) as z:
        return json.loads(z.read("version.json").decode("utf-8")).get("id")


# ------------------------------------------------------------- properties

def line_key(line):
    if "=" not in line or line.lstrip().startswith(("#", "!")):
        return None
    return line.split("=", 1)[0].strip()


def props_of(lines):
    """{key: value} of KEY=value lines, as Minecraft and the toolkit read them (no unescaping)."""
    got = {}
    for line in lines:
        k = line_key(line)
        if k:
            got[k] = line.split("=", 1)[1].strip()
    return got


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


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


def test_properties(live_lines, test_lines, port, rcon_port):
    """(lines, changed): the test server's server.properties. It starts from its own file when
    there is one (what Minecraft added to it stays), else from a header; every live key but
    NOT_FROM_LIVE is carried over, then TEST_OVERRIDES, its ports and its own RCON password (kept
    once made). `changed` compares the settings, not the text: Minecraft rewrites the file, with
    a new date at its top, every time it starts."""
    have = props_of(test_lines)
    base = list(test_lines) or ["#Minecraft server properties: the EverHear test server (testserver.py)"]
    live = [(k, v) for k, v in props_of(live_lines).items() if k not in NOT_FROM_LIVE]
    password = have.get("rcon.password") or secrets.token_urlsafe(24)
    lines = merge_lines(base, live + TEST_OVERRIDES + [
        ("server-port", str(port)), ("query.port", str(port)), ("rcon.port", str(rcon_port)),
        ("rcon.password", password)])
    return lines, props_of(lines) != have


# ------------------------------------------------------------------- RCON

def rcon_lock_path(port):
    """The lock every RCON client of the server on `port` holds from send to reply, as
    scripts/_lib/mc.py and toolkit/remote/common.py do (vanilla answers every connection from one
    shared buffer)."""
    return os.environ.get("VFG_RCON_LOCK") or "/tmp/vfg-rcon-%d.lock" % port


def open_lock(path):
    """A descriptor flock can hold on `path`, as scripts/_lib/mc.py and toolkit/remote/common.py
    open it: read-only and without O_CREAT when it exists (flock needs neither write access nor
    ownership, and fs.protected_regular refuses O_CREAT on another user's file in /tmp), else made
    0666 so the test user and this one share it."""
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


class Rcon(object):
    """A vanilla RCON client: one packet per write, a reply over 4096 characters read in parts."""

    def __init__(self, props_path, timeout=10.0):
        p = props_of(read_lines(props_path))
        self.port = int(p.get("rcon.port") or 0)
        self.lock_path = rcon_lock_path(self.port)
        self.lock_fd = open_lock(self.lock_path)
        try:
            self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        except OSError:
            os.close(self.lock_fd)
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

    def command(self, cmd, timeout=10.0):
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        try:
            self.sock.settimeout(timeout)
            self._send(2, 2, cmd)
            _rid, _typ, body = self._read()
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
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        for close in (self.sock.close, lambda: os.close(self.lock_fd)):
            try:
                close()
            except OSError:
                pass


def port_answers(port):
    """True when something accepts a connection on 127.0.0.1:`port` (a running server)."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except OSError:
        return False


def wait_rcon(props_path, timeout):
    """Seconds until RCON answers `list`; Fail(1) past `timeout`."""
    t0 = time.monotonic()
    last = "not answering"
    while time.monotonic() - t0 < timeout:
        try:
            r = Rcon(props_path, timeout=5)
            try:
                r.command("list")
                return time.monotonic() - t0
            finally:
                r.close()
        except (OSError, ValueError, struct.error) as e:
            last = str(e) or e.__class__.__name__
        time.sleep(0.5)
    raise Fail(1, "RCON did not answer within %d s (%s)" % (timeout, last))


# ------------------------------------------------------------------ safety

def under(a, b):
    """True when path a is b or inside it (both resolved)."""
    a, b = os.path.realpath(a), os.path.realpath(b)
    return a == b or a.startswith(b.rstrip("/") + "/")


def non_empty(path):
    try:
        return bool(os.listdir(path))
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        return True


def refusals(a, live_props):
    """Why this setup must not run: it would touch the live server or a folder it did not make."""
    why = []
    for what, test, live in (("Minecraft folder", a.mc, a.live_mc), ("vfg folder", a.vfg, a.live_vfg)):
        if under(test, live) or under(live, test):
            why.append("the test %s %s is the live one %s, or inside it" % (what, test, live))
    if under(a.mc, a.vfg) or under(a.vfg, a.mc):
        why.append("the test Minecraft and vfg folders overlap: %s, %s" % (a.mc, a.vfg))
    for live in (a.live_mc, a.live_vfg):
        if under(a.mc, live) or under(a.vfg, live):
            why.append("a test folder is inside the live %s" % live)
    try:
        live_rcon = int(live_props.get("rcon.port") or LIVE_RCON_PORT)
    except ValueError:
        live_rcon = LIVE_RCON_PORT
    try:
        live_port = int(live_props.get("server-port") or LIVE_PORT)
    except ValueError:
        live_port = LIVE_PORT
    for name, port in (("game", a.port), ("RCON", a.rcon_port)):
        if port in (live_rcon, live_port):
            why.append("the test %s port %d is the live server's" % (name, port))
        if not 1024 < port < 65536:
            why.append("the test %s port %d is not between 1025 and 65535" % (name, port))
    if a.port == a.rcon_port:
        why.append("the test game and RCON ports are both %d" % a.port)
    if os.path.exists(os.path.join(a.live_mc, MARKER)):
        why.append("%s has %s: it is a test server, not the live one" % (a.live_mc, MARKER))
    if not os.path.exists(os.path.join(a.mc, MARKER)):
        for folder in (a.mc, a.vfg):
            if non_empty(folder):
                why.append("%s is there, not empty, and has no %s: testserver.py did not make it, so it "
                           "leaves it alone" % (folder, os.path.join(a.mc, MARKER)))
    return why


# ----------------------------------------------------------- the jars

def fetch(url, dest, size=None):
    """url into dest (through dest.part): curl where there is one (the box's CA certificates),
    else urllib."""
    part = dest + ".part"
    try:
        os.remove(part)
    except OSError:
        pass
    if shutil.which("curl"):
        r = subprocess.run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "20", "--max-time", "300",
                            "-A", "everhear-testserver", "-o", part, url],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if r.returncode != 0:
            raise Fail(1, "could not fetch %s: %s" % (url, r.stdout.strip()[-300:]))
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "everhear-testserver"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as f:
            f.write(resp.read((size + 1) if size else -1))
    return part


def obtain(dest, digest, algo, candidates, url, size=None):
    """dest, checked against `digest`: from the first candidate that matches it, else from url;
    returns where it came from."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for c in candidates:
        if os.path.isfile(c) and sha(c, algo) == digest:
            copy_file(c, dest)
            return c
    part = fetch(url, dest, size)
    got = sha(part, algo)
    if got != digest or (size and os.path.getsize(part) != size):
        os.remove(part)
        raise Fail(1, "%s does not match its pin (%s %s, got %s); nothing kept" % (url, algo, digest, got))
    os.chmod(part, 0o644)
    os.replace(part, dest)
    return url


def ensure_jar(live_mc, mc):
    """<mc>/server.jar, a copy of the live one (never a link: nothing the test side does may
    write the live file); (its sha256, changed)."""
    src = os.path.join(live_mc, "server.jar")
    if not os.path.isfile(src):
        raise Fail(3, "no %s: there is no live server to copy the jar of" % src)
    want = sha(src, "sha256")
    version = jar_version(src)
    if version != MC_VERSION:
        raise Fail(1, "the live server.jar is Minecraft %s, not %s: the test server's Carpet and Fabric pins "
                      "are for %s (update them in bridge/deploy/testserver.py)" % (version, MC_VERSION, MC_VERSION))
    dest = os.path.join(mc, "server.jar")
    if os.path.isfile(dest) and not os.path.islink(dest) and sha(dest, "sha256") == want:
        return want, False
    if os.path.islink(dest):
        os.remove(dest)
    copy_file(src, dest)
    if sha(dest, "sha256") != want:
        raise Fail(1, "%s is not byte-for-byte %s after the copy" % (dest, src))
    say("server.jar: a copy of %s (Minecraft %s, sha256 %s…)" % (src, version, want[:12]))
    return want, True


def ensure_libs(mc, sources):
    """Fabric Loader and its libraries in <mc>/libraries/; (their paths from <mc>, changed)."""
    paths, changed = [], False
    for rel, sha1 in FABRIC_LIBS:
        dest = os.path.join(mc, "libraries", rel)
        if not (os.path.isfile(dest) and sha(dest) == sha1):
            url = next(base for pre, base in MAVEN.items() if rel.startswith(pre)) + rel
            got = obtain(dest, sha1, "sha1", [os.path.join(s, "libraries", rel) for s in sources], url)
            say("%s: from %s (sha1 checked)" % (rel.rsplit("/", 1)[1], got))
            changed = True
        paths.append("libraries/" + rel)
    return paths, changed


def ensure_carpet(mc, sources):
    """Carpet, and nothing else, in <mc>/mods/; changed."""
    mods = os.path.join(mc, "mods")
    os.makedirs(mods, exist_ok=True)
    changed = False
    for name in sorted(os.listdir(mods)):
        if name != CARPET_JAR and name.endswith(".jar"):
            os.remove(os.path.join(mods, name))
            say("mods/%s: removed (the test server runs the live server and Carpet, nothing else)" % name)
            changed = True
    dest = os.path.join(mods, CARPET_JAR)
    if not (os.path.isfile(dest) and sha(dest, "sha256") == CARPET_SHA256):
        got = obtain(dest, CARPET_SHA256, "sha256", [os.path.join(s, "mods", CARPET_JAR) for s in sources],
                     CARPET_URL, CARPET_SIZE)
        say("mods/%s: from %s (sha256 checked)" % (CARPET_JAR, got))
        changed = True
    return changed


def launch_args(libs, aot=None):
    """The java argfile the unit runs: java -Xms256M -Xmx<heap> @launch.args, from <mc>. `aot`:
    -XX:AOTCache=<it> (a cache this Java made from this classpath), or -XX:AOTCacheOutput=<it>
    (the run that makes it)."""
    return "\n".join(["# testserver.py: Fabric Loader %s and Carpet on the live server's jar, for the EverHear test "
                      "server" % FABRIC_LOADER] + JVM_FLAGS + JVM_CPU_FLAGS + ([aot] if aot else []) + [
        "-Dfabric.gameJarPath=server.jar", "-cp", ":".join(libs), FABRIC_MAIN, "nogui"]) + "\n"


def java_argv(java, heap, args="launch.args"):
    return [java, "-Xms" + XMS, "-Xmx" + heap, "@" + args]


def java_id(java, libs):
    """What an AOT cache is good for: this Java's build and this classpath. A cache another one
    made is ignored by the JVM (a warning, then a start without it); this says when to make it again."""
    try:
        out = subprocess.run([java, "-Xlog:disable", "-Xlog:all=off", "-version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    real = shutil.which(java) or java
    return hashlib.sha256(("%s\n%s\n%s" % (os.path.realpath(real), out.strip(), ":".join(libs))).encode()).hexdigest()


def aot_wanted(mc, ident):
    """Why the AOT cache must be made (again), or None."""
    if not ident:
        return None   # no java to ask: start without one
    info = read_json(os.path.join(mc, AOT_INFO), {})
    if not os.path.isfile(os.path.join(mc, AOT)) or not info:
        return "there is none"
    if info.get("java_id") != ident:
        return "Java or the classpath changed"
    return None


# ---------------------------------------------------------- small files

def eula_accepted(path):
    for line in read_lines(path):
        if line_key(line) == "eula" and line.split("=", 1)[1].strip().lower() == "true":
            return True
    return False


EULA_TEXT = ("# Copied by testserver.py: the live server's eula.txt (%s) says its owner accepted Minecraft's\n"
             "# EULA (https://aka.ms/MinecraftEULA); this test server runs the same server for them.\n"
             "eula=true\n")


RESET_SH = r"""#!/usr/bin/env bash
# reset.sh -- the EverHear test server's world, fresh: written by testserver.py; run by
# minecraft-test before every start (ExecStartPre), and by hand only while it is stopped.
#   world/             <- a copy of world.template/ (seed %(seed)s, the sky stage's chunks made)
#   world/datapacks/   <- the live server's, %(live)s/world/datapacks (read, never written), in
#                         place of the template's: a new pack there is enabled as the world loads
#   world/%(fresh)s     the freshness token: vfg test takes it away when a test uses this world
set -euo pipefail
MC=%(mc)s
LIVE=%(live)s
RCON_PORT=%(rcon_port)d
TEST_GROUP=%(test_group)s
cd "$MC"
say() { printf '[%%s] [vfg/INFO]: reset.sh: %%s\n' "$(date -u +%%H:%%M:%%S)" "$*"; }
[[ -f %(marker)s ]] || { say "$MC has no %(marker)s: not a test server; nothing done" >&2; exit 1; }
[[ $(realpath "$MC") != "$(realpath "$LIVE")" ]] || { say "$MC is the live server; nothing done" >&2; exit 1; }
[[ -d world.template ]] || { say "no world.template: make it with deploy.sh testserver (testserver.py)" >&2; exit 1; }
if (exec 3<>"/dev/tcp/127.0.0.1/$RCON_PORT") 2>/dev/null; then
  say "the test server is running (127.0.0.1:$RCON_PORT answers): restart it instead, which resets it first" >&2
  exit 1
fi
exec 9>>%(lock)s
flock -w 600 9 || { say "testserver.py still holds %(lock)s" >&2; exit 1; }
t0=$(date +%%s%%N)
rm -rf world.new world.old
cp -R --preserve=mode,timestamps --reflink=auto world.template world.new
# Exactly the live datapacks, in place of any the template has: never two copies of one, never a
# file the live pack no longer has (the template is made with none; this keeps it so).
rm -rf world.new/datapacks
if [[ -d $LIVE/world/datapacks ]]; then
  cp -R --preserve=mode,timestamps --reflink=auto "$LIVE/world/datapacks" world.new/datapacks
else
  mkdir world.new/datapacks
fi
packs=$(shopt -s nullglob dotglob; cd world.new/datapacks && for p in *; do printf '%%s ' "$p"; done)
date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ > world.new/%(fresh)s
# The sandboxed test (user $TEST_GROUP) reads its RCON password; Minecraft rewrites the file in place.
if [[ -n $TEST_GROUP ]] && ! { chgrp "$TEST_GROUP" server.properties && chmod 640 server.properties; }; then
  say "could not give server.properties to group $TEST_GROUP: a sandboxed test cannot reach RCON" >&2
fi
if [[ -e world ]]; then mv world world.old; fi
mv world.new world
rm -rf world.old
say "a fresh world from world.template, the live datapacks: ${packs:-none}, in $(( ($(date +%%s%%N) - t0) / 1000000 )) ms"
"""


def reset_sh(mc, live_mc, rcon_port, test_group=None):
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
    return RESET_SH % {"mc": q(mc), "live": q(live_mc), "rcon_port": rcon_port, "seed": SEED, "fresh": FRESH,
                       "marker": MARKER, "lock": LOCK, "test_group": q(test_group or "")}


def set_mode(path, mode, gid=None):
    """chmod (and chgrp to `gid`) when it differs; True when it changed. Never through a link."""
    st = os.lstat(path)
    changed = False
    if gid is not None and st.st_gid != gid:
        os.chown(path, -1, gid, follow_symlinks=False)
        changed = True
    if (os.lstat(path).st_mode & 0o7777) != mode:   # a chgrp clears setgid on some systems
        os.chmod(path, mode)
        changed = True
    return changed


EMPTY_STORE = json.dumps({"version": 0, "incantations": []}) + "\n"


def lay_out_vfg(vfg, live_vfg, gid=None):
    """The test vfg folder, as vfg test, scripts.py and the kit use it; changed. With `gid` (the
    test user's group) the test may write run/ and state/ and nothing else."""
    changed = False
    for d in ("scripts", "run", "tests", "state", "made"):
        path = os.path.join(vfg, d)
        if not os.path.isdir(path):
            os.makedirs(path)
            changed = True
    py, target = os.path.join(vfg, "python"), os.path.join(live_vfg, "python")
    if os.path.islink(py):
        if os.readlink(py) != target:
            os.remove(py)
    elif os.path.exists(py):
        raise Fail(3, "%s is there and is not a link to %s" % (py, target))
    if not os.path.islink(py):
        os.symlink(target, py)
        changed = True
    state = os.path.join(vfg, "state")
    for name, text in (("incantations.json", EMPTY_STORE), ("incantations.lock", "")):
        path = os.path.join(state, name)
        if not os.path.lexists(path):
            write_file(path, text, 0o664 if gid is not None else 0o644)
            changed = True
        elif os.path.islink(path) or not os.path.isfile(path):
            raise Fail(3, "%s is not a plain file: the test server's store is made only by this setup" % path)
    # The store was <vfg>/incantations.json before the test ran as its own user: gone, so nothing reads it.
    for name in ("incantations.json", "incantations.lock"):
        old = os.path.join(vfg, name)
        if os.path.lexists(old):
            os.remove(old)
            say("%s: removed (the test server's store is state/incantations.json)" % old)
            changed = True
    if not os.path.exists(os.path.join(vfg, "protected.json")):
        write_file(os.path.join(vfg, "protected.json"), "[]\n")
        changed = True
    env = os.path.join(vfg, ".env")
    if os.path.lexists(env):
        os.remove(env)  # the test side holds no secrets
        say("%s: removed (the test side holds no secrets)" % env)
        changed = True
    # Who writes where: the test user only into run/ and state/, never where this user writes by name.
    if gid is None:
        modes = [(vfg, 0o755, None)] + [(os.path.join(vfg, d), 0o755, None)
                                        for d in ("scripts", "run", "tests", "state", "made")]
    else:
        modes = [(vfg, 0o2755, gid), (os.path.join(vfg, "run"), 0o2775, gid), (state, 0o2775, gid),
                 (os.path.join(state, "incantations.json"), 0o664, gid),
                 (os.path.join(state, "incantations.lock"), 0o664, gid)]
        modes += [(os.path.join(vfg, d), 0o755, None) for d in ("scripts", "tests", "made")]
    for path, mode, g in modes:
        if set_mode(path, mode, g):
            say("%s: mode %o%s" % (path, mode, "" if g is None else ", group %s" % grp.getgrgid(g).gr_name))
            changed = True
    return changed


# -------------------------------------------------------------- template

def template_wanted(mc, jar_sha):
    """Why the template must be made (again), or None."""
    t = read_json(os.path.join(mc, "template.json"), {})
    if not os.path.isdir(os.path.join(mc, "world.template")) or not t:
        return "there is none"
    if t.get("jar_sha256") != jar_sha:
        return "the live server.jar changed"
    if (t.get("seed"), t.get("version"), t.get("format")) != (SEED, MC_VERSION, TEMPLATE_FORMAT):
        return "it was made for another seed, version or format"
    return None


def forceload(r, x0, z0, x1, z1, op):
    """forceload add|remove over any area, 8 x 8 chunks at a time. 26.3 answers `forceload add`
    only once its chunks are generated, hence the long timeout."""
    step = 16 * 8
    for bx in range(x0, x1 + 1, step):
        for bz in range(z0, z1 + 1, step):
            out = r.command("forceload %s %d %d %d %d" % (op, bx, bz, min(bx + step - 1, x1), min(bz + step - 1, z1)),
                            timeout=600)
            if "<--[HERE]" in out or out.startswith(("Too many", "Unknown")):
                raise Fail(1, "forceload %s answered: %s" % (op, out))


def wait_loaded(r, x0, z0, x1, z1, timeout):
    left = [(x, z) for x in range(x0 >> 4, (x1 >> 4) + 1) for z in range(z0 >> 4, (z1 >> 4) + 1)]
    deadline = time.monotonic() + timeout
    while left:
        left = [c for c in left if not r.command("execute if loaded %d 0 %d" % (c[0] * 16, c[1] * 16))
                .startswith("Test passed")]
        if not left:
            return
        if time.monotonic() > deadline:
            raise Fail(1, "%d chunks were still not loaded after %d s" % (len(left), timeout))
        time.sleep(0.5)


def tail(path, offset):
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def end(proc, wait):
    """SIGTERM the server's group, then SIGKILL; its exit code."""
    import signal
    for sig, w in ((signal.SIGTERM, wait), (signal.SIGKILL, 10)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            return proc.wait(w)
        except subprocess.TimeoutExpired:
            continue
    return proc.poll()


def start_server(a, timeout, args="launch.args"):
    """The test server, started here (not through its unit) for the template or the AOT cache;
    (proc, seconds) once RCON answers. Its console is appended to <mc>/console.log, as the unit does."""
    console = os.path.join(a.mc, "console.log")
    offset = os.path.getsize(console) if os.path.exists(console) else 0
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", a.mc),
           "LANG": "C.UTF-8", "TZ": "UTC"}
    log = open(console, "ab")
    try:
        proc = subprocess.Popen(java_argv(a.java, a.heap, args), cwd=a.mc, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, env=env, start_new_session=True,
                                preexec_fn=lambda: os.nice(10))
    finally:
        log.close()
    t0 = time.monotonic()
    ready = re.compile(r"RCON running on 127\.0\.0\.1:%d" % a.rcon_port)
    props = os.path.join(a.mc, "server.properties")
    while True:
        text = tail(console, offset)
        if proc.poll() is not None:
            raise Fail(1, "the test server exited (code %d) while starting; the end of %s:\n%s"
                       % (proc.returncode, console, "\n".join(text.splitlines()[-25:])))
        if ready.search(text):
            try:
                r = Rcon(props)
                r.command("list")
                r.close()
                return proc, time.monotonic() - t0
            except OSError:
                pass
        if time.monotonic() - t0 > timeout:
            end(proc, 30)
            raise Fail(1, "the test server's RCON did not answer within %d s; the end of %s:\n%s"
                       % (timeout, console, "\n".join(text.splitlines()[-25:])))
        time.sleep(0.25)


def make_template(a, jar_sha, why):
    """Start the server on a new world, generate and forceload the stage, stop it, keep the world
    as world.template/."""
    say("making the template world (%s): seed %s, the sky stage at %d %d; a minute or two, once"
        % (why, SEED, STAGE_X, STAGE_Z))
    world, template = os.path.join(a.mc, "world"), os.path.join(a.mc, "world.template")
    shutil.rmtree(world, ignore_errors=True)
    shutil.rmtree(template + ".part", ignore_errors=True)
    proc, secs = start_server(a, 600)
    say("started in %.1f s; generating" % secs)
    t0 = time.monotonic()
    try:
        r = Rcon(os.path.join(a.mc, "server.properties"))
        try:
            for rule, value in GAMERULES:
                out = r.command("gamerule %s %s" % (rule, value))
                if "<--[HERE]" in out or out.startswith(("Unknown", "Incorrect")):
                    say("gamerule %s: %s" % (rule, out.strip()))
            r.command("time set noon")
            r.command("weather clear")
            g = PREGEN * 16
            big = (STAGE_X - g, STAGE_Z - g, STAGE_X + g + 15, STAGE_Z + g + 15)
            forceload(r, big[0], big[1], big[2], big[3], "add")
            wait_loaded(r, big[0], big[1], big[2], big[3], 600)
            c = STAGE_CLEAR
            keep = (STAGE_X - c, STAGE_Z - c, STAGE_X + c, STAGE_Z + c)
            r.command("forceload remove all")
            forceload(r, keep[0], keep[1], keep[2], keep[3], "add")
            r.command("setworldspawn %d %d %d" % (STAGE_X, STAGE_FLOOR + 1, STAGE_Z))
            out = r.command("save-all flush", timeout=300)
            gen_s = time.monotonic() - t0
            try:
                r.command("stop")
            except OSError:
                pass  # it may close the connection before it answers
        finally:
            r.close()
        code = proc.wait(180)
    except BaseException:
        end(proc, 30)
        shutil.rmtree(world, ignore_errors=True)
        raise
    if code not in (0, 143):
        raise Fail(1, "the test server exited with %s after making the template" % code)
    shutil.rmtree(template + ".part", ignore_errors=True)
    copy_tree(world, template + ".part")
    try:
        os.remove(os.path.join(template + ".part", "session.lock"))
    except OSError:
        pass
    shutil.rmtree(template, ignore_errors=True)
    os.replace(template + ".part", template)
    shutil.rmtree(world, ignore_errors=True)  # reset.sh makes it from the template at each start
    chunks = (2 * PREGEN + 2) ** 2
    write_file(os.path.join(a.mc, "template.json"), json.dumps({
        "seed": SEED, "version": MC_VERSION, "format": TEMPLATE_FORMAT, "jar_sha256": jar_sha, "made": now_iso(),
        "made_s": round(time.monotonic() - t0 + secs, 1), "generated": list(big), "chunks": chunks,
        "forceloaded": list(keep), "spawn": [STAGE_X, STAGE_FLOOR + 1, STAGE_Z],
        "stage": {"x": STAGE_X, "z": STAGE_Z, "floor": STAGE_FLOOR, "clear": STAGE_CLEAR}}, indent=2) + "\n")
    say("template made: %d chunks around the stage in %.0f s; %s" % (chunks, gen_s, out.strip() or "saved"))


def make_aot(a, libs, ident, why):
    """A start as the unit's, from a fresh world, recorded: the JVM writes <mc>/test-server.aot as
    it exits (Java 25's AOT cache, JEP 483/514/515). Failing, the server starts without one."""
    say("making the AOT cache (%s): one start, recorded" % why)
    train = os.path.join(a.mc, "launch.train.args")
    part = os.path.join(a.mc, AOT + ".part")
    for path in (part, part + ".config"):
        try:
            os.remove(path)
        except OSError:
            pass
    # -Xlog:aot*=error: the recording warns of every class it leaves out (about 800 lines in console.log).
    write_file(train, launch_args(libs, "-XX:AOTCacheOutput=" + AOT + ".part\n-Xlog:aot*=error"))
    world = os.path.join(a.mc, "world")
    copy_tree(os.path.join(a.mc, "world.template"), world)   # as reset.sh does (it waits for our lock)
    t0 = time.monotonic()
    proc, secs = start_server(a, 600, "launch.train.args")
    try:
        rc = Rcon(os.path.join(a.mc, "server.properties"))
        try:
            # What a test does first, so its classes are in the cache too: a fake player on the stage.
            rc.command("player Steve spawn at %d %d %d facing 180 0 in minecraft:overworld in survival"
                       % (STAGE_X, STAGE_FLOOR + 1, STAGE_Z))
            time.sleep(3)
            rc.command("player Steve kill")
            time.sleep(1)
            try:
                rc.command("stop")
            except OSError:
                pass
        finally:
            rc.close()
        code = proc.wait(300)   # the JVM writes the cache as it exits
    except BaseException:
        end(proc, 30)
        raise
    finally:
        os.remove(train)
        shutil.rmtree(world, ignore_errors=True)   # reset.sh makes it from the template at each start
    if code not in (0, 143) or not os.path.isfile(part) or os.path.getsize(part) < 1 << 20:
        say("no AOT cache (exit %s); the server starts without one" % code)
        return False
    os.chmod(part, 0o644)
    os.replace(part, os.path.join(a.mc, AOT))
    write_file(os.path.join(a.mc, AOT_INFO), json.dumps({"java_id": ident, "made": now_iso(), "start_s": round(secs, 1),
                                                          "made_s": round(time.monotonic() - t0, 1),
                                                          "bytes": os.path.getsize(os.path.join(a.mc, AOT))},
                                                         indent=2) + "\n")
    say("AOT cache made in %.0f s (%d MB): a start reads it" % (time.monotonic() - t0,
                                                                 os.path.getsize(os.path.join(a.mc, AOT)) >> 20))
    return True


# ------------------------------------------------------------------ main

def setup(a):
    live_props = props_of(read_lines(os.path.join(a.live_mc, "server.properties")))
    if not live_props:
        raise Fail(3, "no %s: there is no live server here" % os.path.join(a.live_mc, "server.properties"))
    why = refusals(a, live_props)
    if why:
        raise Fail(3, "refused: " + "; ".join(why))
    a.gid = test_group(a.test_user)
    for d in (a.mc, a.vfg):
        os.makedirs(d, exist_ok=True)
    lock = os.open(os.path.join(a.mc, LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say("waiting for another testserver.py or reset.sh on %s" % a.mc)
            fcntl.flock(lock, fcntl.LOCK_EX)
        return setup_locked(a, live_props)
    finally:
        os.close(lock)


def test_group(test_user):
    """The test user's group id, after checking the user and that this process is in its group
    (it gives the test's folders to it); None without a test user."""
    if not test_user:
        return None
    try:
        user = pwd.getpwnam(test_user)
        group = grp.getgrnam(test_user)
    except KeyError:
        raise Fail(3, "no user and group %s: testserver.sh makes them (useradd --system)" % test_user)
    if user.pw_uid == os.geteuid():
        raise Fail(3, "the test user %s is the user this runs as: a test must not have the live services' uid"
                      % test_user)
    if group.gr_gid not in os.getgroups() and group.gr_gid != os.getegid():
        raise Fail(3, "%s is not in group %s here: usermod -aG %s %s, then a new login (testserver.sh runs this "
                      "through runuser, which has it)" % (pwd.getpwuid(os.geteuid()).pw_name, test_user, test_user,
                                                           pwd.getpwuid(os.geteuid()).pw_name))
    return group.gr_gid


def setup_locked(a, live_props):
    changed = []
    gid = a.gid
    marker = os.path.join(a.mc, MARKER)
    old = read_json(marker, {})
    info = {"made": old.get("made") or now_iso(), "rcon_port": a.rcon_port, "port": a.port,
            "live_mc": os.path.realpath(a.live_mc), "live_vfg": os.path.realpath(a.live_vfg),
            "vfg": os.path.realpath(a.vfg)}
    if a.test_user:
        info["test_user"] = a.test_user
    if write_file(marker, json.dumps(info, indent=2) + "\n"):
        say("%s: written" % marker)
    set_mode(marker, 0o644)
    jar_sha, c = ensure_jar(a.live_mc, a.mc)
    if c:
        changed.append("server.jar")
    libs, c = ensure_libs(a.mc, a.sources)
    if c:
        changed.append("Fabric Loader")
    if ensure_carpet(a.mc, a.sources):
        changed.append("Carpet")
    ident = None if a.no_aot else java_id(a.java, libs)
    aot_ok = ident is not None and aot_wanted(a.mc, ident) is None
    if write_file(os.path.join(a.mc, "launch.args"), launch_args(libs, "-XX:AOTCache=" + AOT if aot_ok else None)):
        changed.append("launch.args")
    props = os.path.join(a.mc, "server.properties")
    lines, c = test_properties(read_lines(os.path.join(a.live_mc, "server.properties")), read_lines(props),
                               a.port, a.rcon_port)
    mode = 0o600 if gid is None else 0o640   # the sandboxed test reads its RCON password
    if c or not os.path.exists(props):
        write_file(props, "\n".join(lines) + "\n", mode)
        changed.append("server.properties")
    if set_mode(props, mode, gid):
        say("%s: mode %o%s" % (props, mode, "" if gid is None else ", group " + a.test_user))
    lines = None
    eula = os.path.join(a.mc, "eula.txt")
    accepted = eula_accepted(os.path.join(a.live_mc, "eula.txt"))
    if accepted:
        if write_file(eula, EULA_TEXT % os.path.join(a.live_mc, "eula.txt")):
            changed.append("eula.txt")
    elif os.path.exists(eula):
        os.remove(eula)
        changed.append("eula.txt")
    if write_file(os.path.join(a.mc, "reset.sh"), reset_sh(a.mc, a.live_mc, a.rcon_port, a.test_user), 0o755):
        changed.append("reset.sh")
    for name in ("console.log",):
        path = os.path.join(a.mc, name)
        if not os.path.exists(path):
            open(path, "a").close()
    if lay_out_vfg(a.vfg, a.live_vfg, gid):
        say("%s: laid out" % a.vfg)
    why = None if a.no_template else ("asked for (--fresh)" if a.fresh else template_wanted(a.mc, jar_sha))
    why_aot = None if a.no_template or a.no_aot else (
        "the template is new" if why else aot_wanted(a.mc, ident))
    if why or why_aot:
        if not accepted:
            raise Fail(3, "the live server's eula.txt (%s) does not say eula=true, so the test server cannot "
                          "start to make its world; everything else is laid out" % a.live_mc)
        if port_answers(a.rcon_port) or port_answers(a.port):
            if changed:   # written now: the run after the stop finds them done and does not list them
                say("%s: changed: %s" % (a.mc, ", ".join(changed)))
            say("the %s must be made (%s), and the test server is running: stop it, then run this again"
                % (("template", why) if why else ("AOT cache", why_aot)))
            return EXIT_STOP_FIRST
    if why:
        make_template(a, jar_sha, why)
        changed.append("world.template")
    if why_aot and make_aot(a, libs, ident, why_aot):
        write_file(os.path.join(a.mc, "launch.args"), launch_args(libs, "-XX:AOTCache=" + AOT))
        changed.append("the AOT cache")
    say("%s: %s (game 127.0.0.1:%d, RCON 127.0.0.1:%d)" % (
        a.mc, "changed: " + ", ".join(changed) + "; (re)start the test server to apply" if changed else "up to date",
        a.port, a.rcon_port))
    return EXIT_RESTART if changed else EXIT_OK


def parse(argv):
    p = argparse.ArgumentParser(prog="testserver.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--live-mc", default=LIVE_MC, help="the live Minecraft folder (default %(default)s)")
    p.add_argument("--live-vfg", default=LIVE_VFG, help="the live vfg folder (default %(default)s)")
    p.add_argument("--mc", help="the test Minecraft folder (default: the live one + -test)")
    p.add_argument("--vfg", help="the test vfg folder (default: the live one + -test)")
    p.add_argument("--port", type=int, default=PORT, help="its game port, on 127.0.0.1 (default %(default)s)")
    p.add_argument("--rcon-port", type=int, default=RCON_PORT, help="its RCON port (default %(default)s)")
    p.add_argument("--heap", default=HEAP, help="its -Xmx while it makes the template (default %(default)s)")
    p.add_argument("--user", default=None, help="the user it must run as (testserver.sh: %s)" % USER)
    p.add_argument("--test-user", default=None, metavar="USER",
                   help="the user a sandboxed test runs as (testserver.sh: vfgtest); its group gets run/, "
                        "state/ and server.properties")
    p.add_argument("--java", default="java", help="a Java 25 (default: java on the PATH)")
    p.add_argument("--from", dest="sources", action="append", default=[], metavar="DIR",
                   help="look for DIR/libraries/<path> and DIR/mods/<Carpet> first (hash-checked)")
    p.add_argument("--fresh", action="store_true", help="make the template world again")
    p.add_argument("--no-template", action="store_true", help="lay out the files only (no template, no AOT cache)")
    p.add_argument("--no-aot", action="store_true", help="no AOT cache: every start loads its classes itself")
    p.add_argument("--wait", type=float, metavar="SECONDS", help="only wait until its RCON answers `list`")
    a = p.parse_args(argv)
    a.live_mc = os.path.abspath(a.live_mc.rstrip("/") or "/")
    a.live_vfg = os.path.abspath(a.live_vfg.rstrip("/") or "/")
    a.mc = os.path.abspath(a.mc or a.live_mc + SUFFIX)
    a.vfg = os.path.abspath(a.vfg or a.live_vfg + SUFFIX)
    a.sources = [os.path.abspath(s) for s in a.sources]
    if not HEAP_RE.match(a.heap):
        p.error("--heap is like 1536M or 2G")
    return a


def main(argv=None):
    a = parse(argv)
    try:
        if a.wait is not None:
            secs = wait_rcon(os.path.join(a.mc, "server.properties"), a.wait)
            say("RCON on %s answers (%.1f s)" % (a.mc, secs))
            return 0
        if os.geteuid() == 0:
            raise Fail(3, "refused: run it as the server's user, not root (testserver.sh runs it as %s)" % USER)
        me = pwd.getpwuid(os.geteuid()).pw_name
        if a.user and a.user != me:
            raise Fail(3, "refused: it runs as %s, not %s" % (me, a.user))
        return setup(a)
    except Fail as f:
        sys.stderr.write("testserver: %s\n" % f.msg)
        return f.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
