"""A local stand-in for Jev's `POST /v1/systemone`, for the router's tests.

Built like jev/tests/fake_jev.py, but it answers the router's questions, about
the state's job, task and context: a question about the card named "x_y" (its
instructions quote the name) is answered with its first label (runs_it, use)
at 0.9 when they hold "x_y" or "x y"; with close at 0.6 (a script) or use at
0.6 (a preset) when they hold one of the name's words split at `_`; otherwise
`no` at 0.9. Every answer carries `probabilities` for every label. `usage.input_tokens` is the request
body's JSON length / 4.

In process: `server = serve()`, then `server.url`, `server.calls` (each
request's headers and body), `server.mode` ("ok", "fail" 500, "hang" sleeps
`hang_seconds`, "refuse" 400 with a body that quotes the key), `server.canned`
(bodies sent back verbatim, one per call), `server.shutdown()`.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = re.compile(r'(?:Example script|Preset) "([^"]+)"')


def answer(question, text):
    labels = list((question.get("criteria") or {}).keys())
    m = NAME.search(question.get("instructions") or "")
    name = m.group(1) if m else ""
    text = text.lower()
    if name and (name in text or name.replace("_", " ") in text):
        label, conf = labels[0], 0.9
    elif name and any(len(w) > 2 and w in text for w in name.split("_")):
        label, conf = ("close", 0.6) if "close" in labels else (labels[0], 0.6)
    else:
        label, conf = "no", 0.9
    rest = (1 - conf) / max(1, len(labels) - 1)
    probs = {l: (conf if l == label else rest) for l in labels}
    return {"type": "choice", "choice": label, "confidence": conf, "probabilities": probs}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        srv = self.server
        if self.path != "/v1/systemone":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        with srv.lock:
            srv.calls.append({"headers": {k: v for k, v in self.headers.items()}, "body": body})
            canned = srv.canned.pop(0) if srv.canned else None
        if srv.mode == "fail":
            return self._send(500, {"error": {"message": "internal error"}})
        if srv.mode == "refuse":
            key = (self.headers.get("Authorization") or "")[len("Bearer "):]
            return self._send(400, {"error": {"message": "bad request for key " + key}})
        if srv.mode == "hang":
            time.sleep(srv.hang_seconds)
            return self._send(500, {"error": "hung"})
        if canned is not None:
            return self._send(200, canned)
        if not isinstance(body, dict) or not isinstance(body.get("questions"), dict) \
                or not isinstance(body.get("state"), dict):
            return self._send(400, {"error": {"message": "no questions or no state"}})
        text = " ".join(str(body["state"].get(k, "")) for k in ("job", "task", "context"))
        answers = {name: answer(q, text) for name, q in body["questions"].items()}
        self._send(200, {"answers": answers, "usage": {"input_tokens": len(raw) // 4}})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def serve(hang_seconds=5.0):
    srv = Server(("127.0.0.1", 0), Handler)
    srv.mode = "ok"
    srv.calls = []
    srv.canned = []
    srv.lock = threading.Lock()
    srv.hang_seconds = hang_seconds
    srv.url = "http://127.0.0.1:%d/v1/systemone" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return srv
