"""The relay (harness/relay.py), against a fake provider on 127.0.0.1: thinking disabled and
output_config dropped on every …/v1/messages POST and nothing else changed, the headers (the key
among them) passed on, the answer streamed back as it comes, each request's usage given to its run
before the answer ends, and every other path passed through untouched."""
import asyncio
import json

import httpx
import pytest

from brain.implementers.harness.relay import Relay, Reading, _model, rewrite


def sse(*events: dict) -> list[bytes]:
    return [("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e))).encode() for e in events]


START = {"type": "message_start", "message": {"id": "msg_1", "model": "deepseek-v4.1-flash", "usage": {
    "input_tokens": 1200, "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 0, "output_tokens": 1}}}
DELTA = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 345}}
STOP = {"type": "message_stop"}
TEXT = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}


class Provider:
    """A fake provider: records each request, and answers from `answers` by path (a list of chunks to
    stream as SSE, or a dict to send as JSON). With `hold`, the last chunk waits for it."""

    def __init__(self):
        self.seen: list[dict] = []
        self.answers: dict[str, object] = {}
        self.hold: asyncio.Event | None = None
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
                if isinstance(answer, list):
                    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nx-provider: fake\r\n"
                                 b"transfer-encoding: chunked\r\n\r\n")
                    for i, chunk in enumerate(answer):
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


def test_a_billed_cost_in_the_usage_is_read_when_there():
    r = Reading("m", 0.0, sse=True)
    r.feed(b"".join(sse(START, dict(DELTA, usage={"output_tokens": 345, "cost": 0.00042}), STOP)))
    assert r.request().billed == 0.00042
