#!/usr/bin/env python3
"""A stand-in model for the implementers: an Anthropic Messages API on 127.0.0.1 that answers every
request with a report, after a delay, and records what it was sent. No model, no key, no money.

    from stand_in_model import StandIn
    model = StandIn(record="requests.jsonl").start()       # model.url: http://127.0.0.1:<port>
    model.rule("torch_ring", delay=[20, 3], report="Done: …")   # a task's answers (its name in the brief)
    # the brain's environment: VFG_IMPL_PROVIDER=deepseek, DEEPSEEK_API_KEY=stand-in,
    #                          VFG_IMPL_BASE_URL=model.url
    model.requests("torch_ring")                            # what its implementer was sent, in order
    model.stop()

    python3 integration/stand_in_model.py [--port N]        # alone, to try it with the Claude CLI

Why: the live tests (integration/brain_live.py) check what the brain does around an implementer —
the task opened with its brief, the implementer started, a follow-up reaching it (a message into
its live run, or its session resumed), its report reaching the conversation agent — not what the
implementer builds, which its own tests check (impl_*.py). The real runner, the real relay and the
real Claude Code CLI run unchanged on the `deepseek` provider (brain/brain/implementers/config.py),
whose address is this server: the CLI's requests go through the relay to here, and each gets one
text answer, `Done: …` with a DONE WHEN line and the DESCRIPTION line, so the run ends at its first
answer, as an implementer that built the thing and reported would. A rule per task (its name, as
the brief's `YOUR TASK: <name>.` or a follow-up's `what <name> is now` says it) sets its delay and
its report; a request waits out its delay before the answer starts, so a run stays live that long
and a follow-up sent meanwhile goes into it as a message. The key it is given is a stand-in's: the
brain's OpenRouter key never comes here (the deepseek provider takes DEEPSEEK_API_KEY).

What it answers: POST …/v1/messages, streamed (SSE: message_start, a text block, message_delta with
end_turn and the usage, message_stop) when the request asks for a stream, else one JSON message;
POST …/v1/messages/count_tokens with a count; anything else 404, as an unknown path is. It records
each messages request: when, the task it is for, whether it streamed, its model, how many messages
it held, and the last user message's text (cut to 4000 characters) — never a header.
"""
import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TASK = re.compile(r"YOUR TASK: ([a-z][a-z0-9_]{1,55})\.|what ([a-z][a-z0-9_]{1,55}) is now")
DEFAULT_DELAY = 2.0
TEXT_MAX = 4000


def text_of(content):
    """A message's content as text: a string, or its text blocks (tool results' text too) joined."""
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            out.append(b.get("text") or "")
        elif b.get("type") == "tool_result":
            out.append(text_of(b.get("content")))
    return "\n".join(out)


def task_of(body):
    """The task a request is for: the last name a `YOUR TASK:` or `what <name> is now` gives, read
    from the last user message back to the system prompt; "" when none does."""
    texts = [text_of(m.get("content")) for m in reversed(body.get("messages") or []) if m.get("role") == "user"]
    system = body.get("system")
    texts.append(system if isinstance(system, str) else text_of(system))
    for t in texts:
        names = [a or b for a, b in TASK.findall(t or "")]
        if names:
            return names[-1]
    return ""


def last_user(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            return text_of(m.get("content"))
    return ""


class StandIn:
    def __init__(self, record=None, delay=DEFAULT_DELAY):
        self.record = record          # a JSONL file every request is appended to, or None
        self.delay = delay
        self.rules = {}               # task name -> {"delay", "report", "status"}
        self.seen = []                # every request, as recorded
        self.lock = threading.Lock()
        self.server = None
        self.url = ""

    def rule(self, task, delay=None, report=None):
        """`task`'s answers: wait `delay` s before each (a list: the n-th request waits the n-th, the
        last for the rest), and say `report` (else the default report)."""
        with self.lock:
            self.rules[task] = {"delay": self.delay if delay is None else delay, "report": report}

    def report_for(self, task, n):
        """The answer's text: the rule's report, else `Done:` with a DONE WHEN line and the
        DESCRIPTION line the runner takes off (brain/brain/implementers/runner.py)."""
        rule = self.rules.get(task) or {}
        if rule.get("report"):
            return rule["report"]
        name = task or "the task"
        return ("Done: %s is built, as the brief asked (a stand-in implementer: its run %d; nothing was made in the "
                "world).\n- every DONE WHEN item: checked\nDESCRIPTION: %s, made by a stand-in implementer for a "
                "live test." % (name, n, name))

    def requests(self, task=None):
        with self.lock:
            return [r for r in self.seen if task is None or r["task"] == task]

    def start(self, port=0):
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # quiet: the record is the log
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n) if n else b""
                path = self.path.split("?")[0]
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                if path.endswith("/v1/messages/count_tokens"):
                    return self.send_json(200, {"input_tokens": max(1, len(raw) // 4)})
                if not path.endswith("/v1/messages"):
                    return self.send_json(404, {"type": "error", "error": {"type": "not_found_error",
                                                                          "message": "no such path here"}})
                stand_in.answer(self, body, len(raw))

            def do_GET(self):
                self.send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "no such path"}})

            def send_json(self, status, doc):
                data = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def answer(self, h, body, size):
        task = task_of(body)
        with self.lock:
            n = sum(1 for r in self.seen if r["task"] == task) + 1
            rule = self.rules.get(task) or {}
            # A request for no task (the CLI's own beside the conversation: a session's title) is answered
            # at once: it must never hold a run up.
            delay = rule.get("delay", self.delay if task else 1.0)
            if isinstance(delay, (list, tuple)):
                delay = delay[min(n, len(delay)) - 1] if delay else self.delay
            row = {"at": time.time(), "task": task, "n": n, "stream": bool(body.get("stream")),
                   "model": body.get("model"), "messages": len(body.get("messages") or []),
                   "tools": len(body.get("tools") or []), "text": last_user(body)[:TEXT_MAX], "bytes": size}
            self.seen.append(row)
            if self.record:
                with open(self.record, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        text = self.report_for(task, n)
        time.sleep(delay)
        usage_in = max(1, size // 4)
        model = body.get("model") or "stand-in"
        mid = "msg_standin_%d_%d" % (int(time.time() * 1000), n)
        if not body.get("stream"):
            return h.send_json(200, {"id": mid, "type": "message", "role": "assistant", "model": model,
                                     "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                     "stop_sequence": None,
                                     "usage": {"input_tokens": usage_in, "output_tokens": len(text) // 4}})
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": mid, "type": "message", "role": "assistant", "model": model, "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": usage_in, "output_tokens": 1, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": max(1, len(text) // 4)}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        data = "".join("event: %s\ndata: %s\n\n" % (k, json.dumps(v)) for k, v in events).encode()
        h.send_response(200)
        h.send_header("content-type", "text/event-stream")
        h.send_header("cache-control", "no-cache")
        h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--record", default=None, help="a JSONL file every request is appended to")
    args = ap.parse_args()
    s = StandIn(record=args.record, delay=args.delay).start(args.port)
    print("stand-in model on %s (Ctrl-C to stop)" % s.url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.stop()


if __name__ == "__main__":
    main()
