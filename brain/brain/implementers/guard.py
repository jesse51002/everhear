"""The PreToolUse hook on every tool an implementer calls. It denies with a reason the model can act
on, one line saying what to send instead, so the next call runs; and otherwise returns {} so the
allow rules (permission mode "dontAsk", runner.rules) decide."""
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import actions
from .config import ROOT, log
from .examples import server_state
from .vfg import VfgRunner


_COMMANDS = []


def refused() -> frozenset:
    """Server commands that are the owner's to run: the one list, scripts/_lib/commands.py, which
    mc.py, vfg and the actions use too. Loaded by path (it is deployed with the kit, not a
    package), on first use."""
    if not _COMMANDS:
        import importlib.util
        path = os.path.join(ROOT, "scripts", "_lib", "commands.py")
        spec = importlib.util.spec_from_file_location("vfg_commands", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _COMMANDS.append(mod)
    return _COMMANDS[0].REFUSED

# Server-wide settings an implementer may not change (scripts/_lib/mc.py does not refuse them:
# seeded games use the world border).
SERVER_WIDE = frozenset(["setworldspawn", "defaultgamemode", "difficulty", "worldborder"])


class DENY:
    owner = "That is the server owner's to do; report it."

    @staticmethod
    def server_wide(word: str) -> str:
        return "%s changes the whole server: implementers may not run it. Leave it out and say so in your report." % word

    @staticmethod
    def library(server: str) -> str:
        return ("scripts/ is the library (its _presets/ and _lib/ too) and is read-only during play. Copy what you "
                "need into this server's folder instead: vfg script new <name> --from <script> --preset <preset> "
                "makes made/%s/scripts/<name>/main.py and its action, and you edit those." % server)

    @staticmethod
    def library_actions(server: str) -> str:
        return ("actions/ is the library's and read-only during play. A new action goes in made/%s/actions/<name>.py: "
                "vfg script new writes one with its script, and a variant is a class there that subclasses a library "
                "action (from actions.railgun import Railgun)." % server)

    @staticmethod
    def made_only(server: str) -> str:
        return ("You write only in this server's folder, made/%s/: a script and its action made with vfg script new, "
                "a variant in made/%s/actions/, a preset with vfg preset save." % (server, server))

    @staticmethod
    def running(name: str, inst: dict, server: str) -> str:
        who = inst.get("copy") or inst.get("instance") or name
        return ("Refused: %s%s runs made/%s/scripts/%s/, and code a running copy uses is not changed: "
                "vfg script new <new_name> --from %s copies it, with its action, for you to Edit; then vfg script stop "
                "%s and run the new action." % (who, " (%s)" % inst["task"] if inst.get("task") else "", server, name,
                                                 name, who))

    library_flag = "--library is for coding sessions: leave it out, and vfg script new writes into this server's folder."
    expansion = "Write the server command literally inside single quotes: no $( ), backticks or variables in a vfg cmd."
    # An implementer's Bash: each says what to send instead, in one line.
    shape = ("Refused: that program does not run here. These do: vfg action|cmd|script|preset save|state|place|history ..., "
             "python3 -m py_compile <file>, sleep N, echo, and ls, grep, head, tail on files in the repository; "
             "join them with && or ;, and pipe vfg into head -n N, tail -n N or grep <pattern>.")
    variables = ("Refused: no $( ), backticks or variables, $? included: write every value out; each command's output "
                 "and exit status come back to you as they are.")
    loop = "Refused: no for, while or if: write each command out, joined with ; (vfg cmd 'a'; vfg cmd 'b')."
    group = "Refused: no ( ), { } or a lone & (the background): send the commands one after another, joined with && or ;."
    redirect = ("Refused: no redirection into or out of a file: drop it and the output comes back to you "
                "(2>&1 and >/dev/null are fine).")
    pipe = "Refused: pipe only into head -n N, tail -n N or grep <pattern>, with no file named after them."
    env = "Refused: no NAME=value before a command: send the command alone."
    cd = "Refused: no cd: you are already in the repository; send the rest on its own."
    read = "Refused: use the Read tool (offset and limit for a part of a file); an Edit needs it first anyway."
    find = "Refused: use the Glob tool (a pattern like made/**/main.py), or ls <folder>."
    ps = "Refused: vfg script list shows every running copy, and vfg script stop <copy> stops one."
    python = ("Refused: only python3 -m py_compile <file in the repository> runs; a script runs on the server through "
              "its action: vfg action run <action> key=value ... --task <task id>.")
    outside_bash = "Refused: ls, grep, head, tail and py_compile read only inside the repository: give a path in it."
    wild = ("Refused: no unquoted * ? [ ], {a,b} or ~name after ls, grep, head, tail, echo, sleep or py_compile, since "
            "bash would expand them where the guard cannot see: put a pattern in single quotes ('def .*') and name "
            "each file by its path in the repository.")
    grep_file = "Refused: grep reads a file in the repository, or what a vfg command pipes into it: name one."
    pattern_file = "Refused: no pattern files: give grep its pattern on the command line, or with -e."
    json_object = ("Refused: a JSON object of two keys or more in one word is refused by the permission check, quoted or "
                   "not: give each value as a word of its own, values.<name>=<value> "
                   "(vfg action run incantation_edit player=Steve phrase='fire ball' values.power=6 values.breaks=true "
                   "--task T7).")
    tail_follow = "Refused: tail -f never ends: use tail -n N, or vfg script logs <copy> -n 40."

    @staticmethod
    def mkdir(server: str) -> str:
        return ("Refused: no mkdir: vfg script new <name> --from <script> --preset <preset> makes made/%s/scripts/<name>/ "
                "with the code copied in, and its action made/%s/actions/<name>.py; then Read and Edit them."
                % (server, server))

    @staticmethod
    def files(server: str) -> str:
        return ("Refused: files change only by Edit and Write in made/%s/, and vfg script new <name> --from <script> "
                "copies a script with its action." % server)

    @staticmethod
    def vfg(words: list[str]) -> str:
        """How to send a vfg command the guard did not let through."""
        b, c = (words + [None, None, None])[1:3]
        if b and b.startswith("-"):
            return "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg action run ..., as it is."
        form = VFG_USAGE.get("%s %s" % (b, c)) or VFG_USAGE.get(str(b))
        if form:
            return "Refused: send it as %s." % form
        if b == "script":
            return "Refused: vfg script takes new|set|stop|logs|list|check|save|deploy; a script runs through its action."
        return "Refused: vfg %s is not an implementer's. These are: %s." % (b, VFG_FORMS)

    script_run = ("Refused: vfg script run is gone: a script runs only through its action, vfg action run <action> "
                  "key=value ... [as=<copy>] --task <task id>.")
    action_task = "Refused: say which task it is for: vfg action run <action> key=value ... --task <task id>."
    follow = "vfg script logs --follow never ends: use vfg script logs <copy> -n 40, and sleep between looks."
    outside = "Only the repository can be searched: give a path inside it, or none."
    read_outside = "Only files in the repository can be read."
    background = "Run commands in the foreground: scripts already run detached on the server (vfg action run)."


# Every vfg command an implementer runs, and how each is sent.
VFG_FORMS = ("vfg action run <action> key=value ... --task <task id> | vfg action list | vfg action check <name> | "
             "vfg cmd '<command>' | vfg script new|set|stop|logs|list|check|save|deploy ... | "
             "vfg preset save <script> <function> <name> | vfg state | vfg place save|list ... | vfg history ...")
VFG_USAGE = {
    "action": "vfg action run <action> key=value ... [as=<copy>] --task <task id>, vfg action list, or "
              "vfg action check <name>",
    "action run": "vfg action run <action> key=value ... [as=<copy>] --task <task id>",
    "action list": "vfg action list, or vfg action list --json",
    "action check": "vfg action check <name>",
    "cmd": "vfg cmd '<command>'",
    "state": "vfg state, or vfg state --json, with nothing after it",
    "preset": "vfg preset save <script> <function> <name>",
    "place": "vfg place save <name> --at <x> <y> <z> --by <task id>, or vfg place list",
    "script list": "vfg script list, with --json or --fresh or nothing after it",
    "script new": "vfg script new <name> --from <script> --preset <preset> ...",
    "script save": "vfg script save <copy> <new_name>",
    "script set": "vfg script set <copy> key=value ...",
    "script stop": "vfg script stop <copy>",
    "script logs": "vfg script logs <copy> -n 40",
    "script deploy": "vfg script deploy, or vfg script deploy <name>",
}


@dataclass
class GuardContext:
    """One implementer's: the runner keeps it with the task, so a follow-up keeps what it made."""
    repo: str
    # VFG_SERVER: implementers write only in made/<server>/.
    server: str = "main"
    # The task the implementer works on.
    task: str | None = None
    # The copies running now (running.json, else `vfg state --json`), each with its script and task,
    # or None when that cannot be told; no callable, no check (running_copies is the runner's).
    running: Callable[[], Awaitable[list[dict] | None]] | None = None
    # The scripts in made/<server>/scripts/ this implementer made itself, or was let change once;
    # its own to change while it works.
    own: set[str] = field(default_factory=set)


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
    return command_word_in(cmd, refused())


@dataclass
class Redirect:
    fd: str  # the number before the operator, or ""
    op: str  # >, >>, >|, >&, &>, &>>, <, <<, <<<, <&, <>
    target: str

    def harmless(self) -> bool:
        """2>&1 and >&2 (one output onto the other), or anything into /dev/null."""
        if self.op in (">&", "<&"):
            return self.target in ("1", "2") and self.fd in ("", "1", "2")
        return self.target == "/dev/null" and self.op in (">", ">>", ">|", "&>", "&>>", "<")


@dataclass
class ShellCommand:
    words: list[str]
    # A $( ), backtick or $variable outside single quotes.
    expands: bool = False
    # Its redirections, in order.
    redirects: list[Redirect] = field(default_factory=list)
    # Its input is the output of the command before it, through a pipe.
    piped: bool = False
    # A ( or ), or a lone & after it (the background).
    grouped: bool = False
    # By word: something bash expands in it that the guard does not follow (bash_expands).
    wild: list[bool] = field(default_factory=list)


# Redirection operators, the longest first.
REDIRECT_OPS = ("<<<", "<<", "<&", "<>", "<", ">>", ">&", ">|", ">")


def bash_expands(shape: str) -> bool:
    """Does bash expand something in a word that the guard does not follow? `shape` is the word
    with each quoted or escaped character as \\0: a glob (* ? [), a brace ({a,b}, {1..3}), or a ~
    other than a leading ~ or ~/ (~user, ~+, a=~). The guard judges a path by its literal word,
    and only ~ and ~/ it expands as bash does."""
    if any(c in shape for c in "*?["):
        return True
    inside = shape[shape.find("{") + 1:shape.rfind("}")] if -1 < shape.find("{") < shape.rfind("}") else ""
    if "," in inside or ".." in inside:
        return True
    if "~" in shape[1:]:
        return True
    return shape.startswith("~") and shape[1:2] not in ("", "/")


def split_shell(line: str) -> list[ShellCommand]:
    """Splits a shell command line into simple commands, honouring quotes: their words, their
    redirections, and whether each reads the one before it through a pipe."""
    out: list[ShellCommand] = []
    cmd = ShellCommand([])
    word = ""
    shape = ""  # the word with each quoted or escaped character as \0 (bash_expands)
    in_word = False
    bare = True  # no quoted or escaped part: only a bare number is a redirection's fd
    pending: Redirect | None = None  # an operator waiting for its target word

    def end_word():
        nonlocal word, shape, in_word, bare, pending
        if in_word:
            if pending is not None:
                pending.target = word
                cmd.redirects.append(pending)
                pending = None
            else:
                cmd.words.append(word)
                cmd.wild.append(bash_expands(shape))
        word, shape, in_word, bare = "", "", False, True

    def end_cmd(piped_next: bool = False, grouped: bool = False):
        nonlocal cmd, pending
        end_word()
        if pending is not None:  # an operator with nothing after it
            cmd.redirects.append(pending)
            pending = None
        cmd.grouped = cmd.grouped or grouped
        if cmd.words or cmd.redirects or cmd.grouped:
            out.append(cmd)
        cmd = ShellCommand([], piped=piped_next)

    n = len(line)
    i = 0
    while i < n:
        c = line[i]
        if c == "'":
            in_word, bare = True, False
            j = line.find("'", i + 1)
            end = n if j < 0 else j
            word += line[i + 1:end]
            shape += "\0" * (end - i - 1)
            i = end
        elif c == '"':
            in_word, bare = True, False
            j = i + 1
            while j < n and line[j] != '"':
                d = line[j]
                if d == "\\" and j + 1 < n and line[j + 1] in '"\\$`\n':
                    j += 1
                    word += line[j]
                else:
                    if d in "$`":
                        cmd.expands = True
                    word += d
                shape += "\0"
                j += 1
            i = j
        elif c == "\\":
            in_word, bare = True, False
            if i + 1 < n:
                i += 1
                word += line[i]
                shape += "\0"
        elif c in " \t":
            end_word()
        elif c in "\n;":
            end_cmd()
        elif c == "&":
            if line.startswith("&&", i):
                end_cmd()
                i += 1
            elif line.startswith("&>", i):
                end_word()
                op = "&>>" if line.startswith("&>>", i) else "&>"
                pending = Redirect("", op, "")
                i += len(op) - 1
            else:
                end_cmd(grouped=True)  # a lone &: the background
        elif c == "|":
            if line.startswith("||", i):
                end_cmd()
            else:
                end_cmd(piped_next=True)  # | and |&
            if line.startswith(("||", "|&"), i):
                i += 1
        elif c in "()":
            end_cmd(grouped=True)
        elif c in "<>":
            fd = ""
            if in_word and bare and word.isdigit() and pending is None:
                fd, word, shape, in_word = word, "", "", False
            else:
                end_word()
            op = next(o for o in REDIRECT_OPS if line.startswith(o, i))
            i += len(op) - 1
            m = re.match(r"\d+|-", line[i + 1:]) if op in (">&", "<&") else None
            if m:
                cmd.redirects.append(Redirect(fd, op, m.group(0)))
                i += len(m.group(0))
            else:
                pending = Redirect(fd, op, "")
        else:
            if c in "$`":
                cmd.expands = True
            in_word = True
            word += c
            shape += c
        i += 1
    end_cmd()
    return out


def allowed_form(words: list[str]) -> bool:
    """One of the commands the allow rules let an implementer run (runner.py `rules`)."""
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
    if b == "state":
        return words[2:] in ([], ["--json"])
    if b == "preset":
        return c == "save" and len(words) == 6
    if b == "action":
        if c == "list":
            return words[3:] in ([], ["--json"])
        if c == "check":
            return len(words) == 4
        return c == "run" and len(words) >= 4
    if b != "script":
        return False
    if c == "list":
        return len(set(words[3:])) == len(words[3:]) and set(words[3:]) <= {"--json", "--fresh"}
    if c in ("deploy", "check"):
        return c == "check" or len(words) <= 4
    if c == "new":
        return len(words) >= 4
    if c == "save":
        return len(words) == 5
    if c == "set":
        return len(words) >= 5
    return c in ("stop", "logs") and len(words) >= 4


def task_given(words: list[str]) -> bool:
    """Does `vfg action run ...` say its task: --task T7 or --task=T7?"""
    for i, a in enumerate(words):
        if a == "--task" and i + 1 < len(words) and re.fullmatch(r"T\d+", words[i + 1], re.ASCII):
            return True
        if a.startswith("--task=") and re.fullmatch(r"T\d+", a[len("--task="):], re.ASCII):
            return True
    return False


def command_values(words: list[str]) -> list[str]:
    """The server commands a `vfg action run` or a `vfg script set` gives: the value of every key
    that names commands (command, commands, …), whole, item by item as a JSON list, and comma by
    comma as a list on the command line."""
    if words[1:3] not in (["action", "run"], ["script", "set"]):
        return []
    out = []
    for a in words[3:]:
        key, eq, value = a.partition("=")
        if not eq or "command" not in key.lower():
            continue
        out.append(value)
        try:
            items = json.loads(value)
        except ValueError:
            items = None
        if isinstance(items, list):
            out += [x for x in items if isinstance(x, str)]
        out += [x for x in value.split(",") if x.strip()]
    return out


def scan_args(args: list[str], short_arg: str = "", long_arg: tuple[str, ...] = ()) -> tuple[list[tuple[str, str]], list[str]]:
    """A command's flags and operands, as getopt reads them: `--` ends the flags, `--x=v` is one
    flag, `-inE` is three, a flag that takes a value (in short_arg or long_arg) has the rest of its
    cluster or the next word as its value, and `-40` is head's and tail's count."""
    flags: list[tuple[str, str]] = []
    ops: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            ops.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if not eq and name in long_arg and i + 1 < len(args):
                i += 1
                val = args[i]
            flags.append((name, val))
        elif a.startswith("-") and len(a) > 1:
            if a[1:].isdigit():
                flags.append(("-n", a[1:]))
            else:
                for j in range(1, len(a)):
                    if a[j] in short_arg:
                        val = a[j + 1:]
                        if not val and i + 1 < len(args):
                            i += 1
                            val = args[i]
                        flags.append(("-" + a[j], val))
                        break
                    flags.append(("-" + a[j], ""))
        else:
            ops.append(a)
        i += 1
    return flags, ops


# grep's and head's and tail's flags that take a value; ls's.
GREP_SHORT, GREP_LONG = "efABCmdD", ("--regexp", "--file", "--after-context", "--before-context", "--context",
                                     "--max-count", "--directories", "--devices", "--include", "--exclude",
                                     "--exclude-dir", "--exclude-from", "--label", "--binary-files")
HEAD_SHORT, HEAD_LONG = "ncs", ("--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats")
LS_SHORT, LS_LONG = "IwT", ("--ignore", "--hide", "--width", "--tabsize", "--format", "--sort", "--time")

# Programs implementers reach for, and where each points them instead.
LOOP_WORDS = frozenset(["for", "while", "until", "do", "done", "if", "then", "elif", "else", "fi", "case", "esac",
                        "select", "function"])
READERS = frozenset(["cat", "less", "more", "sed", "awk", "nl", "strings", "od", "xxd", "hexdump", "file", "stat"])
FINDERS = frozenset(["find", "tree", "locate", "fd", "du"])
FILE_CHANGERS = frozenset(["rm", "rmdir", "mv", "cp", "touch", "chmod", "chown", "ln", "tee", "truncate", "dd",
                           "install", "unlink"])
PROCESSES = frozenset(["ps", "pgrep", "pkill", "kill", "killall", "top", "htop", "pidof", "jobs", "wait"])
PYTHONS = frozenset(["python", "python3", "python3.9", "python3.12", "pip", "pip3", "uv"])
FILTERS = frozenset(["head", "tail", "grep"])


def file_reason(prog: str, args: list[str], piped: bool, repo: str | None) -> str | None:
    """Why ls, grep, head or tail is refused: a file outside the repository, or one named where a
    pipe feeds it, or none where nothing does; tail -f; grep's pattern files."""
    if prog == "ls":
        flags, ops = scan_args(args, LS_SHORT, LS_LONG)
        files = ops
    elif prog == "grep":
        flags, ops = scan_args(args, GREP_SHORT, GREP_LONG)
        if any(f in ("-f", "--file", "--exclude-from") for f, _ in flags):
            return DENY.pattern_file
        given = any(f in ("-e", "--regexp") for f, _ in flags)
        files = ops if given else ops[1:]
        recursive = any(f in ("-r", "-R", "--recursive", "--dereference-recursive") for f, _ in flags)
        if not piped and not files and not recursive:
            return DENY.grep_file
    else:  # head, tail
        flags, ops = scan_args(args, HEAD_SHORT, HEAD_LONG)
        if prog == "tail" and any(f in ("-f", "-F", "--follow", "--retry") or f.startswith("--follow")
                                  for f, _ in flags):
            return DENY.tail_follow
        files = ops
    if piped:
        return DENY.pipe if files else None
    if any(repo is None or not within(repo, f) for f in files):
        return DENY.outside_bash
    return None


# key={...,...}: a JSON object of two keys or more, which Claude Code's own Bash check refuses in
# "dontAsk" mode however it is quoted, with no reason the implementer can act on.
OBJECT_WORD = re.compile(r"^[a-z_][a-z0-9_.]*=\{.*,.*\}$", re.DOTALL)


def command_reason(cmd: ShellCommand, repo: str | None, server: str) -> str | None:
    """Why one simple command is refused, in one line saying what to send instead; or None."""
    w = cmd.words
    first = w[0] if w else ""
    # A ~name, a brace or a glob in what follows a program that reads files, or prints its
    # words, could name a file outside the repository however its word reads.
    wild = any(cmd.wild[1:])
    if first in LOOP_WORDS:
        return DENY.loop
    if cmd.expands:
        return DENY.expansion if w[:2] == ["vfg", "cmd"] else DENY.variables
    if cmd.grouped or first in ("{", "}"):
        return DENY.group
    if any(not r.harmless() for r in cmd.redirects):
        return DENY.redirect
    if not w:
        return DENY.shape
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", first):
        # what the rest would get, else: send it without the NAME=value
        rest = list(w)
        while rest and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", rest[0]):
            rest.pop(0)
        bare = ShellCommand(rest, piped=cmd.piped, wild=cmd.wild[len(w) - len(rest):])
        return command_reason(bare, repo, server) or DENY.env
    if cmd.piped:
        if first not in FILTERS:
            return DENY.pipe
        return DENY.wild if wild else file_reason(first, w[1:], True, repo)
    if first == "vfg":
        if any(OBJECT_WORD.match(x) for x in w[2:]):
            return DENY.json_object
        if w[1:3] == ["script", "run"]:
            return DENY.script_run
        if not allowed_form(w):
            return DENY.vfg(w)
        if w[1:3] == ["action", "run"] and not task_given(w):
            return DENY.action_task
        if w[1:3] == ["script", "logs"] and "--follow" in w:
            return DENY.follow
        if w[1:3] == ["script", "new"] and "--library" in w:
            return DENY.library_flag
        return None
    if first == "echo":
        return DENY.wild if wild else None
    if first == "sleep":
        return DENY.shape if not allowed_form(w) else DENY.wild if wild else None
    if first in PYTHONS:
        if not allowed_form(w):
            return DENY.python
        if wild:
            return DENY.wild
        return DENY.outside_bash if any(repo is None or not within(repo, f) for f in w[3:]) else None
    if first == "ls" or first in FILTERS:
        return DENY.wild if wild else file_reason(first, w[1:], False, repo)
    if first == "cd":
        return DENY.cd
    if first == "mkdir":
        return DENY.mkdir(server)
    if first in READERS:
        return DENY.read
    if first in FINDERS:
        return DENY.find
    if first in FILE_CHANGERS:
        return DENY.files(server)
    if first in PROCESSES:
        return DENY.ps
    return DENY.shape


def check_bash(command: str, repo: str | None = None, server: str = "main") -> str | None:
    """Why an implementer's Bash command is refused, or None to let the rules decide. Claude Code's
    own refusal of a disallowed command reads as if Bash itself were off, and a small model stops
    trying; each of these says what to send instead. vfg's own commands run, joined with && or ;,
    with 2>&1 or >/dev/null, piped into head, tail or grep; so do echo, sleep, py_compile, and ls,
    grep, head and tail on files in the repository (`repo`; None: on none), with no ~name, brace or
    glob outside quotes in their words, which bash would expand past the check of the literal path."""
    cmds = split_shell(command)
    # Server commands that are the owner's, however they are dressed up, and the server-wide settings:
    # in a vfg cmd, and in the commands an action run or an edit gives.
    for cmd in cmds:
        w = list(cmd.words)
        while w and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w.pop(0)  # FOO=bar vfg ...
        prog = w[0].split("/")[-1] if w else None
        if prog != "vfg" or len(w) < 2:
            continue
        if w[1] == "cmd":
            args = [" ".join(w[2:])] + w[2:]
        else:
            args = command_values(["vfg"] + w[1:])
        for a in args:
            if refused_word(a):
                return DENY.owner
        for a in args:
            word = command_word_in(a, SERVER_WIDE)
            if word:
                return DENY.server_wide(word)
    for cmd in cmds:
        reason = command_reason(cmd, repo, server)
        if reason:
            return reason
    return None


def decide(call: dict, g: GuardContext) -> str | None:
    """The guard's decision for one tool call: a reason to deny it, or None to let the rules decide."""
    name = call.get("tool_name")
    inp = call.get("tool_input") or {}
    if name == "Bash":
        if inp.get("run_in_background") is True:
            return DENY.background
        command = inp.get("command")
        return check_bash("" if command is None else str(command), g.repo, g.server)

    # The Glob and Grep allow rules are unrestricted: keep them, and Read, inside the repository.
    if name in ("Glob", "Grep") and inp.get("path") and not within(g.repo, str(inp["path"])):
        return DENY.outside
    if name == "Read" and inp.get("file_path") and not within(g.repo, str(inp["file_path"])):
        return DENY.read_outside

    # An implementer writes in this server's folder and nowhere else: never in the library.
    if name in ("Write", "Edit") and inp.get("file_path"):
        path = str(inp["file_path"])
        if within(os.path.join(g.repo, "made", g.server), path, g.repo):
            return None
        if within(os.path.join(g.repo, "scripts"), path, g.repo):
            return DENY.library(g.server)
        if within(os.path.join(g.repo, "actions"), path, g.repo):
            return DENY.library_actions(g.server)
        return DENY.made_only(g.server)
    return None


# ------------------------------------------------------------------ a script a running copy uses

# `vfg state --json` gets this long when the guard asks what runs and running.json cannot be read;
# its answer is kept this long, so a run of Edits asks once.
STATE_TIMEOUT_S = 5.0
STATE_CACHE_S = 3.0


def running_copies(home: str | None, run: VfgRunner) -> Callable[[], Awaitable[list[dict] | None]]:
    """GuardContext.running for the runner: the copies in `home`/running.json, which vfg writes
    through on every run, edit and stop; else those `vfg state --json` lists; None when neither
    can tell (the guard then lets the edit through)."""
    kept: dict[str, Any] = {"at": 0.0, "instances": None}

    async def running() -> list[dict] | None:
        copies = actions.read_running(home) if home else None
        if copies is not None:
            return [{**c, "instance": name, "copy": name} for name, c in copies.items()]
        if kept["instances"] is not None and time.monotonic() - kept["at"] < STATE_CACHE_S:
            return kept["instances"]
        state, failed = await server_state(STATE_TIMEOUT_S, run)
        if state is None:
            log("guard", "what runs is unknown, the edit goes through: %s" % failed)
            return None
        kept["instances"] = [i for i in state.get("instances") or [] if isinstance(i, dict)]
        kept["at"] = time.monotonic()
        return kept["instances"]

    return running


def made_script(path: str, g: GuardContext) -> str | None:
    """The <name> when `path` is a .py file in made/<server>/scripts/<name>/, else None."""
    root = realish(os.path.join(g.repo, "made", g.server, "scripts"))
    real = realish(os.path.abspath(os.path.join(g.repo, _expand_home(path))))
    if not real.startswith(root + "/") or not real.endswith(".py"):
        return None
    parts = real[len(root) + 1:].split("/")
    return parts[0] if len(parts) >= 2 else None


def new_script_name(words: list[str]) -> str | None:
    """The script `vfg script new <name>` or `vfg script save <copy> <name>` makes, or None."""
    if words[:3] not in (["vfg", "script", "new"], ["vfg", "script", "save"]):
        return None
    args, positional = words[3:], []
    i = 0
    while i < len(args):
        if args[i] in ("--from", "--preset"):
            i += 2
            continue
        if not args[i].startswith("--"):
            positional.append(args[i])
        i += 1
    want = 0 if words[2] == "new" else 1
    return positional[want] if len(positional) > want else None


def note_made(call: dict, g: GuardContext) -> None:
    """After an implementer's Bash call is let through: a script its vfg script new or vfg script save
    is about to make in made/<server>/scripts/ (with its action) is its own to change."""
    command = str((call.get("tool_input") or {}).get("command") or "")
    for cmd in split_shell(command):
        name = new_script_name(cmd.words)
        if name and not os.path.exists(os.path.join(g.repo, "made", g.server, "scripts", name)):
            g.own.add(name)


async def check_running(call: dict, g: GuardContext) -> str | None:
    """Why an implementer may not change this script: it is in made/<server>/scripts/, it did not make
    it, and a copy another task started runs it. New code is a new script with its own name."""
    if g.running is None or call.get("tool_name") not in ("Write", "Edit"):
        return None
    name = made_script(str((call.get("tool_input") or {}).get("file_path") or ""), g)
    if name is None or name in g.own:
        return None
    if not os.path.exists(os.path.join(g.repo, "made", g.server, "scripts", name)):
        g.own.add(name)  # a script being made: nothing runs it
        return None
    try:
        instances = await g.running()
    except Exception as e:
        log("guard", "what runs is unknown, the edit goes through: %s" % e)
        return None
    if instances is None:
        return None
    others = [i for i in instances if i.get("script") == name and (g.task is None or i.get("task") != g.task)]
    if others:
        return DENY.running(name, others[0], g.server)
    g.own.add(name)
    return None


HookCallback = Callable[[dict, str | None, Any], Awaitable[dict]]


def make_guard(g: GuardContext) -> HookCallback:
    seen_effort: set[str] = set()
    who = "implementer %s" % (g.task or "-")

    async def hook(inp: dict, tool_use_id: str | None, context: Any) -> dict:
        if inp.get("hook_event_name") != "PreToolUse":
            return {}
        # The effort a turn actually runs at, after any downgrade for its model: logged once.
        effort = inp.get("effort")
        level = effort.get("level") if isinstance(effort, dict) else None
        if level and level not in seen_effort:
            seen_effort.add(level)
            log("effort", "%s runs at %s" % (who, level))
        call = {"tool_name": inp.get("tool_name"), "tool_input": inp.get("tool_input")}
        reason = decide(call, g) or await check_running(call, g)
        if reason:
            log("guard", "denied %s %s: %s (%s)" % (who, call["tool_name"], reason, brief(call["tool_input"])))
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        if call["tool_name"] == "Bash":
            note_made(call, g)
        return {}

    return hook


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
