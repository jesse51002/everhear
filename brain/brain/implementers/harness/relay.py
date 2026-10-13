"""A relay on 127.0.0.1 between Claude Code and the provider's Claude-compatible address, in the
brain's own event loop, which sends every model request at the implementers' effort
(VFG_IMPL_EFFORT, config.EFFORTS).

Claude Code told to disable thinking leaves the field out (anthropics/claude-code#100775); untold,
it asks for adaptive thinking (`{"type": "adaptive", "display": "updates"}`, 2.1.296) at its own
effort for a model it does not know (high); and DeepSeek reads a request with no thinking field as
thinking on. So on every POST to …/v1/messages the relay sets the effort itself: at off,
`"thinking": {"type": "disabled"}` and no `output_config`, exactly as the measured relay of
2026-10-11 did (thinking on cost twice as much and took three to four times as long:
Prototype_Implementer_Harness, _Measured on DeepSeek_); at low, high or max, `"output_config":
{"effort": <it>}` and no `thinking`, which both providers take (DeepSeek's Claude-compatible API
takes only `effort` in `output_config`, and ignores a thinking budget; OpenRouter turns it into its
`reasoning.effort`, where a thinking budget would have become an effort of its own). What number
the word reaches depends on the host (config.EFFORTS). Everything else goes through unchanged: the
other fields, the headers (the key in Authorization among them), any other path, and the answer,
streamed back as it comes.

It also reads each request's usage as the answer streams through (message_start's usage, then
message_delta's) and hands it to the run that sent it, as a Request event, before the bytes that end
the answer reach Claude Code: so every request a run makes is counted, the ones Claude Code makes
beside the conversation (a session's title) too, and all of them before the run's result. Its
reasoning is OpenRouter's `usage.output_tokens_details.thinking_tokens`, a part of `output_tokens`;
an answer whose usage does not say has it estimated from its thinking's text (THINKING_CHARS).

An answer that breaks off as it streams (the provider's connection drops, or Claude Code's) is logged
once, Claude Code's connection is closed so it sends the request again, and the request is still
counted, marked broken: OpenRouter's usage comes only at the answer's end, so the relay asks
OpenRouter for its record of the generation (its x-generation-id) and counts what it billed; when
there is no record, it counts what was read before the break, a floor.

Each run has its own address, http://127.0.0.1:<port>/r/<token>, its ANTHROPIC_BASE_URL: the token
says whose a request is. One relay serves every implementer; it starts at the first run and stops
at close. It logs, per request, what Claude Code sent for thinking and effort and the request's
shape (its system prompt's length, its messages, its tools), and never the key or the prompt."""
import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..config import log, price_key
from .base import Request

# Request headers not sent on, and response headers not sent back: the hop's own, and the body's
# length and encoding, which the relay sets (it asks the provider for the identity encoding, so it
# can read the stream, and sends the answer back chunked).
HOP = frozenset(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te",
                 "trailers", "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding",
                 "content-encoding"])
# The longest answer that is not a stream the relay reads usage from; a longer one goes through unread.
JSON_MAX = 4 << 20
# How long a request may wait for the provider's first byte, and between two: 600 s, the CLI's own
# wait for an answer (the Anthropic SDK's default, unless API_TIMEOUT_MS says), so the relay never
# gives up on a request before the CLI would. With thinking on, a streamed answer sends its thinking
# as it comes, so a long think is no long silence; the run's own limit (VFG_IMPLEMENTER_MINUTES)
# ends a run whose request never answers.
TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=None)
# How long the relay waits, in turn, before it asks OpenRouter for its record of an answer that broke
# off: the record comes a moment after the generation ends (16 s of waits). The whole look takes
# LOOKUP_S at most, each ask GENERATION_ASK_S; a run's end waits that much longer for a look (settle).
GENERATION_WAITS = (1.0, 2.0, 4.0, 9.0)
GENERATION_ASK_S = 5.0
LOOKUP_S = 20.0
# Characters of DeepSeek V4.1 Flash's thinking a token, for an answer whose usage does not count its
# reasoning: 2.1 to 3.4 on its answers through OpenRouter, 2026-10-13 (2.9 the middle).
THINKING_CHARS = 3.0
_ROUTE = re.compile(r"^/r/([0-9a-f]{16})(/[^ ]*)?$")


@dataclass
class _Route:
    """One run's address: whose it is, where its Request events go, its requests in flight, and of
    them those whose answer broke off and wait for OpenRouter's record."""
    name: str
    sink: Callable[[Request], None]
    flight: int = 0
    lookups: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self):
        self.idle.set()


def rewrite(body: bytes, extra: dict | None = None, effort: str = "off") -> tuple[bytes, dict]:
    """The body sent on, at `effort` (off: thinking disabled and no output_config; else
    output_config's effort and no thinking) with `extra`'s fields, and what Claude Code sent: its
    thinking, its effort, its model and its shape, for the log. A body that is not a JSON object
    goes on as it came."""
    try:
        d = json.loads(body)
    except ValueError:
        return body, {"error": "the body is not JSON"}
    if not isinstance(d, dict):
        return body, {"error": "the body is not a JSON object"}
    oc = d.get("output_config")
    system = d.get("system")
    seen = {
        "model": d.get("model"),
        "thinking": d.get("thinking"),
        "effort": oc.get("effort") if isinstance(oc, dict) else oc,
        "system": len(system) if isinstance(system, str) else sum(
            len(b.get("text") or "") for b in system if isinstance(b, dict)) if isinstance(system, list) else 0,
        "messages": len(d.get("messages") or []),
        "tools": len(d.get("tools") or []),
        "stream": bool(d.get("stream")),
    }
    if effort == "off":
        d["thinking"] = {"type": "disabled"}
        d.pop("output_config", None)
    else:
        d.pop("thinking", None)
        d.pop("output_config", None)  # set again, last, as the effort alone
        d["output_config"] = {"effort": effort}
    d.update(extra or {})
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), seen


def _tokens(u: dict, key: str) -> int:
    v = u.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def reasoning_of(usage: dict) -> int | None:
    """The reasoning tokens a usage counts (OpenRouter's output_tokens_details.thinking_tokens, or
    its reasoning_tokens); None when it does not say."""
    d = usage.get("output_tokens_details")
    if isinstance(d, dict):
        for k in ("thinking_tokens", "reasoning_tokens"):
            v = d.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
    return None


class Reading:
    """One answer's usage, read as it streams through: an SSE stream's message_start and
    message_delta, or a whole JSON answer's `usage`; and the length of its thinking's text, for
    an estimate of its reasoning when the usage does not count it. feed() says when the usage is
    final."""

    def __init__(self, model: str, at: float, sse: bool):
        self.model = model
        self.at = at
        self.sse = sse
        self.usage: dict = {}
        self.id: str | None = None
        self.provider: str | None = None  # who served it (OpenRouter's message.provider)
        self.final = False
        self.stop_reason: str | None = None  # why the answer ended (max_tokens: out of output)
        self.thought = 0  # characters of thinking seen
        self._buf = b""
        self._json = bytearray()

    def feed(self, chunk: bytes) -> bool:
        """Reads one chunk; True when it made the usage final (at message_delta)."""
        if self.final:
            return False
        if not self.sse:
            if len(self._json) + len(chunk) <= JSON_MAX:
                self._json += chunk
            return False
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "content_block_delta" and isinstance(ev.get("delta"), dict):
                if ev["delta"].get("type") == "thinking_delta":
                    self.thought += len(str(ev["delta"].get("thinking") or ""))
            elif ev.get("type") == "message_start" and isinstance(ev.get("message"), dict):
                m = ev["message"]
                self.id = m.get("id") or self.id
                self.provider = m.get("provider") or self.provider
                if isinstance(m.get("usage"), dict):
                    self.usage.update(m["usage"])
            elif ev.get("type") == "message_delta" and isinstance(ev.get("usage"), dict):
                if isinstance(ev.get("delta"), dict):
                    self.stop_reason = ev["delta"].get("stop_reason") or self.stop_reason
                self.usage.update({k: v for k, v in ev["usage"].items() if v is not None})
                self.final = True
                return True
        return False

    def end(self) -> bool:
        """At the answer's end: True when there is usage not yet given (a JSON answer, or a
        stream cut short after its message_start)."""
        if self.final:
            return False
        if not self.sse:
            try:
                d = json.loads(bytes(self._json))
            except ValueError:
                return False
            if not isinstance(d, dict) or not isinstance(d.get("usage"), dict):
                return False
            self.id = d.get("id")
            self.provider = d.get("provider")
            self.stop_reason = d.get("stop_reason")
            self.usage = d["usage"]
            self.thought = sum(len(str(b.get("thinking") or "")) for b in d.get("content") or []
                               if isinstance(b, dict) and b.get("type") == "thinking")
        self.final = bool(self.usage)
        return self.final

    def request(self) -> Request:
        u = self.usage
        cost = u.get("cost")
        output = _tokens(u, "output_tokens")
        reasoning = reasoning_of(u)
        estimated = False
        if reasoning is None:  # the thinking's text, never more than the output it is part of
            reasoning = min(output, round(self.thought / THINKING_CHARS)) if self.thought else 0
            estimated = reasoning > 0
        return Request(model=self.model, input=_tokens(u, "input_tokens"),
                       cache_read=_tokens(u, "cache_read_input_tokens"),
                       cache_write=_tokens(u, "cache_creation_input_tokens"),
                       output=output, at=self.at, id=self.id, provider=self.provider,
                       billed=_dollars(cost), reasoning=reasoning, reasoning_estimated=estimated)


def _dollars(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def generation_request(record: dict, model: str, at: float, gid: str) -> Request:
    """A broken request's tokens from OpenRouter's record of its generation (GET
    /api/v1/generation?id=gen-…): its native counts, the cached part of the prompt as read from the
    cache, its completion with its reasoning in it, as the usage counts them, and what it billed."""
    def n(k: str) -> int:
        v = record.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) else 0
    cached = n("native_tokens_cached")
    return Request(model=model, input=max(0, n("native_tokens_prompt") - cached), cache_read=cached, cache_write=0,
                   output=n("native_tokens_completion"), at=at, id=gid, provider=record.get("provider_name"),
                   billed=_dollars(record.get("total_cost")), reasoning=n("native_tokens_reasoning"), broken=True)


class Relay:
    def __init__(self, upstream: str, extra: dict | None = None, effort: str = "off"):
        """`upstream`: the provider's Claude-compatible address, which Claude Code would have been
        given (https://api.deepseek.com/anthropic, https://openrouter.ai/api). `extra`: fields set on
        every messages request too (OpenRouter's `provider` routing). `effort`: every messages
        request's (config.EFFORTS; the adapter gives VFG_IMPL_EFFORT), off by default: thinking
        disabled."""
        self.upstream = upstream.rstrip("/")
        self.extra = dict(extra or {})
        self.effort = effort
        self.port: int | None = None
        self._server: asyncio.base_events.Server | None = None
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._routes: dict[str, _Route] = {}
        self._conns: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Listens on 127.0.0.1 at a free port, once; again after stop()."""
        async with self._lock:
            if self._server is not None:
                return
            self._http = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
            self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            log("relay", "on 127.0.0.1:%d to %s, %s on every request" % (self.port, self.upstream, _at(self.effort)))

    def open(self, name: str, sink: Callable[[Request], None]) -> str:
        """A run's own address, its requests' usage given to `sink`."""
        if self.port is None:
            raise RuntimeError("the relay is not started")
        token = secrets.token_hex(8)
        self._routes[token] = _Route(name, sink)
        return "http://127.0.0.1:%d/r/%s" % (self.port, token)

    def forget(self, address: str) -> None:
        """The run is over: a request still sent to its address is refused."""
        self._routes.pop(address.rsplit("/", 1)[-1], None)

    async def settle(self, address: str, timeout: float) -> bool:
        """Waits until no request of this run is in flight, `timeout` at most, and LOOKUP_S more while
        a broken one waits for OpenRouter's record, so it is counted in its run; False when one still
        is in flight."""
        route = self._routes.get(address.rsplit("/", 1)[-1])
        if route is None:
            return True
        try:
            await asyncio.wait_for(route.idle.wait(), timeout)
            return True
        except TimeoutError:
            if not route.lookups:
                return False
        try:
            await asyncio.wait_for(route.idle.wait(), LOOKUP_S)
            return True
        except TimeoutError:
            return False

    async def stop(self) -> None:
        async with self._lock:
            if self._server is None:
                return
            self._server.close()
            for t in list(self._conns):
                t.cancel()
            if self._conns:
                await asyncio.wait(set(self._conns), timeout=5)
            await self._server.wait_closed()
            await self._http.aclose()
            self._server = self._http = self.port = None
            self._routes.clear()
            log("relay", "stopped")

    # ------------------------------------------------------------------ one connection

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        me = asyncio.current_task()
        self._conns.add(me)
        try:
            while True:
                head = await _read_head(reader)
                if head is None:
                    return
                method, target, version, headers = head
                body = await _read_body(reader, headers)
                close = (version == "HTTP/1.0"
                         or any(k.lower() == "connection" and v.lower() == "close" for k, v in headers))
                if not await self._one(method, target, headers, body, writer) or close:
                    return
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError):
            return
        except asyncio.CancelledError:
            return
        except Exception as e:  # never out of the handler unsaid: the connection ends, the relay goes on
            log("relay", "a connection ended in an error: %s: %s" % (type(e).__name__, e))
            return
        finally:
            self._conns.discard(me)
            writer.close()

    async def _one(self, method: str, target: str, headers: list[tuple[str, str]], body: bytes,
                   writer: asyncio.StreamWriter) -> bool:
        """One request through; False when its answer broke off and the connection is closed."""
        m = _ROUTE.match(target)
        route = self._routes.get(m.group(1)) if m else None
        if route is None:
            log("relay", "refused %s %s: no run has that address" % (method, target.split("?")[0][:80]))
            await _answer(writer, 404, b'{"type":"error","error":{"type":"not_found_error","message":"no such run"}}')
            return True
        path = m.group(2) or "/"
        seen = None
        if method == "POST" and path.split("?")[0].endswith("/messages") and body:
            body, seen = rewrite(body, self.extra, self.effort)
        out = [(k, v) for k, v in headers if k.lower() not in HOP] + [("accept-encoding", "identity")]
        at = time.time()
        route.flight += 1
        route.idle.clear()
        status = 0
        reading = None
        broke = None  # why the answer broke off, when it did
        sent = 0  # the answer's bytes passed on
        gid = None  # OpenRouter's id of the generation

        def said() -> None:
            """The request's one log line: a messages request's, or any whose answer broke off."""
            if seen is not None or broke is not None:
                log("relay", "%s %s %s %d: %s%s%s" % (
                    route.name, method, path.split("?")[0], status,
                    _sent(seen, self.effort) if seen is not None else "not a messages request",
                    "; served by %s" % reading.provider if reading is not None and reading.provider else "",
                    "; %s after %d bytes, and the connection was closed" % (broke, sent) if broke else ""))
        try:
            try:
                req = self._http.build_request(method, self.upstream + path, headers=out, content=body)
                r = await self._http.send(req, stream=True)
            except httpx.HTTPError as e:
                status = 502
                msg = "the relay could not reach %s: %s" % (self.upstream, type(e).__name__)
                await _answer(writer, 502, json.dumps({"type": "error", "error": {"type": "api_error",
                                                                                  "message": msg}}).encode())
                return True
            try:
                status = r.status_code
                gid = r.headers.get("x-generation-id")
                sse = "text/event-stream" in (r.headers.get("content-type") or "")
                reading = Reading(_model(seen), at, sse) if seen is not None and "error" not in seen else None
                bodiless = method == "HEAD" or status in (204, 304) or 100 <= status < 200
                lines = ["HTTP/1.1 %d %s" % (status, r.reason_phrase or "")]
                lines += ["%s: %s" % (k, v) for k, v in r.headers.multi_items() if k.lower() not in HOP]
                if not bodiless:
                    lines.append("transfer-encoding: chunked")
                try:
                    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
                    if not bodiless:
                        async for chunk in r.aiter_bytes():
                            if not chunk:
                                continue
                            if reading is not None and reading.feed(chunk):
                                self._give(route, reading)  # before the bytes that end the answer go back
                            writer.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                            sent += len(chunk)
                            await writer.drain()
                        if reading is not None and reading.end():
                            self._give(route, reading)
                        writer.write(b"0\r\n\r\n")
                    await writer.drain()
                except httpx.HTTPError as e:  # the provider's side dropped mid-answer
                    broke = "the provider's answer broke off: %s%s" % (type(e).__name__, ": %s" % e if str(e) else "")
                except ConnectionError as e:  # Claude Code's side closed mid-answer
                    broke = "Claude Code closed its side: %s" % type(e).__name__
            finally:
                try:
                    await r.aclose()
                except httpx.HTTPError:
                    pass
            if broke is not None:
                writer.close()  # no end to the answer: Claude Code sees it broken, and sends it again
                said()
                if reading is not None and not reading.final:
                    await self._give_broken(route, reading, gid, headers)
            return broke is None
        finally:
            route.flight -= 1
            if route.flight == 0:
                route.idle.set()
            if broke is None:
                said()

    async def _give_broken(self, route: _Route, reading: Reading, gid: str | None,
                           headers: list[tuple[str, str]]) -> None:
        """A request whose answer broke off before its usage was final, counted, marked broken:
        from OpenRouter's record of its generation when there is one, else what was read (a floor)."""
        record = None
        if gid:
            route.lookups += 1
            try:
                async with asyncio.timeout(LOOKUP_S):
                    record = await self._generation(gid, headers)
            except TimeoutError:
                record = None
            finally:
                route.lookups -= 1
        if record is not None:
            q = generation_request(record, reading.model, reading.at, gid)
            log("relay", "%s: the broken request %s is counted from OpenRouter's record: %d tokens in, %d out, "
                         "$%s billed" % (route.name, gid, q.size, q.output, q.billed))
        else:
            q = reading.request()
            q.broken = True
            log("relay", "%s: the broken request%s is counted as far as it was read, %d tokens in, %d out: "
                         "a floor%s" % (route.name, " %s" % gid if gid else "", q.size, q.output,
                                        ", OpenRouter had no record of it" if gid else ""))
        try:
            route.sink(q)
        except Exception as e:  # a sink that breaks never breaks the relay
            log("relay", "%s: its usage could not be given: %s: %s" % (route.name, type(e).__name__, e))

    async def _generation(self, gid: str, headers: list[tuple[str, str]]) -> dict | None:
        """OpenRouter's record of a generation (its /v1/generation), asked with the request's own key,
        which goes to the provider it came for and nowhere else; None when there is none in time."""
        auth = [(k, v) for k, v in headers if k.lower() in ("authorization", "x-api-key")]
        for wait in GENERATION_WAITS:
            await asyncio.sleep(wait)
            try:
                r = await self._http.get(self.upstream + "/v1/generation", params={"id": gid}, headers=auth,
                                         timeout=GENERATION_ASK_S)
                data = r.json().get("data") if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError, AttributeError):
                continue
            if isinstance(data, dict):
                return data
        return None

    def _give(self, route: _Route, reading: Reading) -> None:
        q = reading.request()
        if reading.stop_reason == "max_tokens" and q.output > 0 and q.reasoning >= q.output:
            # the model thought until its output ran out, and answered nothing: said loudly, changed not
            log("relay", "%s: A REQUEST SPENT ITS WHOLE OUTPUT ON THINKING (%d tokens, %d s) AND GAVE NO ANSWER%s" % (
                route.name, q.output, round(time.time() - reading.at),
                "; served by %s" % reading.provider if reading.provider else ""))
        try:
            route.sink(q)
        except Exception as e:  # a sink that breaks never breaks the answer
            log("relay", "%s: its usage could not be given: %s: %s" % (route.name, type(e).__name__, e))


def _model(seen: dict | None) -> str:
    """The model a request asked for, as the price table has it: what it is priced by."""
    return price_key(str((seen or {}).get("model") or ""))


def _at(effort: str) -> str:
    """What a messages request is sent with, for the log."""
    return "thinking disabled" if effort == "off" else "effort %s" % effort


def _sent(seen: dict, effort: str = "off") -> str:
    """What Claude Code sent and what went on, for the log: never the prompt."""
    if "error" in seen:
        return "%s, sent on as it came" % seen["error"]
    return ("sent thinking %s and effort %s, sent on %s; model %s, system %d chars, %d messages, %d tools%s" % (
        json.dumps(seen["thinking"]), json.dumps(seen["effort"]),
        "with thinking disabled and no effort" if effort == "off" else "at effort %s and no thinking field" % effort,
        seen["model"], seen["system"], seen["messages"], seen["tools"], "" if seen["stream"] else ", not streamed"))


async def _answer(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    reason = {404: "Not Found", 502: "Bad Gateway"}.get(status, "")
    writer.write(b"HTTP/1.1 %d %s\r\ncontent-type: application/json\r\ncontent-length: %d\r\n\r\n%s"
                 % (status, reason.encode(), len(body), body))
    await writer.drain()


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, str, str, list[tuple[str, str]]] | None:
    """(method, target, version, headers) of the next request on the connection; None at its end."""
    line = await reader.readline()
    while line in (b"\r\n", b"\n"):  # blank lines between requests
        line = await reader.readline()
    if not line:
        return None
    parts = line.decode("latin-1").strip().split(" ")
    if len(parts) != 3:
        raise ValueError("not an HTTP request line")
    headers = []
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers.append((k.strip(), v.strip()))
    return parts[0].upper(), parts[1], parts[2].upper(), headers


async def _read_body(reader: asyncio.StreamReader, headers: list[tuple[str, str]]) -> bytes:
    h = {k.lower(): v for k, v in headers}
    if "chunked" in h.get("transfer-encoding", "").lower():
        out = bytearray()
        while True:
            size = int((await reader.readline()).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):  # trailers
                    pass
                return bytes(out)
            out += await reader.readexactly(size)
            await reader.readexactly(2)
    n = int(h.get("content-length") or 0)
    return await reader.readexactly(n) if n > 0 else b""
