#!/usr/bin/env python3
"""A stand-in for the bridge's POST /v1/lines (docs/CONTRACTS.md §2), and its incantation invoke
(POST /v1/casts) and incantation list (GET /v1/incantations), Python 3 stdlib only.

    VFG_CLIENT_TOKEN=test python3 tools/fake_bridge.py --port 8787 [--fail N] [--status CODE] [--delay S]
        [--incantation "fire ball=fireball,fire bowl" ...]

401 for a wrong token, checked first; 400 with the reason for a body that breaks section 2;
otherwise 202 {"id": "<26-character ULID>"}, the same id again for a repeated client_line_id.
One line is printed per accepted request: time, player, peak and mean, text.
--fail N answers 500 to the first N requests, --status forces a code, --delay waits before answering.

--incantation binds a phrase, with its aliases after "=", for every player. A cast of a bound
phrase is answered 202 {"id", "status": "fired", "copy"}, one not bound 404; a repeated
client_cast_id gets the first answer. The list is version 1, every player's the same; asked with
after=1 it is held for wait seconds (at most 30), then answered 304. Its server_phrases are every bound
phrase and each --server-phrase (another player's, say), deduplicated; --no-server-phrases leaves the
field out, as a bridge before 0.3.5 did.
"""

import argparse
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS = ("client_line_id", "player_uuid", "player_name", "text",
        "started_at", "ended_at", "peak_dbfs", "mean_dbfs")
CAST_KEYS = ("client_cast_id", "player_uuid", "player_name", "phrase", "heard", "started_at", "ended_at")
CAST_REQUIRED = ("client_cast_id", "player_name", "phrase", "started_at", "ended_at")
NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid():
    """A 26-character ULID: 48 bits of milliseconds, then 80 random bits, Crockford base32."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def parse_timestamp(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def check_body(body):
    """Returns None when the body is a valid section 2 line, else the reason it is not."""
    if not isinstance(body, dict):
        return "body is not a JSON object"
    missing = [k for k in KEYS if k not in body]
    if missing:
        return "missing key(s): " + ", ".join(missing)
    extra = sorted(k for k in body if k not in KEYS)
    if extra:
        return "extra key(s): " + ", ".join(extra)
    for key in ("client_line_id", "player_uuid", "player_name", "text", "started_at", "ended_at"):
        if not isinstance(body[key], str):
            return key + " is not a string"
    for key in ("peak_dbfs", "mean_dbfs"):
        if isinstance(body[key], bool) or not isinstance(body[key], (int, float)):
            return key + " is not a number"
        if body[key] > 0:
            return key + " is above 0"
    try:
        line_id = uuid.UUID(body["client_line_id"])
    except ValueError:
        return "client_line_id is not a UUID"
    if line_id.version != 4 or str(line_id) != body["client_line_id"].lower():
        return "client_line_id is not a version-4 UUID"
    text = body["text"].strip()
    if not 1 <= len(text) <= 2000:
        return "text is %d characters after trimming, not 1-2000" % len(text)
    for key in ("started_at", "ended_at"):
        if not TIMESTAMP.match(body[key]):
            return key + " is not shaped YYYY-MM-DDTHH:MM:SS.mmmZ"
    try:
        started = parse_timestamp(body["started_at"])
        ended = parse_timestamp(body["ended_at"])
    except ValueError:
        return "a timestamp is not a real date and time"
    if ended < started:
        return "ended_at is before started_at"
    return None


def check_cast(body):
    """Returns None when the body is a valid POST /v1/casts body, else the reason it is not."""
    if not isinstance(body, dict):
        return "body is not a JSON object"
    missing = [k for k in CAST_REQUIRED if k not in body]
    if missing:
        return "missing key(s): " + ", ".join(missing)
    extra = sorted(k for k in body if k not in CAST_KEYS)
    if extra:
        return "extra key(s): " + ", ".join(extra)
    try:
        cast_id = uuid.UUID(body["client_cast_id"])
    except (ValueError, TypeError, AttributeError):
        return "client_cast_id is not a UUID"
    if cast_id.version != 4 or str(cast_id) != body["client_cast_id"].lower():
        return "client_cast_id is not a version-4 UUID"
    if body.get("player_uuid") is not None and not isinstance(body["player_uuid"], str):
        return "player_uuid is not a string"
    if not isinstance(body["player_name"], str) or not NAME.match(body["player_name"]):
        return "player_name is not 1-16 letters, digits or _"
    for key in ("phrase", "heard"):
        if key in body and (not isinstance(body[key], str) or not body[key].strip()):
            return key + " is not a non-empty string"
    for key in ("started_at", "ended_at"):
        if not isinstance(body[key], str) or not TIMESTAMP.match(body[key]):
            return key + " is not shaped YYYY-MM-DDTHH:MM:SS.mmmZ"
    if parse_timestamp(body["ended_at"]) < parse_timestamp(body["started_at"]):
        return "ended_at is before started_at"
    return None


def parse_incantation(value):
    """ "fire ball=fireball,fire bowl" -> {"phrase", "aliases", "cooldown_s"} """
    phrase, _, aliases = value.partition("=")
    return {"phrase": phrase.strip(), "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
            "cooldown_s": 3.0}


class State:
    def __init__(self, token, fail, status, delay, incantations=()):
        self.token = token
        self.fail = fail
        self.status = status
        self.delay = delay
        self.incantations = list(incantations)
        # every phrase on the server; None leaves the field out (a bridge before 0.3.5)
        self.server_phrases = list(dict.fromkeys(i["phrase"] for i in self.incantations))
        self.requests = 0
        self.ids = {}
        self.casts = {}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-vfg-bridge/1"
    state = None  # set in main

    def log_message(self, fmt, *args):  # quiet: one line per accepted request is printed instead
        pass

    def reply(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def counted(self):
        """The request's number, after --delay; None when --fail or --status answered it."""
        st = self.state
        with st.lock:
            st.requests += 1
            n = st.requests
        if st.delay > 0:
            time.sleep(st.delay)
        if n <= st.fail:
            self.reply(500, {"error": "failing on purpose (%d of %d)" % (n, st.fail)})
            return None
        if st.status:
            self.reply(st.status, {"error": "status forced by --status"})
            return None
        return n

    def do_POST(self):
        st = self.state
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if self.path == "/v1/casts":
            self.post_cast(raw)
            return
        with st.lock:
            st.requests += 1
            n = st.requests
        if st.delay > 0:
            time.sleep(st.delay)
        if self.path != "/v1/lines":
            self.reply(404, {"error": "not found"})
            return
        if n <= st.fail:
            self.reply(500, {"error": "failing on purpose (%d of %d)" % (n, st.fail)})
            return
        if st.status:
            self.reply(st.status, {"error": "status forced by --status"})
            return
        if self.headers.get("Authorization", "") != "Bearer " + st.token:
            self.reply(401, {"error": "bad token"})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.reply(400, {"error": "body is not JSON"})
            return
        reason = check_body(body)
        if reason:
            self.reply(400, {"error": reason})
            return
        with st.lock:
            line_id = st.ids.get(body["client_line_id"])
            repeated = line_id is not None
            if not repeated:
                line_id = ulid()
                st.ids[body["client_line_id"]] = line_id
        if not repeated:
            print("%s %s peak %.1f mean %.1f | %s" % (
                datetime.now(timezone.utc).strftime("%H:%M:%S"), body["player_name"],
                body["peak_dbfs"], body["mean_dbfs"], body["text"]), flush=True)
        self.reply(202, {"id": line_id})

    def post_cast(self, raw):
        st = self.state
        if self.counted() is None:
            return
        if self.headers.get("Authorization", "") != "Bearer " + st.token:
            self.reply(401, {"error": "bad token"})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.reply(400, {"error": "body is not JSON"})
            return
        reason = check_cast(body)
        if reason:
            self.reply(400, {"error": reason})
            return
        with st.lock:
            answer = st.casts.get(body["client_cast_id"])
            repeated = answer is not None
            if not repeated:
                bound = any(i["phrase"] == body["phrase"] for i in st.incantations)
                answer = ((202, {"id": ulid(), "status": "fired", "copy": "fake_%d" % (len(st.casts) + 1)})
                          if bound else (404, {"error": "no incantation \"%s\" for %s"
                                               % (body["phrase"], body["player_name"])}))
                st.casts[body["client_cast_id"]] = answer
        if not repeated:
            print("%s %s cast \"%s\" (heard \"%s\") %s -> %s" % (
                datetime.now(timezone.utc).strftime("%H:%M:%S"), body["player_name"], body["phrase"],
                body.get("heard"), body["started_at"], answer[1].get("status", answer[0])), flush=True)
        self.reply(*answer)

    def do_GET(self):
        st = self.state
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != "/v1/incantations":
            self.reply(404, {"error": "only POST /v1/lines and /v1/casts, GET /v1/incantations"})
            return
        if self.counted() is None:
            return
        if self.headers.get("Authorization", "") != "Bearer " + st.token:
            self.reply(401, {"error": "bad token"})
            return
        query = urllib.parse.parse_qs(parts.query)
        player = (query.get("player") or [""])[0]
        if not NAME.match(player):
            self.reply(400, {"error": "player: must match ^[A-Za-z0-9_]{1,16}$"})
            return
        after = (query.get("after") or [None])[0]
        if after == "1":
            time.sleep(min(float((query.get("wait") or ["25"])[0]), 30.0))
            self.reply_empty(304)
            return
        answer = {"version": 1, "player": player, "incantations": st.incantations}
        if st.server_phrases is not None:
            answer["server_phrases"] = st.server_phrases
        self.reply(200, answer)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stand-in for the bridge's POST /v1/lines.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--fail", type=int, default=0, help="answer 500 to the first N requests")
    parser.add_argument("--status", type=int, default=0, help="answer every request with this code")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait before answering")
    parser.add_argument("--incantation", action="append", default=[], metavar="PHRASE[=ALIAS,...]",
                        help="bind a phrase (and its aliases) for every player; repeatable")
    parser.add_argument("--server-phrase", action="append", default=[], metavar="PHRASE",
                        help="another phrase on the server, listed in server_phrases only; repeatable")
    parser.add_argument("--no-server-phrases", action="store_true",
                        help="leave server_phrases out of GET /v1/incantations, as an older bridge does")
    args = parser.parse_args(argv)
    token = os.environ.get("VFG_CLIENT_TOKEN", "")
    if not token:
        print("set VFG_CLIENT_TOKEN", file=sys.stderr)
        return 2
    Handler.state = State(token, args.fail, args.status, args.delay,
                          [parse_incantation(i) for i in args.incantation])
    if args.no_server_phrases:
        Handler.state.server_phrases = None
    else:
        phrases = [i["phrase"] for i in Handler.state.incantations] + [p.strip() for p in args.server_phrase]
        Handler.state.server_phrases = list(dict.fromkeys(p for p in phrases if p))
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("fake bridge on http://%s:%d/v1/lines" % (args.host, httpd.server_address[1]), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
