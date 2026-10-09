"""The in-process MCP server "orch" with the reply tool, and the outbox that delivers replies."""
import asyncio
import time
from typing import Literal

from claude_agent_sdk import create_sdk_mcp_server, tool
from pydantic import BaseModel, Field

from .config import js_num, log
from .tasks import Store
from .vfg import VfgRunner, why

REPLY_TOOL = "mcp__orch__reply"

DESCRIPTION = (
    'Answer a task; it is the only way anything you say reaches the conversation. status "done" or '
    '"failed" closes the task (exactly once per task); "progress" is optional, for tasks that clearly '
    "take over a minute, at most once a minute. text: one or two plain sentences, at most 300 characters."
)


class ReplyArgs(BaseModel):
    task: str = Field(pattern=r"^T\d+$", description='The task id, like "T7".')
    status: Literal["progress", "done", "failed"]
    text: str = Field(min_length=1, description=(
        "What now exists or happened in the game, where, for whom, and how to stop it; "
        "or why it failed and what would work instead."))


def _untitled(schema):
    """Pydantic's JSON schema without the "title" it gives every field."""
    if isinstance(schema, dict):
        return {k: _untitled(v) for k, v in schema.items() if k != "title" or not isinstance(v, str)}
    if isinstance(schema, list):
        return [_untitled(v) for v in schema]
    return schema


def reply_schema() -> dict:
    """The reply tool's input schema, from ReplyArgs."""
    return _untitled(ReplyArgs.model_json_schema())


def handle_reply(store: Store, args: ReplyArgs) -> dict:
    """The reply tool's logic, apart from the SDK so it can be tested."""
    r = store.reply(args.task, args.status, args.text)
    if not r.ok:
        log("reply", "refused %s %s: %s" % (args.task, args.status, r.error))
        return {"is_error": True, "content": [{"type": "text", "text": r.error}]}
    log("reply", "%s %s: %s" % (args.task, args.status, r.reply.text))
    closed = "" if args.status == "progress" else " %s is now closed." % args.task
    return {"content": [{"type": "text", "text": "Recorded %s %s; it goes to the conversation.%s"
                         % (args.task, args.status, closed)}]}


def create_reply_server(store: Store) -> dict:
    @tool("reply", DESCRIPTION, reply_schema())
    async def reply(args):
        return handle_reply(store, ReplyArgs.model_validate(args))

    server = create_sdk_mcp_server(name="orch", version="0.1.0", tools=[reply])
    # Always loaded, never deferred behind tool search. The Python SDK passes the server's config,
    # less its instance, to the CLI's --mcp-config, whose sdk servers take alwaysLoad.
    return {**server, "alwaysLoad": True}


RETRY_MS = [2_000, 4_000, 8_000, 16_000]
STEADY_MS = 30_000


class Outbox:
    """Sends unsent replies in order, one `vfg orch reply <task> <status> <text>` each, retrying a
    failure after 2, 4, 8, 16 and then every 30 s. It never drops one; unsent replies are in
    state.json and survive a restart."""

    def __init__(self, store: Store, run: VfgRunner, retry_ms: list[float] | None = None,
                 steady_ms: float = STEADY_MS):
        self.store = store
        self.run = run
        self.retry_ms = RETRY_MS if retry_ms is None else retry_ms
        self.steady_ms = steady_ms
        self._running = False
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.Task | None = None
        self._failures = 0
        self._backoff = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop().create_task(self._work())

    def kick(self) -> None:
        """Something new to send: try now, unless waiting out a retry delay."""
        if not self._backoff:
            self._wake_up()

    async def stop(self) -> None:
        self._running = False
        self._wake_up()
        # A vfg call in flight may take a while; shutdown does not wait on it.
        if self._loop:
            await asyncio.wait({self._loop}, timeout=1.0)

    def pending(self) -> int:
        return len(self.store.unsent())

    async def drain(self, timeout_ms: float) -> bool:
        """Waits until everything is sent or the time is up. True when nothing is left."""
        end = time.monotonic() + timeout_ms / 1000
        self._wake_up()  # shutting down: try at once, then on the usual schedule
        while self.pending() > 0 and time.monotonic() < end:
            await asyncio.sleep(0.05)
        return self.pending() == 0

    def _wake_up(self) -> None:
        if self._wake:
            self._wake.set()

    async def _sleep_or_wake(self, ms: float) -> None:
        wake = self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(wake.wait(), ms / 1000)
        except TimeoutError:
            pass
        finally:
            if self._wake is wake:
                self._wake = None

    async def _work(self) -> None:
        while self._running:
            unsent = self.store.unsent()
            if not unsent:
                await self._sleep_or_wake(1_000)
                continue
            tid, reply = unsent[0]
            r = await self.run(["orch", "reply", tid, reply.status, reply.text], 60_000)
            if r.code == 0:
                reply.sent = True
                self.store.save()
                self._failures = 0
                log("outbox", "sent %s %s" % (tid, reply.status))
                continue
            delay = self.retry_ms[self._failures] if self._failures < len(self.retry_ms) else self.steady_ms
            self._failures += 1
            log("outbox", "vfg orch reply %s %s failed (%s); retrying in %s s" % (tid, reply.status, why(r), js_num(delay / 1000)))
            if self._running:
                self._backoff = True
                await self._sleep_or_wake(delay)
                self._backoff = False
