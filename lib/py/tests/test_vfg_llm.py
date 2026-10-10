"""structured() against an httpx.MockTransport: no network, no real key."""

import asyncio
import json
import re
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

import vfg_llm
from vfg_llm import LlmError, structured

KEY = "sk-or-v1-test-not-a-real-key"


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
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "cost": cost},
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
    answer = await structured("Name two mobs.", Mobs, system="Be brief.")

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
    assert KEY not in repr(answer)


async def test_providers_go_as_an_order_with_fallbacks(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD), ok(GOOD))
    await structured("Name two mobs.", Mobs, providers=["parasail", "wafer"])
    assert json.loads(seen[0].content)["provider"] == {
        "order": ["parasail", "wafer"], "allow_fallbacks": True, "require_parameters": True}
    await structured("Name two mobs.", Mobs, providers=[])  # empty is OpenRouter's own choice
    assert json.loads(seen[1].content)["provider"] == {"require_parameters": True}


async def test_reasoning_true_leaves_the_model_default(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD))
    await structured("Name two mobs.", Mobs, reasoning=True)
    assert "reasoning" not in json.loads(seen[0].content)


async def test_reasoning_effort_goes_as_effort(monkeypatch):
    seen = serve(monkeypatch, ok(GOOD))
    await structured("Name two mobs.", Mobs, reasoning="low")
    assert json.loads(seen[0].content)["reasoning"] == {"effort": "low"}


class Tagged(BaseModel):
    mob: Mob
    note: str = ""


async def test_openai_models_get_a_strict_schema(monkeypatch):
    seen = serve(monkeypatch, ok('{"mob": {"name": "Cow", "temper": "passive"}, "note": ""}'), ok(GOOD))
    await structured("Tag a mob.", Tagged, model="openai/gpt-6.1-sol")
    schema = json.loads(seen[0].content)["response_format"]["json_schema"]["schema"]
    for obj in (schema, schema["$defs"]["Mob"]):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    await structured("Name two mobs.", Mobs)  # any other model: the schema as pydantic writes it
    assert json.loads(seen[1].content)["response_format"]["json_schema"]["schema"] == Mobs.model_json_schema()


async def test_invalid_answer_repaired_on_second_try(monkeypatch):
    seen = serve(monkeypatch, ok(BAD, cost=0.001), ok(GOOD, cost=0.002))
    answer = await structured("Name two mobs.", Mobs)

    assert answer.value.mobs[1].name == "Cow"
    assert answer.cost == pytest.approx(0.003)
    retry = json.loads(seen[1].content)["messages"]
    assert retry[1] == {"role": "assistant", "content": BAD}
    assert retry[2]["role"] == "user"
    assert "mobs.0.temper" in retry[2]["content"] and "'hostile'" in retry[2]["content"]


async def test_still_invalid_raises(monkeypatch):
    seen = serve(monkeypatch, ok(BAD), ok("not json at all"))
    with pytest.raises(LlmError, match="twice failed to match Mobs"):
        await structured("Name two mobs.", Mobs)
    assert len(seen) == 2


@pytest.mark.parametrize("first", [
    httpx.Response(503, text="upstream busy"),
    httpx.Response(429, text="slow down"),
    httpx.ConnectError("connection reset"),
], ids=["503", "429", "dropped"])
async def test_transient_failure_retried_once(monkeypatch, first):
    seen = serve(monkeypatch, first, ok(GOOD))
    answer = await structured("Name two mobs.", Mobs)
    assert answer.value.mobs[0].temper == "hostile"
    assert len(seen) == 2


async def test_two_503s_raise(monkeypatch):
    seen = serve(monkeypatch, httpx.Response(503), httpx.Response(503))
    with pytest.raises(LlmError, match="503"):
        await structured("Name two mobs.", Mobs)
    assert len(seen) == 2


async def test_401_raises_without_the_key(monkeypatch):
    # The server echoing the key back must not put it in the message.
    seen = serve(monkeypatch, httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(LlmError, match="401") as e:
        await structured("Name two mobs.", Mobs)
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert len(seen) == 1


async def test_timeout_bounds_the_call(monkeypatch):
    async def slow(request):
        await asyncio.sleep(5)
    monkeypatch.setattr(vfg_llm, "client", httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(LlmError, match="within 0.05 s"):
        await structured("Name two mobs.", Mobs, timeout=0.05)


async def test_key_from_the_file_the_config_names(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_ROUTER_API")
    keyfile = tmp_path / "keys" / "openrouter"
    keyfile.parent.mkdir()
    keyfile.write_text("  sk-or-from-file\n")
    (tmp_path / "config").write_text(
        f'# a comment\nVFG_OWNER=Steve\nexport VFG_OPENROUTER_KEY="{keyfile}"\n')
    seen = serve(monkeypatch, ok(GOOD))
    await structured("Name two mobs.", Mobs)
    assert seen[0].headers["authorization"] == "Bearer sk-or-from-file"


async def test_missing_key_names_the_file_to_create(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_ROUTER_API")
    seen = serve(monkeypatch)
    with pytest.raises(LlmError, match=re.escape(str(tmp_path / "openrouter.key"))):
        await structured("Name two mobs.", Mobs)
    assert seen == []
