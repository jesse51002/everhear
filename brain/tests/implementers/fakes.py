"""Fakes for the implementer tests: the vfg runner, and a harness whose runs give scripted events.
No process starts, and nothing here knows Claude Code: the fake harness puts each tool call through
the guard as every adapter must (harness/base.py)."""
import asyncio
import inspect
from collections import deque

from brain.implementers.guard import gate
from brain.implementers.harness.base import ERROR, OK, STOPPED, TURNS, Done, Request, Session, Text, ToolCall, ToolResult
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


# ---------------------------------------------------------------- a run's events

def session(s: str = "s1") -> Session:
    return Session(s)


def done(text: str | None = "Done: it is built.", ended: str = OK, errors: list[str] | None = None,
         refused: list[str] | None = None) -> Done:
    return Done(ended, text or "", turns=3, errors=errors or [], seconds=1.2, refused=refused or [])


def failed_turns(text: str = "") -> Done:
    return done(text, TURNS)


def error(text: str = "", errors: list[str] | None = None) -> Done:
    return done(text, ERROR, errors)


def stopped(why: str) -> Done:
    return done(why, STOPPED)


def tool(name: str, **inp) -> ToolCall:
    """A tool the model calls: the fake harness puts it through the guard."""
    return ToolCall(name, inp, "t%d" % id(inp))


def bash(command: str) -> ToolCall:
    return tool("Bash", command=command)


def said(text: str) -> Text:
    return Text(text)


def result_of(call: ToolCall, text: str, error: bool = True) -> ToolResult:
    return ToolResult(call.id, error, text)


def request(input: int = 0, read: int = 0, write: int = 0, out: int = 0, model: str = "deepseek-flash",
            at: float = 1_760_000_000.0) -> Request:
    """One model request's tokens; `at` defaults to a Thursday, 08:53 UTC: DeepSeek's peak."""
    return Request(model, input, read, write, out, at)


class FakeRun:
    """One run: its scripted events. Each ToolCall goes through the guard before it is given, and
    `effect(name, input)` does what an allowed one would (writes the file a Write names, say). With a
    gate, it holds the last event until the gate opens; stop() opens it, unless told to ignore stops. `breaks` is raised once the events are out. send() takes a message while the
    run is live (until its Done is given), and `on_send(run, text)` does what reading it would."""

    def __init__(self, kind: str, task: str, system, prompt: str, resumed: str | None, guard, events,
                 gate: asyncio.Event | None, deaf: bool, effect, breaks: Exception | None, on_send=None):
        self.kind, self.task, self.system, self.prompt, self.resumed, self.guard = kind, task, system, prompt, resumed, guard
        self.script = list(events)
        self.gate = gate
        self.deaf = deaf
        self.effect = effect
        self.breaks = breaks
        self.on_send = on_send
        self.session = resumed
        self.stopped = False
        self.closed = False
        self.ended = False
        self.sent: list[str] = []
        # (tool, input, the guard's reason or None), for every tool call put through the guard
        self.verdicts: list[tuple[str, dict, str | None]] = []

    async def events(self):
        for i, ev in enumerate(self.script):
            if self.gate is not None and i == len(self.script) - 1:
                await self.gate.wait()
            if isinstance(ev, ToolCall):
                reason = await gate({"tool_name": ev.name, "tool_input": ev.input}, self.guard)
                self.verdicts.append((ev.name, ev.input, reason))
                if reason is None and self.effect is not None:
                    self.effect(ev.name, ev.input)
            if isinstance(ev, Session):
                self.session = ev.session
            if isinstance(ev, Done):
                self.ended = True
            yield ev
            if isinstance(ev, Done):
                return
        self.ended = True
        if self.breaks is not None:
            raise self.breaks

    async def send(self, text: str) -> bool:
        if self.ended or self.closed:
            return False
        self.sent.append(text)
        if self.on_send is not None:
            self.on_send(self, text)
        return True

    async def stop(self) -> None:
        self.stopped = True
        if self.gate is not None and not self.deaf:
            self.gate.set()

    async def close(self) -> None:
        self.closed = True


class FakeHarness:
    """The harness: each run started or resumed takes the next script add() gave, else a session
    and a Done. `raises` is raised by start() or resume() instead."""
    name = "fake"

    def __init__(self):
        self.scripts: deque = deque()
        self.runs: list[FakeRun] = []
        self.closed = False

    def add(self, *events, gate: asyncio.Event | None = None, raises: Exception | None = None,
            deaf: bool = False, effect=None, breaks: Exception | None = None, on_send=None) -> "FakeHarness":
        self.scripts.append((events or (session(), done()), gate, raises, deaf, effect, breaks, on_send))
        return self

    def _next(self, kind, task, system, prompt, resumed, guard) -> FakeRun:
        events, gate, raises, deaf, effect, breaks, on_send = (
            self.scripts.popleft() if self.scripts else ((session(), done()), None, None, False, None, None, None))
        if raises is not None:
            raise raises
        run = FakeRun(kind, task, system, prompt, resumed, guard, events, gate, deaf, effect, breaks, on_send)
        self.runs.append(run)
        return run

    async def start(self, task, system, brief, guard) -> FakeRun:
        return self._next("start", task, system, brief, None, guard)

    async def resume(self, task, system, session, text, guard) -> FakeRun:
        return self._next("resume", task, system, text, session, guard)

    async def close(self) -> None:
        self.closed = True
