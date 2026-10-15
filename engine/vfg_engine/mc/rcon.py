"""The engine's one RCON connection to Minecraft (the game engine spec, "Reading the world without
waiting": both threads share it, behind the box's RCON lock).

    r = Rcon()                          # server.properties from VFG_MC_PROPERTIES, else /opt/mc
    r.command("time query gametime")    # -> "The game time is 1234 tick(s)"
    with r.held():                      # a batch: the lock held across several commands
        for c in commands: r.command(c)

What it does that a plain client does not, each measured on 26.3 (engine/MINECRAFT.md):

- **The shared lock.** Vanilla answers every RCON connection from one shared buffer, so every client
  on the machine holds the flock /tmp/vfg-rcon-<port>.lock (VFG_RCON_LOCK overrides it) from a send
  to the end of its reply; a re-entrancy marker shared by every RCON client loaded in one process
  (sys.modules["_vfg_rcon_held"]) lets a thread that already holds it take it again. A thread
  lock in front of it shares the one connection between the main thread and the mirror's.
- **Never pipelined.** Vanilla reads each packet with a fresh 1460-byte read and drops the
  connection when a read does not hold exactly one packet, so a second packet sent before the
  first is read loses both. One command is sent, its whole reply read, then the next.
- **Whole replies.** Vanilla cuts a reply into packets of 4096 UTF-16 units. A packet shorter than
  that is the last; after one of exactly 4096 the end is unknowable, so a marker packet (type 0,
  which vanilla answers "Unknown request 0") is sent and everything up to its answer is the reply.
  The marker is sent only once the reply has started, when the server has read the command.
- **No 40 ms per extra packet.** Each packet after the first used to wait ~40 ms for the client's
  delayed ACK; TCP_QUICKACK after every recv removes it (38 KB: 42 ms -> 0.2 ms, measured).
- **Exception-safe.** The engine's 50 ms cut is an exception raised from a SIGALRM handler, which
  Python runs on the main thread between any two bytecodes, even just after a recv() returned and
  before its bytes were kept (blocking the signal does not help: the kernel hands it to any other
  thread, and Python still runs the handler on the main one). So whatever is raised between a send
  and the end of its reply, the exchange is finished on the same socket before the lock is let go
  (_recover): an end marker is sent and the raw bytes read until its answer's exact bytes appear,
  which puts the reading back in step however many bytes were lost. Closing the socket is not
  enough: the server still runs the command, and through the shared buffer its output lands in the
  next command's reply, on any connection (measured). A late reply is never read as the next
  command's; if the rest cannot be read, the connection is dropped and the next command
  reconnects. Socket errors and timeouts drop the connection the same way.
- **Backoff.** A failed connect waits 0.1 s, doubling to 5 s, before the next try; meanwhile
  commands fail at once with RconError, saying why, rather than holding up the tick.

The password is read from server.properties when connecting and kept only in memory: it is never
printed, logged or written. Standard library only.
"""

from __future__ import annotations

import collections
import contextlib
import fcntl
import os
import socket
import struct
import sys
import threading
import time

PROPERTIES = os.environ.get("VFG_MC_PROPERTIES", "/opt/mc/server.properties")
HOST = os.environ.get("VFG_RCON_HOST", "127.0.0.1")
# RCON reads a command in one 1460-byte packet; a longer one makes vanilla drop the connection
# (measured), so it is refused before it is sent.
MAX_COMMAND = 1446
PACKET_UNITS = 4096          # vanilla cuts a reply every 4096 UTF-16 units
REPLY_TIMEOUT = 5.0          # seconds for a whole reply
CONNECT_TIMEOUT = 2.0
LOCK_WAIT = 2.0              # seconds to wait for another client's turn on the lock
BACKOFF_FIRST, BACKOFF_MAX = 0.1, 5.0
QUICKACK = getattr(socket, "TCP_QUICKACK", None)     # Linux only
UNKNOWN_REQUEST_0 = b"Unknown request 0"            # vanilla's answer to a packet of type 0
RECOVER_WAIT = 0.05          # _recover's look for an answer that may have been read already


class RconError(OSError):
    """RCON could not be used for this command; the message says why in words."""


def _held_locks():
    """The process's RCON locks, shared with every RCON client loaded in it (toolkit/remote/common.py,
    integration/realmc): {path: [thread ident, depth, fd]}."""
    h = sys.modules.get("_vfg_rcon_held")
    if h is None:
        h = type(sys)("_vfg_rcon_held")
        h.locks, h.guard = {}, threading.RLock()
        h = sys.modules.setdefault("_vfg_rcon_held", h)
    return h


def _open_lock(path: str) -> int:
    """A descriptor flock can hold on `path`, made if missing: read-only and without O_CREAT when it
    exists (a sticky /tmp refuses O_CREAT on another user's file)."""
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


class _Flock:
    """The lock file, held from a send to the end of its reply (or across a batch)."""

    def __init__(self, path: str):
        self.path, self.fd = path, None

    def acquire(self, wait: float = LOCK_WAIT) -> float:
        """Takes the lock; returns the seconds waited. RconError when another client keeps it."""
        if self.fd is None:
            self.fd = _open_lock(self.path)
        held, me = _held_locks(), threading.get_ident()
        with held.guard:
            got = held.locks.get(self.path)
            if got and got[0] == me:
                got[1] += 1
                return 0.0
        start = time.monotonic()
        try:
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start > wait:
                        raise RconError("another RCON client has held %s for over %.0f s"
                                        % (self.path, wait)) from None
                    time.sleep(0.0003)
            with held.guard:
                held.locks[self.path] = [me, 1, self.fd]
        except BaseException:
            # Raised after flock took it (a cut can land anywhere): never leave it held.
            with held.guard:
                got = held.locks.get(self.path)
                if got and got[0] == me:
                    del held.locks[self.path]
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            raise
        return time.monotonic() - start

    def release(self):
        if self.fd is None:
            return
        held, me = _held_locks(), threading.get_ident()
        fd = self.fd
        with held.guard:
            got = held.locks.get(self.path)
            if got and got[0] == me and got[1] > 1:
                got[1] -= 1
                return
            if got and got[0] == me:
                del held.locks[self.path]
                fd = got[2]
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def read_properties(path: str = PROPERTIES) -> tuple[str, int]:
    """(password, port) from server.properties."""
    password, port = None, 25575
    with open(path, encoding="utf-8", errors="replace") as f:
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
        raise RconError("no rcon.password in %s" % path)
    return password, port


def lock_path_for(port: int) -> str:
    return os.environ.get("VFG_RCON_LOCK") or "/tmp/vfg-rcon-%d.lock" % port


def units(body: bytes) -> int:
    """How many UTF-16 units Java counts in a packet's text (it cuts replies by those)."""
    if len(body) < PACKET_UNITS:
        return len(body)            # a char is at least one byte: this many bytes is at most this many units
    return len(body.decode("utf-8", "replace").encode("utf-16-le")) // 2


class Stats:
    """How the connection has done: counts, and the recent exchange times for p50/p95."""

    def __init__(self):
        self.exchanges = self.packets = self.multi = self.markers = self.stray = 0
        self.sent = self.received = 0
        self.connects = self.broken = self.refused = self.recovered = 0
        self.lock_wait_s = self.lock_wait_max_s = 0.0
        self.recent = collections.deque(maxlen=1024)       # seconds per exchange
        self.last_error = None

    def percentile(self, q: float) -> float | None:
        if not self.recent:
            return None
        xs = sorted(self.recent)
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    def as_dict(self) -> dict:
        p50, p95 = self.percentile(0.5), self.percentile(0.95)
        return {"exchanges": self.exchanges, "packets": self.packets, "multi_packet": self.multi,
                "markers": self.markers, "stray_packets": self.stray, "sent_bytes": self.sent,
                "received_bytes": self.received, "connects": self.connects, "broken": self.broken,
                "refused": self.refused, "recovered": self.recovered,
                "lock_wait_ms": round(self.lock_wait_s * 1000, 3),
                "lock_wait_max_ms": round(self.lock_wait_max_s * 1000, 3),
                "p50_ms": None if p50 is None else round(p50 * 1000, 3),
                "p95_ms": None if p95 is None else round(p95 * 1000, 3),
                "last_error": self.last_error}


class Rcon:
    """One RCON connection, shared by threads, safe to interrupt. See the module's docstring."""

    def __init__(self, properties: str = PROPERTIES, host: str = HOST, port: int | None = None,
                 password: str | None = None, timeout: float = REPLY_TIMEOUT, quickack: bool = True):
        self.properties, self.host = properties, host
        self._port, self._password = port, password
        self.timeout = timeout
        self.quickack = bool(quickack and QUICKACK is not None)
        self.stats = Stats()
        self._lock = threading.RLock()
        self._flock: _Flock | None = None
        self._sock: socket.socket | None = None
        self._id = 0
        self._rid = self._marker = None     # the exchange in progress, for _recover
        self._done = True
        self._sent = self._started = False
        self._rbuf = bytearray()            # bytes received and not yet read as packets
        self._backoff = 0.0
        self._retry_at = 0.0
        self._closed = False

    # ---- settings
    @property
    def port(self) -> int:
        if self._port is None:
            self._password, self._port = read_properties(self.properties)
        return self._port

    def lock_path(self) -> str:
        return lock_path_for(self.port)

    # ---- the lock
    @contextlib.contextmanager
    def held(self):
        """Holds the connection and the RCON lock: one command, or a batch of them (flush)."""
        with self._lock:
            if self._flock is None:
                self._flock = _Flock(self.lock_path())
            waited = self._flock.acquire()
            try:
                self.stats.lock_wait_s += waited
                self.stats.lock_wait_max_s = max(self.stats.lock_wait_max_s, waited)
                yield self
            finally:
                self._flock.release()

    # ---- the connection
    def connected(self) -> bool:
        return self._sock is not None

    def _next_id(self) -> int:
        self._id = self._id % 2_000_000_000 + 1
        return self._id

    def _connect(self) -> socket.socket:
        if self._closed:
            raise RconError("the RCON connection was closed")
        now = time.monotonic()
        if now < self._retry_at:
            raise RconError("RCON is down (%s); next try in %.1f s"
                            % (self.stats.last_error or "not connected", self._retry_at - now))
        try:
            if self._password is None or self._port is None:
                self._password, self._port = read_properties(self.properties)
            s = socket.create_connection((self.host, self._port), timeout=CONNECT_TIMEOUT)
            try:
                s.settimeout(self.timeout)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                auth = self._next_id()
                self._send(s, auth, 3, self._password)
                while True:
                    rid, _kind, _body = self._read_packet(s)
                    if rid == -1:
                        raise RconError("RCON refused the password in %s" % self.properties)
                    if rid == auth:
                        break
            except BaseException:
                s.close()
                raise
        except (OSError, ValueError) as e:
            self._backoff = min(BACKOFF_MAX, max(BACKOFF_FIRST, self._backoff * 2))
            self._retry_at = time.monotonic() + self._backoff
            self.stats.last_error = "connect to %s:%s: %s" % (self.host, self._port, _words(e))
            raise RconError(self.stats.last_error) from e
        self._backoff, self._retry_at = 0.0, 0.0
        self.stats.connects += 1
        self._sock = s
        self._rbuf = bytearray()
        return s

    def _break(self, why: str):
        """The connection can no longer be trusted: close it, and the next command reconnects."""
        s, self._sock = self._sock, None
        self.stats.broken += 1
        self.stats.last_error = why
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _send(self, s: socket.socket, rid: int, kind: int, body: str):
        data = body.encode("utf-8")
        s.sendall(struct.pack("<iii", len(data) + 10, rid, kind) + data + b"\x00\x00")
        self.stats.sent += len(data) + 14

    def _recv(self, s: socket.socket):
        """More bytes into the buffer (what a recv returned is kept the moment it returns)."""
        c = s.recv(65536)
        if self.quickack:
            s.setsockopt(socket.IPPROTO_TCP, QUICKACK, 1)
        if not c:
            raise RconError("the server closed the RCON connection")
        self._rbuf += c

    def _read_packet(self, s: socket.socket) -> tuple[int, int, bytes]:
        """The next whole packet from the buffer, receiving as needed. A packet leaves the buffer
        only once it is whole, so an interruption mid-read loses at most what one recv returned."""
        while len(self._rbuf) < 4:
            self._recv(s)
        (length,) = struct.unpack_from("<i", self._rbuf, 0)
        if length < 10 or length > (1 << 21):
            raise RconError("a bad RCON packet (length %d)" % length)
        while len(self._rbuf) < 4 + length:
            self._recv(s)
        data = bytes(self._rbuf[4:4 + length])
        del self._rbuf[:4 + length]
        self.stats.received += length + 4
        rid, kind = struct.unpack("<ii", data[:8])
        return rid, kind, data[8:-2]

    def command(self, cmd: str) -> str:
        """Runs one command and returns its whole reply. RconError when it cannot be sent or its
        reply cannot be read (the connection is then dropped and the next command reconnects).
        Anything else raised meanwhile (a cut) first finishes the exchange (_recover), so the
        server is done with it before the lock is let go, and then goes on up."""
        cmd = (cmd or "").strip()
        if cmd.startswith("/"):
            cmd = cmd[1:]
        size = len(cmd.encode("utf-8"))
        if not cmd:
            raise RconError("an empty command")
        if size > MAX_COMMAND:
            self.stats.refused += 1
            raise RconError("the command is %d bytes, over the %d RCON takes (vanilla drops the "
                            "connection): %s..." % (size, MAX_COMMAND, cmd[:60]))
        with self.held():
            s = self._sock or self._connect()
            start = time.perf_counter()
            self._rid, self._marker, self._done = None, None, False
            self._sent = self._started = False
            try:
                reply = self._exchange(s, cmd)
            except (OSError, ValueError, struct.error) as e:
                self._done = True
                self._break("%s: %s" % (cmd[:60], _words(e)))
                raise RconError("RCON failed on %r: %s" % (cmd[:60], _words(e))) from e
            except BaseException as e:
                self._recover(s, e)
                raise
            self.stats.exchanges += 1
            self.stats.recent.append(time.perf_counter() - start)
            return reply

    def _exchange(self, s: socket.socket, cmd: str) -> str:
        self._rid = rid = self._next_id()
        self._send(s, rid, 2, cmd)
        self._sent = True
        parts, count = [], 0
        while True:
            got, _kind, body = self._read_packet(s)
            self.stats.packets += 1
            if got == rid:
                self._started = True
                parts.append(body)
                count += 1
                if self._marker is None:
                    if units(body) < PACKET_UNITS:
                        break
                    # Maybe more: the server has read the command (it is answering), so a
                    # marker now arrives in a read of its own, and is answered after the reply.
                    # Recorded before it is sent: if a cut lands between the two, _recover looks
                    # for its answer rather than sending a second marker into the same read.
                    self._marker = self._next_id()
                    self._send(s, self._marker, 0, "")
                    self.stats.markers += 1
                continue
            if self._marker is not None and got == self._marker:
                break
            self.stats.stray += 1        # a packet of some other id: never part of this reply
        self._done = True
        if count > 1:
            self.stats.multi += 1
        return b"".join(parts).decode("utf-8", "replace")

    def _recover(self, s: socket.socket, e: BaseException):
        """An exchange was interrupted (a cut from a signal handler, mid-send or mid-read). Vanilla
        answers every connection from one shared buffer, so the server must be done with it before
        anything else is sent by anyone, and the reading must be put back in step: an end marker
        is sent (or the one already sent used) and the raw bytes read until that marker's answer,
        byte for byte, appears; whatever came before it is dropped. Vanilla drops a connection whose
        read held two packets, so no second marker is sent while one may be unread: one already sent
        is looked for first, for RECOVER_WAIT (its answer may have been read before the interruption).
        The outcomes: the reply then the answer; the answer alone (the command never went); or the
        connection dropped (neither ran). Never raises; if it cannot finish, the connection goes."""
        if self._done:
            return
        why = type(e).__name__
        deadline = time.monotonic() + self.timeout
        try:
            found = self._marker is not None and self._scan(s, self._marker, RECOVER_WAIT)
            if not found:
                if self._sent and not self._started and not self._rbuf:
                    # The server may not have read the command yet, and a marker in the same read
                    # would drop the connection: give its reply a moment to start. (It may have
                    # been read already, the cut landing before that was noted: hence a moment,
                    # not until it comes; by then the server's reader has the command.)
                    s.settimeout(RECOVER_WAIT)
                    try:
                        self._recv(s)
                    except (socket.timeout, TimeoutError):
                        pass
                marker = self._next_id()
                self._send(s, marker, 0, "")
                if not self._scan(s, marker, max(0.01, deadline - time.monotonic())):
                    raise RconError("no answer to the end marker within %.0f s" % self.timeout)
            s.settimeout(self.timeout)
            self._done = True
            self.stats.recovered += 1
        except BaseException as again:
            self._done = True
            self._break("interrupted (%s) and the rest of its reply could not be read: %s"
                        % (why, _words(again)))

    def _scan(self, s: socket.socket, marker: int, wait: float) -> bool:
        """Reads raw bytes until the marker's answer appears (True), or `wait` seconds pass."""
        body = UNKNOWN_REQUEST_0 + b"\x00\x00"
        needle = struct.pack("<iii", 8 + len(body), marker, 0) + body
        end = time.monotonic() + wait
        while True:
            i = self._rbuf.find(needle)
            if i >= 0:
                del self._rbuf[:i + len(needle)]
                return True
            left = end - time.monotonic()
            if left <= 0:
                return False
            s.settimeout(left)
            try:
                self._recv(s)
            except (socket.timeout, TimeoutError):
                return False

    def close(self):
        with self._lock:
            self._closed = True
            s, self._sock = self._sock, None
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
            if self._flock is not None:
                self._flock.close()
                self._flock = None

    def reopen(self):
        """Allows connecting again after close() (McIo.open after a close)."""
        with self._lock:
            self._closed = False
            self._backoff, self._retry_at = 0.0, 0.0


def _words(e: BaseException) -> str:
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "no reply within the timeout"
    text = " ".join(str(e).split())
    return text or type(e).__name__
