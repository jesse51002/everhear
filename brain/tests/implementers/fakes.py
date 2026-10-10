"""Fakes for the implementer tests: the vfg runner and the Claude Agent SDK client. No process starts."""
import asyncio
import inspect
from collections import deque

from claude_agent_sdk import ResultMessage, SystemMessage

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


def haiku(inp: int, out: int, cost: float, read: int = 0, write: int = 0) -> dict:
    """A modelUsage entry, Haiku 5.5's: the session's totals."""
    return {"claude-haiku-5-5": {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": read,
                                 "cacheCreationInputTokens": write, "costUSD": cost}}


class FakeClient:
    """One SDK client: its scripted messages after query(). With a gate, it holds the last message
    until the gate opens; interrupt() opens it, unless told to ignore interrupts. `raises` is
    raised by connect()."""

    def __init__(self, options, messages, gate: asyncio.Event | None, raises: Exception | None, deaf: bool):
        self.options = options
        self.messages = list(messages)
        self.gate = gate
        self.raises = raises
        self.deaf = deaf
        self.prompt: str | None = None
        self.interrupted = False
        self.disconnected = False

    async def connect(self):
        if self.raises:
            raise self.raises

    async def query(self, prompt: str):
        self.prompt = prompt

    async def receive_response(self):
        for i, m in enumerate(self.messages):
            if self.gate is not None and i == len(self.messages) - 1:
                await self.gate.wait()
            yield m
            if isinstance(m, ResultMessage):
                return

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
            deaf: bool = False) -> "Clients":
        self.scripts.append((messages or (init(), result()), gate, raises, deaf))
        return self

    def __call__(self, options) -> FakeClient:
        messages, gate, raises, deaf = self.scripts.popleft() if self.scripts else ((init(), result()), None, None, False)
        c = FakeClient(options, messages, gate, raises, deaf)
        self.made.append(c)
        return c
