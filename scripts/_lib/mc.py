"""Shared helpers for every script in the scripts folder.

On EC2 a script runs as `python3 -u /opt/vfg/scripts/<name>/main.py`, in its own
folder, with PYTHONPATH=/opt/vfg/scripts/_lib, so it starts with

    from mc import rcon, say, players, pos, spawn, on_stop, log, TAG

Python 3.9, standard library only. Seeded from the old rcon.py and demon.py.

The RCON password is read from server.properties on first use and kept only in
memory: it is never printed, logged or written.
"""
import json
import math
import os
import random
import re
import signal
import socket
import struct
import sys
import threading
import time
from typing import Optional, Tuple

# This script's folder name, from sys.argv[0], and the tag for everything it summons.
NAME = os.path.basename(os.path.dirname(os.path.abspath(sys.argv[0] or "."))) or "script"
TAG = "vfg_" + NAME

PROPERTIES = os.environ.get("VFG_MC_PROPERTIES", "/opt/mc/server.properties")
HOST = os.environ.get("VFG_RCON_HOST", "127.0.0.1")
TIMEOUT = 10.0

# The server's owner: VFG_OWNER, which `vfg script run` sets from ~/.vfg/config; "" when unset.
OWNER = os.environ.get("VFG_OWNER", "").strip()
if not re.match(r"^[A-Za-z0-9_]{1,16}$", OWNER):
    OWNER = ""

# Server commands that are the owner's to run, never a script's. The orchestrator's
# guard refuses the same words.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip",
                     "whitelist", "kick", "save-off"])

_lock = threading.RLock()
_local = threading.local()
_conn = None
_settings = None
_next_id = [1]
_stoppers = []


def log(*parts) -> None:
    """Print "HH:MM:SS NAME parts" (UTC) and flush; it is what `vfg script logs` shows."""
    line = "%s %s %s" % (time.strftime("%H:%M:%S", time.gmtime()), NAME,
                         " ".join(str(p) for p in parts))
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def refused_word(cmd: str) -> Optional[str]:
    """The refused word in a command (its first word, or the word after any `run`), or None."""
    words = cmd.strip().split()
    if not words:
        return None
    checks = [words[0]]
    for i, w in enumerate(words[:-1]):
        if w.lower() == "run":
            checks.append(words[i + 1])
    for w in checks:
        w = w.lstrip("/").lower()
        if w in REFUSED:
            return w
    return None


def _read_settings() -> Tuple[str, int]:
    global _settings
    if _settings is None:
        password, port = None, 25575
        with open(PROPERTIES, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\r\n")
                if line.startswith("rcon.password="):
                    password = line[len("rcon.password="):]
                elif line.startswith("rcon.port="):
                    try:
                        port = int(line[len("rcon.port="):].strip())
                    except ValueError:
                        pass
        if not password:
            raise OSError("no rcon.password in server.properties")
        _settings = (password, port)
    return _settings


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def _send(s: socket.socket, req_id: int, kind: int, body: str) -> None:
    data = struct.pack("<ii", req_id, kind) + body.encode("utf-8") + b"\x00\x00"
    s.sendall(struct.pack("<i", len(data)) + data)


def _read_packet(s: socket.socket) -> Tuple[int, int, str]:
    (length,) = struct.unpack("<i", _recv_exact(s, 4))
    if length < 10 or length > 1 << 20:
        raise OSError("bad packet length")
    data = _recv_exact(s, length)
    req_id, kind = struct.unpack("<ii", data[:8])
    return req_id, kind, data[8:-2].decode("utf-8", errors="replace")


def _new_id() -> int:
    _next_id[0] = _next_id[0] % 2000000000 + 1
    return _next_id[0]


def _connect() -> socket.socket:
    password, port = _read_settings()
    s = socket.create_connection((HOST, port), timeout=TIMEOUT)
    try:
        s.settimeout(TIMEOUT)
        auth_id = _new_id()
        _send(s, auth_id, 3, password)
        while True:
            req_id, kind, _ = _read_packet(s)
            if req_id == -1:
                raise OSError("rcon login refused")
            if req_id == auth_id and kind == 2:
                return s
    except Exception:
        s.close()
        raise


def _exchange(s: socket.socket, cmd: str) -> str:
    req_id = _new_id()
    _send(s, req_id, 2, cmd)
    while True:
        got, _, body = _read_packet(s)
        if got == req_id:
            return body
        # A leftover chunk of an earlier, longer reply: skip it.


def _drop() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def rcon(cmd: str) -> str:
    """Run one server command and return its output; "" on failure or refusal (logged).

    One connection is kept open. Responses are read as one packet, so a reply over
    about 4 KB is cut: ask narrow questions.
    """
    global _conn
    cmd = (cmd or "").strip()
    if cmd.startswith("/"):
        cmd = cmd[1:]
    if not cmd:
        return ""
    bad = refused_word(cmd)
    if bad:
        log("refused:", repr(bad), "is the server owner's to do")
        return ""
    if getattr(_local, "inside", False):
        # This thread is already inside rcon: an on_stop cleanup interrupted a call
        # mid-read. Use a fresh connection of its own.
        try:
            s = _connect()
            try:
                return _exchange(s, cmd)
            finally:
                s.close()
        except Exception as e:
            log("rcon error:", type(e).__name__, e)
            return ""
    with _lock:
        _local.inside = True
        try:
            for attempt in (1, 2):
                try:
                    if _conn is None:
                        _conn = _connect()
                    return _exchange(_conn, cmd)
                except Exception as e:
                    _drop()
                    if attempt == 2:
                        log("rcon error:", type(e).__name__, e)
            return ""
        finally:
            _local.inside = False


def say(text, to="@a", who="AI", color="aqua") -> None:
    """tellraw "[who] text" to everyone or one player. For a minigame's own
    announcements, never for answering players."""
    msg = [{"text": "[%s] " % who, "color": color, "bold": True},
           {"text": str(text), "color": "white", "bold": False}]
    rcon("tellraw %s %s" % (to, json.dumps(msg)))


def players(alive=False) -> list:
    """Who is online, from "list". alive=True keeps only those with Health > 0."""
    m = re.search(r"online:(.*)", rcon("list"))
    names = [p.strip() for p in m.group(1).split(",") if p.strip()] if m else []
    if not alive:
        return names
    out = []
    for p in names:
        h = re.search(r"(-?[\d.]+)f", rcon("data get entity %s Health" % p))
        if h and float(h.group(1)) > 0:
            out.append(p)
    return out


def default_player() -> str:
    """The player a script acts for when its arguments name none: the owner (VFG_OWNER),
    else the first player online. With neither, it logs why and exits 2."""
    if OWNER:
        return OWNER
    online = players()
    if online:
        return online[0]
    log("no player named, VFG_OWNER is not set and nobody is online")
    sys.exit(2)


def owner_near(distance) -> str:
    """A selector for the owner within `distance` blocks, to spare them ("unless entity ..."),
    or one that matches nobody when VFG_OWNER is unset."""
    if OWNER:
        return "@a[name=%s,distance=..%s]" % (OWNER, distance)
    return "@a[tag=vfg_no_owner,distance=..%s]" % distance


def not_owner() -> str:
    """",name=!<owner>", to leave the owner out of an @e selector; "" when VFG_OWNER is unset."""
    return ",name=!" + OWNER if OWNER else ""


def pos(player) -> Optional[tuple]:
    """(x, y, z) as floats from "data get entity <p> Pos", or None."""
    v = re.findall(r"(-?[\d.]+(?:[eE]-?\d+)?)d", rcon("data get entity %s Pos" % player))
    return (float(v[0]), float(v[1]), float(v[2])) if len(v) >= 3 else None


def count(selector) -> int:
    """How many entities match a selector, from "execute if entity <selector>"."""
    m = re.search(r"[Cc]ount: (\d+)", rcon("execute if entity %s" % selector))
    return int(m.group(1)) if m else 0


def score(player, objective) -> int:
    """A player's score on an objective, 0 when unset."""
    m = re.search(r"has (-?\d+) ", rcon("scoreboard players get %s %s" % (player, objective)))
    return int(m.group(1)) if m else 0


def spawn(player, mob, nbt="", spread=6, y=1) -> bool:
    """Summon a mob at a safe spot near a player (2 blocks of room above solid
    ground, never on top of them), tagged ["vfg", TAG] and persistent.
    y > 3 summons in the air that many blocks up. False when no safe spot was found."""
    tags = 'Tags:["vfg","%s"],PersistenceRequired:1b' % TAG
    data = "{" + tags + ("," + nbt if nbt else "") + "}"
    for _ in range(12):
        a = random.uniform(0, 6.283)
        d = random.uniform(max(7, spread * 0.8), max(10, spread * 1.4))
        dx, dz = d * math.cos(a), d * math.sin(a)
        for dy in ((y,) if y > 3 else (0, 1, -1, 2, -2, 3)):
            at = "execute at %s positioned ~%.1f ~%d ~%.1f " % (player, dx, dy, dz)
            if y > 3:
                test = at + "if block ~ ~ ~ air if block ~ ~1 ~ air"
            else:
                test = (at + "if block ~ ~ ~ #minecraft:replaceable if block ~ ~1 ~ #minecraft:replaceable"
                        " unless block ~ ~-1 ~ #minecraft:replaceable")
            if "passed" in rcon(test).lower():
                rcon("execute at %s run summon %s ~%.1f ~%d ~%.1f %s" % (player, mob, dx, dy, dz, data))
                return True
    return False


def _on_signal(signum, frame) -> None:
    log("stopping (signal %d)" % signum)
    for fn in reversed(list(_stoppers)):
        try:
            fn()
        except Exception as e:
            log("cleanup error:", type(e).__name__, e)
    # Anything summoned with TAG goes too, even if a cleanup forgot it.
    rcon("kill @e[tag=%s]" % TAG)
    log("stopped")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


def on_stop(fn) -> None:
    """Run fn when the script is stopped (SIGTERM or SIGINT). Every fn runs, newest
    first; then everything tagged TAG is killed and the script exits 0."""
    _stoppers.append(fn)


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
