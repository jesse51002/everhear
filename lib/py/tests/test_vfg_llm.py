"""structured() against an httpx.MockTransport: no network, no real key."""

import asyncio
import json
import logging
import re
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

import vfg_llm
from vfg_llm import Component, LlmError, structured

KEY = "sk-or-v1-test-not-a-real-key"
# Every row a call queues, in toolkit/vfg's usage_row order, then what only a call knows.
ROW_KEYS = ["at", "component", "model", "input_tokens", "output_tokens", "cost_usd", "ref",
            "cache_read_tokens", "cache_write_tokens", "ok", "error", "ms"]


async def ask(prompt, output, **kw):
    """structured() as the brain calls it, for the tests that are not about the usage row."""
    return await structured(prompt, output, **{"component": "brain", "call_id": "#5", **kw})


def queued(tmp_path) -> list[dict]:
    """The rows in $VFG_HOME/usage.pending.jsonl."""
    path = tmp_path / "usage.pending.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class Mob(BaseModel):
    name: str
    temper: Literal["hostile", "passive", "neutral"]


class Mobs(BaseModel):
    mobs: list[Mob]


GOOD = '{"mobs": [{"name": "Creeper", "temper": "hostile"}, {"name": "Cow", "temper": "passive"}]}'
BAD = '{"mobs": [{"name": "Creeper", "temper": "angry"}]}'


def ok(content: str, cost: float = 0.001) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "cost": cost,
                  "prompt_tokens_details": {"cached_tokens": 16}},
    })


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No test reads the real ~/.vfg, waits to retry, or keeps another's client."""
    monkeypatch.setenv("OPEN_ROUTER_API", KEY)
    monkeypatch.setenv("VFG_HOME", str(tmp_path))
    monkeypatch.delenv("VFG_OPENROUTER_KEY", raising=False)
    monkeypatch.setattr(vfg_llm, "RETRY_WAIT", 0)
    monkeypatch.setattr(vfg_llm, "client", None)


def serve(monkeypatch, *replies):
    """Answer each request with the next reply (a Response, or an exception to raise);
    return the requests as they arrive."""
    seen, queue = [], list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(vfg_llm, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


async def test_valid_answer(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD, cost=0.0000123))
    answer = await ask("Name two mobs.", Mobs, system="Be brief.")

    assert answer.value == Mobs.model_validate_json(GOOD)
    assert answer.cost == pytest.approx(0.0000123)
    assert isinstance(answer.ms, int) and answer.ms >= 0
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert seen[0].url == vfg_llm.ENDPOINT
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "~deepseek/deepseek-v4-flash-latest"
    assert body["messages"] == [{"role": "system", "content": "Be brief."},
                                {"role": "user", "content": "Name two mobs."}]
    assert body["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "Mobs", "schema": Mobs.model_json_schema()}}
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"enabled": False}
    assert (answer.prompt_tokens, answer.cached_tokens, answer.completion_tokens) == (20, 16, 30)
    assert KEY not in repr(answer)


async def test_cached_blocks_go_before_the_prompt_each_marked_or_not(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD), ok(GOOD))
    await ask("TAIL", Mobs, cached=[("RULES ", True), ("a ", False), ("b ", True), ("", True), ("c ", False)])
    marked = {"type": "ephemeral"}
    assert json.loads(seen[0].content)["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "RULES ", "cache_control": marked}, {"type": "text", "text": "a "},
        {"type": "text", "text": "b ", "cache_control": marked}, {"type": "text", "text": "c "},
        {"type": "text", "text": "TAIL"}]}]
    await ask("TAIL", Mobs, cached="RULES ")  # one opening: one mark
    assert json.loads(seen[1].content)["messages"][0]["content"] == [
        {"type": "text", "text": "RULES ", "cache_control": marked}, {"type": "text", "text": "TAIL"}]
    with pytest.raises(ValueError, match="at most 4"):
        await ask("TAIL", Mobs, cached=[("x", True)] * 5)


async def test_providers_go_as_an_order_with_fallbacks(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD), ok(GOOD))
    await ask("Name two mobs.", Mobs, providers=["parasail", "wafer"])
    assert json.loads(seen[0].content)["provider"] == {
        "order": ["parasail", "wafer"], "allow_fallbacks": True, "require_parameters": True}
    await ask("Name two mobs.", Mobs, providers=[])  # empty is OpenRouter's own choice
    assert json.loads(seen[1].content)["provider"] == {"require_parameters": True}


async def test_reasoning_true_leaves_the_model_default(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD))
    await ask("Name two mobs.", Mobs, reasoning=True)
    assert "reasoning" not in json.loads(seen[0].content)


async def test_reasoning_effort_goes_as_effort(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD))
    await ask("Name two mobs.", Mobs, reasoning="low")
    assert json.loads(seen[0].content)["reasoning"] == {"effort": "low"}


class Tagged(BaseModel):
    mob: Mob
    note: str = ""


async def test_openai_models_get_a_strict_schema(monkeypatch):
    seen = serve(monkeypatch, ok('{"mob": {"name": "Cow", "temper": "passive"}, "note": ""}'), ok(GOOD))
    await ask("Tag a mob.", Tagged, model="openai/gpt-6.1-sol")
    schema = json.loads(seen[0].content)["response_format"]["json_schema"]["schema"]
    for obj in (schema, schema["$defs"]["Mob"]):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    await ask("Name two mobs.", Mobs)  # any other model: the schema as pydantic writes it
    assert json.loads(seen[1].content)["response_format"]["json_schema"]["schema"] == Mobs.model_json_schema()


async def test_invalid_answer_repaired_on_second_try(monkeypatch):
    seen = serve(monkeypatch, ok(BAD, cost=0.001), ok(GOOD, cost=0.002))
    answer = await ask("Name two mobs.", Mobs)

    assert answer.value.mobs[1].name == "Cow"
    assert answer.cost == pytest.approx(0.003) and answer.prompt_tokens == 40  # both attempts
    retry = json.loads(seen[1].content)["messages"]
    assert retry[1] == {"role": "assistant", "content": BAD}
    assert retry[2]["role"] == "user"
    assert "mobs.0.temper" in retry[2]["content"] and "'hostile'" in retry[2]["content"]


async def test_still_invalid_raises(monkeypatch):
    seen = serve(monkeypatch, ok(BAD), ok("not json at all"))
    with pytest.raises(LlmError, match="twice failed to match Mobs"):
        await ask("Name two mobs.", Mobs)
    assert len(seen) == 2


@pytest.mark.parametrize("first", [
    httpx.Response(503, text="upstream busy"),
    httpx.Response(429, text="slow down"),
    httpx.ConnectError("connection reset"),
], ids=["503", "429", "dropped"])
async def test_transient_failure_retried_once(monkeypatch, first):
    seen = serve(monkeypatch, first, ok(GOOD))
    answer = await ask("Name two mobs.", Mobs)
    assert answer.value.mobs[0].temper == "hostile"
    assert len(seen) == 2


async def test_two_503s_raise(monkeypatch):
    seen = serve(monkeypatch, httpx.Response(503), httpx.Response(503))
    with pytest.raises(LlmError, match="503"):
        await ask("Name two mobs.", Mobs)
    assert len(seen) == 2


async def test_401_raises_without_the_key(monkeypatch):
    # The server echoing the key back must not put it in the message.
    seen = serve(monkeypatch, httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(LlmError, match="401") as e:
        await ask("Name two mobs.", Mobs)
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert len(seen) == 1


async def test_timeout_bounds_the_call(monkeypatch):
    async def slow(request):
        await asyncio.sleep(5)
    monkeypatch.setattr(vfg_llm, "client", httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(LlmError, match="within 0.01 s"):
        await ask("Name two mobs.", Mobs, timeout=0.01)


async def test_key_from_the_file_the_config_names(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_ROUTER_API")
    keyfile = tmp_path / "keys" / "openrouter"
    keyfile.parent.mkdir()
    keyfile.write_text("  sk-or-from-file\n")
    (tmp_path / "config").write_text(
        f'# a comment\nVFG_OWNER=Steve\nexport VFG_OPENROUTER_KEY="{keyfile}"\n')
    seen = serve(monkeypatch, ok(GOOD))
    await ask("Name two mobs.", Mobs)
    assert seen[0].headers["authorization"] == "Bearer sk-or-from-file"


async def test_missing_key_names_the_file_to_create(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_ROUTER_API")
    seen = serve(monkeypatch)
    with pytest.raises(LlmError, match=re.escape(str(tmp_path / "openrouter.key"))):
        await ask("Name two mobs.", Mobs)
    assert seen == []


# ------------------------------------------------------------------ the usage row

async def test_an_answer_queues_one_row_in_the_format_vfg_usage_add_queues(monkeypatch, tmp_path):
    serve(monkeypatch, ok(BAD, cost=0.001), ok(GOOD, cost=0.002))
    answer = await structured("Name two mobs.", Mobs, component=Component.PLANNER, call_id="T7 #2",
                              model="~deepseek/deepseek-v4-flash-latest")
    [row] = queued(tmp_path)
    assert list(row) == ROW_KEYS
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", row["at"])
    assert {k: v for k, v in row.items() if k != "at"} == {
        "component": "planner", "model": "~deepseek/deepseek-v4-flash-latest", "input_tokens": 40,
        "output_tokens": 60, "cost_usd": pytest.approx(0.003), "ref": "T7 #2", "cache_read_tokens": 32,
        "cache_write_tokens": None, "ok": 1, "error": None, "ms": answer.ms}  # both attempts, as the answer's
    assert (tmp_path / "usage.pending.lock").exists()


async def test_a_failed_call_queues_its_row_with_its_cost_and_the_error_carries_it(monkeypatch, tmp_path):
    serve(monkeypatch, ok(BAD, cost=0.001), ok("not json at all", cost=0.002))
    with pytest.raises(LlmError, match="twice failed to match Mobs") as e:
        await ask("Name two mobs.", Mobs)
    assert (e.value.cost, e.value.prompt_tokens, e.value.cached_tokens, e.value.completion_tokens) == \
        (pytest.approx(0.003), 40, 32, 60)
    [row] = queued(tmp_path)
    assert (row["component"], row["ref"], row["ok"], row["cost_usd"], row["input_tokens"], row["ms"]) == \
        ("brain", "#5", 0, pytest.approx(0.003), 40, e.value.ms)
    assert row["error"].startswith("~deepseek/deepseek-v4-flash-latest twice failed to match Mobs: - ")
    assert "\n" not in row["error"] and len(row["error"]) <= vfg_llm.ERROR_MAX


@pytest.mark.parametrize("replies, why", [
    ((httpx.Response(503), httpx.Response(503)), "OpenRouter 503"),
    ((httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}),), "OpenRouter 401"),
    ((httpx.ConnectError("connection reset"), httpx.ConnectError("connection reset")), "OpenRouter unreachable"),
], ids=["503", "401", "dropped"])
async def test_every_failure_is_one_row_never_with_the_key(monkeypatch, tmp_path, replies, why):
    serve(monkeypatch, *replies)
    with pytest.raises(LlmError):
        await ask("Name two mobs.", Mobs)
    [row] = queued(tmp_path)
    assert (row["ok"], row["cost_usd"]) == (0, 0) and row["error"].startswith(why)
    assert KEY not in (tmp_path / "usage.pending.jsonl").read_text()


async def test_a_timeout_and_a_missing_key_are_rows_too(monkeypatch, tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
    monkeypatch.setattr(vfg_llm, "client", httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(LlmError, match="within 0.01 s") as e:
        await ask("Name two mobs.", Mobs, timeout=0.01)
    assert e.value.ms >= 10
    monkeypatch.delenv("OPEN_ROUTER_API")
    with pytest.raises(LlmError, match="no OpenRouter key"):
        await ask("Name two mobs.", Mobs, call_id="#6")
    assert [(r["ref"], r["ok"], r["error"][:22]) for r in queued(tmp_path)] == [
        ("#5", 0, "no answer from ~deepse"), ("#6", 0, "no OpenRouter key: put")]


async def test_a_cancelled_call_is_a_row(monkeypatch, tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
    monkeypatch.setattr(vfg_llm, "client", httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    call = asyncio.create_task(ask("Name two mobs.", Mobs))
    await asyncio.sleep(0.01)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert [(r["ok"], r["error"]) for r in queued(tmp_path)] == [(0, "cancelled")]


async def test_a_component_off_the_list_or_a_bad_call_id_asks_nothing(monkeypatch, tmp_path):
    seen = serve(monkeypatch)
    for kw in ({"component": "dispatcher", "call_id": "#1"}, {"component": "brain", "call_id": ""},
               {"component": "brain", "call_id": "x" * 65}):
        with pytest.raises(ValueError):
            await structured("Name two mobs.", Mobs, **kw)
    with pytest.raises(TypeError):
        await structured("Name two mobs.", Mobs)  # both are required
    assert seen == [] and queued(tmp_path) == []
    assert [c.value for c in Component] == ["jev", "brain", "respond", "implementer", "planner", "alias",
                                            "conversation", "orchestrator"]


async def test_a_row_that_cannot_be_written_never_reaches_the_caller(monkeypatch, tmp_path, caplog):
    home = tmp_path / "not-a-folder"
    home.write_text("")
    monkeypatch.setenv("VFG_HOME", str(home))
    serve(monkeypatch, ok(GOOD, cost=0.004), httpx.Response(400, text="bad request"))
    with caplog.at_level(logging.WARNING, logger="vfg_llm"):
        answer = await ask("Name two mobs.", Mobs)
        with pytest.raises(LlmError, match="OpenRouter 400"):  # its own error, not the row's
            await ask("Name two mobs.", Mobs)
    assert answer.value.mobs[0].name == "Creeper"
    warned = [r.getMessage() for r in caplog.records if "usage row not queued" in r.getMessage()]
    assert len(warned) == 2 and '"cost_usd": 0.004' in warned[0] and '"ok": 0' in warned[1]


async def test_rows_from_calls_at_once_are_whole_lines(monkeypatch, tmp_path):
    serve(monkeypatch, *[ok(GOOD) for _ in range(20)])
    await asyncio.gather(*(ask("Name two mobs.", Mobs, call_id=f"#{n}") for n in range(20)))
    assert sorted(r["ref"] for r in queued(tmp_path)) == sorted(f"#{n}" for n in range(20))
