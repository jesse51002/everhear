"""The guard: one table of calls and the verdict each gets, then the hook, the rule on a script
another task's running copy uses, and what a task made, as the guard records it."""
import os

from brain.implementers import guard
from brain.implementers.guard import (DENY, VFG_FORMS, VFG_USAGE, GuardContext, Redirect, check_bash, decide,
                                      made_by, made_now, make_guard, split_shell)

from .fakes import FakeVfg, bad, ok

REPO = "/home/k/everhear"


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
    (bash("vfg action refs"), usage("action")),
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
    (bash("sed -n 1,20p scripts/README.md"), DENY.read),
    (bash("find . -name main.py"), DENY.find),
    (bash("cp scripts/zeus/main.py made/main/scripts/z/main.py"), DENY.files("main")),
    (bash("ps -eo pid,args | grep -i '[t]nt_rain'"), DENY.ps),
    (bash("python3 made/main/scripts/x/main.py"), DENY.python),
    (call("Bash", command="sleep 30", run_in_background=True), DENY.background),
    # ls, grep, head, tail and py_compile, inside the repository only
    (bash("ls -la made/main/scripts"), None),
    (bash("ls /etc"), DENY.outside_bash),
    (bash("ls made/../../"), DENY.outside_bash),
    (bash("ls ~/.ssh"), DENY.outside_bash),
    (bash("grep -rn crater scripts/_presets"), None),
    (bash("grep -r crater"), None),
    (bash("grep -A3 -n -e root -e crater made/main/scripts/x/main.py"), None),
    (bash("grep crater"), DENY.grep_file),
    (bash("grep TYPESAFE ~/.vfg/config"), DENY.outside_bash),
    (bash("grep -f /etc/passwd scripts/README.md"), DENY.pattern_file),
    (bash("tail -20 scripts/README.md"), None),
    (bash("tail -f made/main/scripts/x/main.py"), DENY.tail_follow),
    (bash("vfg script logs x -n 5 | tail -F"), DENY.tail_follow),
    (bash("python3 -m py_compile ~/.vfg/config"), DENY.outside_bash),
    # what bash expands and the guard does not follow: ~name, braces, globs outside quotes
    (bash("head ~jm/.ssh/id_ed25519"), DENY.wild),
    (bash("ls ~+/.."), DENY.wild),
    (bash("grep -r . {/var/home/jm/.vfg,x}"), DENY.wild),
    (bash("ls /{u..w}ar"), DENY.wild),
    (bash("echo made/main/scripts/*"), DENY.wild),
    (bash("ls /?tc /[e]tc"), DENY.wild),
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
    (call("Glob", pattern="*", path="~/.ssh"), DENY.outside),
    (call("Grep", pattern=".", path=REPO + "/../.."), DENY.outside),
    (call("Grep", pattern="tnt", path=REPO + "/scripts"), None),
    (call("Glob", pattern="scripts/*/README.md"), None),
    (call("Read", file_path="scripts/README.md"), None),
    (call("Read", file_path="~/.vfg/config"), DENY.read_outside),
    (call("Read", file_path="/etc/passwd"), DENY.read_outside),
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


async def test_the_hook_denies_in_the_sdks_shape_and_lets_the_rest_through():
    hook = make_guard(GuardContext(repo=REPO, task="torch_ring_steve"))
    base = {"session_id": "s", "hook_event_name": "PreToolUse"}
    denied = await hook({**base, "tool_name": "Bash", "tool_input": {"command": "curl x"}}, "u1", None)
    assert denied == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": DENY.shape}}
    assert await hook({**base, "tool_name": "Bash", "tool_input": {"command": "vfg state"}}, "u2", None) == {}
    assert await hook({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, "u3", None) == {}


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
    out = await make_guard(g)({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp}, "u", None)
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason") if out else None


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
    ("vfg state", []),
    ("echo vfg script new torch_ring_steve", []),
]


def test_made_by_reads_what_one_command_makes():
    wrong = [(cmd, want, got) for cmd, want in MADE_BY if (got := made_by(split_shell(cmd)[0].words)) != want]
    assert wrong == []


async def test_the_hook_records_what_the_calls_it_lets_through_make_in_order_and_once(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="torch_ring_steve")
    hook = make_guard(g)

    async def run(command):
        return await hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}},
                          "u", None)

    assert await run("vfg script new torch_ring_steve --from torch_ring && vfg action check torch_ring_steve && "
                     "vfg action run torch_ring_steve as=torch_ring_steve --task torch_ring_steve") == {}
    assert await run("vfg action run torch_ring_steve as=torch_ring_steve --task torch_ring_steve; "
                     "vfg preset save torch_ring_steve follow_player follow 2>&1 | tail -n 3") == {}
    # a call the guard denies records nothing, even the commands in it that would run alone
    assert (await run("vfg script new other_ring && curl x"))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (await run("vfg action run torch_ring as=ring_9"))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert g.made == {"scripts": ["torch_ring_steve"], "actions": ["torch_ring_steve"], "presets": ["follow"],
                      "copies": ["torch_ring_steve"]}


async def test_a_write_of_a_new_made_action_or_preset_is_recorded_and_an_edit_is_not(tmp_path):
    repo = str(tmp_path)
    (tmp_path / "made" / "main" / "actions").mkdir(parents=True)
    (tmp_path / "made" / "main" / "actions" / "railgun_steve.py").write_text("")
    g = GuardContext(repo=repo, task="gatling_gun_steve")

    async def call(tool, path):
        return await make_guard(g)({"hook_event_name": "PreToolUse", "tool_name": tool,
                                    "tool_input": {"file_path": path, "content": "x"}}, "u", None)

    assert await call("Write", "made/main/actions/gatling_gun_steve.py") == {}  # new: recorded
    assert await call("Write", os.path.join(repo, "made/main/presets/spin_up.py")) == {}  # new, by its whole path
    assert await call("Edit", "made/main/actions/railgun_steve.py") == {}  # an Edit makes nothing new
    assert await call("Write", "made/main/actions/railgun_steve.py") == {}  # nor a Write over a file that is there
    assert await call("Write", "made/main/scripts/gatling_gun_steve/main.py") == {}  # a script comes by vfg script new
    assert await call("Write", "made/main/actions/_helpers.py") == {}  # not an action's name
    assert await call("Write", "made/main/actions/sub/deep.py") == {}
    assert (await call("Write", "actions/gatling.py"))["hookSpecificOutput"]["permissionDecision"] == "deny"
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
