"""The one session: its options, the input queue that never ends, the message loop, the idle
check that makes sure every task is answered, the watchdog, and resume and restart."""
import asyncio
import os
import re
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

from .config import Config, Mode, hms, js_round, log, now_ms, parse_time
from .guard import GuardContext, brief, make_guard, task_id_of
from .reply import REPLY_TOOL, Outbox, create_reply_server
from .tasks import Store
from .usage import UsageMeter


class FatalError(Exception):
    pass


class MSG:
    interrupted = "The orchestrator restarted while this was running; anything it started may be half done."
    stopped = "The orchestrator was stopped."
    no_answer = "The orchestrator ended without an answer"

    @staticmethod
    def could_not_run(err: str) -> str:
        return "The orchestrator could not run: %s" % err[:120]

    @staticmethod
    def nudge(ids: list[str], mode: Mode = "implementers") -> str:
        what = ("do what it needs" if mode == "direct" else
                "do it or start the implementer it needs" if mode == "hybrid" else
                "start the implementer it needs")
        return "RUNNER: still open with nothing running: %s. Reply done or failed for each now, or %s." % (", ".join(ids), what)

    @staticmethod
    def closed(ids: list[str]) -> str:
        return "RUNNER: closed %s as failed." % ", ".join(ids)

    @staticmethod
    def restarted(at: float, ids: list[str], mode: Mode = "implementers") -> str:
        cut = ("Work in progress was cut off; scripts started on the server may still be running." if mode == "direct" else
               "Implementers from before are gone; scripts they started on the server may still be running.")
        return "RUNNER: restarted at %s UTC. Closed as failed: %s. %s" % (hms(at), ", ".join(ids), cut)

    @staticmethod
    def overtime(minutes: int) -> str:
        return "RUNNER: this task ran over %d minutes and was stopped. Reply failed for it, saying how far it got." % minutes


def rules(repo: str, mode: Mode = "implementers") -> list[str]:
    """The allow rules; with permission mode "dontAsk" everything else is denied without a prompt."""
    return [
        REPLY_TOOL, *(["Agent", "TaskStop"] if mode != "direct" else []),
        "Read(/%s/**)" % repo, "Glob", "Grep",
        # hybrid: the session never writes; implementers (who run under these rules too) do
        "Edit(/%s/scripts/**)" % repo, "Write(/%s/scripts/**)" % repo,
        "Bash(vfg cmd *)",
        "Bash(vfg script deploy)", "Bash(vfg script deploy *)",
        "Bash(vfg script run *)", "Bash(vfg script stop *)", "Bash(vfg script logs *)",
        "Bash(vfg script list)",
        "Bash(vfg history)", "Bash(vfg history *)",
        "Bash(python3 -m py_compile *)",
        "Bash(sleep *)",
    ]


@dataclass
class Prompts:
    orchestrator: str
    implementer: str


def fill_prompt(text: str, cfg: Config) -> str:
    return text.replace("%REPO%", cfg.repo).replace("%MAX_IMPLEMENTERS%", str(cfg.max_implementers))


def session_env(source: Mapping[str, str]) -> dict[str, str]:
    """The CLI's environment: the owner's subscription through the CLI's own login in ~/.claude,
    never an API key; and nothing of the Claude session that started the runner (vfg orch start
    runs from the conversation), whose variables describe that session, not this one."""
    env = {}
    for k, v in source.items():
        if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            continue
        if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION"):
            continue
        if k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN":
            continue
        env[k] = v
    return env


def scrub_environ() -> None:
    """The Python SDK lays options.env over this process's own environment, where the TypeScript
    SDK used options.env alone; so what session_env leaves out is taken out of this process's
    environment before the CLI starts. vfg, the only other program the runner starts, uses none of it."""
    keep = session_env(os.environ)
    for k in list(os.environ):
        if k not in keep:
            del os.environ[k]


def build_options(cfg: Config, prompts: Prompts, store: Store, guard: GuardContext, resume: str | None = None,
                  agent: str | None = None, stderr: Callable[[str], None] | None = None) -> ClaudeAgentOptions:
    """The session's options. The permission probe builds its one-shot session with this too
    (`agent`: it runs the implementer as the main thread)."""
    env = session_env(os.environ)
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "vfg-orchestrator/0.1"
    env["PYTHONPYCACHEPREFIX"] = os.path.join(cfg.orch_dir, "pycache")  # py_compile leaves no __pycache__ in scripts/
    direct = cfg.mode == "direct"
    opts = ClaudeAgentOptions(
        model=cfg.model,
        effort=cfg.effort,
        system_prompt=fill_prompt(prompts.orchestrator, cfg),
        cwd=cfg.repo,
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        mcp_servers={"orch": create_reply_server(store)},
        # direct: the session works itself with the implementer's tools. hybrid: it runs simple
        # commands itself and briefs implementers; it has no Write or Edit (the guard refuses them
        # on the main thread). Write and Edit stay in the list so implementers can have them.
        tools=(["Bash", "Read", "Write", "Edit", "Glob", "Grep"] if direct else
               ["Agent", "TaskStop", "Bash", "Read", "Write", "Edit", "Glob", "Grep"]),
        allowed_tools=rules(cfg.repo, cfg.mode),
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(hooks=[make_guard(guard)])]},
        env=env,
        stderr=stderr if stderr is not None else (lambda line: log("cli", line.rstrip())),
    )
    if not direct:
        opts.agents = {
            "implementer": AgentDefinition(
                description="Carries out one job on the Minecraft server with vfg commands and the scripts folder, then reports.",
                prompt=fill_prompt(prompts.implementer, cfg),
                model=cfg.implementer_model,
                effort=cfg.implementer_effort,
                tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                background=True,
                maxTurns=40,
            ),
        }
    if resume:
        opts.resume = resume
    if agent:
        opts.extra_args = {**opts.extra_args, "agent": agent}  # --agent, as the TypeScript SDK passed it
    return opts


class SdkQuery:
    """The TypeScript SDK's Query, over the Python SDK's ClaudeSDKClient. It connects as it is
    made, as query() did; iterating it gives the CLI's messages as their raw dicts (the typed
    messages leave out fields the loop reads, a result's queued_turn_count among them);
    account_info() is the initialize response's account, which is what the TypeScript
    accountInfo() returned; close() ends it without waiting."""

    def __init__(self, prompt: AsyncIterator[dict], options: ClaudeAgentOptions):
        self._client = ClaudeSDKClient(options)
        self._closed = False
        self._closing: asyncio.Task | None = None
        self._connected = asyncio.get_running_loop().create_task(self._connect(prompt))
        # A failed connection reaches whoever waits on it; unwaited, it is not an error of its own.
        self._connected.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _connect(self, prompt: AsyncIterator[dict]) -> None:
        scrub_environ()
        await self._client.connect(prompt)

    async def _ready(self) -> bool:
        """Waits for the connection; False when close() called it off."""
        try:
            await asyncio.shield(self._connected)
        except asyncio.CancelledError:
            if self._connected.cancelled():
                return False
            raise
        return True

    async def account_info(self) -> dict | None:
        await self._ready()
        info = await self._client.get_server_info()
        return (info or {}).get("account")

    def __aiter__(self) -> AsyncIterator[dict]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[dict]:
        try:
            if not await self._ready():
                return
            # The raw stream ClaudeSDKClient.receive_messages() parses into typed messages.
            async for msg in self._client._query.receive_messages():
                yield msg
        except Exception:
            if not self._closed:
                raise
        finally:
            self.close()

    async def interrupt(self) -> None:
        await self._ready()
        await self._client.interrupt()

    async def stop_task(self, task_id: str) -> None:
        await self._ready()
        await self._client.stop_task(task_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._connected.done():
            self._connected.cancel()  # connect() disconnects what it had started
            return
        try:
            self._closing = asyncio.get_running_loop().create_task(self._client.disconnect())
        except RuntimeError:
            pass  # no event loop: the process is ending, and the SDK's exit hook stops the CLI

    async def wait_closed(self) -> None:
        """After close(): waits until the CLI has exited (the probe uses it; the runner does not wait)."""
        if self._closing is not None:
            await asyncio.wait({self._closing})


def sdk_query(prompt: AsyncIterator[dict], options: ClaudeAgentOptions) -> SdkQuery:
    return SdkQuery(prompt, options)


class InputQueue:
    """The prompt: an async iterable fed from memory that never ends on its own."""

    def __init__(self):
        self._buf: deque[dict] = deque()
        self._waiter: asyncio.Future | None = None
        self._gen = 0
        # A message was handed to the session.
        self.on_pull: Callable[[], None] = lambda: None

    @staticmethod
    def message(text: str) -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None}

    def _hand(self, m: dict) -> bool:
        w, self._waiter = self._waiter, None
        if w is None or w.done():
            return False
        self.on_pull()
        w.set_result(m)
        return True

    def push(self, text: str) -> None:
        m = InputQueue.message(text)
        if not self._hand(m):
            self._buf.append(m)

    def unshift(self, text: str) -> None:
        """Ahead of anything already waiting."""
        m = InputQueue.message(text)
        if not self._hand(m):
            self._buf.appendleft(m)

    @property
    def length(self) -> int:
        return len(self._buf)

    def attach(self) -> AsyncIterator[dict]:
        """A fresh iterable for one query. Whatever is still buffered goes to it."""
        self._gen += 1
        return self._iterate(self._gen)

    async def _iterate(self, gen: int) -> AsyncIterator[dict]:
        try:
            while gen == self._gen:
                if self._buf:
                    m = self._buf.popleft()
                    self.on_pull()
                    yield m
                    continue
                w = asyncio.get_running_loop().create_future()
                self._waiter = w
                m = await w
                if m is None:
                    return
                yield m
        finally:
            if gen == self._gen:
                self.detach()

    def detach(self) -> None:
        """Ends the current iterable (its query is gone)."""
        self._gen += 1
        w, self._waiter = self._waiter, None
        if w is not None and not w.done():
            w.set_result(None)


@dataclass
class Implementer:
    task: str | None
    description: str
    started: float
    stopping: bool = False


@dataclass
class Delays:
    """Restart delays in ms: the first, the most, and how long a healthy run resets them."""
    first: float = 5_000
    max: float = 60_000
    healthy: float = 600_000


class Session:
    def __init__(self, cfg: Config, store: Store, outbox: Outbox, usage: UsageMeter, prompts: Prompts,
                 query_fn: Callable[..., Any] | None = None, now: Callable[[], float] | None = None,
                 delays: Delays | None = None, on_fatal: Callable[[FatalError], None] | None = None):
        self.cfg = cfg
        self.store = store
        self.outbox = outbox
        self.usage = usage
        self.prompts = prompts
        self.delays = delays or Delays()
        # An API key turned up after start: the process must exit 2.
        self.on_fatal = on_fatal
        self.now = now or now_ms
        self.query_fn = query_fn or sdk_query
        self.input = InputQueue()
        self.q = None
        self.running: dict[str, Implementer] = {}  # SDK task_id -> implementer
        self.pending: dict[str, dict] = {}  # tool_use_id -> {"task", "at"}: allowed, not started
        self.busy = False
        self.saw_result = False
        self.queued_turns = 0
        self.wake_deadline = 0.0
        self.last_result_error: str | None = None
        self.turns_this_query = 0
        self.stopping = False
        self._loop_done: asyncio.Task | None = None
        self._stderr_tail: list[str] = []
        self._usage_chain: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._wake_restart: asyncio.Event | None = None
        self._background: set[asyncio.Task] = set()
        # The main thread's context: the input of its last model call (fresh, cache-read and
        # cache-written tokens).
        self.context_tokens = 0
        # Context rollovers so far.
        self.rollovers = 0
        self._rollover_at: int | None = None
        # Direct mode: tasks waiting for the session to finish the one before.
        self.waiting: list[dict] = []
        self._turn_started_at = 0.0
        self._turn_overtime = False

        def on_pull():
            if not self.busy:
                self._turn_started_at = self.now()
                self._turn_overtime = False
            self.busy = True  # a turn is coming

        self.input.on_pull = on_pull

    @property
    def direct(self) -> bool:
        """direct and hybrid: tasks go to the session one at a time, and a turn has a time limit."""
        return self.cfg.mode != "implementers"

    def push(self, text: str) -> None:
        log("session", "push: %s" % text.split("\n")[0])
        self.input.push(text)

    def push_task(self, tid: str, message: str) -> None:
        """A task from the inbox. Implementers mode: straight in, so it can fold into a running
        turn. Direct and hybrid: one at a time; it waits until the session has finished the turn before."""
        if not self.direct:
            return self.push(message)
        self.waiting.append({"id": tid, "message": message})
        log("session", "queued %s; %d waiting" % (tid, len(self.waiting)))
        self.deliver()

    def deliver(self) -> None:
        """Direct mode: hand the next waiting task to the session when it is between turns."""
        if not self.direct or self.stopping or not self.q or not self.waiting:
            return
        if self.busy or self.queued_turns > 0 or self.input.length > 0:
            return
        while self.waiting:
            nxt = self.waiting.pop(0)
            t = self.store.get(nxt["id"])
            if t is None or t.status != "open":
                continue  # closed meanwhile
            self.push(nxt["message"])
            return

    def guard_context(self) -> GuardContext:
        cfg, store = self.cfg, self.store

        def on_agent_allowed(task: str, tool_use_id: str) -> None:
            self.pending[tool_use_id or "anon-%s" % self.now()] = {"task": task, "at": self.now()}

        return GuardContext(
            repo=cfg.repo,
            max_implementers=cfg.max_implementers,
            implementer_model=cfg.implementer_model,
            main_thread_rule=cfg.mode != "direct",  # direct: the main thread is held to the implementer's rules
            hybrid=cfg.mode == "hybrid",  # hybrid: it orchestrates, and runs simple commands under the implementer's rules
            open_task_ids=store.open_ids,
            active_implementers=lambda: len(self.running) + len(self.pending),
            on_agent_allowed=on_agent_allowed,
        )

    def options(self, resume: str | None = None) -> ClaudeAgentOptions:
        def stderr(line: str) -> None:
            text = line.rstrip()
            if not text:
                return
            self._stderr_tail.append(text)
            if len(self._stderr_tail) > 20:
                self._stderr_tail.pop(0)
            log("cli", text)

        return build_options(self.cfg, self.prompts, self.store, self.guard_context(), resume=resume, stderr=stderr)

    async def open(self, resume: str | None = None) -> None:
        """Starts one session, resuming `resume` if given, and checks the login: an API key is
        fatal. Returns once the session is up; the message loop runs on until it ends."""
        self.turns_this_query = 0
        self.saw_result = False
        self.busy = False
        self.queued_turns = 0
        self.wake_deadline = 0.0
        self.running.clear()
        self.pending.clear()
        self._stderr_tail = []
        self.context_tokens = 0
        q = self.query_fn(prompt=self.input.attach(), options=self.options(resume))
        self.q = q
        self._loop_done = asyncio.get_running_loop().create_task(self._loop(q))
        try:
            info = await with_timeout(q.account_info(), 60_000)
        except Exception as e:
            # The loop sees the session end and the restart logic takes it from there; the
            # init message's apiKeySource is checked as well.
            log("session", "accountInfo failed: %s" % e)
            return
        src = (info or {}).get("apiKeySource")
        if src and src != "none":
            raise FatalError("the CLI would use an API key (%s), not the logged-in subscription; refusing to run" % src)
        log("session", "logged in: subscription %s, model %s, implementers %s%s" % (
            (info or {}).get("subscriptionType") or "unknown", self.cfg.model, self.cfg.implementer_model,
            ", resuming " + resume if resume else ", new session"))

    async def _loop(self, q) -> None:
        try:
            async for msg in q:
                self.handle(msg)
            log("session", "the session ended")
        except Exception as e:
            if not self.stopping:
                log("session", "the session broke: %s" % e)

    def handle(self, msg: dict) -> None:
        store = self.store
        sid = msg.get("session_id")
        if sid and sid != store.state.session_id and not msg.get("parent_tool_use_id"):
            store.state.session_id = sid
            store.save()
            log("session", "session %s" % sid)
        kind = msg.get("type")
        if kind == "assistant":
            self.busy = True
            if not msg.get("parent_tool_use_id"):
                self._log_assistant(msg)
                u = (msg.get("message") or {}).get("usage")
                if isinstance(u, dict):
                    self.context_tokens = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                           + (u.get("cache_creation_input_tokens") or 0))
        elif kind in ("user", "stream_event"):
            self.busy = True
        elif kind == "result":
            self._on_result(msg)
        elif kind == "rate_limit_event":
            log("session", "rate limit: %s" % brief(msg.get("rate_limit_info")))
        elif kind == "system":
            self._on_system(msg)

    def _log_assistant(self, msg: dict) -> None:
        for b in (msg.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                inp = b.get("input")
                desc = (str((inp.get("description") if isinstance(inp, dict) else None) or "")
                        if b.get("name") == "Agent" else brief(inp))
                log("orch", "%s %s" % (b.get("name"), desc))
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                log("orch", "says: %s" % re.sub(r"\s+", " ", b["text"].strip())[:300])

    def _on_system(self, msg: dict) -> None:
        sub = msg.get("subtype")
        if sub == "init":
            log("session", "init: model %s, permission mode %s, tools %s" % (
                msg.get("model"), msg.get("permissionMode"), ",".join(msg.get("tools") or [])))
            src = msg.get("apiKeySource")
            if src and src != "none" and self.on_fatal:
                self.on_fatal(FatalError("the CLI is using an API key (%s), not the logged-in subscription" % src))
        elif sub == "task_started":
            if not is_implementer_task(msg):
                return
            self._track(msg.get("task_id"), msg.get("description"), msg.get("tool_use_id"))
            log("session", "implementer started: %s (%s); %d running" % (msg.get("description"), msg.get("task_id"), len(self.running)))
        elif sub == "task_notification":
            r = self.running.get(msg.get("task_id"))
            self._untrack(msg.get("task_id"))
            summary = re.sub(r"\s+", " ", str(msg.get("summary") or ""))[:200]
            log("session", "implementer %s: %s; %d running. %s" % (
                msg.get("status"), r.description if r else msg.get("task_id"), len(self.running), summary))
        elif sub == "background_tasks_changed":
            # Every live background task, replace semantics: the surest view of what is running.
            live = {}
            for t in msg.get("tasks") or []:
                if is_implementer_task(t) and not t.get("ambient"):
                    live[t.get("task_id")] = t
            for tid in list(self.running):
                if tid not in live:
                    self._untrack(tid)
            for tid, t in live.items():
                if tid in self.running:
                    continue
                self._track(tid, t.get("description"))
                log("session", "implementer seen: %s (%s); %d running" % (t.get("description"), tid, len(self.running)))
        elif sub == "task_updated":
            patch = msg.get("patch")
            st = patch.get("status") if isinstance(patch, dict) else None
            if st in ("completed", "failed", "killed"):
                self._untrack(msg.get("task_id"))
        elif sub == "api_retry":
            log("session", "API retry: %s" % brief(msg))

    def _track(self, tid: str, description: str | None, tool_use_id: str | None = None) -> None:
        """An implementer is running: count it, drop its allowance, clear its task's nudge."""
        store = self.store
        task = task_id_of(description)
        if tid not in self.running:
            self.running[tid] = Implementer(task=task, description=description or "", started=self.now())
        if tool_use_id and tool_use_id in self.pending:
            del self.pending[tool_use_id]
        else:
            for k, v in list(self.pending.items()):
                if v["task"] == task:
                    del self.pending[k]
                    break
        t = store.get(task) if task else None
        if t is not None and t.nudged:
            t.nudged = False
            store.save()
        if task:
            self.usage.note([task])

    def _untrack(self, tid: str) -> None:
        """An implementer ended. Its report starts a turn: give that a minute before calling it idle."""
        if self.running.pop(tid, None) is None:
            return
        if not self.busy:
            self.wake_deadline = max(self.wake_deadline, self.now() + 60_000)

    def _on_result(self, msg: dict) -> None:
        self.busy = False
        self.saw_result = True
        self.wake_deadline = 0.0
        self.turns_this_query += 1
        self.queued_turns = msg.get("queued_turn_count") or 0
        is_error = bool(msg.get("is_error")) or msg.get("subtype") != "success"
        self.last_result_error = result_error(msg) if is_error else None
        log("session", "turn %s (%s, %s rounds, %d s)%s" % (
            "error" if is_error else "done", msg.get("subtype"), msg.get("num_turns"),
            js_round((msg.get("duration_ms") or 0) / 1000), ": " + self.last_result_error if is_error else ""))
        for d in msg.get("permission_denials") or []:
            log("denied", "%s %s" % (d.get("tool_name"), brief(d.get("tool_input"))))
        self._chain_usage(msg.get("modelUsage"))
        if self.maybe_rollover():
            return
        self.deliver()
        self.idle_check()

    def _chain_usage(self, model_usage) -> None:
        """Usage rows go out one result after another, never two at once."""
        prev = self._usage_chain

        async def link():
            if prev is not None:
                await asyncio.wait({prev})
            try:
                await self.usage.on_result(model_usage)
            except Exception as e:
                log("usage", str(e))

        self._usage_chain = asyncio.get_running_loop().create_task(link())

    def is_idle(self) -> bool:
        return (self.q is not None and self.saw_result and not self.busy and self.queued_turns == 0
                and self.input.length == 0 and not self.running and not self.pending
                and self.now() >= self.wake_deadline)

    def idle_check(self) -> None:
        """When idle with tasks still open: nudge each once; close those nudged before."""
        if self.stopping or not self.is_idle():
            return
        store = self.store
        waiting = {w["id"] for w in self.waiting}
        open_ids = [tid for tid in store.open_ids() if tid not in waiting]
        if not open_ids:
            return
        to_close = [tid for tid in open_ids if store.get(tid).nudged]
        to_nudge = [tid for tid in open_ids if not store.get(tid).nudged]
        if to_close:
            why = MSG.could_not_run(self.last_result_error) if self.last_result_error else MSG.no_answer
            for tid in to_close:
                store.fail(tid, why)
            log("session", "closed %s as failed: %s" % (", ".join(to_close), why))
            self.push(MSG.closed(to_close))
        if to_nudge:
            for tid in to_nudge:
                store.get(tid).nudged = True
            store.save()
            self.push(MSG.nudge(to_nudge, self.cfg.mode))

    def tick(self) -> None:
        """Every 10 s: stale allowances, the implementer (or direct turn) time limit, delivery, the idle check."""
        now = self.now()
        limit_min = self.cfg.implementer_minutes
        if (self.direct and self.busy and not self._turn_overtime and self._turn_started_at
                and now - self._turn_started_at > limit_min * 60_000):
            self._turn_overtime = True
            log("session", "a turn ran over %d min: interrupting it" % limit_min)
            if self.q:
                self._spawn(self.q.interrupt(), "interrupt failed")
            self.push(MSG.overtime(limit_min))
        if self.maybe_rollover():
            return
        self.deliver()
        for k, v in list(self.pending.items()):
            if now - v["at"] > 60_000:
                del self.pending[k]
        limit = self.cfg.implementer_minutes * 60_000
        for tid, imp in self.running.items():
            if not imp.stopping and now - imp.started > limit:
                imp.stopping = True
                log("session", "implementer over %d min, stopping: %s" % (self.cfg.implementer_minutes, imp.description))
                if self.q:
                    self._spawn(self.q.stop_task(tid), "stopTask failed")
        self.idle_check()

    def _spawn(self, coro, failed: str) -> None:
        """Runs a call to the session in the background; a failure is logged."""
        async def run():
            try:
                await coro
            except Exception as e:
                log("session", "%s: %s" % (failed, e))

        task = asyncio.get_running_loop().create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def start_watchdog(self, ms: float = 10_000) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.get_running_loop().create_task(self._every(ms))

    async def _every(self, ms: float) -> None:
        while True:
            await asyncio.sleep(ms / 1000)
            try:
                self.tick()
            except Exception:
                log("main", "uncaught exception: %s" % traceback.format_exc())

    def close_open(self, text: str, all_tasks: bool = False) -> list[str]:
        """Closes every open task as failed; returns their ids. Tasks still waiting to start stay,
        unless `all_tasks`."""
        waiting = set() if all_tasks else {w["id"] for w in self.waiting}
        ids = [tid for tid in self.store.open_ids() if tid not in waiting]
        for tid in ids:
            self.store.fail(tid, text)
        return ids

    def maybe_rollover(self) -> bool:
        """Over the context budget and idle (between tasks, no turn running or queued, no
        implementer running): close this session; keep_alive opens a fresh one and hands it what
        it needs. Implementers live inside the session's process, so a rollover waits until none
        is running."""
        budget = self.cfg.max_context
        if (not budget or self.stopping or self._rollover_at is not None or not self.q
                or self.context_tokens <= budget or not self.is_idle()):
            return False
        self._rollover_at = self.context_tokens
        log("session", "rollover: the session's context reached %d tokens (budget %d); starting a fresh one"
            % (self.context_tokens, budget))
        self.close_query()
        return True

    def handoff(self, tokens: int) -> str:
        """What a fresh session needs from the last one, from the runner's own records."""
        store, cfg = self.store, self.cfg

        def clip(t: str, n: int) -> str:
            x = re.sub(r"\s+", " ", t).strip()
            return x[:n - 1] + "…" if len(x) > n else x

        waiting = {w["id"] for w in self.waiting}
        open_ids = [tid for tid in store.open_ids() if tid not in waiting]
        lines = ["RUNNER: this is a fresh session: the last one had grown to %d tokens of context. What you need from it:" % tokens]
        lines.append("Open tasks, each still needing exactly one done or failed reply:" if open_ids else "Open tasks: none.")
        for tid in open_ids:
            t = store.get(tid)
            last = t.replies[-1] if t.replies else None
            at = parse_time(t.at)
            lines.append("- %s (sent %s UTC): %s" % (tid, hms(at) if at is not None else "?", clip(t.task, 300))
                         + (" Context: %s" % clip(t.context, 300) if t.context.strip() else "")
                         + (" Said so far: %s" % clip(last.text, 200) if last else ""))
        if cfg.mode != "direct":
            lines.append("Implementers running: none (a fresh session starts only when none are).")
        recent = []
        for tid, t in store.state.tasks.items():
            if t.status == "open":
                continue
            answers = [r for r in t.replies if r.status != "progress"]
            if answers:
                recent.append((tid, answers[-1]))
        recent = sorted(recent, key=lambda x: x[1].n)[-8:]
        lines.append("Recent answers, newest last (they say what was built or left running, and how to stop it):"
                     if recent else "Recent answers: none.")
        for tid, r in recent:
            lines.append("- %s %s: %s" % (tid, r.status, clip(r.text, 300)))
        if self.waiting:
            lines.append("%d more task(s) are waiting; they come one at a time." % len(self.waiting))
        return "\n".join(lines)

    async def keep_alive(self, first_started_at: float) -> None:
        """Keeps a session running for the life of the process: the first open() has already run;
        when the session ends or throws, open tasks are failed as interrupted, and after 5 s
        (doubling to 60 s, back to 5 s after 10 healthy minutes) it is started again with resume."""
        delays = self.delays
        delay = delays.first
        started_at = first_started_at
        resumed = self.store.state.session_id
        while not self.stopping:
            if self._loop_done is not None:
                await self._loop_done
            self.input.detach()
            self.q = None
            if self.stopping:
                break
            if self._rollover_at is not None:
                # A context rollover, not a failure: nothing is closed. The old session's last
                # usage rows go out first, then its snapshot is dropped, since a new session's
                # totals start at zero.
                tokens = self._rollover_at
                old = self.store.state.session_id
                await self.settled()
                self.forget_session()
                started_at = self.now()
                delay = delays.first
                await self.open(None)
                self._rollover_at = None
                if self.stopping:
                    break
                handoff = self.handoff(tokens)
                self.input.unshift(handoff)  # ahead of anything that came in meanwhile
                self.rollovers += 1
                waiting = {w["id"] for w in self.waiting}
                carried = [tid for tid in self.store.open_ids() if tid not in waiting]
                log("session", "rollover #%d done: %s at %d tokens -> a fresh session; handoff %d characters, open tasks carried: %s"
                    % (self.rollovers, old or "the last session", tokens, len(handoff), ", ".join(carried) or "none"))
                resumed = None
                self.deliver()
                continue
            ran_for = self.now() - started_at
            resume_failed = (bool(resumed) and self.turns_this_query == 0
                             and (ran_for < 30_000 or re.search(r"no conversation found", " ".join(self._stderr_tail), re.I) is not None))
            closed = self.close_open(MSG.interrupted)
            if resume_failed:
                log("session", "resuming %s failed before any turn ran: starting a new session" % resumed)
                self.forget_session()
            else:
                if ran_for >= delays.healthy:
                    delay = delays.first
                log("session", "restarting in %s s" % _seconds(delay))
                await self._restart_delay(delay)
                delay = min(delay * 2, delays.max)
            if self.stopping:
                break
            started_at = self.now()
            resumed = self.store.state.session_id
            await self.open(resumed)
            if closed:
                self.push(MSG.restarted(self.now(), closed, self.cfg.mode))
            self.deliver()

    async def _restart_delay(self, ms: float) -> None:
        wake = self._wake_restart = asyncio.Event()
        try:
            await asyncio.wait_for(wake.wait(), ms / 1000)
        except TimeoutError:
            pass
        finally:
            self._wake_restart = None

    def forget_session(self) -> None:
        self.store.state.session_id = None
        self.store.state.usage = {}  # a new session's totals start from zero
        self.store.save()

    async def stop(self) -> list[str]:
        """Stops the session for good: open tasks are failed as stopped; the session is closed."""
        self.stopping = True
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = None
        if self._wake_restart:
            self._wake_restart.set()
        closed = self.close_open(MSG.stopped, True)
        self.waiting.clear()
        return closed

    def close_query(self) -> None:
        try:
            if self.q:
                self.q.close()
        except Exception:
            pass  # already gone
        self.input.detach()

    async def settled(self) -> None:
        """Waits for the usage rows queued so far (a rollover, before it drops the snapshot; tests)."""
        chain = self._usage_chain
        if chain is not None:
            await asyncio.wait({chain})


def _seconds(ms: float) -> str:
    s = ms / 1000
    return str(int(s)) if float(s).is_integer() else repr(s)


def is_implementer_task(t: dict) -> bool:
    """A local agent task: an implementer (the only agent the orchestrator has)."""
    if t.get("task_type"):
        return t["task_type"] == "local_agent"
    return t.get("subagent_type") == "implementer" or bool(task_id_of(t.get("description")))


async def with_timeout(aw, ms: float):
    """`aw`'s result, or an error after `ms`; `aw` is not cancelled, as Promise.race would not."""
    task = asyncio.ensure_future(aw)
    done, _ = await asyncio.wait({task}, timeout=ms / 1000)
    if not done:
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        raise TimeoutError("no answer in %s s" % _seconds(ms))
    return task.result()


def result_error(msg: dict) -> str:
    if msg.get("subtype") == "success":
        return re.sub(r"\s+", " ", str(msg.get("result") or "error"))
    errs = msg.get("errors")
    return re.sub(r"\s+", " ", "; ".join(errs) if errs else str(msg.get("subtype")))
