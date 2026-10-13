"""The engine's control socket (the game engine spec, THE CONTROL OPS): a Unix socket, by default
/opt/vfg/engine/engine.sock, mode 600, through which the bridge, the toolkit's remote program and
``python -m vfg_engine.ctl`` hand requests to the running engine.

One connection carries one request. In: one line of JSON, at most 1 MiB,

    {"op": "give", "args": {"player": "Steve", "item": "fire_staff"}, "timeout_s": 10}

``op`` is required, ``args`` an object (none is ``{}``), ``timeout_s`` how long to wait for the
engine (by default 10 s, ``reload`` 120 s; at most 600). Out: one line of JSON, then the server
closes the connection. It is the engine's reply, ``{"ok": true, ...}`` or ``{"ok": false,
"error": "..."}``, or one the socket made itself, which also says ``reason``:

- ``bad_request``: the line was not a request (not JSON, no op, args not an object, too long);
- ``timeout``: the engine did not answer in time. ``cancelled`` says whether the request was
  taken off the engine's queue before it began (so it is never carried out); when false it may
  still be;
- ``stopping``: the engine is stopping, and the request was not carried out;
- ``failed``: the engine raised instead of answering, or answered with no reply; ``error`` says what.

A reply always comes back, whatever went wrong. The request goes through ``Engine.submit(op,
args)`` and is handled on the engine's next tick, in order with its other inputs; this thread only
waits for it. Standard library only, and nothing of the engine's is imported: the server is handed
the engine, so the tests run it on a fake.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import stat
import threading
import time

log = logging.getLogger("vfg_engine.server")

DEFAULT_SOCKET = "/opt/vfg/engine/engine.sock"
MAX_REQUEST = 1 << 20
READ_TIMEOUT_S = 10.0        # how long a connection may take to send its line
DEFAULT_TIMEOUT_S = 10.0
OP_TIMEOUT_S = {"reload": 120.0}   # every check of every class, and each live instance's state
MIN_TIMEOUT_S = 0.01
MAX_TIMEOUT_S = 600.0
WAIT_SLICE_S = 0.05          # how often a waiting request looks whether the server is closing
MAX_SOCKET_PATH = 107        # sun_path is 108 bytes, its last a NUL
ERROR_MAX = 500


class SetupError(Exception):
    """The socket cannot be made where it was asked for; the message says what to change."""


class BadRequest(Exception):
    pass


def one_line(exc) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:ERROR_MAX]


def default_timeout(op: str) -> float:
    return OP_TIMEOUT_S.get(op, DEFAULT_TIMEOUT_S)


def refusal(error: str, reason: str, **more) -> dict:
    """A reply the socket makes itself."""
    return dict({"ok": False, "error": error, "reason": reason}, **more)


def parse(line: bytes) -> tuple[str, dict, float]:
    """(op, args, timeout_s) of a request line; BadRequest says what is wrong with it."""
    if len(line) > MAX_REQUEST:
        raise BadRequest("the request is longer than %d bytes" % MAX_REQUEST)
    if not line.strip():
        raise BadRequest("no request: send one line of JSON, {\"op\": ..., \"args\": {...}}")
    try:
        req = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BadRequest("the request is not JSON: %s" % one_line(exc)) from None
    if not isinstance(req, dict):
        raise BadRequest("the request must be a JSON object, {\"op\": ..., \"args\": {...}}")
    op = req.get("op")
    if not isinstance(op, str) or not op.strip():
        raise BadRequest("op: must be the name of a control op, such as \"status\"")
    args = req.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise BadRequest("args: must be a JSON object")
    timeout = req.get("timeout_s")
    if timeout is None:
        timeout = default_timeout(op)
    elif isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout != timeout:
        raise BadRequest("timeout_s: must be a number of seconds")
    timeout = min(max(float(timeout), MIN_TIMEOUT_S), MAX_TIMEOUT_S)
    return op.strip(), args, timeout


def encode(reply: dict) -> bytes:
    """One reply line. A value JSON cannot hold goes as its text, so the reply still goes."""
    return (json.dumps(reply, default=str, separators=(",", ":")) + "\n").encode("utf-8")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(READ_TIMEOUT_S)
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
        except (socket.timeout, OSError) as exc:
            reply = refusal("no request line came within %g s (%s)" % (READ_TIMEOUT_S, one_line(exc)),
                            "bad_request")
        else:
            reply = self.server.answer(line)
        try:
            self.connection.settimeout(READ_TIMEOUT_S)
            self.wfile.write(encode(reply))
            self.wfile.flush()
        except OSError as exc:  # the client gave up: nothing to tell it
            self.server.log.info("a control reply could not be sent: %s", one_line(exc))


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The socket, handing each request to ``engine.submit``. ``bind()`` (called by the
    constructor) makes it mode 600, replacing a stale one; ``start()`` serves on a thread;
    ``close()`` answers every request still waiting with ``stopping`` and removes the socket."""

    daemon_threads = True
    block_on_close = False

    def __init__(self, path: str, engine, logger: logging.Logger | None = None):
        self.path = path
        self.engine = engine
        self.log = logger or log
        self.closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._inode = None
        if len(os.fsencode(path)) > MAX_SOCKET_PATH:
            raise SetupError("the control socket's path %s is %d bytes; a Unix socket's is at most %d:"
                             " set VFG_ENGINE_SOCKET to a shorter one"
                             % (path, len(os.fsencode(path)), MAX_SOCKET_PATH))
        super().__init__(path, Handler, bind_and_activate=False)
        try:
            self.server_bind()
            self.server_activate()
        except SetupError:
            self.socket.close()
            raise
        except OSError as exc:
            self.socket.close()
            raise SetupError("the control socket %s cannot be made: %s" % (path, one_line(exc))) from None

    def server_bind(self):
        """Bind with no moment at which anyone else can connect: the file is made under umask 177,
        so it is 600 from the start. A file already there is replaced only when it is a socket; the
        caller holds the engine's lock, so no other engine is listening on it."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode):
                raise SetupError("%s is there and is not a socket: move it, or set VFG_ENGINE_SOCKET"
                                 % self.path)
            os.unlink(self.path)
        old = os.umask(0o177)
        try:
            self.socket.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self._inode = os.stat(self.path).st_ino
        self.server_address = self.path

    def start(self, on_fail=None) -> None:
        """Serve on a thread. ``on_fail()`` is called if the serving loop itself dies (a request's
        error never kills it), so the process can stop rather than run on unreachable."""
        def serve():
            try:
                self.serve_forever(0.1)
            except Exception:
                self.log.exception("the control socket stopped serving")
                if on_fail is not None:
                    on_fail()

        self._thread = threading.Thread(target=serve, name="control", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop taking connections, answer what still waits (``stopping``), remove the socket file
        if it is still this server's."""
        self.closing.set()
        if self._thread is not None:
            self.shutdown()
            self._thread.join(5)
        self.server_close()
        try:
            if self._inode is not None and os.stat(self.path).st_ino == self._inode:
                os.unlink(self.path)
        except OSError:
            pass

    def handle_error(self, request, client_address):
        self.log.exception("the control socket: an error escaped a request")

    # -- one request ---------------------------------------------------------------

    def answer(self, line: bytes) -> dict:
        """The reply to one request line: the engine's, or the socket's own (``reason``)."""
        start = time.monotonic()
        try:
            op, args, timeout = parse(line)
        except BadRequest as exc:
            self.log.info("control request refused: %s", exc, extra={"reason": "bad_request"})
            return refusal(str(exc), "bad_request")
        reply = self._ask(op, args, timeout, start)
        ms = round((time.monotonic() - start) * 1000, 1)
        fields = {"op": op, "ok": reply.get("ok"), "ms": ms}
        if not reply.get("ok"):
            fields["error"] = str(reply.get("error"))[:ERROR_MAX]
            if reply.get("reason"):
                fields["reason"] = reply["reason"]
        self.log.info("control %s: %s in %g ms", op, "ok" if reply.get("ok") else "refused", ms,
                      extra=fields)
        return reply

    def _ask(self, op: str, args: dict, timeout: float, start: float) -> dict:
        if self.closing.is_set():
            return refusal("the engine is stopping; %s was not carried out" % op, "stopping")
        try:
            future = self.engine.submit(op, args)
        except Exception as exc:
            self.log.exception("the engine would not take control op %s", op)
            return refusal("the engine would not take %s: %s" % (op, one_line(exc)), "failed")
        done = threading.Event()
        future.add_done_callback(lambda f: done.set())
        deadline = start + timeout
        # CONTRACT: the core takes a request off its queue with set_running_or_notify_cancel(),
        # skipping one whose Future was cancelled; so a request cancelled here, before the engine
        # began it, is never carried out ("cancelled": true).
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                cancelled = future.cancel()
                return refusal("the engine did not answer %s within %g s; %s" % (
                    op, timeout, "it was taken off the queue and will not be carried out" if cancelled
                    else "it may still be carried out"), "timeout", cancelled=cancelled)
            if done.wait(min(WAIT_SLICE_S, left)):
                break
            if self.closing.is_set():
                cancelled = future.cancel()
                return refusal("the engine stopped before it answered %s; %s" % (
                    op, "it was not carried out" if cancelled else "it may have been carried out"),
                    "stopping", cancelled=cancelled)
        if future.cancelled():
            return refusal("the engine dropped %s without answering it" % op, "stopping", cancelled=True)
        try:
            reply = future.result(0)
        except Exception as exc:
            self.log.error("the engine failed on control op %s: %s", op, one_line(exc), exc_info=exc)
            return refusal("the engine failed on %s: %s" % (op, one_line(exc)), "failed")
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            self.log.error("the engine answered control op %s with %r, not a reply", op, reply)
            return refusal("the engine answered %s with no reply (%s)" % (op, type(reply).__name__), "failed")
        return reply
