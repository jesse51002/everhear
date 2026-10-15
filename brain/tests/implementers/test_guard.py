"""The guard: one table of calls and the verdict each gets, then gate() (every adapter's one call),
what a task made, as the guard records it and made_now checks it against the disk and the engine,
and the snapshot gate() gives each call it lets through."""
import json
import re

from brain.implementers import guard
from brain.implementers.guard import (DENY, VFG_FORMS, VFG_USAGE, GuardContext, Redirect, check_bash, decide,
                                      gate, made_by, made_now, split_shell)

from .fakes import FakeVfg, bad, ok

REPO = "/home/k/everhear"
SCOPE = DENY.read_scope("main")
TASK = "fire_staff_steve"


def call(tool, **inp):
    return {"tool_name": tool, "tool_input": inp}


def bash(command):
    return call("Bash", command=command)


def usage(key):
    return "Refused: send it as %s." % VFG_USAGE[key]


def not_ours(word):
    return "Refused: vfg %s is not an implementer's. These are: %s." % (word, VFG_FORMS)


TABLE = [
    # the server owner's commands and the server-wide settings, however they are dressed up
    (bash("vfg cmd 'op Steve'"), DENY.owner),
    (bash("vfg cmd 'execute at Steve run /kick Steve'"), DENY.owner),
    (bash("vfg cmd /OP Steve"), DENY.owner),
    (bash("vfg engine start meteor at=home && vfg cmd 'deop Steve'"), DENY.owner),
    (bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner),
    (bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner),
    # in a scenario's args: the library's command scenario, say
    (bash("""vfg engine start command 'commands=["time set day","kick Alex"]'"""), DENY.owner),
    (bash("vfg engine start command 'commands=time set day,execute as @a run ban Bob'"), DENY.owner),
    (bash("vfg engine bind Steve 'go away' --scenario command command='op Steve' --by %s" % TASK), DENY.owner),
    (bash("vfg cmd '/Difficulty peaceful'"), DENY.server_wide("difficulty")),
    (bash("vfg cmd 'execute as @a run worldborder set 100'"), DENY.server_wide("worldborder")),
    (bash("vfg engine start command command='setworldspawn 0 64 0'"), DENY.server_wide("setworldspawn")),
    (bash("vfg cmd 'say difficulty is hard, stop that'"), None),
    (bash("""vfg cmd 'tellraw @a {"text":"run stop"}'"""), None),
    (bash("vfg engine start sign text='stop here, ban the creepers' at=home"), None),
    (bash('vfg cmd "$(echo op) Steve"'), DENY.expansion),
    (bash("vfg cmd 'say costs $5'"), None),
    # vfg engine, in exactly the contract's forms
    (bash("vfg engine reload"), None),
    (bash("vfg engine reload fire_staff_steve fire_staff_steve_bolt"), None),
    (bash("vfg engine reload Fire-Staff"), usage("engine reload")),
    (bash("vfg engine classes"), None),
    (bash("vfg engine classes --json | head -n 40"), None),
    (bash("vfg engine classes fireball"), usage("engine classes")),
    (bash("vfg engine meta"), None),
    (bash("vfg engine meta fireball --json"), None),
    (bash("vfg engine meta fireball shrunk"), usage("engine meta")),
    (bash("vfg engine config get fireball"), None),
    (bash("vfg engine config get fireball --json"), None),
    (bash("vfg engine config set fireball radius=4"), DENY.config),
    (bash("vfg engine config undo fireball"), DENY.config),
    (bash("vfg engine config fireball"), usage("engine config")),
    (bash("vfg engine instances"), None),
    (bash("vfg engine instances --kind ability --user Steve --json"), None),
    (bash("vfg engine instances --kind=scenario"), None),
    (bash("vfg engine instances --kind rocket"), usage("engine instances")),
    (bash("vfg engine instances --user @a"), DENY.per_player),
    (bash("vfg engine log -n 40"), None),
    (bash("vfg engine log -n many"), usage("engine log")),
    (bash("vfg engine log --follow"), usage("engine log")),
    (bash("vfg engine places"), None),
    (bash("vfg engine places --json"), None),
    (bash("vfg engine incantations --player Steve"), None),
    (bash("vfg engine status --json"), None),
    (bash("vfg engine start meteor"), None),
    (bash("vfg engine start meteor at='197 68 251' count=3 --wait 10"), None),
    (bash("""vfg engine start castle at=home 'blocks=["stone","andesite"]'"""), None),
    (bash("vfg engine start meteor at=home extra"), usage("engine start")),
    (bash("vfg engine start meteor --wait soon"), usage("engine start")),
    (bash("vfg engine start"), usage("engine start")),
    (bash("vfg engine start meteor --json"), usage("engine start")),  # --json is for the reads
    (bash("vfg engine stop meteor"), None),
    (bash("vfg engine stop --instance scenario:meteor#4"), None),
    (bash("vfg engine stop meteor --instance scenario:meteor#4"), usage("engine stop")),
    (bash("vfg engine stop"), usage("engine stop")),
    (bash("vfg engine grant Steve fireball"), None),
    (bash("vfg engine revoke Steve fireball"), None),
    (bash("vfg engine grant @a fireball"), DENY.per_player),
    (bash("vfg engine grant Steve"), usage("engine grant")),
    (bash("vfg engine give Steve fire_staff && vfg engine take Alex fire_staff"), None),
    (bash("vfg engine give Steve fire staff"), usage("engine give")),
    (bash("vfg engine apply Alex frozen"), None),
    (bash("vfg engine apply Alex frozen strength=2 --seconds 10"), None),
    (bash("vfg engine apply Alex frozen --seconds=10"), None),
    (bash("vfg engine apply @r frozen"), DENY.per_player),
    (bash("vfg engine clear Alex frozen"), None),
    # a binding: one player by name, its phrase, an ability or a scenario with fixed args, and the task
    (bash("vfg engine bind Steve 'fire ball' fireball --by %s" % TASK), None),
    (bash("vfg engine bind Steve 'fire ball' fireball --by=%s" % TASK), None),
    (bash("vfg engine bind Steve 'rain fire' --scenario meteor at=caster count=5 --by %s" % TASK), None),
    (bash("vfg engine bind Steve 'rain fire' --by %s --scenario meteor" % TASK), None),
    (bash("vfg engine bind Steve 'rain fire' --scenario meteor --cooldown 10 --by %s" % TASK), None),
    (bash("vfg engine bind Steve 'rain fire' --scenario meteor --cooldown soon --by %s" % TASK), usage("engine bind")),
    (bash("vfg engine bind Steve 'fire ball' fireball --cooldown 3 --by %s" % TASK), usage("engine bind")),
    (bash("vfg engine bind Steve 'fire ball' fireball"), DENY.by(TASK)),
    (bash("vfg engine bind Steve 'fire ball' fireball --by castle_alex"), DENY.by(TASK)),
    (bash("vfg engine bind Steve 'fire ball' fireball --by T7"), DENY.by(TASK)),
    (bash("vfg engine bind @a 'fire ball' fireball --by %s" % TASK), DENY.per_player),
    (bash("vfg engine bind Steve 'fire ball' fireball count=3 --by %s" % TASK), usage("engine bind")),
    (bash("vfg engine bind Steve '' fireball --by %s" % TASK), usage("engine bind")),
    (bash("vfg engine bind Steve 'fire ball' --by %s" % TASK), usage("engine bind")),
    (bash("vfg engine unbind Steve 'fire ball'"), None),
    (bash("vfg engine unbind Steve"), usage("engine unbind")),
    # not an implementer's
    (bash("vfg engine spawn giant"), DENY.spawn),
    (bash("vfg engine migrate"), DENY.engine("migrate")),
    (bash("vfg engine overview"), DENY.engine("overview")),
    (bash("vfg engine"), DENY.engine(None)),
    (bash("vfg engine --json classes"), DENY.engine_flag),
    # a task's test, on the test server: vfg test <id>, with -n N and --timeout S
    (bash("vfg test fire_staff_steve"), None),
    (bash("vfg test fire_staff_steve -n 3 --timeout 90 2>&1 | tail -n 40"), None),
    (bash("vfg test fire_staff_steve --timeout=60"), None),
    (bash("vfg test"), usage("test")),
    (bash("vfg test fire_staff_steve --live"), usage("test")),
    (bash("vfg test fire_staff_steve --kind cast-projectile"), usage("test")),
    (bash("vfg test fire_staff_steve -n many"), usage("test")),
    (bash("vfg test fire_staff_steve -n 3 -n 4"), usage("test")),
    (bash("vfg test ../x"), usage("test")),
    (bash("vfg state --json"), None),
    (bash("vfg state Steve"), usage("state")),
    (bash("vfg history --source ai --since -10m"), None),
    (bash("vfg --timeout 30 cmd 'time set day'"),
     "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg engine ..., as it is."),
    (bash("""vfg cmd 'summon zombie ~ ~ ~ {CustomName:"x",Glowing:1b}'"""), None),
    # the old system is gone: a saved place is the engine's, the rest is not an implementer's
    (bash("vfg place save castle --at 197 68 251 --by castle_steve"), usage("place")),
    (bash("vfg script new hot_fireball --from fireball"), not_ours("script")),
    (bash("vfg action run castle at=home --task castle_steve"), not_ours("action")),
    (bash("vfg preset save a b c"), not_ours("preset")),
    (bash("vfg incantation list"), not_ours("incantation")),
    (bash("vfg say 'hi'"), not_ours("say")),
    (bash("vfg route --task torch_ring_steve"), not_ours("route")),
    (bash("vfg prefix"), not_ours("prefix")),
    # the tasks table is the brain's alone: an implementer neither writes nor reads it
    (bash("""vfg task set '{"name": "torch_ring_steve", "status": "done"}'"""), not_ours("task")),
    (bash("vfg task set - < made/main/x.json"), DENY.redirect),
    (bash("vfg task list"), not_ours("task")),
    # the shapes that run: joined, 2>&1 and /dev/null, piped into head, tail and grep
    (bash("sleep 5 && vfg engine log -n 20"), None),
    (bash("python3 -m py_compile made/main/content/fire_staff_steve.py && vfg engine reload fire_staff_steve"), None),
    (bash("vfg engine instances; echo ---; vfg state; ls %s/made/main/content/ 2>/dev/null" % REPO), None),
    (bash("""vfg history --events 2>&1 | grep -i -E "horde|king" | tail -20"""), None),
    (bash("vfg engine stop meteor &>/dev/null"), None),
    (bash("vfg state >&2"), None),
    (bash("vfg state |& grep Steve"), None),
    (bash("""vfg cmd 'tellraw @a {"text":"a > b"}'"""), None),
    # and those that do not, each pointing at what to send
    (bash("curl https://example.com"), DENY.shape),
    (bash("""vfg cmd 'time set day'; echo "EXIT=$?\""""), DENY.variables),
    (bash("echo `id`"), DENY.variables),
    (bash("for y in 60 62; do vfg cmd \"execute if block 6 $y 21 air\"; done"), DENY.loop),
    (bash("if vfg state; then echo ok; fi"), DENY.loop),
    (bash("vfg engine start meteor &"), DENY.group),
    (bash("(vfg state; vfg engine instances)"), DENY.group),
    (bash("{ vfg state; }"), DENY.group),
    (bash("FOO=1 vfg state"), DENY.env),
    (bash("PYTHONPATH=engine python3 -I -c 'import vfg_engine' 2>&1"), DENY.python),
    (bash("FOO=1"), DENY.shape),
    (bash("vfg state | head -n 20 /etc/passwd"), DENY.pipe),
    (bash("vfg state | sort"), DENY.pipe),
    (bash("vfg state > out.txt"), DENY.redirect),
    (bash("vfg cmd 'list' < /etc/passwd"), DENY.redirect),
    (bash("cat <<EOF\nop Steve\nEOF"), DENY.redirect),
    (bash("vfg state 2>&3"), DENY.redirect),
    (bash("vfg state >"), DENY.redirect),
    (bash("cd /x/repo/engine && ls"), DENY.cd),
    (bash("mkdir -p made/main/content && vfg state 2>&1 | head -40"), DENY.files("main")),
    (bash("awk 'NR<20' engine/WRITING.md"), DENY.read),
    (bash("find . -name '*.py'"), DENY.find("main")),
    (bash("cp engine/library/fireball.py made/main/content/fire_staff_steve.py"), DENY.files("main")),
    (bash("ps -eo pid,args | grep -i '[v]fg_engine'"), DENY.ps),
    (bash("python3 made/main/content/fire_staff_steve.py"), DENY.python),
    (call("Bash", command="sleep 30", run_in_background=True), DENY.background),
    # ls, cat, sed -n, grep, head, tail and py_compile: in engine/ and made/<server>/ only
    (bash("ls -la made/main/content"), None),
    (bash("ls engine engine/library made/main"), None),
    (bash("ls"), None),  # the repository's folders by name, nothing in them
    (bash("ls -R"), SCOPE),
    (bash("ls -laR"), SCOPE),
    (bash("ls made"), SCOPE),
    (bash("ls made/other/content"), SCOPE),
    (bash("ls scripts"), SCOPE),
    (bash("ls actions"), SCOPE),
    (bash("ls integration/replay"), SCOPE),
    (bash("ls /etc"), SCOPE),
    (bash("ls made/../../"), SCOPE),
    (bash("ls ~/.ssh"), SCOPE),
    (bash("grep -rn explode engine/library"), None),
    (bash("grep -r crater"), SCOPE),
    (bash("grep -rn hot_fireball integration/replay/"), SCOPE),
    (bash("grep -rn fire engine made/main"), None),
    (bash("grep -A3 -n -e root -e explode made/main/content/x.py"), None),
    (bash("grep crater"), DENY.grep_file),
    (bash("grep TYPESAFE ~/.vfg/config"), SCOPE),
    (bash("head -n 40 brain/brain/implementers/guard.py"), SCOPE),
    (bash("cat engine/WRITING.md engine/KIT.md made/main/content/x.py"), None),
    (bash("cat -n engine/vfg_engine/ctx.py | head -n 80"), None),
    (bash("cat docs/CONTRACTS.md"), SCOPE),
    (bash("cat toolkit/README.md"), SCOPE),
    (bash("cat"), DENY.cat),
    (bash("vfg state | cat"), DENY.pipe),
    (bash("sed -n '1,80p' engine/library/fireball.py"), None),
    (bash("sed -n 120p made/main/content/x.py && sed -n '40,$p' engine/examples/fireball.py"), None),
    (bash("sed -n 1,20p /etc/passwd"), SCOPE),
    (bash("sed -i s/a/b/ made/main/content/x.py"), DENY.sed),
    (bash("sed -n '/def/p' engine/library/fireball.py"), DENY.sed),
    (bash("sed -n 1,5p"), DENY.sed),
    (bash("sed -n 1,5p -e x engine/library/fireball.py"), DENY.sed),
    (bash("grep -f /etc/passwd engine/WRITING.md"), DENY.pattern_file),
    (bash("tail -20 engine/WRITING.md"), None),
    (bash("tail -f made/main/content/x.py"), DENY.tail_follow),
    (bash("vfg engine log -n 5 | tail -F"), DENY.tail_follow),
    (bash("python3 -m py_compile ~/.vfg/config"), SCOPE),
    (bash("python3 -c 'import os'"), DENY.python),
    # what bash expands and the guard does not follow: ~name, braces, globs outside quotes
    (bash("head ~jm/.ssh/id_ed25519"), DENY.wild),
    (bash("ls ~+/.."), DENY.wild),
    (bash("grep -r . {/var/home/jm/.vfg,x}"), DENY.wild),
    (bash("ls /{u..w}ar"), DENY.wild),
    (bash("echo made/main/content/*"), DENY.wild),
    (bash("ls /?tc /[e]tc"), SCOPE),  # a glob in a read: expanded, and /etc is outside
    (bash("FOO=1 ls ~jm"), DENY.wild),
    (bash("grep -n 'def .*' engine/WRITING.md"), None),
    (bash('grep -n "a*b{c,d}" made/main/content/x.py'), None),
    (bash("head -n 5 made/main/content/x\\*"), None),
    # files: a class and its test written in this server's folder, read and searched in engine/ and made/<server>/
    (call("Write", file_path=REPO + "/made/main/content/fire_staff_steve.py"), None),
    (call("Edit", file_path="made/main/content/fire_staff_steve.py"), None),
    (call("Write", file_path="made/main/tests/fire_staff_steve.py"), None),
    (call("Edit", file_path=REPO + "/made/main/tests/fire_staff_steve.py"), None),
    (call("Write", file_path="made/main/content/FireStaff.py"), DENY.content_name("main")),
    (call("Write", file_path="made/main/content/_helpers.py"), DENY.content_name("main")),
    (call("Write", file_path="made/main/content/sub/deep.py"), DENY.content_name("main")),
    (call("Write", file_path="made/main/content/notes.md"), DENY.content_name("main")),
    (call("Write", file_path="made/main/tests/x.json"), DENY.content_name("main")),
    (call("Write", file_path=REPO + "/engine/library/fireball.py"), DENY.made_only("main")),
    (call("Edit", file_path="engine/vfg_engine/ctx.py"), DENY.made_only("main")),
    (call("Write", file_path=REPO + "/made/main/../../engine/library/x.py"), DENY.made_only("main")),
    (call("Write", file_path="made/main/notes/x/main.py"), DENY.made_only("main")),
    (call("Write", file_path="made/main/actions/x.py"), DENY.made_only("main")),
    (call("Write", file_path=REPO + "/made/other/content/x.py"), DENY.made_only("main")),
    (call("Write", file_path=REPO + "/made/x.py"), DENY.made_only("main")),
    (call("Write", file_path="/tmp/x.py"), DENY.made_only("main")),
    (call("Glob", pattern="*", path="~/.ssh"), SCOPE),
    (call("Grep", pattern=".", path=REPO + "/../.."), SCOPE),
    (call("Grep", pattern="explode", path=REPO + "/engine"), None),
    (call("Grep", pattern="explode", path="made/main/content/"), None),
    (call("Grep", pattern="hot_fireball"), SCOPE),  # no path: the whole repository
    (call("Grep", pattern="hot_fireball", path="integration/replay"), SCOPE),
    (call("Glob", pattern="*.py", path="engine/library"), None),
    (call("Glob", pattern="engine/library/*.py"), SCOPE),
    (call("Glob", pattern="../../**/*.py", path="engine"), SCOPE),
    (call("Glob", pattern="/etc/*", path="engine"), SCOPE),
    (call("Read", file_path="engine/WRITING.md"), None),
    (call("Read", file_path=REPO + "/engine/library/fireball.py"), None),
    (call("Read", file_path="made/main/content/x.py"), None),
    (call("Read", file_path="made/main/tests/fire_staff_steve.py"), None),  # a task's test
    (call("Read", file_path="toolkit/README.md"), SCOPE),
    (call("Read", file_path="actions/_types.py"), SCOPE),
    (call("Read", file_path="integration/replay/tasks.jsonl"), SCOPE),
    (call("Read", file_path="made/other/content/x.py"), SCOPE),
    (call("Read", file_path="~/.vfg/config"), SCOPE),
    (call("Read", file_path="/etc/passwd"), SCOPE),
]


def test_the_guard():
    assert {"op", "stop", "ban", "whitelist"} <= guard.refused()  # the engine's list, engine/vfg_engine/mc/commands.py
    g = GuardContext(repo=REPO, task=TASK)
    wrong = [(c["tool_input"], want, got) for c, want in TABLE if (got := decide(c, g)) != want]
    assert wrong == []
    assert not [want for _, want in TABLE if want and "\n" in want]  # each refusal is one line


def test_the_list_of_allowed_forms_names_every_op_and_flag_the_guard_lets_through():
    from brain.implementers.guard import ENGINE_FORMS
    for op, form in ENGINE_FORMS.items():
        assert re.search(r"vfg engine (\S+\|)*%s\b" % op, VFG_FORMS), op
        for flag in form.flags:
            assert flag in VFG_FORMS, (op, flag)
    for word in ("vfg cmd", "vfg test <id> [-n N] [--timeout S]", "vfg state [--json]", "vfg history"):
        assert word in VFG_FORMS


def test_a_binding_with_no_task_known_takes_any_tasks_name():
    assert check_bash("vfg engine bind Steve 'fire ball' fireball --by castle_alex") is None
    assert check_bash("vfg engine bind Steve 'fire ball' fireball") == DENY.by(None)
    assert "--by <your task>." in DENY.by(None)


def test_split_shell_keeps_words_redirections_pipes_and_what_bash_would_expand():
    assert [c.words for c in split_shell('a \'b c\' "d \\"e\\"" f\\ g; h | i && j')] == [
        ["a", "b c", 'd "e"', "f g"], ["h"], ["i"], ["j"]]
    a, b = split_shell("vfg history --events 2>&1 | grep -i 'a|b'")
    assert a.redirects == [Redirect("2", ">&", "1")] and not a.piped and b.words == ["grep", "-i", "a|b"] and b.piped
    [e] = split_shell("echo '2'>x")  # a quoted number is a word, not an fd
    assert e.words == ["echo", "2"] and e.redirects == [Redirect("", ">", "x")] and not e.redirects[0].harmless()
    [w] = split_shell("ls 'a*' b\\* c* ~ ~/x ~jm {a,b} '{a,b}' a=~ \"~jm\"")
    assert w.wild == [False, False, False, True, False, False, True, True, False, True, False]
    assert check_bash("vfg cmd 'list' && vfg cmd 'data get entity Steve Pos'") is None


async def test_gate_gives_the_reason_to_refuse_or_none():
    g = GuardContext(repo=REPO, task=TASK)
    assert await gate(bash("curl x"), g) == DENY.shape
    assert await gate(bash("vfg state"), g) is None
    assert await gate(call("Read", file_path="/etc/passwd"), g) == SCOPE


class Noting:
    """A snapshot: the calls gate() gave it, before they ran."""

    def __init__(self, breaks=False):
        self.seen = []
        self.breaks = breaks

    async def before(self, c):
        self.seen.append(c["tool_input"])
        if self.breaks:
            raise OSError("disk full")


async def test_gate_gives_each_call_it_lets_through_to_the_snapshot_before_it_records_it(tmp_path):
    g = GuardContext(repo=str(tmp_path), task=TASK, snapshot=Noting())
    assert await gate(bash("vfg engine grant Steve fireball"), g) is None
    assert await gate(bash("curl x"), g) == DENY.shape  # refused: not noted
    assert g.snapshot.seen == [{"command": "vfg engine grant Steve fireball"}]
    assert g.made["grants"] == ["Steve:fireball"]
    g.snapshot = Noting(breaks=True)  # a snapshot that breaks never blocks the call
    assert await gate(bash("vfg state"), g) is None


# ---------------------------------------------------------------- what a task made

MADE_BY = [
    ("vfg engine bind Steve 'fire ball' fireball --by fire_staff_steve", [("bindings", "steve.fire_ball")]),
    ("vfg engine bind Steve 'Fire-Ball!' fireball --by fire_staff_steve", [("bindings", "steve.fire_ball")]),
    ("vfg engine bind Alex \"rain o' fire\" --scenario meteor at=caster --by fire_staff_steve",
     [("bindings", "alex.rain_o_fire")]),
    ("vfg engine bind Alex 'café' fireball --by fire_staff_steve", [("bindings", "alex.cafe")]),
    ("vfg engine grant Steve fireball", [("grants", "Steve:fireball")]),
    # nothing else makes anything of the record's kinds
    ("vfg engine revoke Steve fireball", []),
    ("vfg engine unbind Steve 'fire ball'", []),
    ("vfg engine give Steve fire_staff", []),
    ("vfg engine start meteor at=home", []),
    ("vfg engine apply Alex frozen", []),
    ("vfg engine reload fire_staff_steve", []),
    ("vfg engine grant @a fireball", []),
    ("vfg engine bind Steve fireball", []),
    ("vfg state", []),
    ("echo vfg engine grant Steve fireball", []),
]


def test_made_by_reads_what_one_command_makes():
    wrong = [(cmd, want, got) for cmd, want in MADE_BY if (got := made_by(split_shell(cmd)[0].words)) != want]
    assert wrong == []


async def test_gate_records_what_the_calls_it_lets_through_make_in_order_and_once(tmp_path):
    g = GuardContext(repo=str(tmp_path), task=TASK)

    async def run(tool, **inp):
        return await gate({"tool_name": tool, "tool_input": inp}, g)

    assert await run("Write", file_path="made/main/content/fire_staff_steve.py", content="x") is None
    assert await run("Edit", file_path=str(tmp_path / "made/main/content/fire_staff_steve.py")) is None  # once
    assert await run("Edit", file_path="made/main/content/castle_alex.py") is None  # another's, changed: recorded
    assert await run("Write", file_path="made/main/tests/fire_staff_steve.py", content="x") is None  # a test: not
    assert await run("Bash", command="vfg engine reload fire_staff_steve && vfg engine grant Steve fire_staff_steve && "
                     "vfg engine bind Steve 'fire ball' fire_staff_steve --by fire_staff_steve") is None
    assert await run("Bash", command="vfg engine grant Steve fire_staff_steve; vfg engine grant Alex fire_staff_steve "
                     "2>&1 | tail -n 3") is None
    # a call the guard denies records nothing, even the commands in it that would run alone
    assert await run("Bash", command="vfg engine grant Bob fireball && curl x") is not None
    assert await run("Bash", command="vfg engine bind Bob 'x' fireball") is not None  # no --by
    assert await run("Write", file_path="engine/library/fireball.py", content="x") is not None
    assert g.made == {"content": ["fire_staff_steve", "castle_alex"], "bindings": ["steve.fire_ball"],
                      "grants": ["Steve:fire_staff_steve", "Alex:fire_staff_steve"]}


async def test_made_now_keeps_the_classes_on_disk_and_what_the_engine_has(tmp_path):
    g = GuardContext(repo=str(tmp_path), server="named", task=TASK)
    g.made = {"content": ["fire_staff_steve", "never_written"], "bindings": ["steve.fire_ball", "steve.gone"],
              "grants": ["Steve:fireball", "Alex:fireball"]}
    for rel in ("made/named/content/fire_staff_steve.py", "made/main/content/never_written.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    vfg = FakeVfg()
    vfg.on("engine", "incantations", answer=ok(json.dumps({"ok": True, "incantations": [
        {"id": "steve.fire_ball", "player": "Steve", "phrase": "fire ball", "ability": "fireball"}]})))
    vfg.on("engine", "instances", answer=ok(json.dumps({"ok": True, "instances": [
        {"instance": "ability:fireball#3", "kind": "ability", "id": "fireball", "user": "steve"}]})))
    now = await made_now(g, vfg)
    assert now == {"content": ["fire_staff_steve"], "bindings": ["steve.fire_ball"], "grants": ["Steve:fireball"]}
    assert vfg.calls == [["engine", "incantations", "--json"], ["engine", "instances", "--kind", "ability", "--json"]]
    now["grants"].append("x")  # a copy: the record is not changed through it
    assert g.made["grants"] == ["Steve:fireball", "Alex:fireball"]
    # the engine down: the bindings and grants as recorded; no vfg: the same, with no read
    down = FakeVfg().on("engine", answer=bad("vfg: the engine is not running", 6))
    assert (await made_now(g, down))["bindings"] == ["steve.fire_ball", "steve.gone"]
    assert (await made_now(g))["grants"] == ["Steve:fireball", "Alex:fireball"]
    # nothing of a kind recorded: no read of it
    empty = FakeVfg()
    assert await made_now(GuardContext(repo=str(tmp_path)), empty) == {"content": [], "bindings": [], "grants": []}
    assert empty.calls == []


def test_no_web_on_any_harness():
    g = GuardContext(repo=REPO)
    assert decide(call("WebFetch", url="https://example.com"), g) == DENY.web
    assert decide(call("WebSearch", query="minecraft fireball"), g) == DENY.web
    assert decide(call("TodoWrite", todos=[]), g) is None


def test_a_class_file_never_takes_a_library_classs_id(tmp_path):
    """A made class of a library class's id runs in the library's place, for everyone: refused."""
    (tmp_path / "engine" / "library").mkdir(parents=True)
    (tmp_path / "engine" / "library" / "fireball.py").write_text("class Fireball: ...\n")
    g = GuardContext(repo=str(tmp_path), task=TASK)
    for tool in ("Write", "Edit"):
        assert decide(call(tool, file_path="made/main/content/fireball.py"), g) == DENY.library_name("fireball")
        assert decide(call(tool, file_path=str(tmp_path / "made/main/content/fireball_steve.py")), g) is None
    assert decide(call("Write", file_path="made/main/tests/fireball.py"), g) is None     # a test may take any id
    assert "engine/library/fireball.py" in DENY.library_name("fireball")


LIBRARY_FIREBALL = "from vfg_engine import Ability\n\n\nclass Fireball(Ability):\n    pass\n"


def test_a_class_never_takes_a_library_id_by_its_class_name_whatever_its_files_name(tmp_path):
    """my_ball.py holding `class Fireball(Ability)` is fireball: it would shadow the library's for everyone."""
    lib = tmp_path / "engine" / "library"
    lib.mkdir(parents=True)
    (lib / "fireball.py").write_text(LIBRARY_FIREBALL)
    (lib / "gravity.py").write_text("from vfg_engine import Ability\n\n\nclass Lift(Ability):\n    id = 'up_lift'\n")
    g = GuardContext(repo=str(tmp_path), task=TASK)
    ball = "made/main/content/my_ball.py"
    for content, refused in (("from vfg_engine import Ability\n\n\nclass Fireball(Ability):\n    pass\n", "fireball"),
                             ("import vfg_engine\n\n\nclass Fireball(vfg_engine.Ability):\n    pass\n", "fireball"),
                             ("from vfg_engine import Ability\n\n\nclass MyBall(Ability):\n    id = 'fireball'\n",
                              "fireball"),
                             ("from vfg_engine import Item\n\n\nclass UpLift(Item):\n    pass\n", "up_lift"),
                             ("from vfg_engine import Ability\n\n\nclass MyBall(Ability):\n    pass\n", None),
                             ("class Fireball:\n    pass\n", None),             # no engine kind: no class of the engine
                             ("class Fireball(Ability:\n", None)):              # no parse: the engine loads nothing
        got = decide(call("Write", file_path=ball, content=content), g)
        assert got == (DENY.library_name(refused, "gravity" if refused == "up_lift" else "fireball") if refused
                       else None), content
    assert "engine/library/gravity.py" in DENY.library_name("up_lift", "gravity")
    # an Edit: the file it would leave is what counts
    (tmp_path / "made" / "main" / "content").mkdir(parents=True)
    (tmp_path / ball).write_text("from vfg_engine import Ability\n\n\nclass MyBall(Ability):\n    pass\n")
    rename = call("Edit", file_path=ball, old_string="class MyBall(", new_string="class Fireball(")
    assert decide(rename, g) == DENY.library_name("fireball", "fireball")
    tweak = call("Edit", file_path=str(tmp_path / ball), old_string="pass", new_string="every = 0.5")
    assert decide(tweak, g) is None
    assert decide(call("Edit", file_path=ball, old_string="not there", new_string="class Fireball("), g) is None
    both = call("Edit", file_path=ball, old_string="MyBall", new_string="Fireball", replace_all=True)
    assert decide(both, g) == DENY.library_name("fireball", "fireball")
    assert decide(call("Write", file_path="made/main/tests/my_ball.py", content=LIBRARY_FIREBALL), g) is None  # a test


def test_a_glob_in_a_read_is_allowed_when_every_match_stays_readable(tmp_path):
    repo = str(tmp_path)
    for rel in ("engine/library/fireball.py", "engine/library/README.md", "made/main/content/x.py",
                "integration/replay/t.py", "lib/py/vfg_llm.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    for command, want in [
        ("cat engine/library/*.py", None),
        ("ls made/main/*/", None),
        ("grep -n fire engine/library/*.py made/main/content/*.py", None),
        ("head -n 5 engine/library/nothing_*.py", None),  # no match: bash leaves the word, which is readable
        ("python3 -m py_compile made/main/content/*.py", None),
        ("sed -n 1,5p engine/library/[fR]*", None),
        ("grep -n 'def .*' engine/library/fireball.py", None),  # quoted: no glob
        ("cat */replay/*.py", SCOPE),
        ("ls *", SCOPE),  # the repository's top: integration/, lib/
        ("grep -n x engine/../lib/*/*.py", SCOPE),
        ("head -n 3 /etc/pass*", SCOPE),
        ("cat {engine,lib}/x", DENY.wild),  # a brace: still refused
        ("echo engine/*", DENY.wild),  # globs in a read only
    ]:
        assert check_bash(command, repo, "main") == want, command


async def test_a_test_rerun_with_nothing_changed_is_refused_and_four_runs_is_the_most(tmp_path):
    repo = str(tmp_path)
    g = GuardContext(repo=repo, task="speed_dash_steve")
    g.made["content"] = ["speed_dash_steve_push"]

    def put(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    async def test_run(command="vfg test speed_dash_steve"):
        return await gate(bash(command), g)

    put("made/main/tests/speed_dash_steve.py", "t1")
    put("made/main/content/speed_dash_steve.py", "s1")
    assert await test_run() is None
    assert await test_run("vfg test speed_dash_steve -n 2 2>&1 | tail -n 30") == DENY.test_again  # nothing changed
    assert await test_run("vfg test speed_dash_steve -n 60") == DENY.test_again  # more lines: with the next change
    assert g.tests_run == 1  # a refused call counts nothing
    put("made/main/content/speed_dash_steve.py", "s2")  # the ability
    assert await test_run() is None
    put("made/main/content/speed_dash_steve_push.py", "p1")  # another class the task made
    assert await test_run() is None
    put("made/main/tests/speed_dash_steve.py", "t2")  # the test
    assert await test_run() is None
    put("made/main/content/speed_dash_steve.py", "s3")
    assert await test_run() == DENY.test_cap  # a fifth: report what still fails
    assert await test_run("vfg test speed_dash_steve && curl x") == DENY.shape  # the guard's own refusal first
    g.new_run()  # the next run (a follow-up) has its own four
    assert await test_run() is None
    assert await gate(bash("vfg engine log -n 40"), g) is None
    assert "-n 60" in DENY.test_again  # the test's log is the test server's
    # a rerun after vfg test could not run (exit 3, 4 or 5) is refused too: the refusal says to report it untested
    assert "exited 3, 4 or 5" in DENY.test_again and "never test on the live server" in DENY.test_again


async def test_once_the_test_runs_are_spent_a_hand_probe_of_the_live_world_is_refused(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="high_step_steve")
    looks = ["vfg cmd 'data get entity Steve Pos'", "sleep 2 && vfg cmd 'execute if entity @e[tag=vfg_x]'",
             "vfg cmd 'list'", "vfg cmd 'scoreboard players get Steve vfg_click'",
             "vfg cmd 'execute as Steve at @s run data get entity @s Motion'", "vfg cmd '/data get block 0 84 0'"]
    acts = ["vfg cmd 'effect give Steve slow_falling 5 0 true'", "vfg cmd 'execute as Steve at @s run summon zombie ~ ~ ~'"]
    for c in looks + acts:  # with test runs left: all of them run (the rules keep the live world out of testing)
        assert await gate(bash(c), g) is None, c
    g.tests_run = 4
    for c in looks:
        assert await gate(bash(c), g) == DENY.tests_spent, c
    for c in acts:  # a command that acts is a build step
        assert await gate(bash(c), g) is None, c
    assert await gate(bash("vfg engine stop high_step"), g) is None
    g.new_run()
    assert await gate(bash(looks[0]), g) is None
    assert guard.probe("execute unless block 0 84 0 air") and not guard.probe("execute if block 0 84 0 air run say hi")


async def test_with_a_test_server_a_run_takes_three_looks_at_the_live_world(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="high_step_steve", test_server=True)
    assert await gate(bash("vfg cmd 'data get entity Steve Pos'"), g) is None
    assert await gate(bash("vfg cmd 'list' && sleep 1 && vfg cmd 'execute if entity @e[tag=vfg_x]'"), g) is None  # two
    assert g.probes_run == 3
    assert await gate(bash("sleep 2 && vfg cmd 'scoreboard players get Steve vfg_click'"), g) == DENY.probes_spent
    assert await gate(bash("vfg cmd 'effect give Steve slow_falling 5 0 true'"), g) is None  # a build step
    assert g.probes_run == 3  # a refused call counts nothing
    g.new_run()
    assert await gate(bash("vfg cmd 'list'"), g) is None
    free = GuardContext(repo=str(tmp_path), task="high_step_steve")  # no test server: a look may be all the check
    for _ in range(5):
        assert await gate(bash("vfg cmd 'data get entity Steve Pos'"), free) is None


async def test_a_comment_or_a_docstring_alone_is_no_change_for_a_rerun(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="t1")

    def put(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    put("made/main/tests/t1.py", 'def test(t):\n    t.check("a", t.moved(4))\n')
    put("made/main/content/t1.py", '"""A dash."""\nclass T1(Ability):\n    pass\n')
    assert await gate(bash("vfg test t1"), g) is None
    put("made/main/tests/t1.py", '# try again\ndef test(t):   \n\n    t.check("a", t.moved(4))  # bar\n')
    put("made/main/content/t1.py", '"""A dash, forward."""\n\nclass T1(Ability):\n    pass  # go\n')
    assert await gate(bash("vfg test t1"), g) == DENY.test_again  # comments, blank lines, a docstring: unchanged
    put("made/main/tests/t1.py", 'def test(t):\n    t.check("a", t.moved(2))\n')
    assert await gate(bash("vfg test t1"), g) is None  # a real edit (the bar)
    put("made/main/content/t1.py", "class T1(\n")  # does not parse: judged as it is
    assert await gate(bash("vfg test t1"), g) is None
    assert guard.code_print(b"x = 1  # a\n", "a.py") == guard.code_print(b"\n\nx=1\n", "b.py")
    assert guard.code_print(b"# a\n", "notes.txt") != guard.code_print(b"# b\n", "notes.txt")
