"""The one interface between the runner and a harness, the program around the model (Claude Code,
OpenCode): it sends the model the prompt, runs the tools it asks for, and keeps the session a
follow-up resumes. The runner, the guard and the usage are ours, whatever the harness; each harness
gets an adapter behind this interface (Prototype_Implementer_Harness).

    run = await harness.start(task, system_file, brief, guard)   # or resume(task, system_file, session, text, guard)
    async for event in run.events():        # Session, ToolCall, ToolResult, Text, Request, Compacted, Done
        ...
    await run.close()

A run's events, in order:

- **Session**: the harness's session id, as soon as it has one (and again should it change); a
  follow-up resumes it, after a restart too.
- **ToolCall**: a tool the model asked for, its name and input. The adapter has put it, or puts it,
  through the guard before it runs: a refusal goes back to the model as that tool's error, with the
  guard's reason, and the guard records what the calls it lets through make.
- **ToolResult**: what a tool call gave back, by the call's id: whether it was an error, and the
  start of its text. Optional: the runner uses it only to say a stopped run's last errors.
- **Text**: something the model said along the way.
- **Request**: one model request's tokens, as the provider counted them: input it did not have
  cached, read from the cache, written to it, and output, and of the output how much was the
  model's thinking (its reasoning, an estimate from the thinking's text when the provider does not
  count it); its model and when it was sent. Its size, the input of all three kinds, is what
  Haiku's price steps on. The runner prices it (usage.py) and logs it, its reasoning too.
- **Compacted**: the harness summarised the session to make room, which it is set never to do.
- **Done**: the run ended, once and last, with no message sent to it left unread (a message that
  lands as the model ends its turn starts the next turn, and the run goes on): how (ok; error;
  turns, its turn cap reached; stopped, the adapter stopped it, why in `text`), its last message
  (the report, covering every message), its turns over all its turns, its errors, and the tool
  calls the harness's own permission checks refused, which should be none; and any message the
  run ended without reading (when the harness would not take it up), which the runner resumes with.

A message (send()) is read at the run's next safe point: on Claude Code, as soon as the tool calls in
flight finish, in the same turn; or, when it lands as the model ends its turn, as the next turn.

An adapter makes its own checks off and leaves the guard as the only gate."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Protocol

from ..guard import GuardContext


@dataclass
class Session:
    session: str


@dataclass
class ToolCall:
    name: str
    input: dict
    id: str | None = None


@dataclass
class ToolResult:
    id: str | None
    error: bool
    text: str = ""  # the start of what came back


@dataclass
class Text:
    text: str


@dataclass
class Request:
    model: str
    input: int  # input the provider did not have cached: a cache miss
    cache_read: int
    cache_write: int
    output: int
    at: float  # when it was sent, seconds since the epoch
    id: str | None = None
    provider: str | None = None  # who served it, when the provider says (OpenRouter's providers)
    billed: float | None = None  # what the provider says it charged, in dollars (OpenRouter's usage.cost), if it does
    # Of `output`, the model's thinking (never more output: priced once, as output): the provider's
    # count, else, with reasoning_estimated, an estimate from the thinking's text; 0 when it did not think.
    reasoning: int = 0
    reasoning_estimated: bool = False
    # Its answer broke off mid-stream (and the harness sent the request again): its counts are the
    # provider's record of it when there is one, else what was read before the break, a floor.
    broken: bool = False

    @property
    def size(self) -> int:
        """The request's input, all of it: what it read."""
        return self.input + self.cache_read + self.cache_write


@dataclass
class Compacted:
    trigger: str = ""
    tokens_before: int | None = None


# Done.ended
OK, ERROR, TURNS, STOPPED = "ok", "error", "turns", "stopped"


@dataclass
class Done:
    ended: str  # OK, ERROR, TURNS or STOPPED
    text: str = ""  # its last message, the report; for an error, what went wrong; for stopped, why
    turns: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    refused: list[str] = field(default_factory=list)  # what the harness's own checks refused
    # messages sent into the run that it ended without reading (as sent): the runner resumes with them
    unread: list[str] = field(default_factory=list)


Event = Session | ToolCall | ToolResult | Text | Request | Compacted | Done


class Run(Protocol):
    session: str | None  # set once the harness has made one

    def events(self) -> AsyncIterator[Event]:
        """The run's events in order, ending with Done (or ending early when the harness died, by
        an exception, or with no Done)."""
        ...

    async def send(self, text: str) -> bool:
        """A message into the live run, as it is; False once the run has ended (its Done decided),
        and the runner then resumes the session with it."""
        ...

    async def stop(self) -> None:
        """Asks the run to end now (an interrupt): at shutdown. Its events then end."""
        ...

    async def close(self) -> None:
        """Ends the run's process and frees it: always, once the runner is done with the run."""
        ...


class Harness(Protocol):
    name: str  # "claude-code", "opencode"

    async def start(self, task: str, system: Path, brief: str, guard: GuardContext) -> Run:
        """A new session for the task: `system` is the system prompt's file, `brief` the first prompt."""
        ...

    async def resume(self, task: str, system: Path, session: str, text: str, guard: GuardContext) -> Run:
        """The task's session resumed, with `text` as the next prompt. The system prompt is given
        again: a harness may not keep it with the session (Claude Code does not)."""
        ...

    async def close(self) -> None:
        """At shutdown, after every run is closed: what the harness keeps (a relay) is stopped."""
        ...
