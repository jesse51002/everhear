"""The OpenCode adapter: an implementer is one session of `opencode serve` (OpenCode 1.x), driven
through its HTTP API from Python, on DeepSeek V4.1 Flash through the provider the settings name
(config.py): OpenRouter (`openrouter`, its OpenAI-style API) or DeepSeek's own API (`deepseek`).

- **One server per Implementers**, started at the first run and stopped at close(). Everything it
  reads and keeps is under the brain's folder, `cfg.brain_dir`/opencode/: its config, its database
  (the sessions, which a follow-up after a brain restart resumes) and its caches. The owner's own
  OpenCode setup (~/.config/opencode, ~/.local/share/opencode, ~/.opencode, a project's
  opencode.json or AGENTS.md, ~/.claude) is never read: the XDG folders and OpenCode's own home
  point there, and the flags OpenCode 1.18 honours turn the rest off.
- **The provider's key** goes into the server's environment only, which its config names
  (`{env:VFG_OPENCODE_KEY}`); it is never logged or written, and a shell a tool starts gets it
  blanked.
- **Our system prompt** is the agent's prompt (`{file:<the system file>}`), which takes the place of
  OpenCode's own. OpenCode adds its environment block and its skills list after it.
- **The effort is on every request** (VFG_IMPL_EFFORT, config.EFFORTS): the agent's options put it
  on each request's body, as OpenRouter's `reasoning: {"effort": <it>}` or DeepSeek's own
  `thinking: {"type": "enabled"}` and `reasoning_effort: <it>`; at off, thinking off, as
  OpenRouter's `reasoning: {"enabled": false}` or DeepSeek's `thinking: {"type": "disabled"}`.
- **A command may run 10 minutes** when the model sets no timeout
  (OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS, config.bash_timeouts_ms), not OpenCode's 2, so a
  `vfg test` waiting its turn is not cut short.
- **Every tool is allowed in OpenCode's own permissions**, so nothing of OpenCode's refuses or asks,
  and the guard is the only gate: the plugin's `tool.execute.before` hook (opencode_plugin/guard.js)
  asks it, at an endpoint this adapter serves on 127.0.0.1 with a secret per process, and throws its
  reason, which the model gets as the tool's error. The plugin refuses when it cannot ask. The tools
  OpenCode would put to a person (`question`, and the plan mode's switches) are left out: no person
  answers them.
- **Turns** are the run's model requests, OpenCode's steps (a subagent's too): a run is aborted at
  its turn cap (`cfg.max_turns`) and ends with Done(TURNS).
- **A message** (send()) is a prompt into the busy session, which OpenCode reads at its next step; a
  run ends only when the session is idle and its last prompt has its answer. A prompt that lands
  as the session goes idle, unanswered, is given to it again (same message, same part).
- **Each request's tokens** are its step-finish part's: input not cached, read from the cache,
  written to it, and output (thinking included), its reasoning the thinking OpenCode counts apart.
  A subagent's requests (OpenCode's `task` tool) are the run's too.
- **The title request is never made**: a session is made with its title, `vfg <task>`."""
import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

import httpx

from .. import guard as guards
from ..config import BASE_URLS, ImplementerConfig, bash_timeouts_ms, log, model_price, provider_routing
from ..guard import GuardContext, brief
from .base import ERROR, OK, STOPPED, TURNS, Compacted, Done, Request, Session, Text, ToolCall, ToolResult

PLUGIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode_plugin", "guard.js")
AGENT = "vfg"
# The server's user name for its password (OpenCode's own default).
USER = "opencode"

# The providers OpenCode reaches the model at, by cfg.provider: OpenCode's id for it, the AI SDK
# package that speaks to it, and its OpenAI-style address by default (effort_options: the effort).
PROVIDERS = {
    "openrouter": ("openrouter", "@openrouter/ai-sdk-provider", "https://openrouter.ai/api/v1"),
    "deepseek": ("deepseek", "@ai-sdk/openai-compatible", "https://api.deepseek.com"),
}
# The most a request may write (OpenCode caps it at 32K itself).
OUTPUT_MAX = 32_000
# The tools OpenCode would put to a person: nobody answers them, so they are never offered.
NO_PERSON = {"question": "deny", "plan_enter": "deny", "plan_exit": "deny"}
# OpenCode's web tools, as the guard names them: the guard refuses both, on every harness.
WEB = {"webfetch": "WebFetch", "websearch": "WebSearch", "codesearch": "WebSearch"}
# OpenCode's other tools the guard lets through as they are: subagents (their own calls come through
# the hook), the to-do list, skills, and OpenCode's stand-in for a malformed call.
KNOWN = {"task", "todowrite", "todoread", "skill", "invalid", "question", "plan_enter", "plan_exit"}
_told: set[str] = set()  # unknown tool names, logged once each

# How long the server may take to listen (its first start installs its plugin package into its
# config folder); how long a call to it may take; how long close() waits for it to exit.
START_TIMEOUT_S = 90.0
HTTP_TIMEOUT_S = 30.0
EXIT_TIMEOUT_S = 5.0
# How long a look that found the session still busy waits to look again (its idle event settles it first).
RECHECK_S = 2.0
# How often a prompt left unanswered as the session went idle is given to it again.
KICKS_MAX = 3
# OpenCode's own refusals, by the start of their text: should be none.
OWN_REFUSALS = ("The user rejected permission", "The user has specified a rule which prevents")

# The XDG folders, as a program finds them when none is set: what a tool's shell gets back.
XDG = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share", "XDG_CACHE_HOME": ".cache",
       "XDG_STATE_HOME": ".local/state"}
# OpenCode's switches: no project's config or AGENTS.md, nothing of ~/.claude, no update, no
# download, no compaction or pruning (they would rewrite the session, and its cache), no models.dev
# fetch (the model is in the config), no plugins but ours, no sharing.
FLAGS = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_AUTOCOMPACT": "1",
    "OPENCODE_DISABLE_PRUNE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_SHARE": "1",
}
_LISTENING = re.compile(r"listening on (https?://[^\s]+)")


def binary(env: Mapping[str, str] | None = None) -> str:
    """The opencode program: VFG_OPENCODE_BIN, else the one on PATH, else its installer's place."""
    env = os.environ if env is None else env
    return (env.get("VFG_OPENCODE_BIN") or shutil.which("opencode")
            or os.path.join(os.path.expanduser("~"), ".opencode", "bin", "opencode"))


def provider_id(cfg: ImplementerConfig) -> str:
    if cfg.provider not in PROVIDERS:
        raise ValueError("VFG_IMPL_PROVIDER=%s does not run in OpenCode: it is Claude Code's (on the owner's login); "
                         "OpenCode takes %s" % (cfg.provider, " or ".join(PROVIDERS)))
    return PROVIDERS[cfg.provider][0]


def openai_base(cfg: ImplementerConfig) -> str:
    """The provider's OpenAI-style address. cfg.base_url is the Claude-compatible one Claude Code is
    given: its default means the provider's own default here, and one set by VFG_IMPL_BASE_URL is
    turned the same way (DeepSeek's without /anthropic, OpenRouter's …/api with /v1)."""
    default = PROVIDERS[cfg.provider][2]
    url = (cfg.base_url or "").rstrip("/")
    if not url or url == BASE_URLS.get(cfg.provider, "").rstrip("/"):
        return default
    if cfg.provider == "deepseek" and url.endswith("/anthropic"):
        return url[:-len("/anthropic")]
    if cfg.provider == "openrouter" and url.endswith("/api"):
        return url + "/v1"
    return url


def model_ref(cfg: ImplementerConfig) -> dict:
    return {"providerID": provider_id(cfg), "modelID": cfg.model}


def effort_options(cfg: ImplementerConfig) -> dict:
    """The effort as the provider's API takes it: OpenRouter's `reasoning` (its `effort`, which it
    turns into the host's own, config.EFFORTS; at off, `enabled: false`), or DeepSeek's own fields
    (`thinking`, and `reasoning_effort`, low, high or max, which the AI SDK's openai-compatible
    package sends from its `reasoningEffort` option and drops when given as it is). Read off what
    OpenCode 1.18.30 sent, 2026-10-13."""
    off = cfg.effort == "off"
    if cfg.provider == "deepseek":
        return {"thinking": {"type": "disabled"}} if off else {"thinking": {"type": "enabled"},
                                                                 "reasoningEffort": cfg.effort}
    return {"reasoning": {"enabled": False} if off else {"effort": cfg.effort}}


def agent_options(cfg: ImplementerConfig) -> dict:
    """The agent's model options, which the AI SDK provider puts in every request's body: the effort,
    and on OpenRouter its `provider` routing (our order of the providers, those that cache badly
    left out, fallbacks allowed)."""
    routing = provider_routing(cfg)
    return {**effort_options(cfg), **({"provider": routing} if routing else {})}


def server_config(cfg: ImplementerConfig, system: str, plugin: str = PLUGIN) -> dict:
    """OpenCode's whole config: one provider with the one model, our agent (our system prompt, the
    effort, every tool allowed but those a person answers), our plugin, and nothing else of
    OpenCode's own around it. It holds no key: the key is `{env:VFG_OPENCODE_KEY}`, read from the
    server's environment."""
    pid = provider_id(cfg)
    _, npm, _ = PROVIDERS[cfg.provider]
    m = model_price(cfg.model)
    permission = {"*": "allow", **NO_PERSON}
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "snapshot": False,
        "model": "%s/%s" % (pid, cfg.model),
        "small_model": "%s/%s" % (pid, cfg.model),
        "default_agent": AGENT,
        "enabled_providers": [pid],
        "provider": {pid: {
            "npm": npm,
            "options": {"apiKey": "{env:VFG_OPENCODE_KEY}", "baseURL": openai_base(cfg)},
            "models": {cfg.model: {"name": cfg.model, "tool_call": True, "reasoning": True, "temperature": True,
                                   "attachment": False,
                                   "limit": {"context": (m.context if m and m.context else 1_000_000),
                                             "output": OUTPUT_MAX}}},
        }},
        "agent": {AGENT: {"mode": "primary", "description": "An EverHear implementer.",
                          "prompt": "{file:%s}" % system, "permission": permission,
                          "options": agent_options(cfg)}},
        "permission": permission,
        "compaction": {"auto": False, "prune": False},
        "lsp": False,
        "formatter": False,
        "mcp": {},
        "instructions": [],
        "plugin": [Path(plugin).as_uri()],
    }


@dataclass
class Dirs:
    """The server's own folders, all under the brain's folder."""
    root: str

    @property
    def config_file(self) -> str:
        return os.path.join(self.root, "opencode.json")

    def xdg(self) -> dict[str, str]:
        return {k: os.path.join(self.root, k.split("_")[1].lower()) for k in XDG}

    def make(self) -> None:
        for d in [self.root, *self.xdg().values()]:
            os.makedirs(d, mode=0o700, exist_ok=True)


def server_env(cfg: ImplementerConfig, dirs: Dirs, guard_url: str, guard_secret: str, password: str,
               source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The server's environment: this process's, with nothing of another OpenCode in it, its XDG
    folders and config under the brain's folder, OpenCode's switches, its shell tool's default
    timeout (OpenCode 1.18.30 reads OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS; it has no setting
    for the most), the provider's key, the guard's address and secret, and the server's password;
    and VFG_SHELL_ENV, what a tool's shell gets back (the XDG folders as they were, the secrets
    blanked)."""
    source = os.environ if source is None else source
    home = source.get("HOME") or os.path.expanduser("~")
    env = {k: v for k, v in source.items() if not k.startswith("OPENCODE") and k not in XDG
           and k not in ("VFG_OPENCODE_KEY", "VFG_GUARD_URL", "VFG_GUARD_SECRET", "VFG_SHELL_ENV")}
    shell = {k: source.get(k) or os.path.join(home, rel) for k, rel in XDG.items()}
    shell.update({"VFG_OPENCODE_KEY": "", "VFG_GUARD_URL": "", "VFG_GUARD_SECRET": "", "OPENCODE_SERVER_PASSWORD": "",
                  "OPENCODE_TEST_HOME": ""})
    env.update(dirs.xdg())
    env.update(FLAGS)
    env.update({
        "OPENCODE_CONFIG": dirs.config_file,
        # The home OpenCode looks in for ~/.opencode (a config folder it always reads) and ~/.claude:
        # its own folder. Only OpenCode's own reading moves; HOME, and every tool's, stay.
        "OPENCODE_TEST_HOME": dirs.root,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": str(bash_timeouts_ms(cfg)[0]),
        "VFG_OPENCODE_KEY": cfg.key,
        "VFG_GUARD_URL": guard_url,
        "VFG_GUARD_SECRET": guard_secret,
        "VFG_SHELL_ENV": json.dumps(shell),
        "PYTHONPYCACHEPREFIX": os.path.join(cfg.brain_dir, "pycache"),  # py_compile leaves no __pycache__ in made/
    })
    return env


# ------------------------------------------------------------------ OpenCode's tool calls, in the guard's shape

_PATCH_FILE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def _abs(repo: str, p: Any) -> str:
    """A path as OpenCode takes it: relative to the session's folder, the repo."""
    s = str(p)
    return s if os.path.isabs(s) or s.startswith("~") else os.path.join(repo, s)


def guard_calls(tool: str, args: Any, repo: str) -> list[dict]:
    """One OpenCode tool call as the guard's calls ({"tool_name", "tool_input"} with Claude Code's
    names): bash, read, write, edit, glob and grep as Bash, Read, Write, Edit, Glob and Grep; list as
    a path check like Glob; a bash's workdir as a path check too; and a tool that writes files
    (apply_patch, patch, multiedit, …) as a Write or an Edit for each file it touches. Any other
    web tool (webfetch, websearch) goes as WebFetch or WebSearch, which the guard refuses; any other
    tool (task, todowrite, skill, …) goes as it is, and the guard lets it through, an unknown name
    logged once."""
    a = args if isinstance(args, dict) else {}
    path = a.get("filePath") or a.get("file_path")
    if tool == "bash":
        out = [{"tool_name": "Bash", "tool_input": {"command": a.get("command")}}]
        if a.get("workdir"):
            out.append({"tool_name": "Glob", "tool_input": {"path": _abs(repo, a["workdir"])}})
        return out
    if tool == "read":
        return [{"tool_name": "Read", "tool_input": {"file_path": _abs(repo, path) if path else path}}]
    if tool == "write":
        return [{"tool_name": "Write", "tool_input": {"file_path": _abs(repo, path) if path else path,
                                                      "content": a.get("content")}}]
    if tool == "edit":
        return [{"tool_name": "Edit", "tool_input": {"file_path": _abs(repo, path) if path else path,
                                                     "old_string": a.get("oldString"),
                                                     "new_string": a.get("newString"),
                                                     "replace_all": a.get("replaceAll")}}]
    if tool in ("glob", "grep", "list"):
        inp = {k: v for k, v in (("pattern", a.get("pattern")), ("glob", a.get("include")),
                                 ("path", _abs(repo, a["path"]) if a.get("path") else None)) if v is not None}
        return [{"tool_name": "Grep" if tool == "grep" else "Glob", "tool_input": inp}]
    if tool in ("apply_patch", "patch") or "patchText" in a:
        out = []
        for kind, p in _PATCH_FILE.findall(str(a.get("patchText") or a.get("patch") or "")):
            name = "Write" if kind in ("Add File", "Move to") else "Edit"
            out.append({"tool_name": name, "tool_input": {"file_path": _abs(repo, p)}})
        return out or [{"tool_name": "Edit", "tool_input": {"file_path": None}}]
    if tool == "multiedit" or (path and re.search(r"edit|write|patch", tool or "")):
        return [{"tool_name": "Edit", "tool_input": {"file_path": _abs(repo, path) if path else path}}]
    if tool in WEB:
        return [{"tool_name": WEB[tool], "tool_input": a}]
    if tool not in KNOWN and tool not in _told:
        _told.add(tool)
        log("guard", "OpenCode's tool %s is not one the guard knows: it goes through as it is" % tool)
    return [{"tool_name": tool, "tool_input": a}]


# ------------------------------------------------------------------ the server's process

class Process(Protocol):
    """A started server: its address, and how to wait for it and end it."""
    address: str

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class _Spawned:
    def __init__(self, proc: asyncio.subprocess.Process, address: str):
        self.proc = proc
        self.address = address
        self.echo: asyncio.Task | None = None

    async def wait(self) -> int:
        return await self.proc.wait()

    def terminate(self) -> None:
        if self.proc.returncode is None:
            self.proc.terminate()

    def kill(self) -> None:
        if self.proc.returncode is None:
            self.proc.kill()


async def _echo(stream: asyncio.StreamReader | None, where: str) -> None:
    """The server's output, a line at a time, into the log."""
    if stream is None:
        return
    while line := await stream.readline():
        text = line.decode("utf-8", "replace").rstrip()
        if text and "server is unsecured" not in text:
            log("opencode", "%s: %s" % (where, text[:500]))


async def spawn(argv: list[str], env: dict[str, str], cwd: str) -> Process:
    """The real server: `opencode serve` on a port of its choosing, its address read from its first
    line."""
    proc = await asyncio.create_subprocess_exec(*argv, env=env, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(START_TIMEOUT_S):
            while True:
                line = await proc.stdout.readline()
                if not line:
                    raise RuntimeError("opencode serve exited before it listened (code %s)" % await proc.wait())
                m = _LISTENING.search(line.decode("utf-8", "replace"))
                if m:
                    break
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        raise
    out = _Spawned(proc, m.group(1).rstrip("/"))

    async def echo() -> None:
        await asyncio.gather(_echo(proc.stdout, "out"), _echo(proc.stderr, "err"))

    out.echo = asyncio.get_running_loop().create_task(echo())
    return out


Spawner = Callable[[list[str], dict[str, str], str], Awaitable[Process]]


# ------------------------------------------------------------------ the guard's endpoint

async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes] | None:
    line = await reader.readline()
    if not line:
        return None
    parts = line.decode("latin-1").strip().split(" ")
    if len(parts) != 3:
        raise ValueError("not an HTTP request line")
    headers = {}
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers[k.strip().lower()] = v.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while True:
            size = int((await reader.readline()).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                break
            body += await reader.readexactly(size)
            await reader.readexactly(2)
        return parts[0].upper(), parts[1], headers, bytes(body)
    n = int(headers.get("content-length") or 0)
    return parts[0].upper(), parts[1], headers, (await reader.readexactly(n) if n > 0 else b"")


async def _answer(writer: asyncio.StreamWriter, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    reason = {200: "OK", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}.get(status, "")
    writer.write(b"HTTP/1.1 %d %s\r\ncontent-type: application/json\r\ncontent-length: %d\r\nconnection: close\r\n\r\n%s"
                 % (status, reason.encode(), len(data), data))
    await writer.drain()


# ------------------------------------------------------------------ one run

_END = object()


class _Broke:
    def __init__(self, error: BaseException):
        self.error = error


class OpenCodeRun:
    """One run of a task's session: its first prompt, and the messages sent into it, to the point
    the session is idle with every prompt answered. Its events come from the server's event stream,
    through one queue."""

    def __init__(self, harness: "OpenCode", task: str, guard: GuardContext, session: str):
        self.h = harness
        self.task = task
        self.guard = guard
        self.session = session
        self.began = time.time()
        self.ended = False  # its Done is decided: a message now resumes the session
        self.turns = 0  # its model requests (OpenCode's steps), a subagent's too
        self.errors: list[str] = []
        self.refused: list[str] = []  # OpenCode's own refusals
        self.denied: set[str] = set()  # the call ids the guard refused
        self._q: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._seen: set[str] = set()  # part ids given as events
        self._calls: set[str] = set()
        self._step_at: dict[str, float] = {}
        self._texts: list[str] = []
        self._stopping: str | None = None  # why stop() or the turn cap ended it
        self._capped = False
        self._kicks = 0
        self._settling: asyncio.Task | None = None
        self._again = False
        self._thought = False

    # ---------------------------------------------------------------- the runner's side

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
        """A prompt into the busy session, read at its next step; False once the run has ended."""
        async with self._lock:
            if self.ended or self._stopping:
                return False
            try:
                await self.h.prompt(self.session, text)
            except Exception as e:
                log("opencode", "%s: a message could not be sent: %s: %s" % (self.task, type(e).__name__, e))
                return False
            return True

    async def stop(self) -> None:
        await self._abort("it was stopped")

    async def close(self) -> None:
        busy = not self.ended
        self.ended = True
        if busy and self.session and self.h.alive():  # never leave a session running unwatched
            try:
                await self.h.abort(self.session)
            except Exception as e:
                log("opencode", "%s: abort at close: %s" % (self.task, e))
        if self._settling is not None and not self._settling.done():
            self._settling.cancel()
        self.h.forget(self)
        self._q.put_nowait(_END)

    # ---------------------------------------------------------------- the adapter's side

    def put(self, ev) -> None:
        if not self.ended:
            self._q.put_nowait(ev)

    def broke(self, error: BaseException) -> None:
        if not self.ended:
            self.ended = True
            self._q.put_nowait(_Broke(error))

    async def _abort(self, why: str, capped: bool = False) -> None:
        if self.ended or self._stopping:
            return
        self._stopping, self._capped = why, capped
        if not self.h.alive():
            self._finish(Done(STOPPED, why, turns=self.turns, errors=self.errors, seconds=self._seconds()))
            return
        try:
            await self.h.abort(self.session)
        except Exception as e:
            log("opencode", "%s: abort: %s: %s" % (self.task, type(e).__name__, e))
        self.settle()  # an idle session sends no idle event again: look

    def settle(self) -> None:
        """Looks whether the run is over, off the event stream's path; once more after a look in
        progress, when an idle came during it."""
        if self.ended:
            return
        if self._settling is not None and not self._settling.done():
            self._again = True
            return
        self._again = False
        self._settling = asyncio.get_running_loop().create_task(self._settle())

    async def _settle(self) -> None:
        try:
            await self._look()
        finally:
            if self._again and not self.ended:
                asyncio.get_running_loop().call_soon(self.settle)

    async def _look(self) -> None:
        try:
            async with self._lock:
                if self.ended:
                    return
                status = await self.h.status()
                if (status.get(self.session) or {}).get("type") not in (None, "idle"):
                    self._later()  # still going: its next idle event settles it, or this look again
                    return
                msgs = await self.h.messages(self.session)
                users = [m for m in msgs if _info(m).get("role") == "user"]
                last_user = users[-1] if users else None
                answers = [m for m in msgs if _info(m).get("role") == "assistant"
                           and last_user is not None and _info(m).get("parentID") == _info(last_user).get("id")]
                last = answers[-1] if answers else None
                li = _info(last) if last else {}
                answered = last is not None and (li.get("error") or li.get("finish") not in (None, "tool-calls", "unknown"))
                if not answered and not self._stopping and last_user is not None:
                    if self._kicks < KICKS_MAX:  # it landed as the session went idle: give it again
                        self._kicks += 1
                        log("opencode", "%s: a prompt was left unread as the session went idle: given again" % self.task)
                        await self.h.kick(self.session, last_user)
                        return
                    self.errors.append("a prompt was left unanswered")
                self._finish(self._done(last, answered))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.broke(RuntimeError("OpenCode did not say how the run ended: %s: %s" % (type(e).__name__, e)))

    def _later(self) -> None:
        async def again() -> None:
            await asyncio.sleep(RECHECK_S)
            self.settle()
        asyncio.get_running_loop().create_task(again())

    def _seconds(self) -> float:
        return round(time.time() - self.began, 1)

    def _done(self, last: dict | None, answered: bool) -> Done:
        common = {"turns": self.turns, "errors": list(self.errors), "seconds": self._seconds(),
                  "refused": list(self.refused)}
        text = _text(last) if last else ""
        if not text and self._texts:
            text = self._texts[-1]
        if self._capped:
            return Done(TURNS, text, **common)
        if self._stopping:
            return Done(STOPPED, self._stopping, **common)
        err = _info(last).get("error") if last else None
        if err:
            return Done(ERROR, "OpenCode: %s" % _error_text(err), **common)
        if not answered:
            return Done(ERROR, text or "the run ended with its last prompt unanswered", **common)
        return Done(OK, text, **common)

    def _finish(self, done: Done) -> None:
        if self.ended:
            return
        self.ended = True
        self._q.put_nowait(done)

    def on_event(self, typ: str, p: dict, sid: str) -> None:
        """One event of this run's session, or of a subagent's session under it."""
        main = sid == self.session
        if typ == "message.part.updated":
            self._part(p.get("part") or {}, main)
        elif typ == "session.idle" and main:
            self.settle()
        elif typ == "session.status" and main:
            st = p.get("status") or {}
            if st.get("type") == "idle":
                self.settle()
            elif st.get("type") == "retry":
                log("opencode", "%s: the provider is retried (attempt %s): %s" % (
                    self.task, st.get("attempt"), str(st.get("message") or "")[:200]))
        elif typ == "session.error" and main:
            err = p.get("error") or {}
            if not (self._stopping and err.get("name") == "MessageAbortedError"):
                self.errors.append(_error_text(err))
        elif typ == "session.compacted" and main:
            self.put(Compacted("auto"))

    def _part(self, part: dict, main: bool) -> None:
        kind, pid = part.get("type"), part.get("id")
        if kind == "step-start":
            self._step_at.setdefault(part.get("messageID") or "", time.time())
        elif kind == "step-finish":
            if not pid or pid in self._seen:
                return
            self._seen.add(pid)
            t = part.get("tokens") or {}
            cache = t.get("cache") or {}
            reasoning = _n(t.get("reasoning"))
            if reasoning and not self._thought and self.h.cfg.effort == "off":
                self._thought = True
                log("opencode", "%s: a request thought (%d reasoning tokens): thinking is not off" % (self.task, reasoning))
            self.put(Request(model=self.h.cfg.model, input=_n(t.get("input")), cache_read=_n(cache.get("read")),
                             cache_write=_n(cache.get("write")), output=_n(t.get("output")) + reasoning,
                             at=self._step_at.pop(part.get("messageID") or "", time.time()), id=pid,
                             reasoning=reasoning))
            self.turns += 1
            if part.get("reason") == "tool-calls" and self.turns >= self.h.cfg.max_turns and not self._stopping:
                log("opencode", "%s: its turn cap, %d turns: aborted" % (self.task, self.h.cfg.max_turns))
                asyncio.get_running_loop().create_task(
                    self._abort("it used its %d turns" % self.h.cfg.max_turns, capped=True))
        elif kind == "tool":
            st = part.get("state") or {}
            status, call = st.get("status"), part.get("callID") or pid
            if status in ("running", "completed", "error") and call not in self._calls:
                self._calls.add(call)
                g = guard_calls(part.get("tool") or "", st.get("input") or {}, self.guard.repo)[0]
                self.put(ToolCall(g["tool_name"], g["tool_input"], call))
            if status in ("completed", "error") and ("r:" + call) not in self._seen:
                self._seen.add("r:" + call)
                said = str((st.get("output") if status == "completed" else st.get("error")) or "")
                self.put(ToolResult(call, status == "error", said[:500]))
                if (status == "error" and call not in self.denied
                        and any(said.startswith(s) for s in OWN_REFUSALS)):
                    self.refused.append("%s %s" % (part.get("tool"), brief(st.get("input"))))
        elif kind == "text" and main:
            if not pid or pid in self._seen or not (part.get("time") or {}).get("end") or part.get("synthetic"):
                return
            text = str(part.get("text") or "")
            if text.strip():
                self._seen.add(pid)
                self._texts.append(text)
                self.put(Text(text))
        elif kind == "compaction" and main and pid not in self._seen:
            self._seen.add(pid)
            self.put(Compacted("auto" if part.get("auto") else "", None))


def _info(m: dict | None) -> dict:
    return (m or {}).get("info") or {}


def _text(m: dict) -> str:
    return "\n".join(str(p.get("text") or "") for p in m.get("parts") or []
                     if p.get("type") == "text" and not p.get("synthetic") and str(p.get("text") or "").strip()).strip()


def _error_text(err: dict) -> str:
    data = err.get("data") or {}
    return "%s: %s" % (err.get("name") or "error", str(data.get("message") or json.dumps(data))[:500])


def _n(v) -> int:
    return int(v) if isinstance(v, (int, float)) else 0


# ------------------------------------------------------------------ the harness

class OpenCode:
    name = "opencode"

    def __init__(self, cfg: ImplementerConfig, spawner: Spawner | None = None):
        """`spawner` starts the server (tests pass a fake)."""
        self.cfg = cfg
        self.spawner = spawner or spawn
        self.dirs = Dirs(os.path.join(cfg.brain_dir, "opencode"))
        self.system: str | None = None
        self._proc: Process | None = None
        self._http: httpx.AsyncClient | None = None
        self._pump: asyncio.Task | None = None
        self._watch: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._lock = asyncio.Lock()
        self._owner: dict[str, OpenCodeRun] = {}  # a session, a subagent's too -> its run
        self._runs: set[OpenCodeRun] = set()
        self._guard_server: asyncio.base_events.Server | None = None
        self._guard_url = ""
        self._secret = ""
        self._closing = False
        self._exited = False

    # ---------------------------------------------------------------- Harness

    async def start(self, task: str, system: Path, brief: str, guard: GuardContext) -> OpenCodeRun:
        await self._ensure(str(system))
        info = await self._call("POST", "/session", {"title": "vfg %s" % task})
        run = OpenCodeRun(self, task, guard, info["id"])
        self._own(run)
        run.put(Session(run.session))
        log("opencode", "%s: session %s, model %s/%s" % (task, run.session, provider_id(self.cfg), self.cfg.model))
        return await self._begin(run, brief)

    async def resume(self, task: str, system: Path, session: str, text: str, guard: GuardContext) -> OpenCodeRun:
        await self._ensure(str(system))
        try:
            await self._call("GET", "/session/%s" % session)
        except httpx.HTTPStatusError as e:
            raise RuntimeError("OpenCode has no session %s to resume (HTTP %d)" % (session, e.response.status_code))
        run = OpenCodeRun(self, task, guard, session)
        self._own(run)
        return await self._begin(run, text)

    async def close(self) -> None:
        self._closing = True
        await self._stop_server()
        if self._guard_server is not None:
            self._guard_server.close()
            await self._guard_server.wait_closed()
            self._guard_server = None
        self._closing = False

    # ---------------------------------------------------------------- the runs' calls

    async def prompt(self, session: str, text: str) -> None:
        await self._call("POST", "/session/%s/prompt_async" % session,
                         {"agent": AGENT, "model": model_ref(self.cfg), "parts": [{"type": "text", "text": text}]})

    async def kick(self, session: str, user: dict) -> None:
        """A prompt the session left unanswered, given to it again as the same message and parts."""
        parts = [{"id": p["id"], "type": "text", "text": p.get("text") or ""} for p in user.get("parts") or []
                 if p.get("type") == "text" and p.get("id")]
        await self._call("POST", "/session/%s/prompt_async" % session,
                         {"messageID": _info(user)["id"], "agent": AGENT, "model": model_ref(self.cfg),
                          "parts": parts})

    async def abort(self, session: str) -> None:
        await self._call("POST", "/session/%s/abort" % session, {})

    async def status(self) -> dict:
        return await self._call("GET", "/session/status") or {}

    async def messages(self, session: str) -> list[dict]:
        return await self._call("GET", "/session/%s/message" % session) or []

    def alive(self) -> bool:
        return (self._proc is not None and self._http is not None and not self._exited
                and self._pump is not None and not self._pump.done())

    def forget(self, run: OpenCodeRun) -> None:
        self._runs.discard(run)
        for sid in [s for s, r in self._owner.items() if r is run]:
            del self._owner[sid]

    # ---------------------------------------------------------------- inside

    def _own(self, run: OpenCodeRun) -> None:
        other = self._owner.get(run.session)
        if other is not None and other is not run:  # a session runs one run at a time: the runner's rule
            log("opencode", "%s: session %s had a run still open: it is closed" % (run.task, run.session))
            other.broke(RuntimeError("a new run of its session began"))
            self.forget(other)
        self._owner[run.session] = run
        self._runs.add(run)

    async def _begin(self, run: OpenCodeRun, text: str) -> OpenCodeRun:
        try:
            await self.prompt(run.session, text)
        except BaseException:
            await run.close()
            raise
        return run

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        if self._http is None:
            raise RuntimeError("the OpenCode server is not running")
        r = await self._http.request(method, path, json=body)
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def _ensure(self, system: str) -> None:
        """The server, started at the first run, and again when it has died; and started anew with
        a new system prompt once no run is open (OpenCode reads the agent's prompt at start)."""
        provider_id(self.cfg)
        if not self.cfg.key:
            raise RuntimeError("no key for %s: %s" % (self.cfg.provider, "set DEEPSEEK_API_KEY"
                                                      if self.cfg.provider == "deepseek" else
                                                      "set OPEN_ROUTER_API, or put it alone in ~/.vfg/openrouter.key"))
        async with self._lock:
            if self.alive() and self.system != system:
                if self._runs:
                    log("opencode", "the system prompt changed (%s): the server keeps %s until no run is open"
                        % (os.path.basename(system), os.path.basename(self.system or "")))
                    return
                log("opencode", "the system prompt changed: the server starts again with %s" % os.path.basename(system))
                await self._stop_server()
            if self.alive():
                return
            if self._proc is not None:  # it died
                await self._stop_server()
            await self._start_server(system)

    async def _start_guard(self) -> None:
        if self._guard_server is not None:
            return
        self._secret = secrets.token_hex(24)
        self._guard_server = await asyncio.start_server(self._serve_guard, "127.0.0.1", 0)
        port = self._guard_server.sockets[0].getsockname()[1]
        self._guard_url = "http://127.0.0.1:%d/guard" % port
        log("opencode", "the guard answers OpenCode's plugin on 127.0.0.1:%d" % port)

    async def _start_server(self, system: str) -> None:
        await self._start_guard()
        self.dirs.make()
        tmp = "%s.%d.tmp" % (self.dirs.config_file, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(server_config(self.cfg, system), f, indent=1)
        os.replace(tmp, self.dirs.config_file)
        password = secrets.token_hex(24)
        env = server_env(self.cfg, self.dirs, self._guard_url, self._secret, password)
        argv = [binary(), "serve", "--port", "0", "--hostname", "127.0.0.1"]
        began = time.time()
        self._proc = await self.spawner(argv, env, self.cfg.repo)
        self._exited = False
        self._http = httpx.AsyncClient(base_url=self._proc.address, auth=httpx.BasicAuth(USER, password),
                                       headers={"accept": "application/json"},
                                       timeout=httpx.Timeout(HTTP_TIMEOUT_S))
        self._connected.clear()
        loop = asyncio.get_running_loop()
        self._pump = loop.create_task(self._read_events())
        self._watch = loop.create_task(self._watch_process(self._proc))
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                await self._connected.wait()
        except BaseException:
            await self._stop_server()
            raise
        self.system = system
        log("opencode", "serve at %s in %.1f s: provider %s at %s, model %s, system prompt %s, data in %s" % (
            self._proc.address, time.time() - began, provider_id(self.cfg), openai_base(self.cfg), self.cfg.model,
            os.path.basename(system), self.dirs.root))

    async def _stop_server(self) -> None:
        proc, http, pump, watch = self._proc, self._http, self._pump, self._watch
        self._proc = self._http = self._pump = self._watch = None
        for t in (pump, watch):
            if t is not None and not t.done():
                t.cancel()
        if http is not None:
            await http.aclose()
        if proc is not None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), EXIT_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            log("opencode", "serve stopped")
        for run in list(self._runs):
            run.broke(RuntimeError("the OpenCode server stopped"))
        self._runs.clear()
        self._owner.clear()

    async def _watch_process(self, proc: Process) -> None:
        code = await proc.wait()
        if proc is self._proc and not self._closing:
            self._exited = True
            log("opencode", "serve exited (code %s)" % code)
            for run in list(self._runs):
                run.broke(RuntimeError("the OpenCode server exited (code %s)" % code))
            if self._pump is not None and not self._pump.done():
                self._pump.cancel()

    async def _read_events(self) -> None:
        """The server's event stream, each event to the run whose session it is; read again when it
        drops, and each open run looked at then, for an idle it missed."""
        first = True
        while True:
            try:
                async with self._http.stream("GET", "/event", headers={"accept": "text/event-stream"},
                                             timeout=httpx.Timeout(HTTP_TIMEOUT_S, read=None)) as r:
                    r.raise_for_status()
                    data: list[str] = []
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            self._dispatch("\n".join(data), first)
                            first, data = False, []
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log("opencode", "the event stream dropped: %s: %s" % (type(e).__name__, e))
            if self._proc is None:
                return
            await asyncio.sleep(0.5)

    def _dispatch(self, raw: str, first: bool) -> None:
        try:
            ev = json.loads(raw)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        typ, p = ev.get("type"), ev.get("properties") or {}
        if typ == "server.connected":
            self._connected.set()
            if not first:
                for run in list(self._runs):
                    run.settle()
            return
        info = p.get("info") if isinstance(p.get("info"), dict) else {}
        part = p.get("part") if isinstance(p.get("part"), dict) else {}
        sid = p.get("sessionID") or info.get("sessionID") or part.get("sessionID")
        if typ == "session.created" and info.get("parentID") in self._owner:
            self._owner[info["id"]] = self._owner[info["parentID"]]  # a subagent's session is its run's
        run = self._owner.get(sid)
        if run is None:
            return
        if typ == "permission.asked":
            # OpenCode's own check asking, which its config should never let happen: the guard has
            # already let the call through, so it is let through here too, and logged.
            log("opencode", "%s: OpenCode asked leave for %s %s: given" % (run.task, p.get("permission"),
                                                                            json.dumps(p.get("patterns"))[:200]))
            asyncio.get_running_loop().create_task(self._reply(p.get("id"), "once"))
            return
        if typ == "question.asked":
            asyncio.get_running_loop().create_task(self._call("POST", "/question/%s/reject" % p.get("id"), {}))
            return
        run.on_event(typ, p, sid)

    async def _reply(self, request: str | None, reply: str) -> None:
        try:
            await self._call("POST", "/permission/%s/reply" % request, {"reply": reply})
        except Exception as e:
            log("opencode", "the leave could not be given: %s" % e)

    # ---------------------------------------------------------------- the guard's endpoint

    async def _owner_of(self, sid: str) -> OpenCodeRun | None:
        """The run a session is, or is under (a subagent's), asking OpenCode for its parents when the
        event that tells has not come yet."""
        seen = []
        cur = sid
        for _ in range(8):
            run = self._owner.get(cur)
            if run is not None:
                for s in seen:
                    self._owner[s] = run
                return run
            try:
                info = await self._call("GET", "/session/%s" % cur)
            except Exception:
                return None
            seen.append(cur)
            cur = (info or {}).get("parentID")
            if not cur:
                return None
        return None

    async def check(self, body: dict) -> str | None:
        """The guard's answer to one tool call the plugin asks about: a reason to refuse it, or None."""
        sid = str(body.get("session") or "")
        tool = str(body.get("tool") or "")
        args = body.get("args")
        run = await self._owner_of(sid) if sid else None
        if run is None or run.ended:
            log("guard", "denied %s in session %s: no open run owns it" % (tool, sid or "-"))
            return "No implementer run is open for this session, so the call was not run."
        g = run.guard
        calls = guard_calls(tool, args, g.repo)
        reason = next((r for r in (guards.decide(c, g) for c in calls) if r), None)
        for c in calls if reason is None else []:
            reason = await guards.gate(c, g)
            if reason:
                break
        if reason:
            run.denied.add(str(body.get("call") or ""))
            log("guard", "denied implementer %s %s: %s (%s)" % (run.task, tool, reason, brief(args)))
        return reason

    async def _serve_guard(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await _read_request(reader)
            if req is None:
                return
            method, target, headers, body = req
            if method != "POST" or target.split("?")[0] != "/guard":
                await _answer(writer, 404, {"error": "no such path"})
                return
            if not hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + self._secret).encode()):
                log("guard", "a call to the guard's endpoint had the wrong secret: refused")
                await _answer(writer, 403, {"error": "wrong secret"})
                return
            try:
                reason = await self.check(json.loads(body or b"{}"))
            except Exception as e:  # the plugin refuses the call on a 500
                log("guard", "the guard broke on an OpenCode call: %s: %s" % (type(e).__name__, e))
                await _answer(writer, 500, {"error": "the guard broke"})
                return
            await _answer(writer, 200, {"deny": reason})
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            return
        finally:
            writer.close()
