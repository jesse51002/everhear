"""The implementers: one Claude Agent SDK query per task, on Haiku 5.5 and the owner's Claude
subscription, which code starts from the brief the orchestrator wrote. A task is known by its name
(Prototype_Named_Tasks), which the script, the action and the copy its implementer makes take too.
The implementer is the main agent, with nothing around it: its system prompt is
prompts/implementer.md and `vfg prefix`, its brief is the orchestrator's with the task's name, the
saved places and the EXAMPLES block (brief.py), and the guard (guard.py) holds every tool call to
its rules and records what it makes.

    implementers = Implementers(ImplementerConfig.load(), on_report)
    await implementers.start("galick_gun_styrk", brief)          # at once; later on_report(…)
    await implementers.follow_up("galick_gun_styrk", "bigger")   # resumes its session: another on_report
    # on_session("galick_gun_styrk", session), when given, as soon as the CLI names the session
    stopped = await implementers.close(5)            # the names still running; the brain fails them

Its last message is its report, which goes to on_report with "failed" for an error, its turns used
up, a crash or the time limit, else the report's first word (Done or Failed). The report's last
line, DESCRIPTION: what the thing is now, is taken off and given on its own (split_description),
with what the task made (guard.made_now). A task's jobs run one after another: a follow-up waits
behind the task's running implementer, then resumes its session, so two implementers never edit one
script. Each run writes its usage with `vfg usage add`, its ref the task's name, off the critical
path, after its report has gone."""
import asyncio
import hashlib
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
from .guard import MADE_KINDS, GuardContext, brief as short, made_now, make_guard, running_copies
from .vfg import VfgRunner, vfg as vfg_runner, why

__all__ = ["Implementers", "ImplementerConfig", "Report", "split_description"]

# (task name, "done" or "failed", the report without its DESCRIPTION line, the description or "",
#  what it made {"scripts", "actions", "presets", "copies"})
Report = Callable[[str, str, str, str, dict[str, list[str]]], Awaitable[None]]
# (task name, the implementer's session id): the brain keeps it, so a follow-up after a restart resumes it
Session = Callable[[str, str], None]
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


def system_file(cfg: ImplementerConfig, system: str) -> str:
    """The system prompt in a file of the brain's folder, named by its hash, written once: about
    180K characters with `vfg prefix`, too long for one argument of the CLI's command line (Linux
    takes at most 128 KiB in one), so it goes to the CLI as --system-prompt-file. The same bytes
    always make the same file, so every implementer's prompt starts the same."""
    data = system.encode("utf-8")
    path = os.path.join(cfg.brain_dir, "implementer-system-%s.md" % hashlib.sha256(data).hexdigest()[:12])
    if not os.path.exists(path):
        os.makedirs(cfg.brain_dir, mode=0o700, exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def build_options(cfg: ImplementerConfig, system: str, guard: GuardContext, resume: str | None = None,
                  stderr: Callable[[str], None] | None = None) -> ClaudeAgentOptions:
    """One implementer's options: the implementer is the main thread."""
    env = session_env(os.environ)
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "vfg-brain/0.1"
    env["PYTHONPYCACHEPREFIX"] = os.path.join(cfg.brain_dir, "pycache")  # py_compile leaves no __pycache__ in scripts/
    return ClaudeAgentOptions(
        model=cfg.model,
        effort=cfg.effort,
        system_prompt={"type": "file", "path": system_file(cfg, system)},
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


def follow_up_prompt(name: str, text: str) -> str:
    return ("FOLLOW-UP to %s, from the brain: %s\nDo it as before, check it once, and end with a new report, its "
            "last line DESCRIPTION: what %s is now." % (name, text.strip(), name))


# The report's DESCRIPTION line, however the model dresses it: "DESCRIPTION:", "**Description:**",
# "- _description_:".
_DESCRIPTION = re.compile(r"^[\s>*_`#-]*description[*_`]*\s*:", re.I)
# The description the brain keeps, at most.
DESCRIPTION_MAX = 600


def split_description(text: str) -> tuple[str, str]:
    """(the report, the description): the description is all after the colon of the report's last
    DESCRIPTION line, to the end, its whitespace collapsed and its markdown stripped, cut to 600
    characters; the report is what comes before that line. With no such line, (text, "")."""
    lines = text.split("\n")
    at = next((i for i in range(len(lines) - 1, -1, -1) if _DESCRIPTION.match(lines[i])), None)
    if at is None:
        return text, ""
    first = lines[at][_DESCRIPTION.match(lines[at]).end():]
    description = " ".join(" ".join([first] + lines[at + 1:]).split())
    while description != description.strip("*_`").strip():
        description = description.strip("*_`").strip()
    report = "\n".join(lines[:at]).rstrip()
    return report or "Done: the implementer ended without a report.", description[:DESCRIPTION_MAX].rstrip()


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
    name: str
    guard: GuardContext
    brief: str = ""
    # The implementer's session, once the CLI gave it: a follow-up resumes it.
    session: str | None = None
    # ("start", brief) and ("follow_up", text), run one after another by the task's worker.
    jobs: deque = field(default_factory=deque)
    worker: asyncio.Task | None = None
    # A run is under way: set as a job starts, cleared as it reports.
    busy: bool = False
    client: Any = None
    # Each model's totals at the session's last result (usage.rows).
    usage: dict = field(default_factory=dict)


class Implementers:
    def __init__(self, cfg: ImplementerConfig, on_report: Report, *, on_session: Session | None = None,
                 client_factory: ClientFactory | None = None, vfg: VfgRunner | None = None):
        """`on_session` is told a task's session as soon as the CLI gives it; `client_factory` makes
        the SDK client (tests pass a fake); `vfg` runs vfg, as vfg.vfg does."""
        self.cfg = cfg
        self.on_report = on_report
        self.on_session = on_session
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

    async def start(self, name: str, brief: str) -> None:
        """Starts the implementer of the task `name` and returns at once; on_report follows when it
        ends. A task already started takes this as a follow-up."""
        t = self._tasks.get(name)
        if t is not None:
            log("implementer", "%s is already started: this brief goes to it as a follow-up" % name)
            await self.follow_up(name, brief)
            return
        t = self._tasks[name] = _Task(name, self._guard(name), brief=brief)
        t.jobs.append(("start", brief))
        self._kick(t)

    async def follow_up(self, name: str, text: str, session: str | None = None, brief: str = "") -> None:
        """To the task's implementer, behind whatever it is running: its session resumed with the
        text, ending in another on_report. `session` and `brief` are for a task this process did
        not start (the brain keeps both in the task's row): its session resumed; with no session at
        all, a new implementer starts on `brief`, what the task is, and the text."""
        t = self._tasks.get(name)
        if t is None:
            t = self._tasks[name] = _Task(name, self._guard(name), brief=brief, session=session)
        else:
            t.session = t.session or session
            t.brief = t.brief or brief
        t.jobs.append(("follow_up", text))
        self._kick(t)

    def running(self) -> list[str]:
        """The tasks whose implementer runs, or has a follow-up waiting. While a run reports, its
        task is here only when a follow-up to it waits, which runs next."""
        return [t.name for t in self._tasks.values() if t.worker is not None and (t.busy or t.jobs)]

    def session(self, name: str) -> str | None:
        """The task's implementer session, for the task's row; None until the CLI gave one."""
        t = self._tasks.get(name)
        return t.session if t else None

    async def close(self, grace_s: float) -> list[str]:
        """Stops every implementer: interrupted, given `grace_s` to end, then cancelled. Follow-ups
        still waiting are dropped, and nothing more is reported. Returns the names it stopped."""
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
        names = [t.name for t in live]
        log("implementer", "closed: %s" % (", ".join(names) or "nothing was running"))
        return names

    # ------------------------------------------------------------------ one task's jobs

    def _guard(self, name: str) -> GuardContext:
        """The task's guard, kept with it: what it made over every run, and its own script, the one
        named as the task, its own to change from the start (GuardContext.own)."""
        return GuardContext(repo=self.cfg.repo, server=self.cfg.server, task=name, running=self._running_copies)

    def _kick(self, t: _Task) -> None:
        if self._closing:
            log("implementer", "%s: closing, so its jobs are dropped" % t.name)
            t.jobs.clear()
            return
        if t.worker is None:
            t.worker = asyncio.get_running_loop().create_task(self._work(t))

    async def _work(self, t: _Task) -> None:
        """The task's jobs in order: each run ends, and reports, before the next starts."""
        try:
            while t.jobs and not self._closing:
                kind, text = t.jobs.popleft()
                t.busy = True
                try:
                    await self._run(t, kind, text)
                except Exception as e:
                    t.busy = False
                    log("implementer", "%s broke: %s" % (t.name, traceback.format_exc()))
                    if not self._closing:  # with what it made so far
                        await self._report(t, "failed", "Failed: the implementer could not run: %s: %s."
                                           % (type(e).__name__, e))
                finally:
                    t.busy = False
        finally:
            t.worker = None

    async def _run(self, t: _Task, kind: str, text: str) -> None:
        resume = t.session if kind == "follow_up" else None
        if resume:
            prompt = follow_up_prompt(t.name, text)
        else:
            if kind == "follow_up" and t.brief:  # nothing to resume: a new implementer, on both
                text = "%s\n\nFOLLOW-UP: %s" % (t.brief.rstrip(), text.strip())
            prompt = await briefing.compose(text, t.name, self.cfg, self.vfg)
        if self._closing:
            return

        def stderr(line: str) -> None:
            if line.strip():
                log("cli", "%s %s" % (t.name, line.rstrip()))

        options = build_options(self.cfg, await self._system(), t.guard, resume=resume, stderr=stderr)
        log("implementer", "%s %s: %d characters of brief" % (t.name, "resumed " + resume if resume else "started",
                                                               len(prompt)))
        result, failed = await self._query(t, options, prompt)
        if self._closing:
            return
        status, report = outcome(result, failed, self.cfg.max_turns)  # the status from the whole text
        report, description = split_description(report)
        rows = []
        if result is not None:
            rows, t.usage = usage.rows(t.usage, result.model_usage, t.name)
        t.busy = False  # this run is over: the brain sees whether a follow-up waits behind it
        await self._report(t, status, report, description)
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
                log("implementer", "%s: disconnect: %s" % (t.name, e))
        return result, failed

    def _seen(self, t: _Task, msg: Any) -> str | None:
        """Notes one message: the session id, what it does, how it ended. Returns why to stop it."""
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            data = msg.data or {}
            self._keep_session(t, data.get("session_id"))
            log("implementer", "%s init: session %s, model %s, permission mode %s" % (
                t.name, t.session, data.get("model"), data.get("permissionMode")))
            src = data.get("apiKeySource")
            if src and src != "none":
                return "the CLI would use an API key (%s), not the subscription, so it was stopped" % src
        elif isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    log("implementer", "%s %s %s" % (t.name, b.name, short(b.input)))
                elif isinstance(b, TextBlock) and b.text.strip():
                    log("implementer", "%s says: %s" % (t.name, re.sub(r"\s+", " ", b.text.strip())[:300]))
        elif isinstance(msg, ResultMessage):
            self._keep_session(t, msg.session_id)
            log("implementer", "%s %s (%s, %s turns, %d s)" % (
                t.name, "error" if msg.is_error else "ended", msg.subtype, msg.num_turns, (msg.duration_ms or 0) // 1000))
            for d in msg.permission_denials or []:
                if isinstance(d, dict):
                    log("denied", "%s %s %s" % (t.name, d.get("tool_name"), short(d.get("tool_input"))))
        return None

    def _keep_session(self, t: _Task, session: str | None) -> None:
        """The session the CLI named, kept here and given to on_session at once: a run the brain's
        stop or a crash cuts short is still resumed by its follow-up."""
        if not session or session == t.session:
            return
        t.session = session
        if self.on_session is not None:
            try:
                self.on_session(t.name, session)
            except Exception:
                log("implementer", "%s: on_session broke: %s" % (t.name, traceback.format_exc()))

    async def _system(self) -> str:
        async with self._prefix_lock:
            if not self._prefix:
                self._prefix = await implementer_prefix.load(self.vfg)
        return implementer_prefix.system_prompt(self._rules, self._prefix)

    async def _report(self, t: _Task, status: str, report: str, description: str = "") -> None:
        """The report to the brain, with the description and what the task made, cumulative over its
        runs in this process and checked against the disk."""
        made = made_now(t.guard)
        log("implementer", "%s %s: %s" % (t.name, status, re.sub(r"\s+", " ", report)[:300]))
        log("implementer", "%s made %s; description: %s" % (
            t.name, "; ".join("%s %s" % (k, ", ".join(made[k])) for k in MADE_KINDS if made[k]) or "nothing",
            description[:300] or "none"))
        try:
            await self.on_report(t.name, status, report, description, made)
        except Exception:
            log("implementer", "%s: on_report broke: %s" % (t.name, traceback.format_exc()))

    async def _interrupt(self, t: _Task) -> None:
        try:
            await asyncio.wait_for(t.client.interrupt(), INTERRUPT_TIMEOUT_S)
        except Exception as e:
            log("implementer", "%s: interrupt: %s" % (t.name, e))

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
