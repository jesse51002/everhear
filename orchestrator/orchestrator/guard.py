"""The PreToolUse hook on every tool. It denies with a reason the model can act on, and
otherwise returns {} so the allow rules (permission mode "dontAsk") decide."""
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import brief as briefing  # guard.brief() is the log helper below
from .config import log
from .reply import REPLY_TOOL

# Server commands that are the owner's to run. scripts/_lib/mc.py refuses the same words.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip", "whitelist", "kick", "save-off"])

# Server-wide settings an implementer may not change (scripts/_lib/mc.py does not refuse them:
# seeded games use the world border).
SERVER_WIDE = frozenset(["setworldspawn", "defaultgamemode", "difficulty", "worldborder"])

# The main thread orchestrates: these, and Bash for `vfg place list` alone.
MAIN_THREAD_TOOLS = frozenset(["Agent", "TaskStop", "Read", "Glob", "Grep", REPLY_TOOL])


class DENY:
    main_thread = "You orchestrate: give this to an implementer."
    owner = "That is the server owner's to do; report it."

    @staticmethod
    def server_wide(word: str) -> str:
        return "%s changes the whole server: implementers may not run it. Leave it out and say so in your report." % word

    lib = "Never edit scripts/_lib/: it is shared by every script. Copy what you need into your own script."
    expansion = "Write the server command literally inside single quotes: no $( ), backticks or variables in a vfg cmd."
    agent_type = 'Use subagent_type "implementer": it is the only agent you have.'
    foreground = ("Implementers run in the background: leave run_in_background out, end your turn, and you are "
                  "woken when it reports.")
    overrides = "Leave model, mode and isolation out: the implementer definition sets them."
    shape = (
        "Refused, but Bash works: only these run, each as a plain command (join several with && or ;): "
        "vfg cmd '<command>' | vfg script deploy|run|stop|logs|list ... | vfg place save|list ... | vfg history ... | "
        "python3 -m py_compile <file> | sleep N. No cd, echo, ls, cat, grep, sed, ps, find, $?, pipes into "
        "other programs, or redirections: vfg's output and exit status come back to you as they are, and the "
        "Read, Glob and Grep tools look at files. Send the allowed command again on its own."
    )
    follow = "vfg script logs --follow never ends: use vfg script logs <name> -n 40, and sleep between looks."
    outside = "Only the repository can be searched: give a path inside it, or none."
    read_outside = "Only files in the repository can be read."
    no_agents = "There are no implementers: do the task yourself with Bash, Read, Write, Edit, Glob and Grep."
    background = "Run commands in the foreground: scripts already run detached on the server (vfg script run)."


@dataclass
class GuardContext:
    repo: str
    max_implementers: int
    implementer_model: str
    # True for the runner: the main thread only orchestrates. False for the permission probe, whose
    # main thread is the implementer, held to the implementer's rules.
    main_thread_rule: bool
    open_task_ids: Callable[[], list[str]]
    # Implementers running plus those allowed and not yet started.
    active_implementers: Callable[[], int]
    # An Agent call was allowed: count it until its task_started arrives.
    on_agent_allowed: Callable[[str, str], None]


def task_id_of(description) -> str | None:
    """The task id at the head of an implementer's description ("T7 castle at spawn")."""
    m = re.match(r"\s*(T\d+)\b", "" if description is None else str(description), re.ASCII)
    return m.group(1) if m else None


def command_word_in(cmd: str, words_set: frozenset) -> str | None:
    """The word of `words_set` a server command runs (its first word, or the word after any `run`), or None."""
    words = cmd.split()
    if not words:
        return None
    checks = [words[0]]
    for i in range(len(words) - 1):
        if words[i].lower() == "run":
            checks.append(words[i + 1])
    for w in checks:
        word = w.lstrip("/").lower()
        if word in words_set:
            return word
    return None


def refused_word(cmd: str) -> str | None:
    """The refused word in a server command (its first word, or the word after any `run`), or None."""
    return command_word_in(cmd, REFUSED)


@dataclass
class ShellCommand:
    words: list[str]
    # A $( ), backtick or $variable outside single quotes.
    expands: bool
    # A < or > outside quotes.
    redirects: bool


def split_shell(line: str) -> list[ShellCommand]:
    """Splits a shell command line into simple commands and their words, honouring quotes."""
    out: list[ShellCommand] = []
    words: list[str] = []
    word = ""
    in_word = False
    expands = False
    redirects = False

    def end_word():
        nonlocal word, in_word
        if in_word:
            words.append(word)
        word = ""
        in_word = False

    def end_cmd():
        nonlocal words, expands, redirects
        end_word()
        if words or redirects:
            out.append(ShellCommand(words, expands, redirects))
        words = []
        expands = False
        redirects = False

    n = len(line)
    i = 0
    while i < n:
        c = line[i]
        if c == "'":
            in_word = True
            j = line.find("'", i + 1)
            end = n if j < 0 else j
            word += line[i + 1:end]
            i = end
        elif c == '"':
            in_word = True
            j = i + 1
            while j < n and line[j] != '"':
                d = line[j]
                if d == "\\" and j + 1 < n and line[j + 1] in '"\\$`\n':
                    j += 1
                    word += line[j]
                else:
                    if d in "$`":
                        expands = True
                    word += d
                j += 1
            i = j
        elif c == "\\":
            in_word = True
            if i + 1 < n:
                i += 1
                word += line[i]
        elif c in " \t":
            end_word()
        elif c in "\n;&|()":
            end_cmd()
        else:
            if c in "$`":
                expands = True
            if c in "<>":
                redirects = True
            in_word = True
            word += c
        i += 1
    end_cmd()
    return out


def allowed_form(words: list[str]) -> bool:
    """One of the commands the allow rules let an implementer run (session.py `rules`)."""
    a, b, c = (words + [None, None, None])[:3]
    if a == "sleep":
        return len(words) >= 2
    if a == "python3":
        return b == "-m" and c == "py_compile" and len(words) >= 4
    if a != "vfg":
        return False
    if b == "cmd":
        return len(words) >= 3
    if b == "history":
        return True
    if b == "place":
        return c == "list" or (c == "save" and len(words) >= 4)
    if b != "script":
        return False
    if c == "list":
        return len(words) == 3
    if c == "deploy":
        return len(words) <= 4
    return c in ("run", "stop", "logs") and len(words) >= 4


def check_bash(command: str) -> str | None:
    """Why an implementer's Bash command is refused, or None to let the rules decide. Claude Code's
    own refusal of a disallowed command reads as if Bash itself were off, and a small model stops
    trying; this one says which commands do run."""
    cmds = split_shell(command)
    # Server commands that are the owner's, however they are dressed up, and the server-wide settings.
    for cmd in cmds:
        w = list(cmd.words)
        while w and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w.pop(0)  # FOO=bar vfg ...
        prog = w[0].split("/")[-1] if w else None
        if prog != "vfg" or len(w) < 2 or w[1] != "cmd":
            continue
        args = w[2:]
        for a in [" ".join(args)] + args:
            if refused_word(a):
                return DENY.owner
        for a in [" ".join(args)] + args:
            word = command_word_in(a, SERVER_WIDE)
            if word:
                return DENY.server_wide(word)
    for cmd in cmds:
        w = cmd.words
        is_cmd = len(w) >= 2 and w[0] == "vfg" and w[1] == "cmd"
        if is_cmd and cmd.expands:
            return DENY.expansion
        if cmd.redirects or cmd.expands or not allowed_form(w):
            return DENY.shape
        if len(w) >= 3 and w[0] == "vfg" and w[1] == "script" and w[2] == "logs" and "--follow" in w:
            return DENY.follow
    return None


def place_list_only(inp: dict) -> bool:
    """Is this Bash call exactly `vfg place list`, the one command the main thread may run?"""
    cmds = split_shell(str(inp.get("command") or ""))
    return (inp.get("run_in_background") is not True and len(cmds) == 1 and cmds[0].words == ["vfg", "place", "list"]
            and not cmds[0].expands and not cmds[0].redirects)


def decide(call: dict, g: GuardContext) -> str | None:
    """The guard's decision for one tool call: a reason to deny it, or None to let the rules decide."""
    name = call.get("tool_name")
    inp = call.get("tool_input") or {}
    main_thread = not call.get("agent_id")
    if (main_thread and g.main_thread_rule and name not in MAIN_THREAD_TOOLS
            and not (name == "Bash" and place_list_only(inp))):
        return DENY.main_thread
    # Bash and file rules as for an implementer: implementers themselves, and the permission probe
    # (the main-thread rule off).
    implementer = not main_thread or not g.main_thread_rule

    if name in ("Agent", "Task"):
        if implementer:
            return DENY.no_agents  # an implementer (or the probe): nobody to brief
        if inp.get("subagent_type") != "implementer":
            return DENY.agent_type
        tid = task_id_of(inp.get("description"))
        open_ids = g.open_task_ids()
        if not tid or tid not in open_ids:
            if open_ids:
                return ('Start the description with the id of an open task, like "%s castle at spawn". Open now: %s.'
                        % (open_ids[0], ", ".join(open_ids)))
            return "No task is open, so there is nothing to start an implementer for."
        if inp.get("run_in_background") is False:
            return DENY.foreground
        if ((inp.get("mode") and inp.get("mode") != "dontAsk") or inp.get("isolation")
                or (inp.get("model") and inp.get("model") != g.implementer_model)):
            return DENY.overrides
        if g.active_implementers() >= g.max_implementers:
            return "%d implementers are running; wait for a report." % g.max_implementers
        return None

    if name == "Bash" and implementer:
        if inp.get("run_in_background") is True:
            return DENY.background
        command = inp.get("command")
        return check_bash("" if command is None else str(command))

    # The Glob and Grep allow rules are unrestricted: keep them, and Read, inside the repository.
    if name in ("Glob", "Grep") and inp.get("path") and not within(g.repo, str(inp["path"])):
        return DENY.outside
    if name == "Read" and inp.get("file_path") and not within(g.repo, str(inp["file_path"])):
        return DENY.read_outside

    if name in ("Write", "Edit") and inp.get("file_path"):
        if within(os.path.abspath(os.path.join(g.repo, "scripts/_lib")), str(inp["file_path"]), g.repo):
            return DENY.lib
    return None


HookCallback = Callable[[dict, str | None, Any], Awaitable[dict]]


def make_guard(g: GuardContext) -> HookCallback:
    seen_effort: set[str] = set()

    async def hook(inp: dict, tool_use_id: str | None, context: Any) -> dict:
        if inp.get("hook_event_name") != "PreToolUse":
            return {}
        # The effort a turn actually runs at, after any downgrade for its model: logged once per agent.
        effort = inp.get("effort")
        level = effort.get("level") if isinstance(effort, dict) else None
        agent_id = inp.get("agent_id")
        who = "implementer %s" % agent_id[:8] if agent_id else "orchestrator"
        if level and "%s %s" % (who, level) not in seen_effort:
            seen_effort.add("%s %s" % (who, level))
            log("effort", "%s runs at %s" % (who, level))
        call = {
            "tool_name": inp.get("tool_name"),
            "tool_input": inp.get("tool_input"),
            "agent_id": agent_id,
            "tool_use_id": inp.get("tool_use_id") if inp.get("tool_use_id") is not None else tool_use_id,
        }
        reason = decide(call, g)
        if reason:
            log("guard", "denied %s %s: %s (%s)" % (who, call["tool_name"], reason, brief(call["tool_input"])))
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        if call["tool_name"] in ("Agent", "Task"):
            tool_input = call["tool_input"] or {}
            g.on_agent_allowed(task_id_of(tool_input.get("description")), call["tool_use_id"] or "")
            return await _rewritten(tool_input)
        return {}

    return hook


async def _rewritten(tool_input: dict) -> dict:
    """The brief as briefing.rewrite() makes it, as the call's updatedInput. With no permissionDecision
    the CLI runs the call with the new input and the allow rules still decide; nothing here can
    stop the call."""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return {}
    try:
        new = await briefing.rewrite(prompt, tool_input)
    except Exception as e:
        log("brief", "rewrite failed, the brief goes as it was: %s" % e)
        return {}
    if new == prompt:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**tool_input, "prompt": new}}}


def realish(p: str) -> str:
    """The real path, following symlinks as far as the path exists (/home -> /var/home)."""
    try:
        return os.path.realpath(p, strict=True)
    except OSError:
        parent = os.path.dirname(p)
        return p if parent == p else os.path.join(realish(parent), os.path.basename(p))


def within(directory: str, p: str, base: str | None = None) -> bool:
    """Is `p` (relative to `base`, default `directory`) inside `directory`?"""
    root = realish(directory)
    real = realish(os.path.abspath(os.path.join(directory if base is None else base, _expand_home(p))))
    return real == root or real.startswith(root + "/")


def _expand_home(p: str) -> str:
    home = os.path.expanduser("~")
    return home if p == "~" else home + p[1:] if p.startswith("~/") else p


def brief(inp) -> str:
    s = json.dumps({} if inp is None else inp, ensure_ascii=False, separators=(",", ":"), default=str)
    return s[:200] + "…" if len(s) > 200 else s
