"""The speech endpoint (contracts section 2): POST /v1/lines, and GET /v1/health.

The token is checked before the body is read, so ``{}`` with a good token is
400 and with a bad one 401.  One log line per request -- method, path, status,
line id, milliseconds -- and never a line's text or the token.
"""

import datetime as _dt
import hmac
import json
import logging
import math
import re
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import timeutil

log = logging.getLogger("vfg_bridge")

MAX_BODY = 16 * 1024
DRAIN_MAX = 64 * 1024
TEXT_MAX = 2000
NAME_RE = re.compile(r"[A-Za-z0-9_]{1,16}")
LENGTH_RE = re.compile(r"\d{1,12}")
GUARD_AHEAD = _dt.timedelta(seconds=30)
GUARD_BEHIND = _dt.timedelta(minutes=10)


class BadRequest(Exception):
    pass


def _time(body, key):
    if key not in body or body[key] is None:
        raise BadRequest("%s: is required" % key)
    try:
        return timeutil.parse_time(body[key])
    except ValueError:
        raise BadRequest("%s: must be an ISO-8601 time with a zone, like "
                         "2026-10-07T18:22:31.400Z" % key) from None


def _dbfs(body, key):
    value = body.get(key)
    if value is None:
        return None
    problem = BadRequest("%s: must be a finite number or null" % key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise problem
    try:
        value = float(value)
    except OverflowError:
        raise problem from None
    if not math.isfinite(value):
        raise problem
    return value


def validate(body):
    """The checked fields of a POST /v1/lines body; the first that fails raises BadRequest."""
    cid = body.get("client_line_id")
    if not isinstance(cid, str) or not 1 <= len(cid) <= 64:
        raise BadRequest("client_line_id: must be a string of 1-64 characters")
    uuid = body.get("player_uuid")
    if uuid is not None and (not isinstance(uuid, str) or len(uuid) > 64):
        raise BadRequest("player_uuid: must be a string of at most 64 characters, or null")
    if uuid is not None and not uuid.strip():
        uuid = None  # "no uuid" is NULL, as on the AI's own lines
    name = body.get("player_name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise BadRequest("player_name: must match ^[A-Za-z0-9_]{1,16}$")
    text = body.get("text")
    if not isinstance(text, str):
        raise BadRequest("text: must be a string")
    text = text.strip()
    if not 1 <= len(text) <= TEXT_MAX:
        raise BadRequest("text: must be 1-2000 characters after trimming")
    started = _time(body, "started_at")
    ended = _time(body, "ended_at")
    if ended < started:
        raise BadRequest("ended_at: must not be before started_at")
    return {
        "client_line_id": cid, "player_uuid": uuid, "player_name": name, "text": text,
        "started_at": started, "ended_at": ended,
        "peak_dbfs": _dbfs(body, "peak_dbfs"), "mean_dbfs": _dbfs(body, "mean_dbfs"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "vfg-bridge"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 15

    # -- plumbing ------------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 -- http.server's signature
        pass  # one line per request is written by _route, without addresses

    def log_error(self, format, *args):  # noqa: A002
        log.warning("http: %s", format % args)

    def _send(self, status, doc):
        body = (json.dumps(doc) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self._status = status

    def _drain(self):
        """Read and drop a body we answered without, so the client sees the answer, not a reset."""
        length = self.headers.get("Content-Length", "")
        if not LENGTH_RE.fullmatch(length.strip()):
            return
        left = min(int(length), DRAIN_MAX)
        try:
            self.connection.settimeout(2)
            while left > 0:
                chunk = self.rfile.read(min(left, 8192))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _route(self, method):
        self._start = time.monotonic()
        self._status = 0
        self._line_id = None
        path = urllib.parse.urlsplit(self.path).path
        try:
            if method == "GET" and path == "/v1/health":
                self._send(200, {"ok": True})
            elif method == "POST" and path == "/v1/lines":
                self._post_line()
            else:
                self._send(404, {"error": "not found"})
                self._drain()
        except Exception:
            log.exception("%s %s failed", method, path)
            if not self._status:
                try:
                    self._send(500, {"error": "internal"})
                except OSError:
                    pass
        finally:
            ms = (time.monotonic() - self._start) * 1000
            log.info("%s %s %d %s %dms", method, path, self._status, self._line_id or "-", ms)

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PUT(self):  # noqa: N802
        self._route("PUT")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")

    def do_PATCH(self):  # noqa: N802
        self._route("PATCH")

    # -- POST /v1/lines --------------------------------------------------------

    def _authorized(self):
        token = self.server.client_token
        got = self.headers.get("Authorization") or ""
        expected = "Bearer " + token
        return bool(token) and hmac.compare_digest(
            got.encode("utf-8", "surrogateescape"), expected.encode("utf-8", "surrogateescape"))

    def _post_line(self):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            self._drain()
            return
        length = (self.headers.get("Content-Length") or "").strip()
        if not LENGTH_RE.fullmatch(length):
            self._send(400, {"error": "body: needs a Content-Length"})
            return
        if int(length) > MAX_BODY:
            self._send(400, {"error": "body: larger than 16 KiB"})
            self._drain()
            return
        raw = self.rfile.read(int(length)) if int(length) else b""
        if len(raw) != int(length):
            self._send(400, {"error": "body: shorter than its Content-Length"})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send(400, {"error": "body: not valid JSON"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "body: must be a JSON object"})
            return
        try:
            line = validate(body)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        started, ended = self.server.clock_guard(line)
        try:
            line_id = self.server.db.insert_voice(
                timeutil.format_time(started), timeutil.format_time(ended),
                line["player_uuid"], line["player_name"], line["text"],
                line["peak_dbfs"], line["mean_dbfs"], line["client_line_id"])
        except (sqlite3.Error, OSError) as exc:  # locked past the busy timeout, a full disk
            log.error("storing a line failed: %s: %s", type(exc).__name__, exc)
            self._send(503, {"error": "busy"})
            return
        self._line_id = line_id
        self._send(202, {"id": line_id})


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db, client_token, clock=None):
        super().__init__(address, Handler)
        self.db = db
        self.client_token = client_token
        self.clock = clock or timeutil.utcnow
        self._warned = set()
        self._warned_lock = threading.Lock()

    def clock_guard(self, line):
        """Move a line whose player's clock is wrong so that it ended now."""
        started, ended = line["started_at"], line["ended_at"]
        now = self.clock()
        if ended - now > GUARD_AHEAD or now - ended > GUARD_BEHIND:
            shift = now - ended
            started, ended = started + shift, now
            key = line["player_name"].lower()
            with self._warned_lock:
                first = key not in self._warned
                self._warned.add(key)
            if first:
                log.warning("clock guard: %s's clock is off by %ds; their line times are moved"
                            " to the bridge's clock", line["player_name"],
                            int(shift.total_seconds()))
        return started, ended

    def handle_error(self, request, client_address):
        log.exception("http: an error escaped a request")


def make_server(bind, port, db, client_token, clock=None):
    return BridgeServer((bind, port), db, client_token, clock)
