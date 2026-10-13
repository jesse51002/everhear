"""testserver_escape: the sandbox-escape regression test, run as an ordinary `vfg test` on a test
server that sits beside a live one (Prototype_Implementer_Quality).

It is a misbehaving test. It reaches for the live server every way the box run of 2026-10-13 found
open — the live RCON password through a live process's /proc, the bridge's secrets, the live RCON
and game and bridge ports, the internet, the live RCON lock in /tmp, root through sudo — and prints
one ESCAPE line per attempt, REACHED or refused, never a secret value. It changes nothing on the
live side: it reads only, opens no RCON command past a login it makes with a deliberately WRONG
password (never one it read), and writes nothing live.

integration/testserver_e2e.py fills in __CFG__ (the live folders, ports and RCON lock), writes this
to made/<server>/tests/<task>.py and runs `vfg test <task>`, then judges the ESCAPE lines: INFO in
user mode on the PC (the same user as the live services, weaker), every one refused under --box (the
box's system sandbox: a separate test user). The t.check below only proves the test itself ran.
"""
import fcntl
import json
import os
import re
import socket
import struct
import subprocess

from kit import test

LIVE = json.loads(r"""__CFG__""")   # {"mc", "vfg", "rcon_port", "game_port", "api_port", "lock"}

t = test.Test("Steve")
_tried = []


def escape(attempt, fn):
    """fn() reaches for the live server. Print one ESCAPE line, REACHED or refused; record whether
    it reached, never what it read."""
    try:
        got = fn()
    except (OSError, socket.error, ValueError) as e:
        print("ESCAPE     %s: refused, %s: %s" % (attempt, type(e).__name__, str(e)[:120]), flush=True)
        _tried.append((attempt, False))
        return
    reached = bool(got)
    print("ESCAPE     %s: %s" % (attempt, "REACHED %s" % (got if got is not True else "yes") if reached
                                 else "refused, returned nothing"), flush=True)
    _tried.append((attempt, reached))


def opened(path):
    """True when `path` can be opened for reading (its bytes are never printed)."""
    with open(path, "rb") as f:
        f.read(1)
    return True


def wrote(path):
    with open(path, "w") as f:
        f.write("vbox-escape")
    return True


def connected(port):
    socket.create_connection(("127.0.0.1", int(port)), timeout=2).close()
    return True


def outbound():
    socket.create_connection(("1.1.1.1", 443), timeout=2).close()
    return True


def rcon_wrong_password(port):
    """A login to the live RCON with a deliberately WRONG password: True when the socket even
    opens (the reach), never a command run. Never a password this test read."""
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=2)
    try:
        body = b"not-the-password"
        s.sendall(struct.pack("<iii", len(body) + 10, 1, 3) + body + b"\x00\x00")
        s.recv(64)
        return True
    finally:
        s.close()


def held(path):
    fd = os.open(path, os.O_RDONLY)          # a read-only fd is enough for flock
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    finally:
        os.close(fd)


def live_pids():
    """The live server's processes this test can see: Minecraft (`-jar server.jar`), the bridge and
    Jev, by command line, each whose cwd or root is in the live mc or vfg folder; {} when none are
    visible (the box's sandbox hides them)."""
    found = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        hit = (re.search(r"-jar\s+\S*server\.jar", cmd) or "fabric" in cmd.lower()
               or LIVE["mc"] in cmd or LIVE["vfg"] in cmd
               or "vfg_bridge" in cmd or "vfg-jev" in cmd or "vfg_jev" in cmd)
        if hit and "vbox_escape" not in cmd:   # never this test itself
            found[pid] = cmd
    return found


pids = live_pids()
print("ESCAPE     a live server process is visible: %s" % ("%d" % len(pids) if pids else "none"), flush=True)
_tried.append(("a live server process is visible", bool(pids)))

# Through a live process's /proc: its cwd and root reach the live folders around InaccessiblePaths,
# its fds are the files it has open, its environ holds the box's secrets.
for pid in list(pids)[:3]:
    escape("read the live RCON password via /proc/%s/cwd/server.properties" % pid,
           lambda pid=pid: opened("/proc/%s/cwd/server.properties" % pid))
    escape("read /proc/%s/root/opt/mc/server.properties" % pid,
           lambda pid=pid: opened("/proc/%s/root/opt/mc/server.properties" % pid))
    escape("read /proc/%s/environ (the box's secrets)" % pid, lambda pid=pid: opened("/proc/%s/environ" % pid))
    escape("read /proc/%s/cwd/.env" % pid, lambda pid=pid: opened("/proc/%s/cwd/.env" % pid))
    escape("write via /proc/%s/root/opt/vfg" % pid,
           lambda pid=pid: wrote("/proc/%s/root/opt/vfg/vbox-escape.txt" % pid))
    try:
        fds = os.listdir("/proc/%s/fd" % pid)
    except OSError:
        fds = []
    if fds:
        escape("read the live server's open files (/proc/%s/fd)" % pid,
               lambda pid=pid, fds=fds: any(opened("/proc/%s/fd/%s" % (pid, fd)) for fd in fds[:20]))

# The live folders by their own paths (around the sandbox's mounts), the ports, the internet, the
# live RCON lock, and root.
escape("read the live server.properties by its path", lambda: opened(os.path.join(LIVE["mc"], "server.properties")))
escape("read the live .env by its path", lambda: opened(os.path.join(LIVE["vfg"], ".env")))
escape("connect to the live RCON port %s" % LIVE["rcon_port"], lambda: connected(LIVE["rcon_port"]))
escape("open the live RCON with a wrong password", lambda: rcon_wrong_password(LIVE["rcon_port"]))
escape("connect to the live game port %s" % LIVE["game_port"], lambda: connected(LIVE["game_port"]))
if LIVE.get("api_port"):
    escape("connect to the live bridge port %s" % LIVE["api_port"], lambda: connected(LIVE["api_port"]))
escape("reach the internet (1.1.1.1:443)", outbound)
escape("hold the live RCON lock %s" % LIVE["lock"], lambda: held(LIVE["lock"]))
escape("sudo -n true", lambda: subprocess.run(["sudo", "-n", "true"], timeout=5,
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)

# the test itself worked: it made its attempts (the suite judges the ESCAPE lines, not this)
t.check("the escape attempts ran", test.verdict(len(_tried) >= 10, "%d attempts" % len(_tried)))
t.done()
