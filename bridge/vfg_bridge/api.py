"""The speech endpoint (contracts section 2): POST /v1/lines, and GET /v1/health; and the
incantations': POST /v1/casts and GET /v1/incantations (casts.py), and the Jev filter's
POST /v1/jev/casts, taken only from the box itself.

The token is checked before the body is read, so ``{}`` with a good token is
400 and with a bad one 401.  One log line per request -- method, path, status,
line or cast id, milliseconds to the answer -- and never a line's text or the token.
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
from .casts import NOTHING, Unavailable

log = logging.getLogger("vfg_bridge")

MAX_BODY = 16 * 1024
DRAIN_MAX = 64 * 1024
TEXT_MAX = 2000
PHRASE_MAX = 200
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


def _short(body, key, most, required=True):
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > most:
        raise BadRequest("%s: must be a string of 1-%d characters%s"
                         % (key, most, "" if required else ", or null"))
    return value.strip()


def validate_cast(body):
    """The checked fields of a POST /v1/casts body; the first that fails raises BadRequest."""
    cid = body.get("client_cast_id")
    if not isinstance(cid, str) or not 1 <= len(cid) <= 64:
        raise BadRequest("client_cast_id: must be a string of 1-64 characters")
    uuid = body.get("player_uuid")
    if uuid is not None and (not isinstance(uuid, str) or len(uuid) > 64):
        raise BadRequest("player_uuid: must be a string of at most 64 characters, or null")
    name = body.get("player_name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise BadRequest("player_name: must match ^[A-Za-z0-9_]{1,16}$")
    phrase = _short(body, "phrase", PHRASE_MAX)
    heard = _short(body, "heard", PHRASE_MAX, required=False)
    started = _time(body, "started_at")
    ended = _time(body, "ended_at")
    if ended < started:
        raise BadRequest("ended_at: must not be before started_at")
    return {"client_cast_id": cid, "player_name": name, "phrase": phrase, "heard": heard,
            "started_at": started, "ended_at": ended}


def validate_jev_cast(body):
    """The checked fields of a POST /v1/jev/casts body; the first that fails raises BadRequest."""
    line_id = body.get("line_id")
    if not isinstance(line_id, str) or not 1 <= len(line_id) <= 64:
        raise BadRequest("line_id: must be a string of 1-64 characters")
    keep = body.get("keep", False)
    if not isinstance(keep, bool):
        raise BadRequest("keep: must be true or false")
    return {"line_id": line_id, "phrase": _short(body, "phrase", PHRASE_MAX), "keep": keep}


LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _query(query):
    """player, after and wait of a GET /v1/incantations query; BadRequest otherwise."""
    params = urllib.parse.parse_qs(query, keep_blank_values=True)

    def one(key):
        got = params.get(key)
        if got and len(got) > 1:
            raise BadRequest("%s: given more than once" % key)
        return got[0] if got else None

    player = one("player")
    if player is None or not NAME_RE.fullmatch(player):
        raise BadRequest("player: must match ^[A-Za-z0-9_]{1,16}$")
    after = one("after")
    if after is not None:
        if not re.fullmatch(r"\d{1,15}", after):
            raise BadRequest("after: must be a version, a whole number")
        after = int(after)
    wait = one("wait")
    if wait is not None:
        try:
            wait = float(wait)
        except ValueError:
            wait = -1.0
        if not 0 <= wait < float("inf"):
            raise BadRequest("wait: must be a number of seconds, 0 or more")
    return player, after, wait


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
        body = b"" if doc is None else (json.dumps(doc) + "\n").encode("utf-8")
        self.send_response(status)
        if doc is not None:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self._status = status
        self._answered = time.monotonic()

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
        self._answered = None
        self._status = 0
        self._line_id = None
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        try:
            if method == "GET" and path == "/v1/health":
                self._send(200, {"ok": True})
            elif method == "POST" and path == "/v1/lines":
                self._post_line()
            elif method == "POST" and path == "/v1/casts":
                self._post_cast()
            elif method == "POST" and path == "/v1/jev/casts":
                self._post_jev_cast()
            elif method == "GET" and path == "/v1/incantations":
                self._get_incantations(parts.query)
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
            ms = ((self._answered or time.monotonic()) - self._start) * 1000
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

    def _body(self):
        """The token, then the JSON object posted; None once it has answered 401 or 400."""
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            self._drain()
            return None
        length = (self.headers.get("Content-Length") or "").strip()
        if not LENGTH_RE.fullmatch(length):
            self._send(400, {"error": "body: needs a Content-Length"})
            return None
        if int(length) > MAX_BODY:
            self._send(400, {"error": "body: larger than 16 KiB"})
            self._drain()
            return None
        raw = self.rfile.read(int(length)) if int(length) else b""
        if len(raw) != int(length):
            self._send(400, {"error": "body: shorter than its Content-Length"})
            return None
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send(400, {"error": "body: not valid JSON"})
            return None
        if not isinstance(body, dict):
            self._send(400, {"error": "body: must be a JSON object"})
            return None
        return body

    def _post_line(self):
        body = self._body()
        if body is None:
            return
        try:
            line = validate(body)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        started, ended = self.server.clock_guard(line)
        casts = self.server.casts
        made = []  # the casts Layer 2 made: each is announced, whatever becomes of the line
        try:
            found = NOTHING
            if casts is not None:
                try:
                    found = casts.on_line(line["player_name"], line["text"], started, ended,
                                          line["client_line_id"], arrived=self._start, made=made)
                except Exception:  # never lose a line over an incantation
                    log.exception("a line's casts failed; it is stored as it is")
            try:
                line_id = self.server.db.insert_voice(
                    timeutil.format_time(started), timeutil.format_time(ended),
                    line["player_uuid"], line["player_name"], line["text"],
                    line["peak_dbfs"], line["mean_dbfs"], line["client_line_id"],
                    dropped=found.dropped, cast_id=found.cast_id, cast_ids=found.cast_ids)
            except (sqlite3.Error, OSError) as exc:  # locked past the busy timeout, a full disk
                log.error("storing a line failed: %s: %s", type(exc).__name__, exc)
                self._send(503, {"error": "busy"})
                return
            self._line_id = line_id
            self._send(202, {"id": line_id})
        finally:
            for cast in made:
                casts.announce(cast)

    # -- POST /v1/casts and GET /v1/incantations ---------------------------------

    def _post_cast(self):
        body = self._body()
        if body is None:
            return
        try:
            cast = validate_cast(body)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        started, ended = self.server.clock_guard(cast)
        casts = self.server.casts
        if casts is None:
            self._send(503, {"error": "incantations are not set up on this bridge"})
            return
        try:
            status, doc, after = casts.cast(cast["player_name"], cast["phrase"], cast["heard"],
                                            started, ended, cast["client_cast_id"],
                                            arrived=self._start)
        except Unavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        except (sqlite3.Error, OSError) as exc:
            log.error("a cast failed: %s: %s", type(exc).__name__, exc)
            self._send(503, {"error": "busy"})
            return
        self._line_id = doc.get("id")
        self._send(status, doc)
        if after is not None:
            after()  # the event, the feed line, and a line it drops: off the hot path

    def _post_jev_cast(self):
        """The Jev filter's cast of a stored line (casts.Casts.jev_cast): from the box itself
        only, with the client token."""
        if self.client_address[0] not in LOOPBACK:
            self._send(403, {"error": "only from the box itself"})
            self._drain()
            return
        body = self._body()
        if body is None:
            return
        try:
            req = validate_jev_cast(body)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        casts = self.server.casts
        if casts is None:
            self._send(503, {"error": "incantations are not set up on this bridge"})
            return
        try:
            status, doc, after = casts.jev_cast(req["line_id"], req["phrase"], arrived=self._start,
                                                keep=req["keep"])
        except Unavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        except (sqlite3.Error, OSError) as exc:
            log.error("a Jev cast failed: %s: %s", type(exc).__name__, exc)
            self._send(503, {"error": "busy"})
            return
        self._line_id = doc.get("id") or req["line_id"]
        self._send(status, doc)
        if after is not None:
            after()  # the event and the feed line: off the hot path

    def _get_incantations(self, query):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            player, after, wait = _query(query)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        casts = self.server.casts
        if casts is None:
            self._send(503, {"error": "incantations are not set up on this bridge"})
            return
        try:
            status, doc = casts.listing(player, after, wait)
        except Unavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        self._send(status, doc)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db, client_token, clock=None, casts=None):
        super().__init__(address, Handler)
        self.db = db
        self.client_token = client_token
        self.clock = clock or timeutil.utcnow
        self.casts = casts  # None: no incantations, casts and the list answer 503
        self._shifts = {}  # player, lowercased: the shift their clock last needed
        self._shifts_lock = threading.Lock()

    def clock_guard(self, line):
        """Move a line (or a cast) whose player's clock is wrong onto the bridge's.

        The first one found off is moved so that it ended now, and that shift is kept for the
        player's later lines and casts while it brings them within bounds, so a cast and the
        line it was said in stay on one clock. It is found again only when it no longer does."""
        started, ended = line["started_at"], line["ended_at"]
        now = self.clock()

        def off(end):
            return end - now > GUARD_AHEAD or now - end > GUARD_BEHIND

        key = line["player_name"].lower()
        found = False
        with self._shifts_lock:
            shift = self._shifts.get(key)
            if shift is not None and off(ended + shift):
                del self._shifts[key]  # their clock moved again, or was put right
                shift = None
            if shift is None and off(ended):
                shift = self._shifts[key] = now - ended
                found = True
        if shift is not None:
            started, ended = started + shift, ended + shift
            if found:
                log.warning("clock guard: %s's clock is off by %ds; their line times are moved"
                            " to the bridge's clock", line["player_name"],
                            int(shift.total_seconds()))
        return started, ended

    def handle_error(self, request, client_address):
        log.exception("http: an error escaped a request")


def make_server(bind, port, db, client_token, clock=None, casts=None):
    return BridgeServer((bind, port), db, client_token, clock, casts)
