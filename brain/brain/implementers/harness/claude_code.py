"""The Claude Code adapter: an implementer is one Claude Agent SDK client, the CLI as the main agent
with nothing around it, on the provider the settings name (config.py):

- **deepseek** and **openrouter**: the CLI talks to the relay (relay.py), which puts thinking off on
  every request and counts each one's tokens, and the relay to the provider's Claude-compatible
  address, with the provider's key. Every model the CLI may call is the one model.
- **anthropic**: Haiku 5.5 on the owner's Claude subscription, through the CLI's own login in
  ~/.claude, at low effort, as before this adapter: no relay, never an API key, and each request's
  tokens read from the CLI's assistant messages (one per message id).

The guard is the CLI's PreToolUse hook, and the only gate: the CLI runs in bypassPermissions, where
its own checks never refuse and a hook's deny still blocks (hooks run first). The CLI will not run
bypassPermissions as root; there it runs dontAsk, every use of each tool allowed.

A message is a second query() on the run's open client. Measured on the CLI (2.1.294, 2026-10-12):
one sent while a tool runs is read as soon as the tool calls in flight finish, in the same turn,
which ends in one result; one that lands as the model ends its turn starts the next turn on the
same connection, with a result of its own. With --replay-user-messages the CLI echoes each user
message as it reads it (not as it arrives), so the run knows which are unread: it ends (Done) at a
result only when every message sent is read, with the turns of all its results summed. The CLI's
max_turns counts each query afresh, a resumed session's too: the runner counts a run's turns.

Why the relay counts the tokens, and not the CLI's messages: the relay sees every request the CLI
makes, the ones beside the conversation (a session's title, a summary) too, with the provider's own
final counts; an assistant message carries its request's usage as it stood when the message was
written, once per content block, and the CLI's side requests never appear as messages at all."""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage,
                              SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage)

from ..config import ImplementerConfig, log, model_price, provider_routing
from ..guard import GuardContext, brief, gate
from .base import ERROR, OK, STOPPED, TURNS, Compacted, Done, Event, Request, Session, Text, ToolCall, ToolResult
from .relay import Relay

TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]

# How long stop() waits for the CLI to take an interrupt; and how long, at most, a run's end waits for
# the relay to finish its requests still in flight, so each is counted in its run (it waits only
# while one is: Relay.settle returns at once when none is).
INTERRUPT_TIMEOUT_S = 5.0
SETTLE_S = 5.0
# A turn that ends with a message unread is followed by the turn that reads it; when none starts
# within this long, the run ends and the runner resumes the session with the message.
UNREAD_GRACE_S = 5.0

# The model variables the CLI reads: every one is set to the one model, so no request goes to a
# model name the provider would map elsewhere.
MODEL_VARIABLES = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                   "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")

# ClaudeAgentOptions -> a client: connect(), query(prompt), receive_response(), interrupt(), disconnect().
ClientFactory = Callable[[ClaudeAgentOptions], Any]


def session_env(source: Mapping[str, str]) -> dict[str, str]:
    """The CLI's environment: no API key and no address of a provider (the adapter sets those it
    needs), and nothing of a Claude session that started the brain, whose variables describe that
    session, not this one."""
    env = {}
    for k, v in source.items():
        if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            continue
        if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION"):
            continue
        if k in ("NODE_OPTIONS", "DEBUG"):  # the TypeScript SDK took these out of the CLI's environment too
            continue
        if k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN":
            continue
        env[k] = v
    return env


def scrub_environ() -> None:
    """The Python SDK lays options.env over this process's own environment, so what session_env
    leaves out is taken out of this process's environment before a CLI starts. The brain, vfg and
    OpenRouter use none of it."""
    keep = session_env(os.environ)
    for k in list(os.environ):
        if k not in keep:
            del os.environ[k]


def context_window(model: str) -> int | None:
    """How many tokens the model reads, from the price table; None when it does not say."""
    m = model_price(model)
    return m.context if m else None


def cli_env(cfg: ImplementerConfig, address: str | None) -> dict[str, str]:
    """options.env: for deepseek and openrouter, the relay's address for this run, the provider's
    key, the one model for every model the CLI may call, no traffic but the model's, and the
    auto-compact window at the top of the model's context (CLAUDE_CODE_MAX_CONTEXT_TOKENS tells the
    CLI how much a model it does not know reads, and the window is capped by that)."""
    env = session_env(os.environ)
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "vfg-brain/0.1"
    env["PYTHONPYCACHEPREFIX"] = os.path.join(cfg.brain_dir, "pycache")  # py_compile leaves no __pycache__ in made/
    # No "<total_tokens>N tokens left</total_tokens>" note on every turn (a 15M countdown the CLI
    # adds by default): an implementer has no use for it, and the only text the CLI put before
    # each turn on which DeepSeek wrote a summary of the conversation in place of its report.
    env["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] = "off"
    if cfg.provider == "anthropic":
        return env
    env.update({
        "ANTHROPIC_BASE_URL": address or cfg.base_url,
        "ANTHROPIC_AUTH_TOKEN": cfg.key,
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",  # never the owner's login on another provider
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    })
    env.update({k: cfg.model for k in MODEL_VARIABLES})
    window = context_window(cfg.model)
    if window:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(window)
    return env


def as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def guard_hook(g: GuardContext, denied: set | None = None, effort: bool = True):
    """The PreToolUse hook: the guard's gate() on every tool call. A refusal is the SDK's deny, its
    reason the guard's one line, which the model gets as the tool's error, and is noted in `denied`
    (its tool_use_id, and its tool and input) so the run's end can tell the guard's refusals from
    the CLI's own; a call let through (noted and recorded by gate()) gets {}, which in
    bypassPermissions lets it run. `effort`: the turn's effort is logged once,
    on the anthropic provider only (the relay drops it on the others)."""
    seen_effort: set[str] = set()
    who = "implementer %s" % (g.task or "-")

    async def hook(inp: dict, tool_use_id: str | None, context: Any) -> dict:
        if inp.get("hook_event_name") != "PreToolUse":
            return {}
        # The effort a turn actually runs at, after any downgrade for its model: logged once.
        said = inp.get("effort")
        level = said.get("level") if effort and isinstance(said, dict) else None
        if level and level not in seen_effort:
            seen_effort.add(level)
            log("effort", "%s runs at %s" % (who, level))
        call = {"tool_name": inp.get("tool_name"), "tool_input": inp.get("tool_input")}
        reason = await gate(call, g)
        if reason:
            log("guard", "denied %s %s: %s (%s)" % (who, call["tool_name"], reason, brief(call["tool_input"])))
            if denied is not None:
                denied.update([tool_use_id, (call["tool_name"], brief(call["tool_input"]))])
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        return {}

    return hook


def build_options(cfg: ImplementerConfig, system: Path, guard: GuardContext, address: str | None = None,
                  resume: str | None = None, stderr: Callable[[str], None] | None = None,
                  denied: set | None = None) -> ClaudeAgentOptions:
    """One implementer's options: the implementer is the main thread, its tools the CLI's own, the
    guard its only gate (its refusals noted in `denied`)."""
    anthropic = cfg.provider == "anthropic"
    root = as_root()
    return ClaudeAgentOptions(
        model=cfg.model,
        effort=cfg.effort if anthropic else None,
        thinking=None if anthropic else {"type": "disabled"},  # the CLI leaves it out anyway: the relay sets it
        system_prompt={"type": "file", "path": str(system)},
        cwd=cfg.repo,
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        tools=list(TOOLS),
        # As root the CLI refuses bypassPermissions: dontAsk, with every use of each tool allowed.
        allowed_tools=list(TOOLS) if root else [],
        permission_mode="dontAsk" if root else "bypassPermissions",
        hooks={"PreToolUse": [HookMatcher(hooks=[guard_hook(guard, denied, effort=anthropic)])]},
        env=cli_env(cfg, address),
        # The prompt cache's life (VFG_IMPL_CACHE_TTL) as a flag setting, on Anthropic's own cache:
        # the CLI reads it from --settings whatever setting_sources says.
        settings=json.dumps({"promptCacheTtl": cfg.cache_ttl}) if anthropic else None,
        # each user message echoed as the CLI reads it: how a run knows a message is not unread
        extra_args={"replay-user-messages": None},
        stderr=stderr,
        max_turns=cfg.max_turns,
        resume=resume,
    )


def sdk_client(options: ClaudeAgentOptions) -> ClaudeSDKClient:
    """The real client: the default client_factory."""
    scrub_environ()
    return ClaudeSDKClient(options)


class SdkUsage:
    """Each request's tokens from the CLI's assistant messages, for the anthropic provider (no
    relay): one request per message id, its counts the largest any of its messages carried, given
    once the next message id comes, or at the result."""

    def __init__(self, model: str):
        self.model = model
        self._id: str | None = None
        self._usage: dict = {}
        self._at = 0.0
        self._done: set[str] = set()

    def see(self, msg: AssistantMessage) -> list[Request]:
        if not msg.message_id or not isinstance(msg.usage, dict) or msg.message_id in self._done:
            return []
        out = []
        if msg.message_id != self._id:
            out = self.flush()
            self._id, self._usage, self._at = msg.message_id, {}, time.time()
        for k, v in msg.usage.items():
            if isinstance(v, int):
                self._usage[k] = max(v, self._usage.get(k) or 0)
        return out

    def flush(self) -> list[Request]:
        if self._id is None:
            return []
        u, mid = self._usage, self._id
        self._done.add(mid)
        self._id, self._usage = None, {}
        return [Request(model=self.model, input=u.get("input_tokens") or 0,
                        cache_read=u.get("cache_read_input_tokens") or 0,
                        cache_write=u.get("cache_creation_input_tokens") or 0,
                        output=u.get("output_tokens") or 0, at=self._at, id=mid)]


class Mapper:
    """The SDK's messages as the harness's events, for one run."""

    def __init__(self, task: str, provider: str, model: str, session: str | None = None,
                 guarded: set | None = None):
        """`guarded`: the guard's refusals in this run (guard_hook's `denied`), which the CLI lists
        among its permission denials too."""
        self.task = task
        self.provider = provider
        self.session = session
        self.guarded = guarded if guarded is not None else set()
        self.usage = SdkUsage(model) if provider == "anthropic" else None

    def events(self, msg: Any) -> list[Event]:
        out: list[Event] = []
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            data = msg.data or {}
            out += self._session(data.get("session_id"))
            log("implementer", "%s init: session %s, model %s, permission mode %s, key source %s" % (
                self.task, data.get("session_id"), data.get("model"), data.get("permissionMode"),
                data.get("apiKeySource")))
            src = data.get("apiKeySource")
            if self.provider == "anthropic" and src and src != "none":
                out.append(Done(STOPPED, "the CLI would use an API key (%s), not the subscription, so it was stopped"
                                % src))
        elif isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
            data = msg.data or {}
            meta = data.get("compact_metadata") or data.get("compactMetadata") or {}
            out.append(Compacted(str(meta.get("trigger") or ""), meta.get("pre_tokens", meta.get("preTokens"))))
        elif isinstance(msg, SystemMessage) and (msg.data or {}).get("status") == "compacting":
            log("implementer", "%s: the CLI is compacting its session" % self.task)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    out.append(ToolResult(b.tool_use_id, bool(b.is_error), _text(b.content)[:300]))
        elif isinstance(msg, AssistantMessage):
            if self.usage is not None:
                out += self.usage.see(msg)
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    out.append(ToolCall(b.name, b.input if isinstance(b.input, dict) else {}, b.id))
                elif isinstance(b, TextBlock) and b.text.strip():
                    out.append(Text(b.text))
        elif isinstance(msg, ResultMessage):
            if self.usage is not None:
                out += self.usage.flush()
            out += self._session(msg.session_id)
            out.append(done_of(msg, self.guarded))
        return out

    def _session(self, session: str | None) -> list[Event]:
        if not session or session == self.session:
            return []
        self.session = session
        return [Session(session)]


def _text(content: Any) -> str:
    """A tool result's or a user message's text: a string, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in content)
    return ""


def echoed(msg: Any) -> str | None:
    """The text of a user message the CLI echoes as it reads it (--replay-user-messages): a prompt or
    a message, never a tool's result."""
    if not isinstance(msg, UserMessage) or msg.parent_tool_use_id:
        return None
    if isinstance(msg.content, str):
        return msg.content
    if any(isinstance(b, ToolResultBlock) for b in msg.content):
        return None
    return "\n".join(b.text for b in msg.content if isinstance(b, TextBlock))


def done_of(r: ResultMessage, guarded: set = frozenset()) -> Done:
    """How the CLI's result says the run ended. Its permission denials hold the guard's refusals
    too; `refused` is the rest, the CLI's own, which bypassPermissions should leave none of."""
    text = (r.result or "").strip()
    refused = ["%s %s" % (d.get("tool_name"), brief(d.get("tool_input"))) for d in r.permission_denials or []
               if isinstance(d, dict) and d.get("tool_use_id") not in guarded
               and (d.get("tool_name"), brief(d.get("tool_input"))) not in guarded]
    common = {"turns": r.num_turns or 0, "errors": list(r.errors or []), "seconds": (r.duration_ms or 0) / 1000,
              "refused": refused}
    if r.subtype == "error_max_turns":
        return Done(TURNS, text, **common)
    if r.is_error or r.subtype != "success":
        return Done(ERROR, text or "; ".join(r.errors or []) or r.subtype, **common)
    return Done(OK, text, **common)


_END = object()


class _Broke:
    def __init__(self, error: BaseException):
        self.error = error


class ClaudeCodeRun:
    """One run on a client kept open for it: the first query, then each message a second query on
    it. Its events come from the client's messages and from the relay's requests, through one
    queue; it ends at a result with no message unread."""

    def __init__(self, task: str, client: Any, mapper: Mapper, relay: Relay | None, address: str | None):
        self.task = task
        self.client = client
        self.mapper = mapper
        self.relay = relay
        self.address = address
        self._q: asyncio.Queue = asyncio.Queue()
        self._pump: asyncio.Task | None = None
        # the messages sent and not yet echoed by the CLI as read, oldest first
        self._unread: list[str] = []
        self.ended = False
        self.stopping = False  # stop() was called: the next result ends it, read or not

    @property
    def session(self) -> str | None:
        return self.mapper.session

    def request(self, r: Request) -> None:
        """The relay's sink: one of this run's requests, its tokens counted."""
        if not self.ended:
            self._q.put_nowait(r)

    async def begin(self, prompt: str) -> None:
        await self.client.connect()
        await self.client.query(prompt)
        self._pump = asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        turns, seconds = 0, 0.0
        held: Done | None = None  # a turn's end with messages unread: the next turn should read them
        messages = self.client.receive_messages().__aiter__()
        nxt = None
        try:
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(messages.__anext__())
                finished, _ = await asyncio.wait({nxt}, timeout=UNREAD_GRACE_S if held is not None else None)
                if not finished:  # no turn took them up: the run ends, and the runner resumes with them
                    log("implementer", "%s: no turn read %d message(s) within %s s: the run ends without them"
                        % (self.task, len(self._unread), UNREAD_GRACE_S))
                    held.unread = list(self._unread)
                    await self._end(held)
                    return
                got, nxt = nxt, None
                try:
                    msg = got.result()
                except StopAsyncIteration:
                    return
                held = None  # the CLI goes on: a new turn
                text = echoed(msg)
                if text is not None:
                    self._read_one(text)
                    continue
                for ev in self.mapper.events(msg):
                    if isinstance(ev, Done):
                        turns += ev.turns
                        seconds += ev.seconds
                        ev.turns, ev.seconds = turns, seconds
                        if self._unread and ev.ended != STOPPED and not self.stopping:
                            log("implementer", "%s: a turn ended with %d message(s) unread: the next turn reads them"
                                % (self.task, len(self._unread)))
                            held = ev
                            continue
                        await self._end(ev)
                        return
                    self._q.put_nowait(ev)
        except Exception as e:
            self._q.put_nowait(_Broke(e))
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()
            self.ended = True
            self._q.put_nowait(_END)

    async def _end(self, done: Done) -> None:
        """The run's Done: from here a message is a resume; the relay's last requests counted first."""
        self.ended = True
        if self.stopping and self._unread and not done.unread:
            done.unread = list(self._unread)  # stopped with messages unread: the runner keeps them
        if self.relay is not None and not await self.relay.settle(self.address, SETTLE_S):
            log("relay", "%s: a request was still in flight as the run ended" % self.task)
        self._q.put_nowait(done)

    def _read_one(self, text: str) -> None:
        """The CLI echoed a user message as read: every unread message in it, oldest first (the CLI may
        read two queued messages as one)."""
        echo = text.strip()
        read = [m for m in self._unread if m.strip() and m.strip() in echo]
        for m in read:
            self._unread.remove(m)
            log("implementer", "%s read its message: %s" % (self.task, " ".join(m.split())[:120]))

    async def events(self):
        while True:
            ev = await self._q.get()
            if ev is _END:
                return
            if isinstance(ev, _Broke):
                raise ev.error
            yield ev
            if isinstance(ev, Done):
                return

    async def send(self, text: str) -> bool:
        """A second query on the open client; False once the run has ended."""
        if self.ended:
            return False
        self._unread.append(text)
        try:
            await self.client.query(text)
        except Exception as e:
            self._unread.remove(text)
            log("implementer", "%s: a message could not be sent: %s: %s" % (self.task, type(e).__name__, e))
            return False
        return True

    async def stop(self) -> None:
        self.stopping = True
        try:
            await asyncio.wait_for(self.client.interrupt(), INTERRUPT_TIMEOUT_S)
        except Exception as e:
            log("implementer", "%s: interrupt: %s" % (self.task, e))

    async def close(self) -> None:
        self.ended = True
        if self._pump is not None and not self._pump.done():
            self._pump.cancel()
            await asyncio.wait({self._pump}, timeout=INTERRUPT_TIMEOUT_S)
        if self.relay is not None and self.address:
            self.relay.forget(self.address)
        try:
            await self.client.disconnect()
        except Exception as e:
            log("implementer", "%s: disconnect: %s" % (self.task, e))


class ClaudeCode:
    name = "claude-code"

    def __init__(self, cfg: ImplementerConfig, client_factory: ClientFactory | None = None):
        """`client_factory` makes the SDK client (tests pass a fake)."""
        self.cfg = cfg
        self.client_factory = client_factory or sdk_client
        self.relay = Relay(cfg.base_url, extra={"provider": r} if (r := provider_routing(cfg)) else None) \
            if cfg.provider != "anthropic" else None
        self._said_root = False

    async def start(self, task: str, system: Path, brief: str, guard: GuardContext) -> ClaudeCodeRun:
        return await self._open(task, system, brief, guard, None)

    async def resume(self, task: str, system: Path, session: str, text: str, guard: GuardContext) -> ClaudeCodeRun:
        return await self._open(task, system, text, guard, session)

    async def close(self) -> None:
        if self.relay is not None:
            await self.relay.stop()

    async def _open(self, task: str, system: Path, prompt: str, guard: GuardContext,
                    resume: str | None) -> ClaudeCodeRun:
        if self.cfg.provider != "anthropic" and not self.cfg.key:
            raise RuntimeError("no key for %s: %s" % (self.cfg.provider, "set DEEPSEEK_API_KEY"
                                                      if self.cfg.provider == "deepseek" else
                                                      "set OPEN_ROUTER_API, or put it alone in ~/.vfg/openrouter.key"))
        if as_root() and not self._said_root:
            self._said_root = True
            log("implementer", "running as root, where the CLI refuses bypassPermissions: dontAsk, every tool allowed")

        def stderr(line: str) -> None:
            if line.strip():
                log("cli", "%s %s" % (task, line.rstrip()))

        denied: set = set()
        run = ClaudeCodeRun(task, None, Mapper(task, self.cfg.provider, self.cfg.model, resume, denied), self.relay,
                            None)
        if self.relay is not None:
            await self.relay.start()
            run.address = self.relay.open(task, run.request)
        run.client = self.client_factory(build_options(self.cfg, system, guard, run.address, resume, stderr, denied))
        try:
            await run.begin(prompt)
        except BaseException:
            await run.close()
            raise
        return run
