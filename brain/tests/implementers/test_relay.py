"""The relay (harness/relay.py), against a fake provider on 127.0.0.1: every …/v1/messages POST at
the effort (at off, thinking disabled and output_config dropped; else output_config's effort and no
thinking) and nothing else changed, the headers (the key among them) passed on, the answer streamed
back as it comes, each request's usage, its reasoning among it, given to its run before the answer
ends, and every other path passed through untouched."""
import asyncio
import json

import httpx
import pytest

from brain.implementers.harness.relay import THINKING_CHARS, Relay, Reading, _model, _sent, rewrite


def sse(*events: dict) -> list[bytes]:
    return [("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e))).encode() for e in events]


START = {"type": "message_start", "message": {"id": "msg_1", "model": "deepseek-v4.1-flash", "usage": {
    "input_tokens": 1200, "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 0, "output_tokens": 1}}}
DELTA = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 345}}
STOP = {"type": "message_stop"}
TEXT = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}


DROP = object()  # in an answer's chunks: the provider's connection drops there, the answer unended


class Provider:
    """A fake provider: records each request, and answers from `answers` by path (a list of chunks to
    stream as SSE, DROP among them where the connection drops; a dict to send as JSON; or a
    function of the target giving a dict, or None for a 404). With `hold`, the last chunk waits for
    it. `headers`: sent with every streamed answer too."""

    def __init__(self):
        self.seen: list[dict] = []
        self.answers: dict[str, object] = {}
        self.hold: asyncio.Event | None = None
        self.headers: dict[str, str] = {}
        self.server = None
        self.port = None

    async def start(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return "http://127.0.0.1:%d/anthropic" % self.port

    async def stop(self):
        self.server.close()

    async def _serve(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                method, target, _ = line.decode().split(" ")
                headers = {}
                while (h := await reader.readline()) not in (b"\r\n", b""):
                    k, _, v = h.decode().partition(":")
                    headers[k.strip().lower()] = v.strip()
                body = await reader.readexactly(int(headers.get("content-length", 0)))
                self.seen.append({"method": method, "target": target, "headers": headers, "body": body})
                answer = self.answers.get(target.split("?")[0], {"ok": True})
                if callable(answer):
                    answer = answer(target)
                    if answer is None:
                        writer.write(b"HTTP/1.1 404 Not Found\r\ncontent-length: 2\r\n\r\n{}")
                        await writer.drain()
                        continue
                if isinstance(answer, list):
                    extra = "".join("%s: %s\r\n" % kv for kv in self.headers.items()).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nx-provider: fake\r\n%s"
                                 b"transfer-encoding: chunked\r\n\r\n" % extra)
                    for i, chunk in enumerate(answer):
                        if chunk is DROP:
                            writer.transport.abort()
                            return
                        if self.hold is not None and i == len(answer) - 1:
                            await self.hold.wait()
                        writer.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        await writer.drain()
                    writer.write(b"0\r\n\r\n")
                else:
                    data = json.dumps(answer).encode()
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: %d\r\n\r\n%s"
                                 % (len(data), data))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def setup():
    provider = Provider()
    relay = Relay(await provider.start())
    await relay.start()
    got = []
    address = relay.open("hot_fireball", got.append)
    async with httpx.AsyncClient(timeout=5) as http:
        yield provider, relay, address, got, http
    await relay.stop()
    await provider.stop()


BODY = {"model": "deepseek-flash", "max_tokens": 32000, "stream": True, "system": [{"type": "text", "text": "rules"}],
        "messages": [{"role": "user", "content": "make a fireball"}], "tools": [{"name": "Bash"}],
        "output_config": {"effort": "low"}, "metadata": {"user_id": "u"}, "temperature": 1}


async def test_a_messages_request_goes_on_with_thinking_off_and_nothing_else_changed(setup):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = sse(START, TEXT, DELTA, STOP)
    r = await http.post(address + "/v1/messages?beta=true", json=BODY,
                        headers={"authorization": "Bearer sk-secret", "anthropic-version": "2023-06-01",
                                 "anthropic-beta": "x-1"})
    assert r.status_code == 200 and r.headers["x-provider"] == "fake"
    [seen] = provider.seen
    assert (seen["method"], seen["target"]) == ("POST", "/anthropic/v1/messages?beta=true")
    sent = json.loads(seen["body"])
    want = dict(BODY, thinking={"type": "disabled"})
    del want["output_config"]
    assert sent == want and list(sent)[:-1] == [k for k in BODY if k != "output_config"]
    h = seen["headers"]
    assert (h["authorization"], h["anthropic-version"], h["anthropic-beta"]) == ("Bearer sk-secret", "2023-06-01", "x-1")
    assert h["accept-encoding"] == "identity" and h["host"] == "127.0.0.1:%d" % provider.port
    # the answer as it came, and the request's usage: message_start's, with message_delta's output
    assert r.content == b"".join(sse(START, TEXT, DELTA, STOP))
    [req] = got
    assert (req.model, req.input, req.cache_read, req.cache_write, req.output, req.size, req.id) == (
        "deepseek-flash", 1200, 90000, 0, 345, 91200, "msg_1")


async def test_the_answer_streams_back_as_it_comes_and_its_usage_is_given_before_it_ends(setup):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = sse(START, TEXT, DELTA, STOP)
    provider.hold = asyncio.Event()
    chunks = []
    async with http.stream("POST", address + "/v1/messages", json=BODY) as r:
        async for chunk in r.aiter_raw():
            chunks.append(chunk)
            if b"message_delta" in b"".join(chunks) and not provider.hold.is_set():
                # the last event is still held by the provider: what came so far came through already,
                # and the request is counted before the answer ends
                assert len(got) == 1 and b"message_stop" not in b"".join(chunks)
                assert not await relay.settle(address, 0.05)  # still in flight
                provider.hold.set()
    assert b"".join(chunks) == b"".join(sse(START, TEXT, DELTA, STOP)) and len(chunks) >= 2
    assert await relay.settle(address, 1)


async def test_every_other_path_passes_through_untouched_and_counts_nothing(setup):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages/count_tokens"] = {"input_tokens": 9}
    r = await http.post(address + "/v1/messages/count_tokens", json=BODY)
    assert r.json() == {"input_tokens": 9}
    r = await http.get(address + "/v1/models?limit=1")
    assert r.json() == {"ok": True}
    assert [(s["method"], s["target"]) for s in provider.seen] == [
        ("POST", "/anthropic/v1/messages/count_tokens"), ("GET", "/anthropic/v1/models?limit=1")]
    assert json.loads(provider.seen[0]["body"]) == BODY  # not a messages request: as it came
    assert got == []


async def test_a_json_answer_is_counted_and_a_body_not_json_goes_on_as_it_came(setup):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = {"id": "msg_2", "usage": {"input_tokens": 40, "output_tokens": 7}}
    r = await http.post(address + "/v1/messages", json=dict(BODY, stream=False))
    assert r.json()["id"] == "msg_2"
    assert [(q.input, q.output, q.id) for q in got] == [(40, 7, "msg_2")]
    await http.post(address + "/v1/messages", content=b"not json")
    assert provider.seen[-1]["body"] == b"not json"


async def test_an_address_no_run_has_is_refused_and_an_unreachable_provider_is_a_502(setup):
    provider, relay, address, got, http = setup
    r = await http.post(address.rsplit("/", 1)[0] + "/0123456789abcdef/v1/messages", json=BODY)
    assert r.status_code == 404 and provider.seen == []
    relay.forget(address)
    assert (await http.post(address + "/v1/messages", json=BODY)).status_code == 404
    dead = Relay("http://127.0.0.1:9/nothing")
    await dead.start()
    try:
        r = await http.post(dead.open("t", got.append) + "/v1/messages", json=BODY)
        assert r.status_code == 502 and r.json()["error"]["type"] == "api_error"
    finally:
        await dead.stop()


async def test_two_runs_each_get_their_own_requests(setup):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = sse(START, DELTA, STOP)
    other = []
    second = relay.open("ice_beam", other.append)
    await asyncio.gather(http.post(address + "/v1/messages", json=BODY), http.post(second + "/v1/messages", json=BODY))
    assert len(got) == 1 and len(other) == 1


def test_rewrite_at_each_effort():
    sent_thinking = dict(BODY, thinking={"type": "enabled", "budget_tokens": 9})
    # off: thinking disabled and no output_config, exactly as before the effort was a setting
    for given in (BODY, sent_thinking):
        body, _ = rewrite(json.dumps(given).encode(), effort="off")
        want = {k: v for k, v in given.items() if k not in ("output_config", "thinking")}
        assert json.loads(body) == dict(want, thinking={"type": "disabled"})
    assert rewrite(json.dumps(BODY).encode())[0] == rewrite(json.dumps(BODY).encode(), effort="off")[0]  # off by default
    # low, high, max: the effort in output_config, whatever the CLI said, and no thinking field (a
    # thinking budget would become an effort of its own on OpenRouter)
    for effort in ("low", "high", "max"):
        for given in (BODY, sent_thinking, {k: v for k, v in BODY.items() if k != "output_config"}):
            body, seen = rewrite(json.dumps(given).encode(), {"provider": {"order": ["together"]}}, effort)
            want = {k: v for k, v in given.items() if k not in ("output_config", "thinking")}
            assert json.loads(body) == dict(want, output_config={"effort": effort}, provider={"order": ["together"]})
            assert seen["effort"] == (given.get("output_config") or {}).get("effort")  # what the CLI sent, for the log
    assert "and no thinking field" in _sent(seen, "high") and "at effort high" in _sent(seen, "high")
    assert "with thinking disabled and no effort" in _sent(seen, "off")


async def test_the_relay_sends_its_effort_on_every_messages_request_and_nothing_else_changed():
    provider = Provider()
    relay = Relay(await provider.start(), effort="high")
    await relay.start()
    got = []
    address = relay.open("hot_fireball", got.append)
    provider.answers["/anthropic/v1/messages"] = sse(START, DELTA, STOP)
    provider.answers["/anthropic/v1/messages/count_tokens"] = {"input_tokens": 9}
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(address + "/v1/messages", json=dict(BODY, thinking={"type": "adaptive"}))
            await http.post(address + "/v1/messages/count_tokens", json=BODY)
    finally:
        await relay.stop()
        await provider.stop()
    sent = json.loads(provider.seen[0]["body"])
    assert sent == dict({k: v for k, v in BODY.items() if k != "output_config"}, output_config={"effort": "high"})
    assert json.loads(provider.seen[1]["body"]) == BODY  # not a messages request: as it came
    assert len(got) == 1


def test_rewrite_says_what_was_sent():
    body, seen = rewrite(json.dumps(dict(BODY, thinking={"type": "enabled", "budget_tokens": 9})).encode())
    assert json.loads(body)["thinking"] == {"type": "disabled"} and "output_config" not in json.loads(body)
    assert seen == {"model": "deepseek-flash", "thinking": {"type": "enabled", "budget_tokens": 9}, "effort": "low",
                    "system": 5, "messages": 1, "tools": 1, "stream": True}
    assert rewrite(b"[1]") == (b"[1]", {"error": "the body is not a JSON object"})


def test_a_reading_across_chunk_edges():
    data = b"".join(sse(START, TEXT, DELTA, STOP))
    r = Reading("m", 0.0, sse=True)
    finals = [r.feed(data[i:i + 7]) for i in range(0, len(data), 7)]
    assert finals.count(True) == 1 and r.request().output == 345 and r.request().input == 1200
    assert not r.end()  # given already
    cut = Reading("m", 0.0, sse=True)
    cut.feed(b"".join(sse(START)))
    assert cut.end() and cut.request().output == 1  # a stream cut short: what message_start said


def test_a_request_is_priced_by_its_model_as_the_price_table_names_it():
    assert _model({"model": "Deepseek-Flash[1m]"}) == "deepseek-flash" and _model(None) == ""


async def test_openrouters_routing_goes_on_every_messages_request_and_the_provider_is_read():
    provider = Provider()
    routing = {"provider": {"allow_fallbacks": True, "order": ["digitalocean"], "ignore": ["venice"]}}
    relay = Relay(await provider.start(), extra=routing)
    await relay.start()
    got = []
    address = relay.open("hot_fireball", got.append)
    start = dict(START, message=dict(START["message"], provider="DigitalOcean"))
    provider.answers["/anthropic/v1/messages"] = sse(start, DELTA, STOP)
    provider.answers["/anthropic/v1/messages/count_tokens"] = {"input_tokens": 9}
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            await http.post(address + "/v1/messages", json=BODY)
            await http.post(address + "/v1/messages/count_tokens", json=BODY)
    finally:
        await relay.stop()
        await provider.stop()
    sent = json.loads(provider.seen[0]["body"])
    assert sent["provider"] == routing["provider"] and sent["thinking"] == {"type": "disabled"}
    assert "provider" not in json.loads(provider.seen[1]["body"])  # only the messages requests
    assert [(r.provider, r.billed) for r in got] == [("DigitalOcean", None)]  # no usage.cost: none


# OpenRouter's Claude-compatible answer with thinking on (2026-10-13, Together): message_start's
# output_tokens_details is null, message_delta's counts the thinking, a part of output_tokens.
THINK = {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "x" * 300}}
OR_START = {"type": "message_start", "message": {"id": "msg_k", "model": "deepseek/deepseek-v4.1-flash",
                                                 "provider": "Together", "usage": {
    "input_tokens": 0, "output_tokens": 0, "output_tokens_details": None, "cache_creation_input_tokens": None,
    "cache_read_input_tokens": None}}}
OR_DELTA = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {
    "input_tokens": 59, "output_tokens": 390, "output_tokens_details": {"thinking_tokens": 387},
    "cache_creation_input_tokens": None, "cache_read_input_tokens": 0, "cost": 0.0004857}}


def test_the_reasoning_is_the_providers_count_when_its_usage_says():
    r = Reading("deepseek/deepseek-v4.1-flash", 0.0, sse=True)
    data = b"".join(sse(OR_START, THINK, THINK, TEXT, OR_DELTA, STOP))
    assert [r.feed(data[i:i + 50]) for i in range(0, len(data), 50)].count(True) == 1
    q = r.request()
    assert (q.input, q.output, q.reasoning, q.reasoning_estimated, q.provider, q.billed) == (
        59, 390, 387, False, "Together", 0.0004857)
    # a JSON answer's usage, the same
    j = Reading("m", 0.0, sse=False)
    j.feed(json.dumps({"id": "msg_j", "content": [{"type": "thinking", "thinking": "y" * 90}, {"type": "text", "text": "4"}],
                       "usage": {"input_tokens": 9, "output_tokens": 40,
                                 "output_tokens_details": {"thinking_tokens": 37}}}).encode())
    assert j.end() and (j.request().reasoning, j.request().reasoning_estimated) == (37, False)


def test_the_reasoning_is_estimated_from_the_thinkings_text_when_the_usage_does_not_say():
    r = Reading("deepseek-flash", 0.0, sse=True)
    r.feed(b"".join(sse(START, THINK, THINK, TEXT, DELTA, STOP)))
    q = r.request()
    assert (q.output, q.reasoning, q.reasoning_estimated) == (345, round(600 / THINKING_CHARS), True)
    # never more than the output it is part of
    big = Reading("deepseek-flash", 0.0, sse=True)
    big.feed(b"".join(sse(START, *[THINK] * 10, DELTA, STOP)))
    assert (big.request().reasoning, big.request().reasoning_estimated) == (345, True)
    # a JSON answer's thinking blocks
    j = Reading("m", 0.0, sse=False)
    j.feed(json.dumps({"id": "msg_j", "content": [{"type": "thinking", "thinking": "y" * 90}, {"type": "text", "text": "4"}],
                       "usage": {"input_tokens": 9, "output_tokens": 40}}).encode())
    assert j.end() and (j.request().reasoning, j.request().reasoning_estimated) == (30, True)
    # no thinking at all: 0, and not an estimate
    plain = Reading("m", 0.0, sse=True)
    plain.feed(b"".join(sse(START, TEXT, DELTA, STOP)))
    assert (plain.request().reasoning, plain.request().reasoning_estimated) == (0, False)


@pytest.fixture
async def unhandled():
    """What the event loop's exception handler got: an exception out of a connection's handler."""
    loop = asyncio.get_running_loop()
    got = []
    loop.set_exception_handler(lambda _, ctx: got.append(ctx))
    yield got
    loop.set_exception_handler(None)


async def test_an_answer_that_breaks_off_is_counted_broken_as_far_as_it_was_read(setup, unhandled, monkeypatch):
    from brain.implementers.harness import relay as relay_mod
    monkeypatch.setattr(relay_mod, "GENERATION_WAITS", (0.01,))
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = sse(START, THINK, TEXT) + [DROP]
    with pytest.raises(httpx.HTTPError):  # Claude Code sees it broken, and sends it again
        await http.post(address + "/v1/messages", json=BODY)
    assert await relay.settle(address, 2)
    [q] = got
    # DeepSeek's own message_start counts the input: that much is known; no x-generation-id, no record
    assert (q.broken, q.input, q.cache_read, q.output, q.billed) == (True, 1200, 90000, 1, None)
    assert [g for g in provider.seen if "generation" in g["target"]] == []
    # the relay goes on: the next request on a new connection is answered and counted
    provider.answers["/anthropic/v1/messages"] = sse(START, DELTA, STOP)
    assert (await http.post(address + "/v1/messages", json=BODY)).status_code == 200
    assert [(q.broken, q.output) for q in got] == [(True, 1), (False, 345)]
    assert unhandled == []


async def test_a_broken_answer_on_openrouter_is_counted_from_its_generation_record(setup, unhandled, monkeypatch):
    from brain.implementers.harness import relay as relay_mod
    monkeypatch.setattr(relay_mod, "GENERATION_WAITS", (0.01, 0.01, 0.01))
    provider, relay, address, got, http = setup
    provider.headers = {"x-generation-id": "gen-1-abc"}
    provider.answers["/anthropic/v1/messages"] = sse(OR_START, THINK) + [DROP]
    asked = []

    def generation(target):  # the record comes a moment after the generation ends
        asked.append(target)
        if len(asked) < 2:
            return None
        return {"data": {"id": "gen-1-abc", "native_tokens_prompt": 111000, "native_tokens_cached": 109000,
                         "native_tokens_completion": 2400, "native_tokens_reasoning": 2100, "total_cost": 0.0041,
                         "provider_name": "Together"}}
    provider.answers["/anthropic/v1/generation"] = generation
    with pytest.raises(httpx.HTTPError):
        await http.post(address + "/v1/messages", json=BODY, headers={"authorization": "Bearer sk-secret"})
    assert await relay.settle(address, 2)
    [q] = got
    assert (q.broken, q.input, q.cache_read, q.cache_write, q.output, q.reasoning, q.billed, q.provider, q.id) == (
        True, 2000, 109000, 0, 2400, 2100, 0.0041, "Together", "gen-1-abc")
    assert asked == ["/anthropic/v1/generation?id=gen-1-abc"] * 2
    lookups = [g for g in provider.seen if "generation" in g["target"]]
    assert all(g["headers"]["authorization"] == "Bearer sk-secret" for g in lookups)  # the request's own key
    assert unhandled == []


async def test_a_runs_end_waits_for_a_broken_requests_record_so_it_is_counted_in_the_run(setup, unhandled, monkeypatch):
    from brain.implementers.harness import relay as relay_mod
    monkeypatch.setattr(relay_mod, "GENERATION_WAITS", (0.3,))  # the record comes after settle's own wait
    provider, relay, address, got, http = setup
    provider.headers = {"x-generation-id": "gen-2"}
    provider.answers["/anthropic/v1/messages"] = sse(OR_START, THINK) + [DROP]
    provider.answers["/anthropic/v1/generation"] = lambda t: {"data": {
        "native_tokens_prompt": 5000, "native_tokens_cached": 4000, "native_tokens_completion": 300,
        "total_cost": 0.001, "provider_name": "Together"}}
    with pytest.raises(httpx.HTTPError):  # the break, as the run ends
        await http.post(address + "/v1/messages", json=BODY)
    assert got == []  # its record is still asked for
    assert await relay.settle(address, 0.05)  # waits on for the look, not just its own 0.05 s
    assert [(q.broken, q.input, q.cache_read, q.output, q.billed) for q in got] == [(True, 1000, 4000, 300, 0.001)]
    assert unhandled == []


async def test_a_break_after_the_usage_was_final_counts_the_request_once_and_whole(setup, unhandled):
    provider, relay, address, got, http = setup
    provider.answers["/anthropic/v1/messages"] = sse(START, TEXT, DELTA) + [DROP]
    with pytest.raises(httpx.HTTPError):
        await http.post(address + "/v1/messages", json=BODY)
    assert await relay.settle(address, 2)
    assert [(q.broken, q.output) for q in got] == [(False, 345)]
    assert unhandled == []


async def test_a_request_that_spent_its_whole_output_on_thinking_is_said_loudly_and_changed_not(setup, capsys):
    from brain.implementers.config import set_quiet
    provider, relay, address, got, http = setup
    capped = {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {
        "input_tokens": 59, "output_tokens": 32000, "output_tokens_details": {"thinking_tokens": 32000}}}
    answered = dict(capped, delta={"stop_reason": "end_turn"})
    set_quiet(False)
    try:
        for delta in (capped, answered, dict(OR_DELTA, delta={"stop_reason": "max_tokens"})):
            provider.answers["/anthropic/v1/messages"] = sse(OR_START, THINK, delta, STOP)
            r = await http.post(address + "/v1/messages", json=BODY)
            assert r.content == b"".join(sse(OR_START, THINK, delta, STOP))  # the answer as it came
    finally:
        set_quiet(True)
    said = [ln for ln in capsys.readouterr().out.splitlines() if "WHOLE OUTPUT ON THINKING" in ln]
    # only the first: the second ended its turn, the third ran out with an answer (387 of 390 thinking)
    assert len(said) == 1 and said[0].endswith(
        " relay hot_fireball: A REQUEST SPENT ITS WHOLE OUTPUT ON THINKING (32000 tokens, 0 s) AND GAVE NO ANSWER; "
        "served by Together")
    assert [(q.output, q.reasoning) for q in got] == [(32000, 32000), (32000, 32000), (390, 387)]  # counted as ever
    sent = [json.loads(s["body"]) for s in provider.seen]
    assert all(b == json.loads(rewrite(json.dumps(BODY).encode())[0]) for b in sent)  # the request as ever


def test_a_billed_cost_in_the_usage_is_read_when_there():
    r = Reading("m", 0.0, sse=True)
    r.feed(b"".join(sse(START, dict(DELTA, usage={"output_tokens": 345, "cost": 0.00042}), STOP)))
    assert r.request().billed == 0.00042
