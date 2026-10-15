"""The guard: one check before every tool an implementer calls, whatever the harness. Every adapter
calls gate() before a tool runs (Claude Code from its PreToolUse hook, OpenCode from its plugin's
tool.execute.before), with the call in Claude Code's shape, {"tool_name", "tool_input"}, and the
tool names Bash, Read, Write, Edit, Glob and Grep. gate() gives a reason to refuse it, one line
saying what to send instead, which goes back to the model as that tool's error; or None, and the
call runs. The adapters turn their harness's own permission checks off, so the guard is the only
gate.

An implementer writes engine classes (made/<server>/content/<id>.py) and their tests, and sets up
what the job needs through `vfg engine` in exactly the forms the PC's contract gives (CONTRACT2:
"The vfg commands an implementer may run"). What a call it lets through makes (a class, a binding,
a grant) it records (made_by, note_written), and the runner gives the brain that record, checked
against the disk and the engine (made_now), with the report. Before such a call runs, it gives it
to the run's snapshot (putback.py), which keeps what the call is about to change so code can put it
back after a cancel or a run that reached its limit."""
import ast
import glob
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ROOT, log
from .vfg import VfgRunner, engine_read


_COMMANDS: list[frozenset] = []

# The engine's list of the owner's commands, which it checks every command it sends against.
COMMANDS_PY = os.path.join("engine", "vfg_engine", "mc", "commands.py")


def frozenset_in(path: str, name: str) -> frozenset:
    """`name = frozenset(...)` (or a set) of literals at the top of the Python file `path`, read as
    text, never imported (the engine's modules import the rest of it). RuntimeError when the file
    has none."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        named = any(isinstance(t, ast.Name) and t.id == name for t in getattr(node, "targets", ()))
        value = getattr(node, "value", None)
        if named and isinstance(value, ast.Call) and len(value.args) == 1:
            return frozenset(ast.literal_eval(value.args[0]))
        if named and isinstance(value, ast.Set):
            return frozenset(ast.literal_eval(value))
    raise RuntimeError("%s has no %s = frozenset(...)" % (path, name))


def refused() -> frozenset:
    """Server commands that are the owner's to run: REFUSED in engine/vfg_engine/mc/commands.py,
    the list the engine holds every command it sends to (`vfg cmd` goes through the library's
    command scenario); on first use. A file without the list is an error: the guard never runs
    without it."""
    if not _COMMANDS:
        path = os.path.join(ROOT, COMMANDS_PY)
        try:
            _COMMANDS.append(frozenset_in(path, "REFUSED"))
        except RuntimeError:
            raise RuntimeError("%s has no REFUSED = frozenset(...): the owner's commands are unknown" % path)
    return _COMMANDS[0]

# Server-wide settings an implementer may not change (the engine does not refuse them: a seeded game
# scenario may use the world border).
SERVER_WIDE = frozenset(["setworldspawn", "defaultgamemode", "difficulty", "worldborder"])


class DENY:
    owner = "That is the server owner's to do; report it."

    @staticmethod
    def server_wide(word: str) -> str:
        return "%s changes the whole server: implementers may not run it. Leave it out and say so in your report." % word

    @staticmethod
    def made_only(server: str) -> str:
        return ("Refused: you write only made/%s/content/<id>.py, one class a file, and made/%s/tests/<id>.py, its "
                "test. engine/ is read-only: copy what you need from engine/library/ into your own class."
                % (server, server))

    @staticmethod
    def library_name(cid: str) -> str:
        return ("Refused: %s is a library class's id: a made class of that id runs in the library's place, for "
                "everyone. Give yours its own id (your task's name), and copy what you need from "
                "engine/library/%s.py." % (cid, cid))

    @staticmethod
    def content_name(server: str) -> str:
        return ("Refused: a class's file is made/%s/content/<id>.py and its test made/%s/tests/<id>.py, the id a "
                "letter, then up to 63 of a-z, 0-9 and _ (the class's name in snake_case)." % (server, server))

    expansion = "Write the server command literally inside single quotes: no $( ), backticks or variables in a vfg cmd."
    # An implementer's Bash: each says what to send instead, in one line.
    shape = ("Refused: that program does not run here. These do: vfg engine|cmd|test|state|history ..., "
             "python3 -m py_compile <file>, sleep N, echo, and ls, cat, sed -n 'A,Bp', grep, head, tail on files in "
             "engine/ and made/; join them with && or ;, and pipe vfg into head -n N, tail -n N or grep <pattern>.")
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
        return "Refused: use the Glob tool with a path (path engine/library, pattern *.py), or ls <folder>."

    @staticmethod
    def read_scope(server: str) -> str:
        return ("Refused: you read and search only engine/ and made/%s/: give a path in one of them "
                "(engine/WRITING.md, engine/library/, made/%s/content/)." % (server, server))
    ps = "Refused: vfg engine instances shows what runs, and vfg engine stop <scenario> stops a scenario."
    python = ("Refused: only python3 -m py_compile <file> runs; your class runs in the engine: vfg engine reload <id>, "
              "then vfg engine start, grant, give or apply.")
    wild = ("Refused: no {a,b} or ~name in a command, and no unquoted * ? [ ] after echo or sleep, since bash would "
            "expand them where the guard cannot see: put a pattern in single quotes ('def .*') and name each file by "
            "its path.")
    grep_file = "Refused: grep reads a file you name, or what a vfg command pipes into it: name one."
    pattern_file = "Refused: no pattern files: give grep its pattern on the command line, or with -e."
    tail_follow = "Refused: tail -f never ends: use tail -n N, or vfg engine log -n 40."

    @staticmethod
    def files(server: str) -> str:
        return ("Refused: files change only by Write and Edit, in made/%s/content/<id>.py and made/%s/tests/<id>.py."
                % (server, server))

    @staticmethod
    def vfg(words: list[str]) -> str:
        """How to send a vfg command the guard did not let through."""
        b, c = (words + [None, None, None])[1:3]
        if b and b.startswith("-"):
            return "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg engine ..., as it is."
        form = VFG_USAGE.get("%s %s" % (b, c)) or VFG_USAGE.get(str(b))
        if form:
            return "Refused: send it as %s." % form
        if b == "engine":
            return DENY.engine(c)
        return "Refused: vfg %s is not an implementer's. These are: %s." % (b, VFG_FORMS)

    @staticmethod
    def engine(word: str | None) -> str:
        return ("Refused: vfg engine %s is not an implementer's. vfg engine takes reload, classes, meta, config get, "
                "instances, log, places, incantations and status (each read with --json if you need it), and start, "
                "stop, grant, revoke, give, take, apply, clear, bind and unbind." % (word or "alone"))

    engine_flag = "Refused: vfg engine takes its command first, and --json after it: vfg engine classes --json."
    config = ("Refused: vfg engine config set and undo are the brain's: change the number in your class's file, then "
              "vfg engine reload <id>.")
    spawn = ("Refused: vfg engine spawn is not an implementer's: a scenario spawns its actors (self.spawn); start one "
             "with vfg engine start <scenario>.")
    per_player = ("Refused: name one player (Steve), never @a or @r: send one command a player, joined with ; "
                  "(vfg engine grant Steve fireball; vfg engine grant Alex fireball).")

    @staticmethod
    def by(task: str | None) -> str:
        return ("Refused: a binding says whose it is, by your task's name: vfg engine bind <player> '<phrase>' "
                "<ability> --by %s." % (task or "<your task>"))

    background = "Run commands in the foreground: what you start runs in the engine (vfg engine start)."
    test_again = ("Refused: nothing changed since the last test run: read the log lines its FAIL printed (add -n 60 to "
                  "your next vfg test for more), then change the ability or the test.")
    test_cap = "Refused: that is a fifth test run in this run, and the rules allow four: report what still fails."
    tests_spent = "Refused: your test runs are spent: report what still fails, item by item."
    probes_spent = ("Refused: the live world is not for testing: write the check into your test (vfg test <your task>), "
                    "or report.")
    web = "Implementers have no web access: work from the repository (engine/ and made/) and vfg."


# Every vfg command an implementer runs, and how each is sent (CONTRACT2).
VFG_FORMS = ("vfg engine reload [<id> ...] | vfg engine classes|places|status [--json] | "
             "vfg engine meta [<id>] [--json] | vfg engine config get <id> [--json] | "
             "vfg engine instances [--kind K] [--user <player>] [--json] | vfg engine log -n N [--json] | "
             "vfg engine incantations [--player <player>] [--json] | "
             "vfg engine start <scenario> [key=value ...] [--wait S] | "
             "vfg engine stop <scenario> | vfg engine stop --instance <instance> | "
             "vfg engine grant|revoke <player> <ability> | vfg engine give|take <player> <item> | "
             "vfg engine apply <player> <mark> [key=value ...] [--seconds S] | vfg engine clear <player> <mark> | "
             "vfg engine bind <player> '<phrase>' <ability> --by <your task> | "
             "vfg engine bind <player> '<phrase>' --scenario <scenario> [key=value ...] [--cooldown S] --by <your task> | "
             "vfg engine unbind <player> '<phrase>' | vfg cmd '<command>' | vfg test <id> [-n N] [--timeout S] | "
             "vfg state [--json] | vfg history ...")
VFG_USAGE = {
    "engine reload": "vfg engine reload <id> ..., or vfg engine reload alone for every class",
    "engine classes": "vfg engine classes, with --json or nothing after it",
    "engine meta": "vfg engine meta <id>, or vfg engine meta alone, with --json if you need it",
    "engine config": "vfg engine config get <id>, with --json if you need it",
    "engine instances": ("vfg engine instances, with --kind K (ability, item, mark, actor or scenario), "
                         "--user <player> or --json after it"),
    "engine log": "vfg engine log -n N, with --json if you need it",
    "engine places": "vfg engine places, with --json or nothing after it",
    "engine incantations": "vfg engine incantations, with --player <player> or --json after it",
    "engine status": "vfg engine status, with --json or nothing after it",
    "engine start": "vfg engine start <scenario> key=value ..., with --wait S if you need it",
    "engine stop": "vfg engine stop <scenario>, or vfg engine stop --instance <instance>",
    "engine grant": "vfg engine grant <player> <ability>",
    "engine revoke": "vfg engine revoke <player> <ability>",
    "engine give": "vfg engine give <player> <item>",
    "engine take": "vfg engine take <player> <item>",
    "engine apply": "vfg engine apply <player> <mark> key=value ..., with --seconds S if you need it",
    "engine clear": "vfg engine clear <player> <mark>",
    "engine bind": ("vfg engine bind <player> '<phrase>' <ability> --by <your task>, or vfg engine bind <player> "
                    "'<phrase>' --scenario <scenario> key=value ... [--cooldown S] --by <your task>"),
    "engine unbind": "vfg engine unbind <player> '<phrase>'",
    "cmd": "vfg cmd '<command>'",
    "state": "vfg state, or vfg state --json, with nothing after it",
    "test": "vfg test <id>, with -n N or --timeout S after it if you need them",
    # a saved place is the engine's now: the library's save_place scenario names one
    "place": "vfg engine start save_place name=<name> at='<x> <y> <z>'",
}


# What a task made, by kind: the keys of on_report's `made` and of the task's row (CONTRACT2, "A
# task's made").
MADE_KINDS = ("content", "bindings", "grants")


@dataclass
class GuardContext:
    """One implementer's: the runner keeps it with the task, so a follow-up keeps what it made."""
    repo: str
    # VFG_SERVER: implementers write only in made/<server>/content/ and made/<server>/tests/.
    server: str = "main"
    # The task the implementer works on: its name, which the class it makes takes, and every
    # binding it makes (--by).
    task: str | None = None
    # What it made, by kind, each once in the order it was made: the classes it wrote, the
    # bindings and grants it sent. made_now checks it against the disk and the engine.
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
    """One of the commands an implementer may run, besides `vfg engine` (engine_reason), in a form
    vfg takes."""
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
    if b == "state":
        return words[2:] in ([], ["--json"])
    return False


def test_form(args: list[str]) -> bool:
    """`vfg test <id>`, then -n N and --timeout S (or --timeout=S) at most once each, numbers."""
    if not args or not CONTENT_ID.fullmatch(args[0]):
        return False
    seen, i = set(), 1
    while i < len(args):
        flag, _, value = args[i].partition("=") if args[i].startswith("--timeout=") else (args[i], "", None)
        if flag not in ("-n", "--timeout") or flag in seen:
            return False
        if value is None:
            if i + 1 >= len(args):
                return False
            value, i = args[i + 1], i + 1
        if not NUMBER.fullmatch(value):
            return False
        seen.add(flag)
        i += 1
    return True


# A class's id, which is its file's name too (engine/WRITING.md): a letter, then up to 63 of a-z,
# 0-9 and _.
CONTENT_ID = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
# A player's name, as the engine takes one.
PLAYER_NAME = re.compile(r"[A-Za-z0-9_]{1,16}", re.ASCII)
# A task's name (Prototype_Named_Tasks): galick_gun_styrk.
TASK_NAME = re.compile(r"[a-z][a-z0-9_]{1,31}", re.ASCII)
# One of a scenario's or a mark's args: key=value.
PAIR = re.compile(r"[a-z_][a-z0-9_]*=", re.ASCII)
NUMBER = re.compile(r"\d+(\.\d+)?", re.ASCII)
# The engine's kinds of class.
KINDS = ("ability", "item", "mark", "actor", "scenario")


@dataclass
class EngineCall:
    """One `vfg engine` command as sent: its op, its words in order, its flags with their values
    (--json's is ""), and its key=value args as given."""
    op: str
    words: list[str]
    flags: dict[str, str]
    pairs: list[str]


def _who(w: str) -> bool:
    """A player's name; an @ selector is let through here for engine_reason to refuse with what to send."""
    return bool(PLAYER_NAME.fullmatch(w)) or w.startswith("@")


def _ids(call: EngineCall, at: int = 0) -> bool:
    return all(CONTENT_ID.fullmatch(w) for w in call.words[at:])


def _bind(c: EngineCall) -> bool:
    """bind <player> <phrase> <ability>, or bind <player> <phrase> --scenario <scenario> key=value ..."""
    if not _who(c.words[0]) or not phrase_of(c.words[1]):
        return False
    if "--scenario" in c.flags:   # a scenario binding: its args set once, and its own cooldown
        return len(c.words) == 2 and bool(CONTENT_ID.fullmatch(c.flags["--scenario"])) \
            and bool(NUMBER.fullmatch(c.flags.get("--cooldown", "0")))
    return len(c.words) == 3 and not c.pairs and "--cooldown" not in c.flags and _ids(c, 2)


@dataclass(frozen=True)
class EngineForm:
    """How one `vfg engine` command is sent (CONTRACT2): how many words follow it, the flags it takes
    with a value, whether key=value args may follow its words, whether it is a read (which takes
    --json too), and the check of what was given."""
    least: int
    most: int
    flags: tuple = ()
    pairs: bool = False
    read: bool = False
    check: Callable[[EngineCall], bool] = _ids


ENGINE_FORMS = {
    "reload": EngineForm(0, 64),
    "classes": EngineForm(0, 0, read=True),
    "meta": EngineForm(0, 1, read=True),
    "config": EngineForm(2, 2, read=True, check=lambda c: c.words[0] == "get" and _ids(c, 1)),
    "instances": EngineForm(0, 0, ("--kind", "--user"), read=True,
                            check=lambda c: c.flags.get("--kind", "ability") in KINDS
                            and _who(c.flags.get("--user", "x"))),
    "log": EngineForm(0, 0, ("-n",), read=True, check=lambda c: c.flags.get("-n", "1").isdigit()),
    "places": EngineForm(0, 0, read=True),
    "incantations": EngineForm(0, 0, ("--player",), read=True, check=lambda c: _who(c.flags.get("--player", "x"))),
    "status": EngineForm(0, 0, read=True),
    "start": EngineForm(1, 1, ("--wait",), pairs=True,
                        check=lambda c: _ids(c) and bool(NUMBER.fullmatch(c.flags.get("--wait", "0")))),
    "stop": EngineForm(0, 1, ("--instance",), check=lambda c: (len(c.words) == 1) != ("--instance" in c.flags)
                       and _ids(c)),
    "grant": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and _ids(c, 1)),
    "revoke": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and _ids(c, 1)),
    "give": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and _ids(c, 1)),
    "take": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and _ids(c, 1)),
    "apply": EngineForm(2, 2, ("--seconds",), pairs=True,
                        check=lambda c: _who(c.words[0]) and _ids(c, 1)
                        and bool(NUMBER.fullmatch(c.flags.get("--seconds", "1")))),
    "clear": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and _ids(c, 1)),
    "bind": EngineForm(2, 3, ("--scenario", "--by", "--cooldown"), pairs=True, check=_bind),
    "unbind": EngineForm(2, 2, check=lambda c: _who(c.words[0]) and bool(phrase_of(c.words[1]))),
}
# The ops whose first word is a player.
PLAYER_FIRST = frozenset(["grant", "revoke", "give", "take", "apply", "clear", "bind", "unbind"])


def engine_call(args: list[str]) -> EngineCall | None:
    """`vfg engine`'s words after `engine` as one call in a form an implementer sends (ENGINE_FORMS),
    or None when they are in none. A flag's value is the next word or follows = (--seconds=10); each
    flag at most once; key=value args come after the words, and nothing after them."""
    if not args or args[0] not in ENGINE_FORMS:
        return None
    form = ENGINE_FORMS[args[0]]
    call = EngineCall(args[0], [], {}, [])
    rest = args[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("-"):
            name, eq, value = a.partition("=")
            if name in call.flags:
                return None
            if name == "--json" and form.read and not eq:
                value = ""
            elif name not in form.flags:
                return None
            elif not eq:
                if i + 1 >= len(rest):
                    return None
                i += 1
                value = rest[i]
            call.flags[name] = value
        elif form.pairs and len(call.words) >= form.least and PAIR.match(a):
            call.pairs.append(a)
        elif call.pairs:
            return None
        else:
            call.words.append(a)
        i += 1
    if not form.least <= len(call.words) <= form.most or not form.check(call):
        return None
    return call


def engine_reason(args: list[str], task: str | None) -> str | None:
    """Why `vfg engine <args>` is refused, in one line saying what to send instead; or None. A
    binding names the task it is for (--by), which is this one's when its name is known."""
    op = args[0] if args else None
    if op == "config" and args[1:2] in (["set"], ["undo"]):
        return DENY.config
    if op == "spawn":
        return DENY.spawn
    if op and op.startswith("-"):
        return DENY.engine_flag
    call = engine_call(args)
    if call is None:
        return DENY.vfg(["vfg", "engine"] + args[:1])
    who = ([call.words[0]] if op in PLAYER_FIRST else []) + [call.flags[f] for f in ("--user", "--player")
                                                              if f in call.flags]
    if any(w.startswith("@") for w in who):
        return DENY.per_player
    if op == "bind":
        by = call.flags.get("--by")
        if not by or not TASK_NAME.fullmatch(by) or (task is not None and by != task):
            return DENY.by(task)
    return None


def command_values(words: list[str]) -> list[str]:
    """The server commands a `vfg engine start`, `apply` or `bind` gives in its args (the library's
    command scenario, say): the value of every key that names commands (command, commands, …),
    whole, item by item as a JSON list, and comma by comma as a list on the command line."""
    if words[1:2] != ["engine"] or words[2:3] not in (["start"], ["apply"], ["bind"]):
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


# What an implementer reads and searches: the engine (WRITING.md, KIT.md, the library, the
# examples, the core) and this server's folder, nothing else of the repository (the replays and
# tests there hold answers, and the rest of the code is not theirs).
def readable(repo: str | None, server: str, p: str) -> bool:
    """Is `p` (relative to the repository) in engine/ or made/<server>/?"""
    if repo is None:
        return False
    return any(within(os.path.join(repo, d), p, repo) for d in ("engine", os.path.join("made", server)))


def made_file(repo: str, server: str, p: str) -> tuple[str, str] | None:
    """("content", id) when `p` (relative to the repository) is made/<server>/content/<id>.py,
    ("tests", id) when it is made/<server>/tests/<id>.py; else None. The only files an implementer
    writes."""
    real = realish(os.path.abspath(os.path.join(repo, _expand_home(p))))
    stem, ext = os.path.splitext(os.path.basename(real))
    if ext != ".py" or not CONTENT_ID.fullmatch(stem):
        return None
    for kind in ("content", "tests"):
        if os.path.dirname(real) == realish(os.path.join(repo, "made", server, kind)):
            return kind, stem
    return None


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
                           "install", "unlink", "mkdir"])
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


def command_reason(cmd: ShellCommand, repo: str | None, server: str, task: str | None = None) -> str | None:
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
        return command_reason(bare, repo, server, task) or DENY.env
    if cmd.piped:
        if first not in FILTERS:
            return DENY.pipe
        return glob_reason(cmd, repo, server) or file_reason(first, w[1:], True, repo, server)
    if first == "vfg":
        if w[1:2] == ["engine"]:
            return engine_reason(w[2:], task)
        return None if allowed_form(w) else DENY.vfg(w)
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
    if first in READERS:
        return DENY.read
    if first in FINDERS:
        return DENY.find(server)
    if first in FILE_CHANGERS:
        return DENY.files(server)
    if first in PROCESSES:
        return DENY.ps
    return DENY.shape


def check_bash(command: str, repo: str | None = None, server: str = "main", task: str | None = None) -> str | None:
    """Why an implementer's Bash command is refused, or None to let the rules decide. Claude Code's
    own refusal of a disallowed command reads as if Bash itself were off, and a small model stops
    trying; each of these says what to send instead. vfg's own commands run in the forms the
    contract gives (`task`: the name a binding's --by must be), joined with && or ;, with 2>&1 or
    >/dev/null, piped into head, tail or grep; so do echo, sleep, py_compile, and ls, cat, sed -n
    'A,Bp', grep, head and tail on files in engine/ and made/<server>/ of the repository (`repo`;
    None: on none). No ~name or brace outside quotes, which bash would expand past the check of the
    literal path; a glob in a read is expanded here as bash would, every match checked."""
    cmds = split_shell(command)
    # Server commands that are the owner's, however they are dressed up, and the server-wide settings:
    # in a vfg cmd, and in the commands a scenario's or a mark's args give.
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
        reason = command_reason(cmd, repo, server, task)
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
        return check_bash("" if command is None else str(command), g.repo, g.server, g.task)

    # Glob, Grep and Read stay in engine/ and made/<server>/; a search names its folder.
    if name in ("Glob", "Grep"):
        path = str(inp.get("path") or "")
        pattern = str(inp.get("pattern") or "") if name == "Glob" else ""
        if (not path or not readable(g.repo, g.server, path) or pattern.startswith(("/", "~"))
                or ".." in pattern.split("/")):
            return DENY.read_scope(g.server)
    if name == "Read" and inp.get("file_path") and not readable(g.repo, g.server, str(inp["file_path"])):
        return DENY.read_scope(g.server)

    # An implementer writes its classes and their tests, and nothing else: never in the engine.
    if name in ("Write", "Edit") and inp.get("file_path"):
        path = str(inp["file_path"])
        made = made_file(g.repo, g.server, path)
        if made and made[0] == "content" and os.path.isfile(os.path.join(g.repo, "engine", "library", made[1] + ".py")):
            return DENY.library_name(made[1])
        if made:
            return None
        if any(within(os.path.join(g.repo, "made", g.server, kind), path, g.repo) for kind in ("content", "tests")):
            return DENY.content_name(g.server)
        return DENY.made_only(g.server)
    return None


# ------------------------------------------------------------------ what a task made

def phrase_of(text: str) -> str:
    """A phrase as the engine keeps it (engine/vfg_engine/incantations.py, normalize): lowercase,
    accents and apostrophes dropped, every other mark (hyphens and underscores too) a space,
    whitespace collapsed."""
    s = unicodedata.normalize("NFKD", str(text or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"['‘’]", "", s)
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def binding_id(player: str, phrase: str) -> str | None:
    """The engine's id of a player's binding, `steve.fire_ball` (CONTRACT2, "A task's made"); None
    for a phrase with no word in it."""
    p = phrase_of(phrase)
    return "%s.%s" % (str(player).lower(), p.replace(" ", "_")) if p else None


def grant_key(player: Any, ability: Any) -> str:
    """`steve:fireball`: one grant, as a record and the engine's instances are matched (a player's
    name in any case is one player)."""
    return "%s:%s" % (str(player).lower(), ability)


def made_by(words: list[str]) -> list[tuple[str, str]]:
    """What one command makes, as (kind, name): `vfg engine bind <player> <phrase> ...` a binding,
    by the engine's id, and `vfg engine grant <player> <ability>` a grant, `<player>:<ability>`."""
    if words[:2] != ["vfg", "engine"]:
        return []
    call = engine_call(words[2:])
    if call is None or not PLAYER_NAME.fullmatch(call.words[0] if call.words else ""):
        return []
    if call.op == "bind":
        bid = binding_id(call.words[0], call.words[1])
        return [("bindings", bid)] if bid else []
    if call.op == "grant":
        return [("grants", "%s:%s" % (call.words[0], call.words[1]))]
    return []


def record(g: GuardContext, kind: str, name: str) -> None:
    """One thing the task made, kept once, in the order it came."""
    names = g.made.setdefault(kind, [])
    if name not in names:
        names.append(name)


def note_made(call: dict, g: GuardContext) -> None:
    """After an implementer's Bash call is let through: what each of its commands makes, in order
    (made_by), is recorded. Whether it landed is made_now's to tell."""
    command = str((call.get("tool_input") or {}).get("command") or "")
    for cmd in split_shell(command):
        for kind, n in made_by(cmd.words):
            record(g, kind, n)


def note_written(call: dict, g: GuardContext) -> None:
    """After an implementer's Write or Edit is let through: a class's file, made/<server>/content/
    <id>.py, is recorded as the task's (made or changed by it). A test is not: it goes with its
    class."""
    path = str((call.get("tool_input") or {}).get("file_path") or "")
    found = made_file(g.repo, g.server, path) if path else None
    if found and found[0] == "content":
        record(g, "content", found[1])


# How long made_now waits for each of the engine's reads.
ENGINE_READ_S = 10.0


async def made_now(g: GuardContext, vfg: VfgRunner | None = None) -> dict[str, list[str]]:
    """What the task made, as on_report gives it: the record checked against what is there now, a
    copy with every kind. A class is kept when its file is in made/<server>/content/; a binding when
    the engine lists it (`vfg engine incantations --json`), a grant when the engine runs it (`vfg
    engine instances --kind ability --json`): one read each, only for a kind the record has. With no
    vfg, or an engine that cannot be read (logged), the bindings and grants are as recorded."""
    content = [n for n in g.made.get("content") or []
               if os.path.isfile(os.path.join(g.repo, "made", g.server, "content", n + ".py"))]
    bindings, grants = list(g.made.get("bindings") or []), list(g.made.get("grants") or [])
    if vfg is not None and bindings:
        data, failed = await engine_read(vfg, "incantations", timeout=ENGINE_READ_S)
        if data is None:
            log("guard", "%s: the engine's bindings could not be read, so its bindings are as recorded: %s"
                % (g.task or "-", failed))
        else:
            there = {e.get("id") for e in data.get("incantations") or [] if isinstance(e, dict)}
            bindings = [b for b in bindings if b in there]
    if vfg is not None and grants:
        data, failed = await engine_read(vfg, "instances", "--kind", "ability", timeout=ENGINE_READ_S)
        if data is None:
            log("guard", "%s: the engine's grants could not be read, so its grants are as recorded: %s"
                % (g.task or "-", failed))
        else:
            there = {grant_key(i.get("user"), i.get("id")) for i in data.get("instances") or [] if isinstance(i, dict)}
            grants = [x for x in grants if grant_key(*x.split(":", 1)) in there]
    return {"content": content, "bindings": bindings, "grants": grants}


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
    """What a test of `name` covers, as it is now: its test file, the class of that name, and every
    class the task made, each by its code alone (code_print)."""
    made = os.path.join(g.repo, "made", g.server)
    paths = [os.path.join(made, "tests", name + ".py"), os.path.join(made, "content", name + ".py")]
    paths += [os.path.join(made, "content", n + ".py") for n in g.made.get("content") or []]
    h = hashlib.sha256()
    for f in sorted(set(paths)):
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
    it makes is recorded (note_made for a Bash call, note_written for a Write or an Edit)."""
    reason = decide(call, g) or test_reason(call, g) or probe_reason(call, g)
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
    elif call.get("tool_name") in ("Write", "Edit"):
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
