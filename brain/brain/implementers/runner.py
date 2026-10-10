"""The implementers: one Claude Agent SDK query per task, on Haiku 5.5 and the owner's Claude
subscription, which code starts from the brain's brief. The implementer is the main agent, with
nothing around it: its system prompt is prompts/implementer.md and `vfg prefix`, its brief is the
brain's with the task, the saved places and the EXAMPLES block (brief.py), and the guard
(guard.py) holds every tool call to its rules.

    implementers = Implementers(ImplementerConfig.load(), on_report)
    await implementers.start("T7", brief)            # at once; later on_report("T7", "done", report)
    await implementers.follow_up("T7", "bigger")     # resumes T7's session: another on_report
    stopped = await implementers.close(5)            # the ids still running; the brain fails them

Its last message is its report, which goes to on_report with "failed" for an error, its turns used
up, a crash or the time limit, else the report's first word (Done or Failed). A task's jobs run one
after another: a follow-up waits behind the task's running implementer, then resumes its session,
so two implementers never edit one script. Each run writes its usage with `vfg usage add`, off the
critical path, after its report has gone."""
import asyncio
import json
import os
import re
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage,
                              SystemMessage, TextBlock, ToolUseBlock)

from . import brief as briefing
from . import prefix as implementer_prefix
from . import usage
from .config import ImplementerConfig, js_num, log
from .guard import GuardContext, brief as short, make_guard, running_copies
from .vfg import VfgRunner, vfg as vfg_runner, why

__all__ = ["Implementers", "ImplementerConfig", "Report"]

# (task id, "done" or "failed", the report)
Report = Callable[[str, str, str], Awaitable[None]]
# ClaudeAgentOptions -> a client: connect(), query(prompt), receive_response(), interrupt(), disconnect().
ClientFactory = Callable[[ClaudeAgentOptions], Any]

TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]

# A close() waits this long at most for an interrupt to be taken, and for a cancelled run to end.
INTERRUPT_TIMEOUT_S = 5.0


def rules(repo: str, server: str = "main") -> list[str]:
    """The allow rules; with permission mode "dontAsk" everything else is denied without a prompt.
    Implementers run actions (`vfg action run`; `vfg script run` is gone) and write only in this
    server's folder, made/<server>/, its actions/ included; the library in scripts/ and actions/ is
    read-only during play. echo, ls, grep, head and tail are here for the forms the guard lets
    through: vfg piped into head, tail or grep, and those on files in the repository."""
    return [
        "Read(/%s/**)" % repo, "Glob", "Grep",
        "Edit(/%s/made/%s/**)" % (repo, server), "Write(/%s/made/%s/**)" % (repo, server),
        "Bash(vfg action run *)", "Bash(vfg action list)", "Bash(vfg action list *)", "Bash(vfg action check *)",
        "Bash(vfg cmd *)",
        "Bash(vfg script deploy)", "Bash(vfg script deploy *)",
        "Bash(vfg script stop *)", "Bash(vfg script logs *)",
        "Bash(vfg script list)", "Bash(vfg script list *)",
        "Bash(vfg script new *)", "Bash(vfg script set *)", "Bash(vfg script save *)",
        "Bash(vfg script check)", "Bash(vfg script check *)", "Bash(vfg preset save *)",
        "Bash(vfg state)", "Bash(vfg state *)",
        "Bash(vfg place save *)", "Bash(vfg place list)", "Bash(vfg place list *)",
        "Bash(vfg history)", "Bash(vfg history *)",
        "Bash(python3 -m py_compile *)",
        "Bash(sleep *)",
        "Bash(echo)", "Bash(echo *)", "Bash(ls)", "Bash(ls *)",
        "Bash(grep *)", "Bash(head *)", "Bash(tail *)",
    ]


def session_env(source: Mapping[str, str]) -> dict[str, str]:
    """The CLI's environment: the owner's subscription through the CLI's own login in ~/.claude,
    never an API key; and nothing of a Claude session that started the brain, whose variables
    describe that session, not this one."""
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


def build_options(cfg: ImplementerConfig, system: str, guard: GuardContext, resume: str | None = None,
                  stderr: Callable[[str], None] | None = None) -> ClaudeAgentOptions:
    """One implementer's options: the implementer is the main thread."""
    env = session_env(os.environ)
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "vfg-brain/0.1"
    env["PYTHONPYCACHEPREFIX"] = os.path.join(cfg.brain_dir, "pycache")  # py_compile leaves no __pycache__ in scripts/
    return ClaudeAgentOptions(
        model=cfg.model,
        effort=cfg.effort,
        system_prompt=system,
        cwd=cfg.repo,
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        tools=list(TOOLS),
        allowed_tools=rules(cfg.repo, cfg.server),
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(hooks=[make_guard(guard)])]},
        env=env,
        # The prompt cache's life (VFG_IMPL_CACHE_TTL) as a flag setting: the CLI reads it from
        # --settings whatever setting_sources says, and session_env leaves out its variable.
        settings=json.dumps({"promptCacheTtl": cfg.cache_ttl}),
        stderr=stderr,
        max_turns=cfg.max_turns,
        resume=resume,
    )


def sdk_client(options: ClaudeAgentOptions) -> ClaudeSDKClient:
    """The real client, on the subscription: the default client_factory."""
    scrub_environ()
    return ClaudeSDKClient(options)


def fill(rules_text: str, cfg: ImplementerConfig) -> str:
    return rules_text.replace("%REPO%", cfg.repo).replace("%SERVER%", cfg.server)


def follow_up_prompt(task: str, text: str) -> str:
    return ("FOLLOW-UP to %s, from the brain: %s\nDo it as before, check it once, and end with a new report."
            % (task, text.strip()))


_FIRST_WORD = re.compile(r"\W*(done|failed)\b", re.I)


def outcome(result: ResultMessage | None, failed: str | None, max_turns: int) -> tuple[str, str]:
    """(status, report) for one run: failed on an error result, its turns used up, a crash or the
    time limit; else the report's first word, Done or Failed, and done when it is neither."""
    if failed:
        return "failed", "Failed: %s." % failed
    if result is None:
        return "failed", "Failed: the implementer ended without a report."
    text = (result.result or "").strip()
    if result.subtype == "error_max_turns":
        return "failed", "Failed: the implementer used its %d turns.%s" % (max_turns, " " + text if text else "")
    if result.is_error or result.subtype != "success":
        return "failed", "Failed: %s" % (text or "; ".join(result.errors or []) or result.subtype)
    if not text:
        return "done", "Done: the implementer ended without a report."
    m = _FIRST_WORD.match(text)
    return (m.group(1).lower() if m else "done"), text


@dataclass
class _Task:
    id: str
    guard: GuardContext
    brief: str = ""
    # The implementer's session, once the CLI gave it: a follow-up resumes it.
    session: str | None = None
    # ("start", brief) and ("follow_up", text), run one after another by the task's worker.
    jobs: deque = field(default_factory=deque)
    worker: asyncio.Task | None = None
    client: Any = None
    # Each model's totals at the session's last result (usage.rows).
    usage: dict = field(default_factory=dict)


class Implementers:
    def __init__(self, cfg: ImplementerConfig, on_report: Report, *, client_factory: ClientFactory | None = None,
                 vfg: VfgRunner | None = None):
        """`client_factory` makes the SDK client (tests pass a fake); `vfg` runs vfg, as vfg.vfg does."""
        self.cfg = cfg
        self.on_report = on_report
        self.client_factory = client_factory or sdk_client
        self.vfg = vfg or vfg_runner
        with open(cfg.prompt, encoding="utf-8") as f:
            self._rules = fill(f.read(), cfg)
        # `vfg prefix`, read at the first start and kept, so every implementer's system prompt is
        # the same bytes; tried again at the next start when it failed.
        self._prefix = ""
        self._prefix_lock = asyncio.Lock()
        self._running_copies = running_copies(cfg.home, self.vfg)
        self._tasks: dict[str, _Task] = {}
        self._background: set[asyncio.Task] = set()
        self._closing = False

    # ------------------------------------------------------------------ the brain's calls

    async def start(self, task_id: str, brief: str) -> None:
        """Starts the task's implementer and returns at once; on_report follows when it ends.
        A task already started takes this as a follow-up."""
        t = self._tasks.get(task_id)
        if t is not None:
            log("implementer", "%s is already started: this brief goes to it as a follow-up" % task_id)
            await self.follow_up(task_id, brief)
            return
        t = self._tasks[task_id] = _Task(task_id, self._guard(task_id), brief=brief)
        t.jobs.append(("start", brief))
        self._kick(t)

    async def follow_up(self, task_id: str, text: str, session: str | None = None) -> None:
        """To the task's implementer, behind whatever it is running: its session resumed with the
        text, ending in another on_report. `session` resumes a task this process did not start
        (the brain's tasks.json keeps it); with no session at all, a new implementer starts on the
        task's brief and the text."""
        t = self._tasks.get(task_id)
        if t is None:
            t = self._tasks[task_id] = _Task(task_id, self._guard(task_id), session=session)
        elif session and not t.session:
            t.session = session
        t.jobs.append(("follow_up", text))
        self._kick(t)

    def running(self) -> list[str]:
        """The tasks whose implementer runs, or has a follow-up waiting."""
        return [t.id for t in self._tasks.values() if t.worker is not None]

    def session(self, task_id: str) -> str | None:
        """The task's implementer session, for the brain's tasks.json; None until the CLI gave one."""
        t = self._tasks.get(task_id)
        return t.session if t else None

    async def close(self, grace_s: float) -> list[str]:
        """Stops every implementer: interrupted, given `grace_s` to end, then cancelled. Follow-ups
        still waiting are dropped, and nothing more is reported. Returns the ids it stopped."""
        self._closing = True
        live = [t for t in self._tasks.values() if t.worker is not None]
        for t in live:
            t.jobs.clear()
        await asyncio.gather(*(self._interrupt(t) for t in live if t.client is not None))
        workers = [t.worker for t in live if t.worker is not None]
        if workers:
            _, late = await asyncio.wait(workers, timeout=grace_s)
            for w in late:
                w.cancel()
            if late:
                await asyncio.wait(late, timeout=INTERRUPT_TIMEOUT_S)
        if self._background:  # usage rows still going out
            await asyncio.wait(set(self._background), timeout=grace_s)
        ids = [t.id for t in live]
        log("implementer", "closed: %s" % (", ".join(ids) or "nothing was running"))
        return ids

    # ------------------------------------------------------------------ one task's jobs

    def _guard(self, task_id: str) -> GuardContext:
        return GuardContext(repo=self.cfg.repo, server=self.cfg.server, task=task_id, running=self._running_copies)

    def _kick(self, t: _Task) -> None:
        if self._closing:
            log("implementer", "%s: closing, so its jobs are dropped" % t.id)
            t.jobs.clear()
            return
        if t.worker is None:
            t.worker = asyncio.get_running_loop().create_task(self._work(t))

    async def _work(self, t: _Task) -> None:
        """The task's jobs in order: each run ends, and reports, before the next starts."""
        try:
            while t.jobs and not self._closing:
                kind, text = t.jobs.popleft()
                try:
                    await self._run(t, kind, text)
                except Exception as e:
                    log("implementer", "%s broke: %s" % (t.id, traceback.format_exc()))
                    if not self._closing:
                        await self._report(t.id, "failed", "Failed: the implementer could not run: %s: %s."
                                           % (type(e).__name__, e))
        finally:
            t.worker = None

    async def _run(self, t: _Task, kind: str, text: str) -> None:
        resume = t.session if kind == "follow_up" else None
        if resume:
            prompt = follow_up_prompt(t.id, text)
        else:
            if kind == "follow_up" and t.brief:  # nothing to resume: a new implementer, on both
                text = "%s\n\nFOLLOW-UP: %s" % (t.brief.rstrip(), text.strip())
            prompt = await briefing.compose(text, t.id, self.cfg, self.vfg)
        if self._closing:
            return

        def stderr(line: str) -> None:
            if line.strip():
                log("cli", "%s %s" % (t.id, line.rstrip()))

        options = build_options(self.cfg, await self._system(), t.guard, resume=resume, stderr=stderr)
        log("implementer", "%s %s: %d characters of brief" % (t.id, "resumed " + resume if resume else "started",
                                                               len(prompt)))
        result, failed = await self._query(t, options, prompt)
        if self._closing:
            return
        status, report = outcome(result, failed, self.cfg.max_turns)
        rows = []
        if result is not None:
            rows, t.usage = usage.rows(t.usage, result.model_usage, t.id)
        await self._report(t.id, status, report)
        for row in rows:  # after the report: the player's answer waits on nothing
            self._spawn(self._usage_row(row))

    async def _query(self, t: _Task, options: ClaudeAgentOptions,
                     prompt: str) -> tuple[ResultMessage | None, str | None]:
        """One SDK query to its result: (the result, None), or (None or the result, why it failed)."""
        client = self.client_factory(options)
        t.client = client
        result = failed = None
        try:
            async with asyncio.timeout(self.cfg.time_limit_s):
                await client.connect()
                await client.query(prompt)
                async for msg in client.receive_response():
                    failed = self._seen(t, msg)
                    if failed:
                        break
                    if isinstance(msg, ResultMessage):
                        result = msg
        except TimeoutError:
            failed = "it ran over its time limit, %s s, and was stopped" % js_num(self.cfg.time_limit_s)
        except Exception as e:
            failed = "it crashed: %s: %s" % (type(e).__name__, str(e)[:300])
        finally:
            t.client = None
            try:
                await client.disconnect()
            except Exception as e:
                log("implementer", "%s: disconnect: %s" % (t.id, e))
        return result, failed

    def _seen(self, t: _Task, msg: Any) -> str | None:
        """Notes one message: the session id, what it does, how it ended. Returns why to stop it."""
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            data = msg.data or {}
            if data.get("session_id"):
                t.session = data["session_id"]
            log("implementer", "%s init: session %s, model %s, permission mode %s" % (
                t.id, t.session, data.get("model"), data.get("permissionMode")))
            src = data.get("apiKeySource")
            if src and src != "none":
                return "the CLI would use an API key (%s), not the subscription, so it was stopped" % src
        elif isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    log("implementer", "%s %s %s" % (t.id, b.name, short(b.input)))
                elif isinstance(b, TextBlock) and b.text.strip():
                    log("implementer", "%s says: %s" % (t.id, re.sub(r"\s+", " ", b.text.strip())[:300]))
        elif isinstance(msg, ResultMessage):
            t.session = msg.session_id or t.session
            log("implementer", "%s %s (%s, %s turns, %d s)" % (
                t.id, "error" if msg.is_error else "ended", msg.subtype, msg.num_turns, (msg.duration_ms or 0) // 1000))
            for d in msg.permission_denials or []:
                if isinstance(d, dict):
                    log("denied", "%s %s %s" % (t.id, d.get("tool_name"), short(d.get("tool_input"))))
        return None

    async def _system(self) -> str:
        async with self._prefix_lock:
            if not self._prefix:
                self._prefix = await implementer_prefix.load(self.vfg)
        return implementer_prefix.system_prompt(self._rules, self._prefix)

    async def _report(self, task_id: str, status: str, report: str) -> None:
        log("implementer", "%s %s: %s" % (task_id, status, re.sub(r"\s+", " ", report)[:300]))
        try:
            await self.on_report(task_id, status, report)
        except Exception:
            log("implementer", "%s: on_report broke: %s" % (task_id, traceback.format_exc()))

    async def _interrupt(self, t: _Task) -> None:
        try:
            await asyncio.wait_for(t.client.interrupt(), INTERRUPT_TIMEOUT_S)
        except Exception as e:
            log("implementer", "%s: interrupt: %s" % (t.id, e))

    async def _usage_row(self, row: dict) -> None:
        r = await self.vfg(*usage.usage_args(row), timeout=30)
        if r.code != 0:
            log("usage", "vfg usage add failed (%s): %s in=%s out=%s" % (why(r), row["ref"], js_num(row["in"]),
                                                                       js_num(row["out"])))
            return
        log("usage", "implementer %s in=%s out=%s cache_read=%s cache_write=%s cost=%.4f ref=%s" % (
            row["model"], js_num(row["in"]), js_num(row["out"]), js_num(row["cacheRead"]), js_num(row["cacheWrite"]),
            row["cost"], row["ref"]))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
