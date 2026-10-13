"""A local stand-in for Jev's `POST /v1/systemone`, for the tests.

It answers every question by keyword in the line it asks about (the text
between the last “ and ” of its instructions; a gather question quotes the ask
first). The cast check's question (`cast`) is answered `cast_<phrase>` 0.9 when
the line, its letters run together, holds the phrase's (`hot fire ball` holds
`hotfireball`), else `not_a_cast` 0.9. The routing call's two questions answer so that the walk gives:
`build`, `make` or `there` -> to_ai 0.9 (by name 0.9); `thanks` -> to_ai 0.85
(answers it 0.85); `cave` or `base` -> context 0.7 (neither 0.7, remember);
anything else -> not_relevant 0.8 (neither 0.8, ignore). `keep` is always
answered with 0.6, so the confidence fed is always `to_the_ai`'s. A gather
question is answered `part_of_the_ask` 0.9 when the line contains `big one`,
else `not_part_of_it` 0.8, so no line joins an ask by accident. A `noul`
question is answered `noul: 0.9`. The state must be a JSON object, as the live
API is called (a bare string is refused with 400); `usage.input_tokens` is its
JSON-encoded length / 4.

    POST /mode   body `ok`, `fail` (500), `hang` (sleeps past the timeout) or `refuse` (400)
    POST /next   a JSON body sent back verbatim, with status 200, for the next call only
    GET  /calls  every /v1/systemone request so far: [{"headers": {...}, "body": {...}}]

In process: `server = serve()`, then `server.url`, `server.calls`, `server.mode`,
`server.shutdown()`. As a process: `python3 fake_jev.py` prints its port on the
first line of stdout and serves until killed.
"""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ASKED_ABOUT = re.compile(r"^.*“(.*)”", re.S)   # the last quote: a gather question quotes the ask first
JOINS = "big one"
WHO = {"to_ai": "says_its_name", "context": "neither", "not_relevant": "neither"}
KEEP = {"to_ai": "ignore_it", "context": "remember_it", "not_relevant": "ignore_it"}
KEEP_CONFIDENCE = 0.6


def asked_about(instructions):
    m = ASKED_ABOUT.search(instructions or "")
    return m.group(1) if m else ""


def verdict(text):
    t = text.lower()
    if any(w in t for w in ("build", "make", "there")):
        return "to_ai", 0.9
    if "thanks" in t:
        return "to_ai", 0.85
    if any(w in t for w in ("cave", "base")):
        return "context", 0.7
    return "not_relevant", 0.8


def choose(question, verdicts=None):
    """`(label, confidence)` for a `choice` question: a gather question's by
    `big one`, else the routing call's two by the line's class — `verdicts[text]`,
    a `(class, confidence)`, or else its keywords (`thanks` answers a question)."""
    text = asked_about(question.get("instructions"))
    labels = question.get("criteria") or {}
    if "not_a_cast" in labels:
        squashed = re.sub(r"[^a-z]", "", text.lower())
        for label in labels:
            if label.startswith("cast_") and label[len("cast_"):].replace("_", "") in squashed:
                return label, 0.9
        return "not_a_cast", 0.9
    if "part_of_the_ask" in labels:
        return ("part_of_the_ask", 0.9) if JOINS in text.lower() else ("not_part_of_it", 0.8)
    cls, conf = (verdicts or {}).get(text) or verdict(text)
    if "remember_it" in labels and "neither" not in labels:   # keep
        return KEEP[cls], KEEP_CONFIDENCE
    if cls == "to_ai" and "thanks" in text.lower():
        return "answers_its_question", conf
    return WHO[cls], conf


def answer(question):
    if question.get("type") == "noul":
        return {"type": "noul", "noul": 0.9}
    label, conf = choose(question)
    labels = list((question.get("criteria") or {}).keys()) or [label]
    rest = (1 - conf) / max(1, len(labels) - 1)
    probs = {name: (conf if name == label else rest) for name in labels}
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

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if self.path == "/calls":
            self._send(200, self.server.calls)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        raw = self._body()
        srv = self.server
        if self.path == "/mode":
            mode = raw.decode().strip()
            if mode not in ("ok", "fail", "hang", "refuse"):
                return self._send(400, {"error": "unknown mode"})
            srv.mode = mode
            return self._send(200, {"mode": mode})
        if self.path == "/next":
            srv.canned.append(raw)
            return self._send(200, {"queued": len(srv.canned)})
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
            return self._send(400, {"error": {"message": "bad request"}})
        if srv.mode == "hang":
            time.sleep(srv.hang_seconds)
            return self._send(500, {"error": "hung"})
        if canned is not None:
            return self._send(200, canned)
        if not isinstance(body, dict) or not isinstance(body.get("questions"), dict):
            return self._send(400, {"error": {"message": "no questions"}})
        if not isinstance(body.get("state"), dict):
            return self._send(400, {"error": {"message": "state must be an object"}})
        size = len(json.dumps(body["state"], ensure_ascii=False))
        answers = {name: answer(q) for name, q in body["questions"].items()}
        self._send(200, {"answers": answers, "usage": {"input_tokens": size // 4}})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # a client that gave up on a hung call is not news


def serve(port=0, hang_seconds=5.0):
    srv = Server(("127.0.0.1", port), Handler)
    srv.mode = "ok"
    srv.calls = []
    srv.canned = []
    srv.lock = threading.Lock()
    srv.hang_seconds = hang_seconds
    srv.url = "http://127.0.0.1:%d/v1/systemone" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, args=(0.005,), daemon=True).start()   # a quick shutdown()
    return srv


if __name__ == "__main__":
    s = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(s.server_address[1], flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
