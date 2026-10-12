"""structured(): one prompt in, one validated pydantic object out, through OpenRouter.

Every EverHear part that wants a structured answer from a model calls this. The
output class's JSON schema goes as a json_schema response format, routed only to
providers that support it (an openai/ model gets it strict, as OpenAI demands: every
object closed, every property required); an answer that still fails validation is sent back once
with pydantic's errors. A 429, a 5xx or a dropped connection is retried once.
Anything else is an LlmError, whose message never carries the key. Reasoning is
off unless asked for: speed comes first. `providers` names OpenRouter providers to try
first, in that order, falling back to the rest; without it OpenRouter chooses.

What goes before the prompt can be cached: `cached` is a fixed opening, or several blocks
(the brain's opening, then its conversation a minute-block at a time), each marked for the
prompt cache or not. A model that bills cache writes, GPT-6.1 Sol among them, caches nothing
unless a block is marked, and caches up to the last mark it finds again.

Every call is in the one usage log (Specs/2026-10-11-Prototype_Central_LLM_Log): it names its
`component`, one of Component, and a `call_id` (the row's ref, at most 64 characters), and
records exactly one row, answered or failed: its tokens and cost summed over every attempt, how
long it took, `ok` and, when it failed, `error`, the reason, short. A call that timed out or was
cancelled with an attempt in flight has no cost in its row (None, NULL in the table): OpenRouter
bills that attempt at a price it never told, so `error` says what the attempts that came back
cost instead. The row is appended to usage.pending.jsonl under usage.pending.lock in the folder
VFG_USAGE_QUEUE names in the environment (default $VFG_HOME, which also finds the key and so
stays put), in the format `vfg usage add` queues its own, so `vfg usage flush` (or the next
`vfg usage add`) with the same VFG_USAGE_QUEUE sends it to the box; a replay points it at its
own folder, so its rows reach the box only when sent on purpose. No SSH here; the row is written
in a worker thread, so the lock never holds up the event loop, once whatever happens to the call
meanwhile; a row that cannot be written is a warning, never an error of the call's. An LlmError
carries what the call spent, as an Answer does, and `cost_known` False when an attempt cut off
in flight is not in it.

The key: OPEN_ROUTER_API from the environment; else the file VFG_OPENROUTER_KEY
names (environment, then ~/.vfg/config; default ~/.vfg/openrouter.key), which holds
the key alone.
"""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
RETRY_WAIT = 1.0  # seconds
MAX_MARKS = 4     # cache_control marks one request may carry (Anthropic's limit; OpenRouter passes it on)
REF_MAX = 64      # a call_id, as `vfg usage add --ref`
ERROR_MAX = 300   # a failed call's reason in its row, in characters
QUEUE, QUEUE_LOCK = "usage.pending.jsonl", "usage.pending.lock"  # in $VFG_USAGE_QUEUE: toolkit/vfg's Pending

# Sent through instead of a fresh client per call: a long-running caller sets it to
# keep one connection pool warm, and the tests set it to a MockTransport client.
client: httpx.AsyncClient | None = None

T = TypeVar("T", bound=BaseModel)


class Answer(BaseModel, Generic[T]):
    value: T
    cost: float  # dollars: OpenRouter's usage.cost, summed over every attempt
    ms: int
    # OpenRouter's usage, summed over every attempt: the prompt, the part of it read from the
    # cache (cached / prompt is the cache rate), and the answer
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0


class Component(StrEnum):
    """Who made a call: a usage row's `component`. The same list as `vfg usage add --component`
    (toolkit/vfg COMPONENTS) and the usage table's CHECK (bridge/vfg_bridge/db.py)."""
    JEV = "jev"
    BRAIN = "brain"
    RESPOND = "respond"
    IMPLEMENTER = "implementer"    # the Agent SDK's runs, recorded by the brain, not here
    PLANNER = "planner"            # valid only for old rows: the action planner, gone with the brain loop
    ALIAS = "alias"                # the incantation alias call
    CONVERSATION = "conversation"  # valid only for old rows
    ORCHESTRATOR = "orchestrator"  # valid only for old rows: the orchestrator call, folded into the brain's loop


class LlmError(Exception):
    """The call failed for good. Safe to log: the message never holds the key. What it spent
    before it failed, summed over every attempt as an Answer's is, is on it too. `cost_known` is
    False when it timed out with an attempt in flight, which OpenRouter bills all the same: `cost`
    is then what the attempts that came back cost, the least it spent (its row's cost is None)."""
    cost: float = 0.0
    cost_known: bool = True
    ms: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0


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
    component: Component | str,
    call_id: str,
    system: str | None = None,
    model: str = "~deepseek/deepseek-v4-flash-latest",
    reasoning: bool | str = False,
    timeout: float = 30,
    providers: list[str] | None = None,
    cached: str | Sequence[tuple[str, bool]] | None = None,
) -> Answer[T]:
    """Ask `model` for an `output`. `timeout` bounds the whole call, retries included.
    `reasoning=True` leaves the model's own default, which may be to think first; a string
    ("low", "medium", "high") asks for that effort, for a model that cannot turn it off.
    `providers`: OpenRouter provider slugs to try first, in order ("parasail", "wafer"),
    falling back to the others; None leaves the choice to OpenRouter.
    `cached`: what the user message holds before `prompt`: one fixed opening, marked for the
    prompt cache (cache_control), or blocks as (text, marked), at most MAX_MARKS marked. A
    cached 9K-token opening cost $0.0015 a Sol call instead of $0.023, and a conversation
    marked every five minute-blocks kept 98% of a batch call's prompt cached.
    `component` and `call_id` name the call's usage row, which it records whatever happens; a
    component not on the list, or a call_id empty or over REF_MAX, is a ValueError, before
    anything is asked."""
    component = Component(component)
    if not 0 < len(call_id) <= REF_MAX:
        raise ValueError(f"call_id is 1 to {REF_MAX} characters, not {len(call_id)}")
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": user_content(prompt, cached)})
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
        "usage": {"include": True},
    }
    if providers:
        body["provider"] = {"order": list(providers), "allow_fallbacks": True, "require_parameters": True}
    if isinstance(reasoning, str):
        body["reasoning"] = {"effort": reasoning}
    elif not reasoning:
        body["reasoning"] = {"enabled": False}
    t0, cost, tokens, flight = time.perf_counter(), 0.0, [0, 0, 0], _Flight()
    key, failed, error = "", None, "cancelled"  # the row's reason when the call ends any other way
    try:
        try:
            key = _key()
            async with asyncio.timeout(timeout), _session() as http:
                for attempt in (1, 2):
                    got = await _post(http, body, key, timeout, flight)
                    usage = got.get("usage") or {}
                    cost += float(usage.get("cost") or 0)
                    tokens[0] += int(usage.get("prompt_tokens") or 0)
                    tokens[1] += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                    tokens[2] += int(usage.get("completion_tokens") or 0)
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
        error = None
    except LlmError as e:
        failed, error = e, str(e)
        raise
    except Exception as e:  # not the model's failure but a bug: in the row all the same, and raised as it was
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        if failed is not None:
            failed.cost, failed.cost_known, failed.ms = cost, not flight.out, ms
            failed.prompt_tokens, failed.cached_tokens, failed.completion_tokens = tokens
        if flight.out:  # cut off with an attempt in flight: billed, at a price nobody told this call
            error = f"{error}; ${cost:.6f} spent before it, the attempt in flight not counted"
        await _queued(usage_row(component, call_id, model, None if flight.out else cost, ms, *tokens,
                                error=None if error is None else _short(error.replace(key, "<key>") if key else error)))
    return Answer[output](value=value, cost=cost, ms=ms,
                          prompt_tokens=tokens[0], cached_tokens=tokens[1], completion_tokens=tokens[2])


def usage_row(component: Component | str, call_id: str, model: str, cost: float | None, ms: int,
              prompt_tokens: int, cached_tokens: int, completion_tokens: int, *, error: str | None) -> dict:
    """One call's usage row as `vfg usage add` queues one (toolkit/vfg usage_row), its time now,
    with what only a call knows: `ok` (1 answered, 0 failed), `error` (None when ok) and `ms`.
    `cost` None is not known (an attempt cut off in flight), as `vfg usage add` without --cost.
    No cache writes: OpenRouter's usage does not say them."""
    return {"at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "component": str(component), "model": model, "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens, "cost_usd": None if cost is None else round(cost, 9), "ref": call_id,
            "cache_read_tokens": cached_tokens, "cache_write_tokens": None,
            "ok": int(error is None), "error": error, "ms": ms}


def record(row: dict) -> None:
    """Append `row` to usage.pending.jsonl in the queue's folder (_queue_dir), under
    usage.pending.lock as toolkit/vfg's Pending takes it, in one O_APPEND write: the queue `vfg
    usage flush` sends. Never raises and never waits on the box: a row that cannot be written is a
    warning that holds it. It waits on the lock, so structured() runs it in a thread (_queued)."""
    try:
        home = _queue_dir()
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        line = (json.dumps(row) + "\n").encode("utf-8")
        lock = os.open(home / QUEUE_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fd = os.open(home / QUEUE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        finally:
            os.close(lock)  # and the lock with it
    except Exception as e:  # noqa: BLE001 — the call's answer, or its own error, goes on up as it is
        with contextlib.suppress(Exception):
            logger.warning("usage row not queued (%s: %s): %s", type(e).__name__, e, json.dumps(row, default=str))


async def _queued(row: dict) -> None:
    """record(row) in a worker thread, so the queue's lock never holds up the event loop. The row
    is written once whatever happens meanwhile: a cancel while it is written (shielded from it)
    waits for the write, which is quick, and is raised once it is done, never losing the row nor
    writing it twice; without a thread to write it in (the loop shutting down), it is written here."""
    write = asyncio.ensure_future(asyncio.to_thread(record, row))
    cancelled = None
    while not write.done():
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError as e:
            cancelled = e
        except Exception:  # noqa: BLE001 — record() never raises: no thread could be had for it
            break
    if not write.cancelled() and write.exception() is not None:
        record(row)
    if cancelled is not None:
        raise cancelled


class _Flight:
    """Whether a call has an attempt out, sent and its answer not back; _post keeps it. A call cut
    off so (its timeout, a cancel) is billed for that attempt, at a price nobody told it."""
    out = False


def _short(text: str) -> str:
    """A failed call's reason as its row keeps it: one line, at most ERROR_MAX characters."""
    text = re.sub(r"\s+", " ", text).strip() or "no reason given"
    return text if len(text) <= ERROR_MAX else text[:ERROR_MAX - 1] + "…"


def user_content(prompt: str, cached: str | Sequence[tuple[str, bool]] | None) -> str | list[dict]:
    """The user message's content: the prompt alone, or one text part per block, then the prompt.
    Each block keeps a part of its own, marked or not, so the text is the same from one call to
    the next wherever the marks move."""
    if not cached:
        return prompt
    blocks = [(cached, True)] if isinstance(cached, str) else list(cached)
    if sum(1 for _, marked in blocks if marked) > MAX_MARKS:
        raise ValueError(f"at most {MAX_MARKS} cached blocks may be marked")
    parts = []
    for text, marked in blocks:
        if text:
            parts.append({"type": "text", "text": text, **({"cache_control": {"type": "ephemeral"}} if marked else {})})
    return parts + [{"type": "text", "text": prompt}]


def _session():
    return contextlib.nullcontext(client) if client else httpx.AsyncClient()


async def _post(http: httpx.AsyncClient, body: dict, key: str, timeout: float, flight: _Flight) -> dict:
    """One attempt's request, sent again once after a 429, a 5xx or a dropped connection.
    `flight` is out from the moment a request goes until something comes back: not while it waits
    to send it again, when nothing is in flight."""
    headers = {"Authorization": f"Bearer {key}", "X-Title": "EverHear"}
    for attempt in (1, 2):
        try:
            flight.out = True
            r = await http.post(ENDPOINT, json=body, headers=headers, timeout=timeout)
            flight.out = False
            if r.status_code == 200:
                return r.json()
            why = f"OpenRouter {r.status_code}: {r.text[:500]}"
            again = r.status_code == 429 or r.status_code >= 500
        except httpx.TransportError as e:
            flight.out = False  # nothing came back, nor will: nothing to count
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


def _home() -> Path:
    return Path(os.environ.get("VFG_HOME") or "~/.vfg").expanduser()


def _queue_dir() -> Path:
    """The usage queue's folder: VFG_USAGE_QUEUE from the environment, as toolkit/vfg's `vfg usage`
    reads it, else $VFG_HOME."""
    return Path(os.environ.get("VFG_USAGE_QUEUE") or _home()).expanduser()


def _key() -> str:
    if key := os.environ.get("OPEN_ROUTER_API", "").strip():
        return key
    home = _home()
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
