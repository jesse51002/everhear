"""The Jev call — TypeSafe's System One model, `POST /v1/systemone`.

Adapted from the workspace's `scripts/lib/typesafe.py`, with one difference
that matters: ONE ATTEMPT PER CALL, no retries inside it. The filter's loop is
what retries, because the loop is what keeps the failing clock.

    body     {"model": ..., "state": {"rules", "look_out_for", "examples", "actions", "history"},
              "questions": {name: question}}
    answer   {"answers": {name: {"choice", "confidence", "probabilities", ...}},
              "usage": {"input_tokens": N}}

ERRORS. `JevRefused` for 400, 413 and 422 — the API answered and said no to
this request. `JevDown` for everything else: a timeout, no connection, 401,
403, 429, 5xx, a malformed or missing answer the call requires (the routing
call's `d_what`), or no key. A message never carries the key, the headers or
the state.
"""
import http.client
import json
import math
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
PRICE_PER_INPUT_TOKEN = 0.000000042   # $42 per billion input tokens, output free
REFUSED_STATUSES = (400, 413, 422)


class JevDown(Exception):
    """Jev did not give a usable answer: counts towards failing open."""


class JevRefused(Exception):
    """Jev answered 400, 413 or 422: this request, not the service."""


class Result:
    """A call's answers, parsed. `answers` maps each well-formed question to
    `(label, confidence)` — or `(None, p)` for a `noul` — and a malformed one
    is simply absent. `input_tokens` is None when the answer gave no usage."""

    def __init__(self, answers, input_tokens, ms):
        self.answers = answers
        self.input_tokens = input_tokens
        self.ms = ms

    @property
    def cost_usd(self):
        if self.input_tokens is None:
            return None
        return self.input_tokens * PRICE_PER_INPUT_TOKEN


def _number(v):
    """A finite probability in [0, 1], or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    if math.isnan(v) or v < 0 or v > 1:
        return None
    return v


def parse_answer(question, answer):
    """`(label, confidence)` for a well-formed answer to `question`, else None.

    A `choice`: `choice` must be one of the question's own criteria labels, and
    the confidence is `confidence`, falling back to `probabilities[choice]`.
    A `noul`: `noul` is the probability, answered as `(None, p)`.
    """
    if not isinstance(answer, dict):
        return None
    kind = question.get("type")
    if kind == "noul":
        p = _number(answer.get("noul"))
        return None if p is None else (None, p)
    labels = question.get("criteria") or {}
    label = answer.get("choice")
    if not isinstance(label, str) or label not in labels:
        return None
    conf = _number(answer.get("confidence"))
    if conf is None:
        probs = answer.get("probabilities")
        if isinstance(probs, dict):
            conf = _number(probs.get(label))
    if conf is None:
        return None
    return label, conf


def _reason(body, key):
    """A short, safe reason from an error body: the API's own message if it
    gives one, never more than 200 characters, never the key."""
    text = body.decode("utf-8", errors="replace").strip()
    try:
        got = json.loads(text)
    except ValueError:
        got = None
    if isinstance(got, dict):
        for name in ("error", "message", "detail"):
            v = got.get(name)
            if isinstance(v, dict):
                v = v.get("message") or v.get("msg") or v.get("type")
            if isinstance(v, list):
                v = "; ".join(str(i.get("msg")) for i in v if isinstance(i, dict) and i.get("msg"))
            if isinstance(v, str) and v:
                text = v
                break
        else:
            text = "(no message)"
    elif got is not None:
        text = "(no message)"
    text = " ".join(text.split())[:200]
    if key:
        text = text.replace(key, "***")
    return text or "(empty body)"


class Jev:
    """One client: `ask(state, questions)` and `probe()`."""

    def __init__(self, key, url=ENDPOINT, model=MODEL, timeout=3.0):
        self.key = key or ""
        self.url = url
        self.model = model
        self.timeout = timeout
        self.last_body = None   # the last response body, as Jev sent it (for --judge --raw)

    def ask(self, state, questions, required="line_1"):
        """One state, every question about it, one attempt. Answers a `Result`.
        Raises `JevDown` when `required` is missing or malformed: the filter
        passes `d_what` for the routing call and None for the gather call (the
        default is the old benches' `line_1`)."""
        if not self.key:
            raise JevDown("no key (TYPESAFE_API_KEY)")
        # UTF-8 JSON, not \u-escaped: the state's size here is the size the
        # budget in prompt.py counted (prompt.state_json)
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json",
        })
        t0 = time.monotonic()
        self.last_body = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                self.last_body = e.read()
                reason = _reason(self.last_body, self.key)
            except Exception:
                reason = "(unreadable body)"
            if e.code in REFUSED_STATUSES:
                raise JevRefused("%d %s" % (e.code, reason)) from None
            raise JevDown("%d %s" % (e.code, reason)) from None
        except urllib.error.URLError as e:
            raise JevDown("no connection: %s" % (e.reason,)) from None
        except (OSError, http.client.HTTPException) as e:
            # socket.timeout is an OSError on 3.9 and TimeoutError after
            raise JevDown("%s: %s" % (type(e).__name__, e)) from None
        ms = int((time.monotonic() - t0) * 1000)
        self.last_body = raw
        try:
            got = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise JevDown("200 but the body is not JSON") from None
        if not isinstance(got, dict):
            raise JevDown("200 but the body is not an object")
        answers = got.get("answers")
        answers = answers if isinstance(answers, dict) else {}
        parsed = {}
        for name, question in questions.items():
            p = parse_answer(question, answers.get(name))
            if p is not None:
                parsed[name] = p
        if required and required not in parsed:
            raise JevDown("malformed or missing %s in the answer" % required)
        usage = got.get("usage")
        tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, bool) or not isinstance(tokens, (int, float)):
            tokens = None
        return Result(parsed, None if tokens is None else int(tokens), ms)

    def probe(self):
        """The state `{"text": "ping"}` and one `noul` question; succeeds on any
        well-formed answer, raises `JevDown` or `JevRefused` otherwise."""
        return self.ask({"text": "ping"}, {"ping": {"type": "noul", "instructions": "Is this text a ping?"}},
                        required="ping")
