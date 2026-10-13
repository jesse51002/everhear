"""The guard: one check before every tool an implementer calls, whatever the harness. Every adapter
calls gate() before a tool runs (Claude Code from its PreToolUse hook, OpenCode from its plugin's
tool.execute.before), with the call in Claude Code's shape, {"tool_name", "tool_input"}, and the
tool names Bash, Read, Write, Edit, Glob and Grep. gate() gives a reason to refuse it, one line
saying what to send instead, which goes back to the model as that tool's error; or None, and the
call runs. The adapters turn their harness's own permission checks off, so the guard is the only
gate.

What a call it lets through makes (a script, an action, a preset, a copy) it records (made_by),
and the runner gives the brain that record, checked against the disk (made_now), with the report.
Before such a call runs, it gives it to the run's snapshot (putback.py), which keeps what the call
is about to change so code can put it back after a cancel or a run that reached its limit."""
import ast
import glob
import hashlib
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
        task = inst.get("task")
        return ("Refused: %s%s runs made/%s/scripts/%s/, and code a running copy uses is not changed: "
                "vfg script new <your task> --from %s copies it, with its action, for you to Edit; then vfg script stop "
                "%s and run the new action." % (who, " (%s)" % task if task and task != who else "", server, name,
                                                 name, who))

    library_flag = "--library is for coding sessions: leave it out, and vfg script new writes into this server's folder."
    expansion = "Write the server command literally inside single quotes: no $( ), backticks or variables in a vfg cmd."
    # An implementer's Bash: each says what to send instead, in one line.
    shape = ("Refused: that program does not run here. These do: vfg action|cmd|script|preset save|state|place|history ..., "
             "python3 -m py_compile <file>, sleep N, echo, and ls, cat, sed -n 'A,Bp', grep, head, tail on files in scripts/, "
             "actions/ and made/; "
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
    read = ("Refused: read a file with cat <file>, sed -n 'A,Bp' <file>, or the Read tool (offset and limit for a part); "
            "an Edit needs the Read tool first anyway.")
    sed = "Refused: sed only reads here, as sed -n 'A,Bp' <file> (lines A to B); use Edit to change a file."
    cat = "Refused: cat reads the files you name: cat <file> ..."

    @staticmethod
    def find(server: str) -> str:
        return ("Refused: use the Glob tool with a path (path made/%s/scripts, pattern */main.py), or ls <folder>."
                % server)

    @staticmethod
    def read_scope(server: str) -> str:
        return ("Refused: you read and search only scripts/, actions/ and made/%s/: give a path in one of them "
                "(made/%s/scripts/, scripts/_lib/, actions/)." % (server, server))
    ps = "Refused: vfg script list shows every running copy, and vfg script stop <copy> stops one."
    python = ("Refused: only python3 -m py_compile <file> runs; a script runs on the server through its action: "
              "vfg action run <action> key=value ... --task <your task>.")
    wild = ("Refused: no {a,b} or ~name in a command, and no unquoted * ? [ ] after echo or sleep, since bash would "
            "expand them where the guard cannot see: put a pattern in single quotes ('def .*') and name each file by "
            "its path.")
    grep_file = "Refused: grep reads a file you name, or what a vfg command pipes into it: name one."
    pattern_file = "Refused: no pattern files: give grep its pattern on the command line, or with -e."
    json_object = ("Refused: a JSON object of two keys or more in one word is refused by the permission check, quoted or "
                   "not: give each value as a word of its own, values.<name>=<value> "
                   "(vfg action run incantation_edit player=Steve phrase='fire ball' values.power=6 values.breaks=true "
                   "--task <your task>).")
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
                  "key=value ... [as=<copy>] --task <your task>.")
    action_task = ("Refused: say which task it is for, by the name YOUR TASK gives: vfg action run <action> key=value ... "
                   "--task <your task>.")
    follow = "vfg script logs --follow never ends: use vfg script logs <copy> -n 40, and sleep between looks."
    background = "Run commands in the foreground: scripts already run detached on the server (vfg action run)."
    test_again = ("Refused: nothing changed since the last test run: read the log lines its FAIL printed (add -n 60 to "
                  "your next vfg test for more), then change the ability or the test.")
    test_cap = "Refused: that is a fifth test run in this run, and the rules allow four: report what still fails."
    tests_spent = "Refused: your test runs are spent: report what still fails, item by item."
    waits_for_trigger = ("Refused: an incantation fires its action once at the cast: make the script act as it starts, "
                         "with no crouch or click to wait for.")
    probes_spent = ("Refused: the live world is not for testing: write the check into your test (vfg test <your task>), "
                    "or report.")
    web = "Implementers have no web access: work from the repository (scripts/, actions/, the kit) and vfg."


# Every vfg command an implementer runs, and how each is sent.
VFG_FORMS = ("vfg action run <action> key=value ... --task <your task> | vfg action list | vfg action check <name> | "
             "vfg action show <name> | "
             "vfg cmd '<command>' | vfg script new|set|stop|logs|list|check|save|deploy ... | "
             "vfg preset save <script> <function> <name> | vfg test <your task> | vfg state | vfg place save|list ... | "
             "vfg history ...")
VFG_USAGE = {
    "action": "vfg action run <action> key=value ... [as=<copy>] --task <your task>, vfg action list, "
              "vfg action check <name>, or vfg action show <name>",
    "action run": "vfg action run <action> key=value ... [as=<copy>] --task <your task>",
    "action list": "vfg action list, or vfg action list --json",
    "action check": "vfg action check <name>",
    "action show": "vfg action show <name>",
    "cmd": "vfg cmd '<command>'",
    "state": "vfg state, or vfg state --json, with nothing after it",
    "preset": "vfg preset save <script> <function> <name>",
    "place": "vfg place save <name> --at <x> <y> <z> --by <your task>, or vfg place list",
    "script list": "vfg script list, with --json or --fresh or nothing after it",
    "script new": "vfg script new <name> --from <script> --preset <preset> ... --kind <kind>, the kind one of "
                  "cast-projectile, cast-launch, item-use, area-blast, build, world-event",
    "script save": "vfg script save <copy> <new_name>",
    "script set": "vfg script set <copy> key=value ...",
    "script stop": "vfg script stop <copy>",
    "script logs": "vfg script logs <copy> -n 40",
    "script deploy": "vfg script deploy, or vfg script deploy <name>",
    "test": "vfg test <name>, with -n N, --timeout S or --kind <kind> (cast-projectile, cast-launch, item-use, "
            "area-blast, build, world-event) after it if you need them",
}


# What a task made, by kind: the keys of on_report's `made` and of the task's row (Prototype_Named_Tasks).
MADE_KINDS = ("scripts", "actions", "presets", "copies")


@dataclass
class GuardContext:
    """One implementer's: the runner keeps it with the task, so a follow-up keeps what it made."""
    repo: str
    # VFG_SERVER: implementers write only in made/<server>/.
    server: str = "main"
    # The task the implementer works on: its name, which the script, the action and the copy it
    # makes take too.
    task: str | None = None
    # The copies running now (running.json, else `vfg state --json`), each with its script and task,
    # or None when that cannot be told; no callable, no check (running_copies is the runner's).
    running: Callable[[], Awaitable[list[dict] | None]] | None = None
    # The scripts in made/<server>/scripts/ this implementer made itself, or was let change once;
    # its own to change while it works. It starts with the task's name: the task's own script, which
    # a follow-up changes in place while the brain's own copies of its action run.
    own: set[str] = field(default_factory=set)
    # What it made, by kind, each name once in the order it was made: made_by's record of the
    # commands let through, and each new action or preset it wrote. made_now checks it against the
    # disk.
    made: dict[str, list[str]] = field(default_factory=lambda: {k: [] for k in MADE_KINDS})
    # The run's putback.Snapshot, which the runner sets as each run starts: what a call let through
    # is about to change, noted before it runs. None: nothing is noted.
    snapshot: Any = None
    # This run's `vfg test` runs: how many, and by name what they covered as they ran (test_print);
    # new_run() clears both as each run starts.
    tests_run: int = 0
    tested: dict = field(default_factory=dict)
    # Is there a test server (VFG_TEST_BOX)? With one, a run's looks at the live world are counted.
    test_server: bool = False
    probes_run: int = 0

    def new_run(self) -> None:
        self.tests_run = 0
        self.tested = {}
        self.probes_run = 0

    def __post_init__(self):
        if self.task:
            self.own.add(self.task)


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
    # By word: the word with each quoted or escaped character as \0, so a glob is expanded as bash would.
    shapes: list[str] = field(default_factory=list)


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
                cmd.shapes.append(shape)
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
    """One of the vfg commands an implementer may run, in a form vfg takes."""
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
    if b == "test":
        return test_form(words[2:])
    if b == "place":
        return c == "list" or (c == "save" and len(words) >= 4)
    if b == "state":
        return words[2:] in ([], ["--json"])
    if b == "preset":
        return c == "save" and len(words) == 6
    if b == "action":
        if c == "list":
            return words[3:] in ([], ["--json"])
        if c in ("check", "show"):
            return len(words) == 4
        return c == "run" and len(words) >= 4
    if b != "script":
        return False
    if c == "list":
        return len(set(words[3:])) == len(words[3:]) and set(words[3:]) <= {"--json", "--fresh"}
    if c in ("deploy", "check"):
        return c == "check" or len(words) <= 4
    if c == "new":
        return len(words) >= 4 and kind_ok(words[3:])
    if c == "save":
        return len(words) == 5
    if c == "set":
        return len(words) >= 5
    return c in ("stop", "logs") and len(words) >= 4


# The test templates' kinds (scripts/_lib/kit/templates/), for vfg script new and vfg test --kind.
TEST_KINDS = frozenset(["cast-projectile", "cast-launch", "item-use", "area-blast", "build", "world-event"])


def kind_ok(args: list[str]) -> bool:
    """Every --kind K (or --kind=K) among these words names a template's kind."""
    for i, a in enumerate(args):
        if a == "--kind" and (i + 1 >= len(args) or args[i + 1] not in TEST_KINDS):
            return False
        if a.startswith("--kind=") and a[len("--kind="):] not in TEST_KINDS:
            return False
    return True


def test_form(args: list[str]) -> bool:
    """`vfg test <name>`, then -n N, --timeout S and --kind K (or --timeout=S, --kind=K) at most once
    each: numbers, and a template's kind."""
    if not args or not MADE_NAME.fullmatch(args[0]):
        return False
    seen, i = set(), 1
    while i < len(args):
        flag, _, value = args[i].partition("=") if args[i].startswith(("--timeout=", "--kind=")) else (args[i], "", None)
        if flag not in ("-n", "--timeout", "--kind") or flag in seen:
            return False
        if value is None:
            if i + 1 >= len(args):
                return False
            value, i = args[i + 1], i + 1
        if not (value in TEST_KINDS if flag == "--kind" else re.fullmatch(r"\d+(\.\d+)?", value)):
            return False
        seen.add(flag)
        i += 1
    return True


# A task's name (Prototype_Named_Tasks): galick_gun_styrk.
TASK_NAME = re.compile(r"[a-z][a-z0-9_]{1,31}", re.ASCII)


def task_given(words: list[str]) -> bool:
    """Does `vfg action run ...` say its task by its name: --task galick_gun_styrk or
    --task=galick_gun_styrk?"""
    for i, a in enumerate(words):
        if a == "--task" and i + 1 < len(words) and TASK_NAME.fullmatch(words[i + 1]):
            return True
        if a.startswith("--task=") and TASK_NAME.fullmatch(a[len("--task="):]):
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


# What an implementer reads and searches: the library and this server's folder, nothing else of the
# repository (the replays and tests there hold answers, and the code is not theirs).
def readable(repo: str | None, server: str, p: str) -> bool:
    """Is `p` (relative to the repository) in scripts/, actions/ or made/<server>/?"""
    if repo is None:
        return False
    return any(within(os.path.join(repo, d), p, repo) for d in ("scripts", "actions", os.path.join("made", server)))


def glob_only(shape: str) -> bool:
    """Is a glob (* ? [) all that bash would expand in this word: no brace and no ~name?"""
    return not bash_expands(re.sub(r"[*?\[]", "_", shape))


def glob_reason(cmd: "ShellCommand", repo: str | None, server: str) -> str | None:
    """A read command's globs, expanded as bash would from the repository: refused when one matches
    a file outside the readable folders (no match: bash leaves the word, checked as it is)."""
    for word, shape, wild in zip(cmd.words[1:], cmd.shapes[1:], cmd.wild[1:]):
        if not wild:
            continue
        if not glob_only(shape) or repo is None:
            return DENY.wild
        pattern = "".join(glob.escape(ch) if sh == "\0" else ch for ch, sh in zip(word, shape))
        pattern = _expand_home(pattern)
        for match in glob.iglob(pattern if os.path.isabs(pattern) else os.path.join(repo, pattern)):
            if not readable(repo, server, match):
                return DENY.read_scope(server)
    return None


# sed's one form here: -n and a line range, A,Bp or Ap (A,$p to the end).
SED_RANGE = re.compile(r"^\d+(,(\d+|\$))?p$")


# grep's and head's and tail's flags that take a value; ls's.
GREP_SHORT, GREP_LONG = "efABCmdD", ("--regexp", "--file", "--after-context", "--before-context", "--context",
                                     "--max-count", "--directories", "--devices", "--include", "--exclude",
                                     "--exclude-dir", "--exclude-from", "--label", "--binary-files")
HEAD_SHORT, HEAD_LONG = "ncs", ("--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats")
LS_SHORT, LS_LONG = "IwT", ("--ignore", "--hide", "--width", "--tabsize", "--format", "--sort", "--time")

# Programs implementers reach for, and where each points them instead.
LOOP_WORDS = frozenset(["for", "while", "until", "do", "done", "if", "then", "elif", "else", "fi", "case", "esac",
                        "select", "function"])
READERS = frozenset(["less", "more", "awk", "nl", "strings", "od", "xxd", "hexdump", "file", "stat"])
FINDERS = frozenset(["find", "tree", "locate", "fd", "du"])
FILE_CHANGERS = frozenset(["rm", "rmdir", "mv", "cp", "touch", "chmod", "chown", "ln", "tee", "truncate", "dd",
                           "install", "unlink"])
PROCESSES = frozenset(["ps", "pgrep", "pkill", "kill", "killall", "top", "htop", "pidof", "jobs", "wait"])
PYTHONS = frozenset(["python", "python3", "python3.9", "python3.12", "pip", "pip3", "uv"])
FILTERS = frozenset(["head", "tail", "grep"])


def file_reason(prog: str, args: list[str], piped: bool, repo: str | None, server: str = "main") -> str | None:
    """Why ls, cat, sed, grep, head or tail is refused: a file outside the readable folders (the
    repository itself included, which ls -R with no folder or grep -r with no path would read), or
    one named where a pipe feeds it, or none where nothing does; sed in any form but -n 'A,Bp';
    tail -f; grep's pattern files."""
    if prog == "ls":
        flags, ops = scan_args(args, LS_SHORT, LS_LONG)
        files = ops
        recursive = any(f in ("-R", "--recursive") for f, _ in flags)
        if not files and recursive and not piped:  # the whole repository; ls alone lists only its folders' names
            return DENY.read_scope(server)
    elif prog == "cat":
        flags, files = scan_args(args)
        if not files:
            return DENY.cat
    elif prog == "sed":
        if len(args) < 3 or args[0] != "-n" or not SED_RANGE.match(args[1]) or any(a.startswith("-") for a in args[2:]):
            return DENY.sed
        files = args[2:]
    elif prog == "grep":
        flags, ops = scan_args(args, GREP_SHORT, GREP_LONG)
        if any(f in ("-f", "--file", "--exclude-from") for f, _ in flags):
            return DENY.pattern_file
        given = any(f in ("-e", "--regexp") for f, _ in flags)
        files = ops if given else ops[1:]
        recursive = any(f in ("-r", "-R", "--recursive", "--dereference-recursive") for f, _ in flags)
        if not piped and not files:
            return DENY.read_scope(server) if recursive else DENY.grep_file
    else:  # head, tail
        flags, ops = scan_args(args, HEAD_SHORT, HEAD_LONG)
        if prog == "tail" and any(f in ("-f", "-F", "--follow", "--retry") or f.startswith("--follow")
                                  for f, _ in flags):
            return DENY.tail_follow
        files = ops
    if piped:
        return DENY.pipe if files else None
    if any(not readable(repo, server, f) for f in files):
        return DENY.read_scope(server)
    return None


# key={...,...}: a JSON object of two keys or more, which Claude Code's own Bash check refused in
# "dontAsk" mode however it was quoted, with no reason the implementer could act on (2026-10-11,
# T207). Claude Code now runs in bypassPermissions; the words form stays the one the rules teach.
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
        bare = ShellCommand(rest, piped=cmd.piped, wild=cmd.wild[len(w) - len(rest):],
                            shapes=cmd.shapes[len(w) - len(rest):])
        return command_reason(bare, repo, server) or DENY.env
    if cmd.piped:
        if first not in FILTERS:
            return DENY.pipe
        return glob_reason(cmd, repo, server) or file_reason(first, w[1:], True, repo, server)
    if first == "vfg":
        if any(OBJECT_WORD.match(x) for x in w[2:]):
            return DENY.json_object
        if w[1:3] == ["script", "run"]:
            return DENY.script_run
        if not allowed_form(w):
            return DENY.vfg(w)
        if w[1:3] == ["action", "run"] and not task_given(w):
            return DENY.action_task
        if w[1:3] == ["action", "run"] and waits_for_trigger(w, repo, server):
            return DENY.waits_for_trigger
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
        return glob_reason(cmd, repo, server) or (
            DENY.read_scope(server) if any(not readable(repo, server, f) for f in w[3:]) else None)
    if first in ("ls", "cat", "sed") or first in FILTERS:
        return glob_reason(cmd, repo, server) or file_reason(first, w[1:], False, repo, server)
    if first == "cd":
        return DENY.cd
    if first == "mkdir":
        return DENY.mkdir(server)
    if first in READERS:
        return DENY.read
    if first in FINDERS:
        return DENY.find(server)
    if first in FILE_CHANGERS:
        return DENY.files(server)
    if first in PROCESSES:
        return DENY.ps
    return DENY.shape


# What a script waits on when a player must do something before it acts: the kit's click, hold,
# charge and crouch watchers and their stats, the sneak and projectile_lands presets (copied in as
# functions), and a launch pad's pad_block. Hot fireball (from boom_bow) and High Step (from
# launch_pad) were bound this way on six5, and a cast never fired them.
_WAITS = re.compile(r"\bitems\.(clicks|holds|charged|sneaking)\s*\(|\bitems\.(CLICK|SNEAK)\b|^def (sneak|projectile_lands)\(|"
                    r"\bv\[\s*[\"']pad_block[\"']\s*\]", re.M)
_ACTION_FIELD = re.compile(r"^\s*(script|runs)\s*=\s*[\"']([a-z_]+)[\"']", re.M)


def _action_fields(repo: str, server: str, name: str, depth: int = 0) -> dict:
    """An action's script and runs, from its file (this server's made one, else the library's),
    following one base class it imports from the library."""
    for path in (os.path.join(repo, "made", server, "actions", name + ".py"), os.path.join(repo, "actions", name + ".py")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        found = dict((k, v) for k, v in _ACTION_FIELD.findall(text))
        base = re.search(r"^from actions\.([a-z_]+) import", text, re.M)
        if base and depth < 2 and ("script" not in found or "runs" not in found):
            found = {**_action_fields(repo, server, base.group(1), depth + 1), **found}
        return found
    return {}


def waits_for_trigger(words: list[str], repo: str | None, server: str) -> bool:
    """Does `vfg action run incantation_create ... action=<name>` bind a made action whose script runs
    until stopped and waits for a crouch, a click or a pad? A cast starts its copy, which would wait
    and never act. Only this server's made scripts are read; anything not found is let through."""
    if repo is None:
        return False
    action, _ = run_words(words[3:])
    if action != "incantation_create":
        return False
    bound = next((x.split("=", 1)[1] for x in words[3:] if x.startswith("action=")), "")
    if not MADE_NAME.fullmatch(bound or ""):
        return False
    fields = _action_fields(repo, server, bound)
    if fields.get("runs") != "until_stopped":
        return False
    try:
        with open(os.path.join(repo, "made", server, "scripts", fields.get("script") or bound, "main.py"),
                  encoding="utf-8") as f:
            return _WAITS.search(f.read()) is not None
    except OSError:
        return False


def check_bash(command: str, repo: str | None = None, server: str = "main") -> str | None:
    """Why an implementer's Bash command is refused, or None to let the rules decide. Claude Code's
    own refusal of a disallowed command reads as if Bash itself were off, and a small model stops
    trying; each of these says what to send instead. vfg's own commands run, joined with && or ;,
    with 2>&1 or >/dev/null, piped into head, tail or grep; so do echo, sleep, py_compile, and ls,
    cat, sed -n 'A,Bp', grep, head and tail on files in scripts/, actions/ and made/<server>/ of the
    repository (`repo`; None: on none). No ~name or brace outside quotes, which bash would expand past
    the check of the literal path; a glob in a read is expanded here as bash would, every match checked."""
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
    """The guard's decision for one tool call: a reason to deny it, or None to let it run."""
    name = call.get("tool_name")
    inp = call.get("tool_input") or {}
    if name in ("WebFetch", "WebSearch"):
        return DENY.web
    if name == "Bash":
        if inp.get("run_in_background") is True:
            return DENY.background
        command = inp.get("command")
        return check_bash("" if command is None else str(command), g.repo, g.server)

    # Glob, Grep and Read stay in scripts/, actions/ and made/<server>/; a search names its folder.
    if name in ("Glob", "Grep"):
        path = str(inp.get("path") or "")
        pattern = str(inp.get("pattern") or "") if name == "Glob" else ""
        if (not path or not readable(g.repo, g.server, path) or pattern.startswith(("/", "~"))
                or ".." in pattern.split("/")):
            return DENY.read_scope(g.server)
    if name == "Read" and inp.get("file_path") and not readable(g.repo, g.server, str(inp["file_path"])):
        return DENY.read_scope(g.server)

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
        if args[i] in ("--from", "--preset", "--kind"):
            i += 2
            continue
        if not args[i].startswith("--"):
            positional.append(args[i])
        i += 1
    want = 0 if words[2] == "new" else 1
    return positional[want] if len(positional) > want else None


# ------------------------------------------------------------------ what a task made

# A name vfg script new, vfg script save and vfg preset save take; and a copy's name, as= (vfg's own
# rules: a command with another name is refused there, and is not recorded here).
MADE_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}", re.ASCII)
COPY_NAME = re.compile(r"[a-z0-9][a-z0-9_]{0,47}", re.ASCII)


def run_words(args: list[str]) -> tuple[str | None, str | None]:
    """(the action, the as=<copy>) of `vfg action run`'s words after `run`, in any order, as vfg
    reads them: the action is the first word that is no flag, no flag's value and no key=value."""
    if args[:1] == ["--"]:
        args = args[1:]
    action = copy = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--task", "--for"):
            i += 2
            continue
        if a.startswith("as="):
            copy = a[len("as="):]
        elif action is None and "=" not in a and not a.startswith("-"):
            action = a
        i += 1
    return action, copy


def made_by(words: list[str]) -> list[tuple[str, str]]:
    """What one command makes, as (kind, name): `vfg script new <name>` a script and its action,
    `vfg script save <copy> <name>` an action, `vfg preset save <script> <function> <name>` a preset,
    and `vfg action run <action> … as=<copy>` a copy, unless the action is edit or stop, which
    change a copy and start none."""
    if words[:1] != ["vfg"]:
        return []
    if words[1:3] in (["script", "new"], ["script", "save"]):
        name = new_script_name(words)
        if not name or not MADE_NAME.fullmatch(name):
            return []
        return [("scripts", name), ("actions", name)] if words[2] == "new" else [("actions", name)]
    if words[1:3] == ["preset", "save"]:
        return [("presets", words[5])] if len(words) == 6 and MADE_NAME.fullmatch(words[5]) else []
    if words[1:3] == ["action", "run"]:
        action, copy = run_words(words[3:])
        if action and action not in ("edit", "stop") and copy and COPY_NAME.fullmatch(copy):
            return [("copies", copy)]
    return []


def record(g: GuardContext, kind: str, name: str) -> None:
    """One thing the task made, kept once, in the order it came."""
    names = g.made.setdefault(kind, [])
    if name not in names:
        names.append(name)


def taken_here(g: GuardContext, kind: str, name: str) -> bool:
    """Is `name` already a script folder, an action or a preset in made/<server>/? vfg script new,
    vfg script save and vfg preset save refuse a name that is, and make nothing."""
    base = os.path.join(g.repo, "made", g.server)
    path = {"scripts": os.path.join(base, "scripts", name), "actions": os.path.join(base, "actions", name + ".py"),
            "presets": os.path.join(base, "presets", name + ".py")}.get(kind)
    return path is not None and os.path.lexists(path)


def note_made(call: dict, g: GuardContext) -> None:
    """After an implementer's Bash call is let through: what each of its commands makes, in order
    (made_by), is recorded, unless a name it would make is already in made/<server>/, which vfg
    refuses (so another task's script is never reported as this one's); and a script its vfg script
    new or vfg script save is about to make in made/<server>/scripts/ (with its action) is its own to
    change."""
    command = str((call.get("tool_input") or {}).get("command") or "")
    for cmd in split_shell(command):
        name = new_script_name(cmd.words)
        if name and not os.path.exists(os.path.join(g.repo, "made", g.server, "scripts", name)):
            g.own.add(name)
        made = made_by(cmd.words)
        if any(taken_here(g, kind, n) for kind, n in made):
            continue
        for kind, n in made:
            record(g, kind, n)


def note_written(call: dict, g: GuardContext) -> None:
    """After an implementer's Write is let through: a new made/<server>/actions/<name>.py (a variant)
    or made/<server>/presets/<name>.py is recorded as made. A Write over a file that is there, or
    an Edit, makes nothing new."""
    path = str((call.get("tool_input") or {}).get("file_path") or "")
    if not path:
        return
    real = realish(os.path.abspath(os.path.join(g.repo, _expand_home(path))))
    stem, ext = os.path.splitext(os.path.basename(real))
    if ext != ".py" or not MADE_NAME.fullmatch(stem) or os.path.exists(real):
        return
    for kind in ("actions", "presets"):
        if os.path.dirname(real) == realish(os.path.join(g.repo, "made", g.server, kind)):
            record(g, kind, stem)


def made_now(g: GuardContext) -> dict[str, list[str]]:
    """What the task made, as on_report gives it: the record checked against the disk, a copy with
    every kind. A script is kept when its main.py is there, an action or a preset when its file is;
    a copy as it was named, since what ran cannot be told from here."""
    base = os.path.join(g.repo, "made", g.server)
    there = {"scripts": lambda n: os.path.join(base, "scripts", n, "main.py"),
             "actions": lambda n: os.path.join(base, "actions", n + ".py"),
             "presets": lambda n: os.path.join(base, "presets", n + ".py")}
    out = {}
    for kind in MADE_KINDS:
        names = list(g.made.get(kind) or [])
        out[kind] = names if kind == "copies" else [n for n in names if os.path.isfile(there[kind](n))]
    return out


async def check_running(call: dict, g: GuardContext) -> str | None:
    """Why an implementer may not change this script: it is in made/<server>/scripts/, it is not its
    own (it did not make it, and it is not named as its task), and a copy another task or the brain
    started runs it. New code is a new script with its own name."""
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


# vfg test runs a run may make: the first, and a rerun after each of the three fixes the rules allow.
TESTS_PER_RUN = 4


def test_names(call: dict) -> list[str]:
    """The names a Bash call's `vfg test` commands test, in order."""
    if call.get("tool_name") != "Bash":
        return []
    return [c.words[2] for c in split_shell(str((call.get("tool_input") or {}).get("command") or ""))
            if c.words[:2] == ["vfg", "test"] and len(c.words) >= 3]


def code_print(data: bytes, path: str) -> bytes:
    """What a file says, for the unchanged-rerun check: a Python file's code with its comments,
    blank lines, formatting and docstrings dropped (ast), so an edit to those alone is no change;
    any other file, or one that does not parse, as it is."""
    if not path.endswith(".py"):
        return data
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return data
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body
                and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree).encode("utf-8")


def test_print(name: str, g: GuardContext) -> str:
    """What a test of `name` covers, as it is now: its test file (the task's, or a library script's),
    the script and the action of that name, and every script, action and preset the task made, each
    Python file by its code alone (code_print)."""
    made = os.path.join(g.repo, "made", g.server)
    paths = [os.path.join(made, "tests", name + ".py"), os.path.join(g.repo, "scripts", name, "test.py"),
             os.path.join(made, "scripts", name), os.path.join(made, "actions", name + ".py")]
    for kind in ("scripts", "actions", "presets"):
        for n in g.made.get(kind) or []:
            paths.append(os.path.join(made, kind, n) if kind == "scripts" else os.path.join(made, kind, n + ".py"))
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        files = [p] if not os.path.isdir(p) else sorted(
            os.path.join(d, f) for d, _, fs in os.walk(p) if "__pycache__" not in d for f in fs)
        for f in files:
            try:
                with open(f, "rb") as fh:
                    h.update(f.encode() + b"\0" + hashlib.sha256(code_print(fh.read(), f)).digest())
            except OSError:
                h.update(f.encode() + b"\0-")
    return h.hexdigest()


# Server commands that only look: the shape of a hand probe of the live world.
_LOOKS = (("data", "get"), ("list",), ("scoreboard", "players", "get"))


def probe(command: str) -> bool:
    """Does a vfg cmd's server command only look (execute if|unless with no run, data get, list,
    scoreboard players get, or one of those after execute ... run)?"""
    words = command.lstrip("/").split()
    if words[:1] == ["execute"]:
        if "run" not in words:
            return True
        words = words[len(words) - words[::-1].index("run"):]
    words = [w.lstrip("/").lower() for w in words]
    return any(tuple(words[:len(look)]) == look for look in _LOOKS)


# Looks at the live world a run may take when there is a test server: its tests are its check.
PROBES_PER_RUN = 3


def probe_reason(call: dict, g: GuardContext) -> str | None:
    """A hand probe of the live world: refused once the run's test runs are spent (the run reports),
    and, when there is a test server, past PROBES_PER_RUN in a run (the check goes in the test). A
    vfg cmd that acts (a build step) always runs. Counted when let through."""
    if call.get("tool_name") != "Bash":
        return None
    looks = [c for c in split_shell(str((call.get("tool_input") or {}).get("command") or ""))
             if c.words[:2] == ["vfg", "cmd"] and len(c.words) >= 3 and probe(" ".join(c.words[2:]))]
    if not looks:
        return None
    if g.tests_run >= TESTS_PER_RUN:
        return DENY.tests_spent
    if g.test_server:
        if g.probes_run + len(looks) > PROBES_PER_RUN:
            return DENY.probes_spent
        g.probes_run += len(looks)
    return None


def test_reason(call: dict, g: GuardContext) -> str | None:
    """Why this call's `vfg test` may not run: a fifth in this run, or nothing it covers changed since
    this run's last test of that name. A test is not a debugger. Counted and noted when let through."""
    names = test_names(call)
    if not names:
        return None
    if g.tests_run + len(names) > TESTS_PER_RUN:
        return DENY.test_cap
    prints = {n: test_print(n, g) for n in names}
    if any(g.tested.get(n) == fp for n, fp in prints.items()):
        return DENY.test_again
    g.tests_run += len(names)
    g.tested.update(prints)
    return None


async def gate(call: dict, g: GuardContext) -> str | None:
    """The guard, for every adapter, before a tool runs: a reason to refuse the call, or None. A call
    let through is first given to the run's snapshot (what it is about to change, kept), then what
    it makes is recorded (note_made for a Bash call, note_written for a Write)."""
    reason = decide(call, g) or await check_running(call, g) or test_reason(call, g) or probe_reason(call, g)
    if reason:
        return reason
    if g.snapshot is not None:
        try:
            await g.snapshot.before(call)
        except Exception as e:  # a snapshot that breaks never blocks the call
            log("guard", "%s: the snapshot before %s broke: %s: %s" % (g.task or "-", call.get("tool_name"),
                                                                      type(e).__name__, e))
    if call.get("tool_name") == "Bash":
        note_made(call, g)
    elif call.get("tool_name") == "Write":
        note_written(call, g)
    return None


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
