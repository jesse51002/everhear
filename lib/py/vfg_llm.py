"""structured(): one prompt in, one validated pydantic object out, through OpenRouter.

Every EverHear part that wants a structured answer from a model calls this. The
output class's JSON schema goes as a json_schema response format, routed only to
providers that support it (an openai/ model gets it strict, as OpenAI demands: every
object closed, every property required); an answer that still fails validation is sent back once
with pydantic's errors. A 429, a 5xx or a dropped connection is retried once.
Anything else is an LlmError, whose message never carries the key. Reasoning is
off unless asked for: speed comes first. `providers` names OpenRouter providers to try
first, in that order, falling back to the rest; without it OpenRouter chooses.

The key: OPEN_ROUTER_API from the environment; else the file VFG_OPENROUTER_KEY
names (environment, then ~/.vfg/config; default ~/.vfg/openrouter.key), which holds
the key alone.
"""

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
RETRY_WAIT = 1.0  # seconds

# Sent through instead of a fresh client per call: a long-running caller sets it to
# keep one connection pool warm, and the tests set it to a MockTransport client.
client: httpx.AsyncClient | None = None

T = TypeVar("T", bound=BaseModel)


class Answer(BaseModel, Generic[T]):
    value: T
    cost: float  # dollars: OpenRouter's usage.cost, summed over every attempt
    ms: int


class LlmError(Exception):
    """The call failed for good. Safe to log: the message never holds the key."""


def strict_schema(schema: dict) -> dict:
    """The schema as OpenAI's structured outputs take it: every object with properties
    closed (additionalProperties false) and every property required. Other providers get
    the schema as pydantic writes it."""
    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    return schema


async def structured(
    prompt: str,
    output: type[T],
    *,
    system: str | None = None,
    model: str = "~deepseek/deepseek-v4-flash-latest",
    reasoning: bool | str = False,
    timeout: float = 30,
    providers: list[str] | None = None,
) -> Answer[T]:
    """Ask `model` for an `output`. `timeout` bounds the whole call, retries included.
    `reasoning=True` leaves the model's own default, which may be to think first; a string
    ("low", "medium", "high") asks for that effort, for a model that cannot turn it off.
    `providers`: OpenRouter provider slugs to try first, in order ("parasail", "wafer"),
    falling back to the others; None leaves the choice to OpenRouter."""
    key = _key()
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    schema = output.model_json_schema()
    if model.startswith("openai/"):
        schema = strict_schema(schema)
    body = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": output.__name__, "schema": schema},
        },
        "provider": {"require_parameters": True},
    }
    if providers:
        body["provider"] = {"order": list(providers), "allow_fallbacks": True, "require_parameters": True}
    if isinstance(reasoning, str):
        body["reasoning"] = {"effort": reasoning}
    elif not reasoning:
        body["reasoning"] = {"enabled": False}
    t0, cost = time.perf_counter(), 0.0
    try:
        async with asyncio.timeout(timeout), _session() as http:
            for attempt in (1, 2):
                got = await _post(http, body, key, timeout)
                cost += float((got.get("usage") or {}).get("cost") or 0)
                text = _content(got)
                try:
                    value = output.model_validate_json(text)
                    break
                except ValidationError as e:
                    errors = "\n".join(
                        f"- {'.'.join(map(str, x['loc'])) or 'the answer'}: {x['msg']}"
                        for x in e.errors(include_url=False)
                    )
                if attempt == 2:
                    raise LlmError(f"{model} twice failed to match {output.__name__}:\n{errors}")
                logger.warning("%s did not match %s; asking once more", model, output.__name__)
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That answer does not match the schema:\n{errors}\n"
                                                "Answer again with the corrected JSON only."},
                ]
    except TimeoutError:
        raise LlmError(f"no answer from {model} within {timeout:g} s") from None
    return Answer[output](value=value, cost=cost, ms=int((time.perf_counter() - t0) * 1000))


def _session():
    return contextlib.nullcontext(client) if client else httpx.AsyncClient()


async def _post(http: httpx.AsyncClient, body: dict, key: str, timeout: float) -> dict:
    headers = {"Authorization": f"Bearer {key}", "X-Title": "EverHear"}
    for attempt in (1, 2):
        try:
            r = await http.post(ENDPOINT, json=body, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            why = f"OpenRouter {r.status_code}: {r.text[:500]}"
            again = r.status_code == 429 or r.status_code >= 500
        except httpx.TransportError as e:
            why, again = f"OpenRouter unreachable: {type(e).__name__} {e}", True
        except ValueError:
            why, again = "OpenRouter sent a 200 that is not JSON", True
        why = why.replace(key, "<key>")
        if not again or attempt == 2:
            raise LlmError(why)
        logger.warning("%s; retrying in %g s", why, RETRY_WAIT)
        await asyncio.sleep(RETRY_WAIT)


def _content(got: dict) -> str:
    try:
        return got["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LlmError(f"OpenRouter sent no answer: {str(got.get('error') or got)[:500]}") from None


def _key() -> str:
    if key := os.environ.get("OPEN_ROUTER_API", "").strip():
        return key
    home = Path(os.environ.get("VFG_HOME") or "~/.vfg").expanduser()
    path = Path(
        os.environ.get("VFG_OPENROUTER_KEY")
        or _config(home / "config").get("VFG_OPENROUTER_KEY")
        or home / "openrouter.key"
    ).expanduser()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if not key:
        raise LlmError(f"no OpenRouter key: put it alone in {path}, or set OPEN_ROUTER_API")
    return key


def _config(path: Path) -> dict[str, str]:
    """KEY=value lines, parsed as toolkit/vfg parses ~/.vfg/config."""
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in map(str.strip, lines):
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (s.strip() for s in line.removeprefix("export ").split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out
