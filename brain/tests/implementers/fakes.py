"""Fakes for the implementer tests: the vfg runner and the Claude Agent SDK client. No process starts."""
import asyncio
import inspect
from collections import deque

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, ToolUseBlock

from brain.implementers.vfg import VfgResult


def ok(stdout: str = "") -> VfgResult:
    return VfgResult(0, stdout, "")


def bad(stderr: str, code: int = 1) -> VfgResult:
    return VfgResult(code, "", stderr)


class FakeVfg:
    """The vfg runner. Records every call's argv and stdin; answers by the longest prefix given to
    on(), the latest of equal ones, else exit 0 with nothing. An answer is a VfgResult or a callable
    taking the argv (sync or async), which may raise or never return."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.stdin: dict[str, str | None] = {}  # by the argv's first word, the last one
        self.rules: list[tuple[tuple[str, ...], object]] = []

    def on(self, *prefix: str, answer) -> "FakeVfg":
        self.rules.insert(0, (prefix, answer))
        return self

    async def __call__(self, *args: str, timeout: float = 60, stdin: str | None = None) -> VfgResult:
        argv = list(args)
        self.calls.append(argv)
        self.stdin[argv[0]] = stdin
        for prefix, answer in sorted(self.rules, key=lambda r: -len(r[0])):
            if tuple(argv[:len(prefix)]) == prefix:
                if callable(answer):
                    got = answer(argv)
                    return await got if inspect.isawaitable(got) else got
                return answer
        return ok()

    def made(self, *prefix: str) -> list[list[str]]:
        return [a for a in self.calls if tuple(a[:len(prefix)]) == prefix]


async def never(argv):
    """An answer that never comes."""
    await asyncio.Event().wait()


def init(session: str = "s1", key: str = "none") -> SystemMessage:
    return SystemMessage(subtype="init", data={"type": "system", "subtype": "init", "session_id": session,
                                               "model": "claude-haiku-5-5", "permissionMode": "dontAsk",
                                               "apiKeySource": key})


def result(text: str | None = "Done: it is built.", session: str = "s1", subtype: str = "success",
           is_error: bool = False, errors: list[str] | None = None, model_usage: dict | None = None) -> ResultMessage:
    return ResultMessage(subtype=subtype, duration_ms=1200, duration_api_ms=900, is_error=is_error, num_turns=3,
                         session_id=session, result=text, errors=errors, model_usage=model_usage)


def tool(name: str, **inp) -> AssistantMessage:
    """An assistant turn that calls one tool: the client puts it through the PreToolUse hook."""
    return AssistantMessage(content=[ToolUseBlock(id="t%d" % id(inp), name=name, input=inp)],
                            model="claude-haiku-5-5")


def bash(command: str) -> AssistantMessage:
    return tool("Bash", command=command)


def haiku(inp: int, out: int, cost: float, read: int = 0, write: int = 0) -> dict:
    """A modelUsage entry, Haiku 5.5's: the session's totals."""
    return {"claude-haiku-5-5": {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
                                 "cacheCreationInputTokens": write, "costUSD": cost}}


class FakeClient:
    """One SDK client: its scripted messages after query(). Each tool an assistant turn calls goes
    through the options' PreToolUse hook first, as the CLI sends it, and `effect(name, input)` does
    what an allowed one would (writes the files vfg script new makes, say). With a gate, it holds
    the last message until the gate opens; interrupt() opens it, unless told to ignore interrupts.
    `raises` is raised by connect(), and `breaks` once the messages are out."""

    def __init__(self, options, messages, gate: asyncio.Event | None, raises: Exception | None, deaf: bool,
                 effect=None, breaks: Exception | None = None):
        self.options = options
        self.messages = list(messages)
        self.gate = gate
        self.raises = raises
        self.deaf = deaf
        self.effect = effect
        self.breaks = breaks
        self.prompt: str | None = None
        self.interrupted = False
        self.disconnected = False
        # (tool, input, the deny reason or None), for every tool call put through the hook
        self.verdicts: list[tuple[str, dict, str | None]] = []

    async def connect(self):
        if self.raises:
            raise self.raises

    async def query(self, prompt: str):
        self.prompt = prompt

    async def receive_response(self):
        for i, m in enumerate(self.messages):
            if self.gate is not None and i == len(self.messages) - 1:
                await self.gate.wait()
            if isinstance(m, AssistantMessage):
                await self._tools(m)
            yield m
            if isinstance(m, ResultMessage):
                return
        if self.breaks is not None:
            raise self.breaks

    async def _tools(self, m: AssistantMessage) -> None:
        hooks = [h for matcher in (self.options.hooks or {}).get("PreToolUse", []) for h in matcher.hooks]
        for b in m.content:
            if not isinstance(b, ToolUseBlock):
                continue
            reason = None
            for hook in hooks:
                out = await hook({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": b.name,
                                  "tool_input": b.input}, b.id, None)
                reason = reason or (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason")
            self.verdicts.append((b.name, b.input, reason))
            if reason is None and self.effect is not None:
                self.effect(b.name, b.input)

    async def interrupt(self):
        self.interrupted = True
        if self.gate is not None and not self.deaf:
            self.gate.set()

    async def disconnect(self):
        self.disconnected = True


class Clients:
    """The client_factory: each client made takes the next script add() gave, else an init and a
    Done result."""

    def __init__(self):
        self.scripts: deque = deque()
        self.made: list[FakeClient] = []

    def add(self, *messages, gate: asyncio.Event | None = None, raises: Exception | None = None,
            deaf: bool = False, effect=None, breaks: Exception | None = None) -> "Clients":
        self.scripts.append((messages or (init(), result()), gate, raises, deaf, effect, breaks))
        return self

    def __call__(self, options) -> FakeClient:
        script = self.scripts.popleft() if self.scripts else ((init(), result()), None, None, False, None, None)
        c = FakeClient(options, *script)
        self.made.append(c)
        return c
