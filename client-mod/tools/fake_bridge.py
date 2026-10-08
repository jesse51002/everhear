#!/usr/bin/env python3
"""A stand-in for the bridge's POST /v1/lines (docs/CONTRACTS.md §2), Python 3 stdlib only.

    VFG_CLIENT_TOKEN=test python3 tools/fake_bridge.py --port 8787 [--fail N] [--status CODE] [--delay S]

401 for a wrong token, checked first; 400 with the reason for a body that breaks section 2;
otherwise 202 {"id": "<26-character ULID>"}, the same id again for a repeated client_line_id.
One line is printed per accepted request: time, player, peak and mean, text.
--fail N answers 500 to the first N requests, --status forces a code, --delay waits before answering.
"""

import argparse
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS = ("client_line_id", "player_uuid", "player_name", "text",
        "started_at", "ended_at", "peak_dbfs", "mean_dbfs")
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


class State:
    def __init__(self, token, fail, status, delay):
        self.token = token
        self.fail = fail
        self.status = status
        self.delay = delay
        self.requests = 0
        self.ids = {}
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

    def do_POST(self):
        st = self.state
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
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

    def do_GET(self):
        self.reply(404, {"error": "only POST /v1/lines"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stand-in for the bridge's POST /v1/lines.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--fail", type=int, default=0, help="answer 500 to the first N requests")
    parser.add_argument("--status", type=int, default=0, help="answer every request with this code")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait before answering")
    args = parser.parse_args(argv)
    token = os.environ.get("VFG_CLIENT_TOKEN", "")
    if not token:
        print("set VFG_CLIENT_TOKEN", file=sys.stderr)
        return 2
    Handler.state = State(token, args.fail, args.status, args.delay)
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
