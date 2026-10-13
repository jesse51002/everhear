"""The guard: one table of calls and the verdict each gets, then gate() (every adapter's one call),
the rule on a script another task's running copy uses, what a task made, as the guard records it,
and the snapshot gate() gives each call it lets through."""
import os

from brain.implementers import guard
from brain.implementers.guard import (DENY, VFG_FORMS, VFG_USAGE, GuardContext, Redirect, check_bash, decide,
                                      gate, made_by, made_now, split_shell)

from .fakes import FakeVfg, bad, ok

REPO = "/home/k/everhear"
SCOPE = DENY.read_scope("main")


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
    (bash("vfg action run tnt_rain --task torch_ring_steve && vfg cmd 'deop Steve'"), DENY.owner),
    (bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner),
    (bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner),
    (bash("""vfg action run command 'commands=["time set day","kick Alex"]' --task torch_ring_steve"""), DENY.owner),
    (bash("vfg action run command 'commands=time set day,execute as @a run ban Bob' --task torch_ring_steve"), DENY.owner),
    (bash("vfg script set crouch_rule on_command='op Steve'"), DENY.owner),
    (bash("vfg cmd '/Difficulty peaceful'"), DENY.server_wide("difficulty")),
    (bash("vfg cmd 'execute as @a run worldborder set 100'"), DENY.server_wide("worldborder")),
    (bash("vfg action run command commands='setworldspawn 0 64 0' --task torch_ring_steve"), DENY.server_wide("setworldspawn")),
    (bash("vfg cmd 'say difficulty is hard, stop that'"), None),
    (bash("""vfg cmd 'tellraw @a {"text":"run stop"}'"""), None),
    (bash("vfg action run sign text='stop here, ban the creepers' --task torch_ring_steve"), None),
    (bash('vfg cmd "$(echo op) Steve"'), DENY.expansion),
    (bash("vfg cmd 'say costs $5'"), None),
    # vfg's forms
    (bash("vfg action run spirit_bomb player=Steve as=bomb_steve --task torch_ring_steve"), None),
    (bash("vfg action run castle at='197 68 251' --task=castle_steve"), None),
    (bash("vfg action run castle at=home"), DENY.action_task),
    # a task is given by its name: never a T id, a number or one letter
    (bash("vfg action run castle at=home --task T7"), DENY.action_task),
    (bash("vfg action run castle at=home --task=T7"), DENY.action_task),
    (bash("vfg action run castle at=home --task 7gun"), DENY.action_task),
    (bash("vfg action run castle at=home --task a"), DENY.action_task),
    (bash("vfg action run castle at=home --task Castle_Steve"), DENY.action_task),
    (bash("vfg action run"), usage("action run")),
    (bash("vfg action list --json | head -n 50"), None),
    (bash("vfg action list railgun"), usage("action list")),
    (bash("vfg action check a b"), usage("action check")),
    # any action in full, the brief having at most seven: vfg action show <name>, alone or joined
    (bash("vfg action show nuke"), None),
    (bash("vfg action show incantation_create && vfg action show incantation_edit"), None),
    (bash("vfg action show nuke | head -n 20"), None),
    (bash("vfg action show"), usage("action show")),
    (bash("vfg action show nuke railgun"), usage("action show")),
    (bash("vfg action refs"), usage("action")),
    # a task's test, on the test server: vfg test <name>, with -n N and --timeout S
    (bash("vfg test hot_fireball_steve"), None),
    (bash("vfg test hot_fireball_steve -n 3 --timeout 90 2>&1 | tail -n 40"), None),
    (bash("vfg test hot_fireball_steve --timeout=60"), None),
    (bash("vfg test"), usage("test")),
    (bash("vfg test hot_fireball_steve --live"), usage("test")),
    (bash("vfg test hot_fireball_steve -n many"), usage("test")),
    (bash("vfg test hot_fireball_steve -n 3 -n 4"), usage("test")),
    (bash("vfg test ../x"), usage("test")),
    # the test templates' kinds, on vfg script new and vfg test
    (bash("vfg script new hot_fireball_steve --from fireball --kind cast-projectile"), None),
    (bash("vfg script new --kind=cast-launch high_step_steve --from launch_pad"), None),
    (bash("vfg test speed_dash_steve --kind cast-launch -n 40"), None),
    (bash("vfg test speed_dash_steve --kind=world-event"), None),
    (bash("vfg script new x --kind rocket"), usage("script new")),
    (bash("vfg script new x --kind"), usage("script new")),
    (bash("vfg test x --kind=rocket"), usage("test")),
    (bash("vfg test x --kind build --kind area-blast"), usage("test")),
    (bash("vfg script run x 2>&1 | head -n 5"), DENY.script_run),
    (bash("vfg script new galick_ice --from galick_gun --preset replace_blocks"), None),
    (bash("vfg script new storm --library --preset lightning"), DENY.library_flag),
    (bash("vfg script new"), usage("script new")),
    (bash("vfg script set galick-alex"), usage("script set")),
    (bash("vfg script save galick-alex galick_no_cooldown"), None),
    (bash("vfg script check"), None),
    (bash("vfg script deploy torch_ring"), None),
    (bash("vfg script list --json --fresh"), None),
    (bash("vfg script list --all"), usage("script list")),
    (bash("vfg script logs tnt_rain --follow"), DENY.follow),
    (bash("vfg script frobnicate x"),
     "Refused: vfg script takes new|set|stop|logs|list|check|save|deploy; a script runs through its action."),
    (bash("vfg preset save galick_ice ice_path ice_trail"), None),
    (bash("vfg preset save galick_ice ice_path"), usage("preset")),
    (bash("vfg state --json"), None),
    (bash("vfg state Steve"), usage("state")),
    (bash("vfg place save castle --at 197 68 251 --by castle_steve"), None),
    (bash("vfg place list --json"), None),
    (bash("vfg place forget castle"), usage("place")),
    (bash("vfg history --source ai --since -10m"), None),
    (bash("vfg --timeout 30 cmd 'time set day'"),
     "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg action run ..., as it is."),
    # key={...,...}, a JSON object of two keys or more in one word, which Claude Code's own check
    # refuses: values.<name>= words instead; an object as a word of its own, or NBT in vfg cmd, passes
    (bash("""vfg action run x values='{"a":1,"b":2}'"""), DENY.json_object),
    (bash("""vfg action run incantation_edit player=Steve phrase='fire ball' values={"a":1,"b":2} --task torch_ring_steve"""),
     DENY.json_object),
    (bash("vfg action run x values.a=1 values.b=2 --task torch_ring_steve"), None),
    (bash("""vfg action run x values='{"a":1}' --task torch_ring_steve"""), None),
    (bash("""vfg cmd 'summon zombie ~ ~ ~ {CustomName:"x",Glowing:1b}'"""), None),
    (bash("vfg say 'hi'"), not_ours("say")),
    (bash("vfg route --task torch_ring_steve"), not_ours("route")),
    # the tasks table is the brain's alone: an implementer neither writes nor reads it
    (bash("""vfg task set '{"name": "torch_ring_steve", "status": "done"}'"""), not_ours("task")),
    (bash("vfg task set - < made/main/x.json"), DENY.redirect),
    (bash("vfg task list"), not_ours("task")),
    (bash("vfg task list --open --json | head -n 5"), not_ours("task")),
    # the shapes that run: joined, 2>&1 and /dev/null, piped into head, tail and grep
    (bash("sleep 5 && vfg script logs tnt_rain -n 20"), None),
    (bash("python3 -m py_compile made/main/scripts/torch_ring/main.py && vfg action run torch_ring --task torch_ring_steve"), None),
    (bash("vfg script list; echo ---; vfg state; ls %s/made/main/scripts/ 2>/dev/null" % REPO), None),
    (bash("""vfg history --events 2>&1 | grep -i -E "horde|king" | tail -20"""), None),
    (bash("vfg script stop x &>/dev/null"), None),
    (bash("vfg state >&2"), None),
    (bash("vfg state |& grep Steve"), None),
    (bash("""vfg cmd 'tellraw @a {"text":"a > b"}'"""), None),
    # and those that do not, each pointing at what to send
    (bash("curl https://example.com"), DENY.shape),
    (bash("""vfg cmd 'time set day'; echo "EXIT=$?\""""), DENY.variables),
    (bash("echo `id`"), DENY.variables),
    (bash("for y in 60 62; do vfg cmd \"execute if block 6 $y 21 air\"; done"), DENY.loop),
    (bash("if vfg state; then echo ok; fi"), DENY.loop),
    (bash("vfg action run x --task torch_ring_steve &"), DENY.group),
    (bash("(vfg state; vfg script list)"), DENY.group),
    (bash("{ vfg state; }"), DENY.group),
    (bash("FOO=1 vfg state"), DENY.env),
    (bash("PYTHONPATH=/opt/vfg/scripts/_lib python3 -I -c 'import mc' 2>&1"), DENY.python),
    (bash("FOO=1"), DENY.shape),
    (bash("vfg state | head -n 20 /etc/passwd"), DENY.pipe),
    (bash("vfg state | sort"), DENY.pipe),
    (bash("vfg state > out.txt"), DENY.redirect),
    (bash("vfg cmd 'list' < /etc/passwd"), DENY.redirect),
    (bash("cat <<EOF\nop Steve\nEOF"), DENY.redirect),
    (bash("vfg state 2>&3"), DENY.redirect),
    (bash("vfg state >"), DENY.redirect),
    (bash("cd /x/repo/scripts && ls"), DENY.cd),
    (bash("mkdir -p made/main/scripts/staff && vfg state 2>&1 | head -40"), DENY.mkdir("main")),
    (bash("awk 'NR<20' scripts/README.md"), DENY.read),
    (bash("find . -name main.py"), DENY.find("main")),
    (bash("cp scripts/zeus/main.py made/main/scripts/z/main.py"), DENY.files("main")),
    (bash("ps -eo pid,args | grep -i '[t]nt_rain'"), DENY.ps),
    (bash("python3 made/main/scripts/x/main.py"), DENY.python),
    (call("Bash", command="sleep 30", run_in_background=True), DENY.background),
    # ls, cat, sed -n, grep, head, tail and py_compile: in scripts/, actions/ and made/<server>/ only
    (bash("ls -la made/main/scripts"), None),
    (bash("ls actions scripts/_lib/kit made/main"), None),
    (bash("ls"), None),  # the repository's folders by name, nothing in them
    (bash("ls -R"), SCOPE),
    (bash("ls -laR"), SCOPE),
    (bash("ls made"), SCOPE),
    (bash("ls made/other/scripts"), SCOPE),
    (bash("ls integration/replay"), SCOPE),
    (bash("ls /etc"), SCOPE),
    (bash("ls made/../../"), SCOPE),
    (bash("ls ~/.ssh"), SCOPE),
    (bash("grep -rn crater scripts/_presets"), None),
    (bash("grep -r crater"), SCOPE),
    (bash("grep -rn hot_fireball integration/replay/"), SCOPE),
    (bash("grep -rn fire scripts actions made/main"), None),
    (bash("grep -A3 -n -e root -e crater made/main/scripts/x/main.py"), None),
    (bash("grep crater"), DENY.grep_file),
    (bash("grep TYPESAFE ~/.vfg/config"), SCOPE),
    (bash("head -n 40 brain/brain/implementers/guard.py"), SCOPE),
    (bash("cat scripts/README.md actions/_types.py made/main/scripts/x/main.py"), None),
    (bash("cat -n scripts/_lib/mc.py | head -n 80"), None),
    (bash("cat docs/CONTRACTS.md"), SCOPE),
    (bash("cat"), DENY.cat),
    (bash("vfg state | cat"), DENY.pipe),
    (bash("sed -n '1,80p' scripts/zeus/main.py"), None),
    (bash("sed -n 120p made/main/scripts/x/main.py && sed -n '40,$p' actions/zeus.py"), None),
    (bash("sed -n 1,20p /etc/passwd"), SCOPE),
    (bash("sed -i s/a/b/ made/main/scripts/x/main.py"), DENY.sed),
    (bash("sed -n '/def/p' scripts/zeus/main.py"), DENY.sed),
    (bash("sed -n 1,5p"), DENY.sed),
    (bash("sed -n 1,5p -e x scripts/zeus/main.py"), DENY.sed),
    (bash("grep -f /etc/passwd scripts/README.md"), DENY.pattern_file),
    (bash("tail -20 scripts/README.md"), None),
    (bash("tail -f made/main/scripts/x/main.py"), DENY.tail_follow),
    (bash("vfg script logs x -n 5 | tail -F"), DENY.tail_follow),
    (bash("python3 -m py_compile ~/.vfg/config"), SCOPE),
    (bash("python3 -c 'import os'"), DENY.python),
    # what bash expands and the guard does not follow: ~name, braces, globs outside quotes
    (bash("head ~jm/.ssh/id_ed25519"), DENY.wild),
    (bash("ls ~+/.."), DENY.wild),
    (bash("grep -r . {/var/home/jm/.vfg,x}"), DENY.wild),
    (bash("ls /{u..w}ar"), DENY.wild),
    (bash("echo made/main/scripts/*"), DENY.wild),
    (bash("ls /?tc /[e]tc"), SCOPE),  # a glob in a read: expanded, and /etc is outside
    (bash("FOO=1 ls ~jm"), DENY.wild),
    (bash("grep -n 'def .*' scripts/README.md"), None),
    (bash('grep -n "a*b{c,d}" made/main/scripts/x/main.py'), None),
    (bash("head -n 5 made/main/scripts/x\\*"), None),
    # files: written only in this server's folder, read and searched only in the repository
    (call("Write", file_path=REPO + "/made/main/scripts/torch_ring/main.py"), None),
    (call("Write", file_path=REPO + "/made/main/presets/ice_trail.py"), None),
    (call("Edit", file_path="made/main/actions/tnt_crouch.py"), None),
    (call("Write", file_path=REPO + "/actions/sniper_rail.py"), DENY.library_actions("main")),
    (call("Edit", file_path="actions/_types.py"), DENY.library_actions("main")),
    (call("Write", file_path=REPO + "/scripts/torch_ring/main.py"), DENY.library("main")),
    (call("Edit", file_path=REPO + "/scripts/_lib/kit/area.py"), DENY.library("main")),
    (call("Write", file_path=REPO + "/made/main/../../scripts/x/main.py"), DENY.library("main")),
    (call("Write", file_path=REPO + "/made/other/actions/x.py"), DENY.made_only("main")),
    (call("Write", file_path=REPO + "/made/x.py"), DENY.made_only("main")),
    (call("Write", file_path="/tmp/x.py"), DENY.made_only("main")),
    (call("Glob", pattern="*", path="~/.ssh"), SCOPE),
    (call("Grep", pattern=".", path=REPO + "/../.."), SCOPE),
    (call("Grep", pattern="tnt", path=REPO + "/scripts"), None),
    (call("Grep", pattern="tnt", path="made/main/scripts/"), None),
    (call("Grep", pattern="hot_fireball"), SCOPE),  # no path: the whole repository
    (call("Grep", pattern="hot_fireball", path="integration/replay"), SCOPE),
    (call("Glob", pattern="*/README.md", path="scripts"), None),
    (call("Glob", pattern="scripts/*/README.md"), SCOPE),
    (call("Glob", pattern="../../**/*.py", path="scripts"), SCOPE),
    (call("Glob", pattern="/etc/*", path="scripts"), SCOPE),
    (call("Read", file_path="scripts/README.md"), None),
    (call("Read", file_path=REPO + "/scripts/_lib/kit/area.py"), None),
    (call("Read", file_path="actions/_types.py"), None),
    (call("Read", file_path="made/main/actions/x.py"), None),
    (call("Read", file_path="scripts/zeus/test.py"), None),  # a library script's test
    (call("Read", file_path="made/main/tests/hot_fireball_steve.py"), None),  # a task's test
    (bash("cat made/main/tests/hot_fireball_steve.py scripts/zeus/test.py"), None),
    (call("Write", file_path="made/main/tests/hot_fireball_steve.py"), None),
    (call("Edit", file_path=REPO + "/made/main/tests/hot_fireball_steve.py"), None),
    (call("Write", file_path="scripts/zeus/test.py"), DENY.library("main")),
    (call("Read", file_path="integration/replay/tasks.jsonl"), SCOPE),
    (call("Read", file_path="made/other/actions/x.py"), SCOPE),
    (call("Read", file_path="~/.vfg/config"), SCOPE),
    (call("Read", file_path="/etc/passwd"), SCOPE),
]


def test_the_guard():
    assert {"op", "stop", "ban", "whitelist"} <= guard.refused()  # scripts/_lib/commands.py, the one list
    g = GuardContext(repo=REPO)
    wrong = [(c["tool_input"], want, got) for c, want in TABLE if (got := decide(c, g)) != want]
    assert wrong == []
    assert not [want for _, want in TABLE if want and "\n" in want]  # each refusal is one line


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
    g = GuardContext(repo=REPO, task="torch_ring_steve")
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
    g = GuardContext(repo=str(tmp_path), task="torch_ring_steve", snapshot=Noting())
    assert await gate(bash("vfg script new torch_ring_steve --from torch_ring"), g) is None
    assert await gate(bash("curl x"), g) == DENY.shape  # refused: not noted
    assert g.snapshot.seen == [{"command": "vfg script new torch_ring_steve --from torch_ring"}]
    assert g.made["scripts"] == ["torch_ring_steve"]
    g.snapshot = Noting(breaks=True)  # a snapshot that breaks never blocks the call
    assert await gate(bash("vfg state"), g) is None


# ---------------------------------------------------------------- a script another task's copy runs

BEAM_ALEX = {"instance": "beam_alex", "copy": "beam_alex", "script": "beam", "task": "beam_duel_alex"}


class Running:
    """GuardContext.running: what it answers, and how often it was asked."""

    def __init__(self, instances):
        self.instances = instances
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if isinstance(self.instances, Exception):
            raise self.instances
        return self.instances


def made_repo(tmp_path) -> str:
    for name in ("beam", "staff"):
        (tmp_path / "made" / "main" / "scripts" / name).mkdir(parents=True)
        (tmp_path / "made" / "main" / "scripts" / name / "main.py").write_text('"""x"""\n')
    return str(tmp_path)


async def verdict(g, tool, **inp):
    return await gate({"tool_name": tool, "tool_input": inp}, g)


async def test_another_tasks_running_copy_keeps_its_script_from_being_changed(tmp_path):
    repo = made_repo(tmp_path)
    running = Running([BEAM_ALEX])
    g = GuardContext(repo=repo, task="torch_ring_steve", running=running)
    reason = await verdict(g, "Edit", file_path=os.path.join(repo, "made/main/scripts/beam/main.py"))
    assert reason == ("Refused: beam_alex (beam_duel_alex) runs made/main/scripts/beam/, and code a running copy uses "
                      "is not changed: vfg script new <your task> --from beam copies it, with its action, for you to "
                      "Edit; then vfg script stop beam_alex and run the new action.")
    # a task's copy named as the task is not named twice
    assert DENY.running("beam", {"copy": "beam_alex", "task": "beam_alex"}, "main").startswith(
        "Refused: beam_alex runs made/main/scripts/beam/,")
    # a script nothing runs is let through, and not asked about again; a preset and a new script are not this rule's
    assert await verdict(g, "Edit", file_path="made/main/scripts/staff/main.py") is None
    assert await verdict(g, "Edit", file_path="made/main/scripts/staff/main.py") is None
    assert await verdict(g, "Write", file_path="made/main/presets/x.py") is None
    assert await verdict(g, "Write", file_path="made/main/scripts/fresh/main.py") is None
    assert running.calls == 2
    # the task that started the copy may fix its own script
    g = GuardContext(repo=repo, task="beam_duel_alex", running=Running([BEAM_ALEX]))
    assert await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py") is None
    # and when what runs cannot be told, the edit goes through
    for answer in (None, RuntimeError("no network")):
        g = GuardContext(repo=repo, task="torch_ring_steve", running=Running(answer))
        assert await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py") is None


async def test_a_script_the_implementer_made_itself_is_its_own_to_change_while_it_runs(tmp_path):
    repo = made_repo(tmp_path)
    running = Running([{"instance": "beam2", "script": "beam2", "task": None}])
    g = GuardContext(repo=repo, task="torch_ring_steve", running=running)
    assert await verdict(g, "Bash", command="vfg script new beam2 --from beam --preset crater 2>&1 | head -n 5") is None
    (tmp_path / "made" / "main" / "scripts" / "beam2").mkdir()
    (tmp_path / "made" / "main" / "scripts" / "beam2" / "main.py").write_text("")
    assert await verdict(g, "Edit", file_path="made/main/scripts/beam2/main.py") is None
    assert running.calls == 0 and g.own == {"torch_ring_steve", "beam2"}


async def test_the_tasks_own_script_is_its_own_from_the_start_while_the_brains_copies_of_its_action_run(tmp_path):
    repo = made_repo(tmp_path)
    running = Running([{"instance": "beam_steve", "copy": "beam_steve", "script": "beam", "task": None}])
    assert GuardContext(repo=repo).own == set()
    g = GuardContext(repo=repo, task="beam", running=running)
    assert g.own == {"beam"}
    assert await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py") is None
    assert running.calls == 0
    # another task's script that the brain's copy runs is still refused
    g = GuardContext(repo=repo, task="staff_alex", running=running)
    assert (await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py")).startswith("Refused: beam_steve runs")


async def test_what_runs_is_running_json_else_vfg_state_kept_three_seconds(tmp_path, monkeypatch):
    (tmp_path / "running.json").write_text(
        '{"copies": {"beam_alex": {"script": "beam", "task": "beam_duel_alex"}, "junk": "x"}}')
    vfg = FakeVfg()
    assert await guard.running_copies(str(tmp_path), vfg)() == [BEAM_ALEX]
    assert vfg.calls == []
    vfg.on("state", answer=ok('{"online": [], "instances": [{"instance": "beam_alex", "script": "beam"}, "junk"]}'))
    running = guard.running_copies(str(tmp_path / "none"), vfg)
    assert await running() == await running() == [{"instance": "beam_alex", "script": "beam"}]
    assert vfg.calls == [["state", "--json"]]
    monkeypatch.setattr(guard, "STATE_CACHE_S", 0)
    vfg.on("state", answer=bad("vfg: EC2 unreachable"))
    assert await running() is None


# ---------------------------------------------------------------- what a task made

MADE_BY = [
    ("vfg script new torch_ring_steve", [("scripts", "torch_ring_steve"), ("actions", "torch_ring_steve")]),
    ("vfg script new torch_ring_steve --from torch_ring --preset ring --preset follow",
     [("scripts", "torch_ring_steve"), ("actions", "torch_ring_steve")]),
    ("vfg script new --from=torch_ring torch_ring_steve", [("scripts", "torch_ring_steve"), ("actions", "torch_ring_steve")]),
    ("vfg script save torch_ring_2 torch_ring_soul", [("actions", "torch_ring_soul")]),
    ("vfg preset save torch_ring_steve follow_player follow", [("presets", "follow")]),
    ("vfg action run torch_ring_steve radius=3 as=torch_ring_steve --task torch_ring_steve",
     [("copies", "torch_ring_steve")]),
    ("vfg action run --task torch_ring_steve as=torch_ring_steve_2 torch_ring_steve radius=3",
     [("copies", "torch_ring_steve_2")]),
    ("vfg action run torch_ring_steve --for Steve --task=torch_ring_steve as=ring_2", [("copies", "ring_2")]),
    # a run with no as= names no copy here; edit and stop start none
    ("vfg action run torch_ring_steve --task torch_ring_steve", []),
    ("vfg action run edit copy=torch_ring_steve radius=5 as=torch_ring_steve --task torch_ring_steve", []),
    ("vfg action run stop copy=torch_ring_steve as=torch_ring_steve --task torch_ring_steve", []),
    # names vfg refuses, and what makes nothing
    ("vfg script new Torch", []),
    ("vfg script new ../../scripts/zoo", []),
    ("vfg action run torch_ring as=Ring --task torch_ring_steve", []),
    ("vfg preset save a b", []),
    ("vfg script set torch_ring_steve radius=5", []),
    ("vfg script stop torch_ring_steve", []),
    ("vfg script new --kind cast-launch dash_steve --from launch_pad", [("scripts", "dash_steve"), ("actions", "dash_steve")]),
    ("vfg state", []),
    ("echo vfg script new torch_ring_steve", []),
]


def test_made_by_reads_what_one_command_makes():
    wrong = [(cmd, want, got) for cmd, want in MADE_BY if (got := made_by(split_shell(cmd)[0].words)) != want]
    assert wrong == []


async def test_gate_records_what_the_calls_it_lets_through_make_in_order_and_once(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="torch_ring_steve")

    async def run(command):
        return await gate({"tool_name": "Bash", "tool_input": {"command": command}}, g)

    assert await run("vfg script new torch_ring_steve --from torch_ring && vfg action check torch_ring_steve && "
                     "vfg action run torch_ring_steve as=torch_ring_steve --task torch_ring_steve") is None
    assert await run("vfg action run torch_ring_steve as=torch_ring_steve --task torch_ring_steve; "
                     "vfg preset save torch_ring_steve follow_player follow 2>&1 | tail -n 3") is None
    # a call the guard denies records nothing, even the commands in it that would run alone
    assert await run("vfg script new other_ring && curl x") is not None
    assert await run("vfg action run torch_ring as=ring_9") is not None
    # a name already in made/main/ is another's, and vfg refuses it: nothing of that command is recorded
    (tmp_path / "made" / "main" / "scripts" / "beam_alex").mkdir(parents=True)
    for kind, name in (("actions", "railgun_steve"), ("presets", "crater")):
        (tmp_path / "made" / "main" / kind).mkdir(parents=True, exist_ok=True)
        (tmp_path / "made" / "main" / kind / (name + ".py")).write_text("")
    assert await run("vfg script new beam_alex --from beam; vfg script save railgun_2 railgun_steve; "
                     "vfg preset save torch_ring_steve dig crater") is None
    assert g.made == {"scripts": ["torch_ring_steve"], "actions": ["torch_ring_steve"], "presets": ["follow"],
                      "copies": ["torch_ring_steve"]}


async def test_a_write_of_a_new_made_action_or_preset_is_recorded_and_an_edit_is_not(tmp_path):
    repo = str(tmp_path)
    (tmp_path / "made" / "main" / "actions").mkdir(parents=True)
    (tmp_path / "made" / "main" / "actions" / "railgun_steve.py").write_text("")
    g = GuardContext(repo=repo, task="gatling_gun_steve")

    async def call(tool, path):
        return await gate({"tool_name": tool, "tool_input": {"file_path": path, "content": "x"}}, g)

    assert await call("Write", "made/main/actions/gatling_gun_steve.py") is None  # new: recorded
    assert await call("Write", os.path.join(repo, "made/main/presets/spin_up.py")) is None  # new, by its whole path
    assert await call("Edit", "made/main/actions/railgun_steve.py") is None  # an Edit makes nothing new
    assert await call("Write", "made/main/actions/railgun_steve.py") is None  # nor a Write over a file that is there
    assert await call("Write", "made/main/scripts/gatling_gun_steve/main.py") is None  # a script comes by vfg script new
    assert await call("Write", "made/main/actions/_helpers.py") is None  # not an action's name
    assert await call("Write", "made/main/actions/sub/deep.py") is None
    assert await call("Write", "actions/gatling.py") is not None
    assert g.made == {"scripts": [], "actions": ["gatling_gun_steve"], "presets": ["spin_up"], "copies": []}


def test_made_now_keeps_what_is_on_disk_and_every_copy(tmp_path):
    g = GuardContext(repo=str(tmp_path), server="named", task="torch_ring_steve")
    g.made = {"scripts": ["torch_ring_steve", "gone"], "actions": ["torch_ring_steve", "torch_ring_soul"],
              "presets": ["follow", "never_saved"], "copies": ["torch_ring_steve", "torch_ring_steve_2"]}
    for rel in ("made/named/scripts/torch_ring_steve/main.py", "made/named/scripts/gone/README.md",
                "made/named/actions/torch_ring_steve.py", "made/named/actions/torch_ring_soul.py",
                "made/named/presets/follow.py", "made/main/presets/never_saved.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    now = made_now(g)
    assert now == {"scripts": ["torch_ring_steve"], "actions": ["torch_ring_steve", "torch_ring_soul"],
                   "presets": ["follow"], "copies": ["torch_ring_steve", "torch_ring_steve_2"]}
    now["copies"].append("x")  # a copy: the record is not changed through it
    assert g.made["copies"] == ["torch_ring_steve", "torch_ring_steve_2"]
    assert made_now(GuardContext(repo=str(tmp_path))) == {"scripts": [], "actions": [], "presets": [], "copies": []}


def test_no_web_on_any_harness():
    g = GuardContext(repo=REPO)
    assert decide(call("WebFetch", url="https://example.com"), g) == DENY.web
    assert decide(call("WebSearch", query="minecraft fireball"), g) == DENY.web
    assert decide(call("TodoWrite", todos=[]), g) is None


def test_a_glob_in_a_read_is_allowed_when_every_match_stays_readable(tmp_path):
    repo = str(tmp_path)
    for rel in ("scripts/zeus/main.py", "scripts/zeus/README.md", "made/main/scripts/x/main.py", "integration/replay/t.py",
                "lib/py/vfg_llm.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    for command, want in [
        ("cat scripts/zeus/*.py", None),
        ("ls made/main/scripts/*/", None),
        ("grep -n fire scripts/*/main.py made/main/scripts/*/main.py", None),
        ("head -n 5 scripts/nothing_*.py", None),  # no match: bash leaves the word, which is readable
        ("python3 -m py_compile made/main/scripts/*/main.py", None),
        ("sed -n 1,5p scripts/zeus/[mR]*", None),
        ("grep -n 'def .*' scripts/zeus/main.py", None),  # quoted: no glob
        ("cat */replay/*.py", SCOPE),
        ("ls *", SCOPE),  # the repository's top: integration/, lib/
        ("grep -n x scripts/../lib/*/*.py", SCOPE),
        ("head -n 3 /etc/pass*", SCOPE),
        ("cat {scripts,lib}/x", DENY.wild),  # a brace: still refused
        ("echo scripts/*", DENY.wild),  # globs in a read only
    ]:
        assert check_bash(command, repo, "main") == want, command


async def test_a_test_rerun_with_nothing_changed_is_refused_and_four_runs_is_the_most(tmp_path):
    repo = str(tmp_path)
    g = GuardContext(repo=repo, task="speed_dash_steve")
    g.made["presets"] = ["dash_push"]

    def put(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    async def test_run(command="vfg test speed_dash_steve"):
        return await gate(bash(command), g)

    put("made/main/tests/speed_dash_steve.py", "t1")
    put("made/main/scripts/speed_dash_steve/main.py", "s1")
    assert await test_run() is None
    assert await test_run("vfg test speed_dash_steve -n 2 2>&1 | tail -n 30") == DENY.test_again  # nothing changed
    assert await test_run("vfg test speed_dash_steve -n 60") == DENY.test_again  # more lines: with the next change
    assert g.tests_run == 1  # a refused call counts nothing
    put("made/main/scripts/speed_dash_steve/main.py", "s2")  # the ability
    assert await test_run() is None
    put("made/main/presets/dash_push.py", "p1")  # a preset the task made
    assert await test_run() is None
    put("made/main/tests/speed_dash_steve.py", "t2")  # the test
    assert await test_run() is None
    put("made/main/actions/speed_dash_steve.py", "a1")  # its action
    assert await test_run() == DENY.test_cap  # a fifth: report what still fails
    assert await test_run("vfg test speed_dash_steve && curl x") == DENY.shape  # the guard's own refusal first
    g.new_run()  # the next run (a follow-up) has its own four
    assert await test_run() is None
    assert await gate(bash("vfg script logs speed_dash_steve -n 40"), g) is None
    assert "vfg script logs" not in DENY.test_again and "-n 60" in DENY.test_again  # the test's log is the test server's
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
    assert await gate(bash("vfg script stop high_step_steve"), g) is None
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


def test_an_incantation_never_binds_a_made_script_that_waits_for_a_crouch_or_a_click(tmp_path):
    repo = str(tmp_path)

    def made(name, runs, script_text, base=None):
        (tmp_path / "made/main/actions").mkdir(parents=True, exist_ok=True)
        head = "from actions.%s import Base\nclass X(Base):\n" % base if base else "class X(Action):\n"
        (tmp_path / "made/main/actions" / (name + ".py")).write_text(
            head + '    script = "%s"\n' % name + ('    runs = "%s"  # or "once"\n' % runs if runs else ""))
        (tmp_path / "made/main/scripts" / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / "made/main/scripts" / name / "main.py").write_text(script_text)

    def bind(name, tool="incantation_create"):
        return check_bash("vfg action run %s player=Steve phrase='hot fireball' action=%s --task %s"
                          % (tool, name, name), repo, "main")

    made("hot_fireball_steve", "until_stopped", "def projectile_lands(player, v):\n    pass\nfor at, p, w in loop(projectile_lands(v['player'], v)):\n    pass\n")
    made("staff_steve", "until_stopped", "for p in loop(items.clicks(v['player'], v['item'])):\n    fire(p)\n")
    made("crouch_steve", "until_stopped", "def sneak(player, v):\n    pass\n")
    made("high_step_steve", "until_stopped", "box = under_feet(p, v)\nif ok(v['pad_block']):\n    launch(p, v)\n")
    made("stat_steve", "until_stopped", "s = items.Stat(*items.SNEAK)\n")
    made("fireball_once", "once", "for p in loop(items.clicks(v['player'], v['item'])):\n    pass\n")  # once: acts as it starts
    made("ice_walk_steve", "until_stopped", "for p, at in loop(walk_steps(v['player'], v)):\n    ice(at)\n")  # no input
    made("dash_steve", "until_stopped", "launch(v['player'], v)\n")
    for name in ("hot_fireball_steve", "staff_steve", "crouch_steve", "high_step_steve", "stat_steve"):
        assert bind(name) == DENY.waits_for_trigger, name
    for name in ("fireball_once", "ice_walk_steve", "dash_steve", "railgun"):  # railgun: a library action, not read
        assert bind(name) is None, name
    assert bind("staff_steve", "incantation_edit") is None  # an edit names no action
    # a variant takes runs from the library action it subclasses
    (tmp_path / "actions").mkdir(exist_ok=True)
    (tmp_path / "actions/boom_bow.py").write_text('class BoomBow(Action):\n    script = "boom_bow"\n    runs = "until_stopped"\n')
    made("bow_steve", None, "def projectile_lands(player, v):\n    pass\n", base="boom_bow")
    assert bind("bow_steve") == DENY.waits_for_trigger


async def test_a_comment_or_a_docstring_alone_is_no_change_for_a_rerun(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="t1")

    def put(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    put("made/main/tests/t1.py", 'from kit import test\nt = test.Test("Steve")\nt.check("a", t.moved(4))\n')
    put("made/main/scripts/t1/main.py", '"""A dash."""\nlaunch(v)\n')
    assert await gate(bash("vfg test t1"), g) is None
    put("made/main/tests/t1.py", 'from kit import test\n# try again\nt = test.Test("Steve")   \n\nt.check("a", t.moved(4))  # bar\n')
    put("made/main/scripts/t1/main.py", '"""A dash, forward."""\n\nlaunch(v)  # go\n')
    assert await gate(bash("vfg test t1"), g) == DENY.test_again  # comments, blank lines, a docstring: unchanged
    put("made/main/tests/t1.py", 'from kit import test\nt = test.Test("Steve")\nt.check("a", t.moved(2))\n')
    assert await gate(bash("vfg test t1"), g) is None  # a real edit (the bar)
    put("made/main/scripts/t1/main.py", "launch(v\n")  # does not parse: judged as it is
    assert await gate(bash("vfg test t1"), g) is None
    assert guard.code_print(b"x = 1  # a\n", "a.py") == guard.code_print(b"\n\nx=1\n", "b.py")
    assert guard.code_print(b"# a\n", "notes.txt") != guard.code_print(b"# b\n", "notes.txt")
