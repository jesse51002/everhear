"""The guard: one table of calls and the verdict each gets, then the hook, and the rule on a script
another task's running copy uses."""
import os

from brain.implementers import guard
from brain.implementers.guard import (DENY, VFG_FORMS, VFG_USAGE, GuardContext, Redirect, check_bash, decide,
                                      make_guard, split_shell)

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
    (bash("vfg action run tnt_rain --task T7 && vfg cmd 'deop Steve'"), DENY.owner),
    (bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner),
    (bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner),
    (bash("""vfg action run command 'commands=["time set day","kick Alex"]' --task T7"""), DENY.owner),
    (bash("vfg action run command 'commands=time set day,execute as @a run ban Bob' --task T7"), DENY.owner),
    (bash("vfg script set crouch_rule on_command='op Steve'"), DENY.owner),
    (bash("vfg cmd '/Difficulty peaceful'"), DENY.server_wide("difficulty")),
    (bash("vfg cmd 'execute as @a run worldborder set 100'"), DENY.server_wide("worldborder")),
    (bash("vfg action run command commands='setworldspawn 0 64 0' --task T7"), DENY.server_wide("setworldspawn")),
    (bash("vfg cmd 'say difficulty is hard, stop that'"), None),
    (bash("""vfg cmd 'tellraw @a {"text":"run stop"}'"""), None),
    (bash("vfg action run sign text='stop here, ban the creepers' --task T7"), None),
    (bash('vfg cmd "$(echo op) Steve"'), DENY.expansion),
    (bash("vfg cmd 'say costs $5'"), None),
    # vfg's forms
    (bash("vfg action run spirit_bomb player=Steve as=bomb_steve --task T7"), None),
    (bash("vfg action run castle at='197 68 251' --task=T12"), None),
    (bash("vfg action run castle at=home"), DENY.action_task),
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
    (bash("vfg place save castle --at 197 68 251 --by T7"), None),
    (bash("vfg place list --json"), None),
    (bash("vfg place forget castle"), usage("place")),
    (bash("vfg history --source ai --since -10m"), None),
    (bash("vfg --timeout 30 cmd 'time set day'"),
     "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg action run ..., as it is."),
    # key={...,...}, a JSON object of two keys or more in one word, which Claude Code's own check
    # refuses: values.<name>= words instead; an object as a word of its own, or NBT in vfg cmd, passes
    (bash("""vfg action run x values='{"a":1,"b":2}'"""), DENY.json_object),
    (bash("""vfg action run incantation_edit player=Steve phrase='fire ball' values={"a":1,"b":2} --task T7"""),
     DENY.json_object),
    (bash("vfg action run x values.a=1 values.b=2 --task T7"), None),
    (bash("""vfg action run x values='{"a":1}' --task T7"""), None),
    (bash("""vfg cmd 'summon zombie ~ ~ ~ {CustomName:"x",Glowing:1b}'"""), None),
    (bash("vfg say 'hi'"), not_ours("say")),
    (bash("vfg route --task T7"), not_ours("route")),
    # the shapes that run: joined, 2>&1 and /dev/null, piped into head, tail and grep
    (bash("sleep 5 && vfg script logs tnt_rain -n 20"), None),
    (bash("python3 -m py_compile made/main/scripts/torch_ring/main.py && vfg action run torch_ring --task T7"), None),
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
    (bash("vfg action run x --task T7 &"), DENY.group),
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
    hook = make_guard(GuardContext(repo=REPO, task="T7"))
    base = {"session_id": "s", "hook_event_name": "PreToolUse"}
    denied = await hook({**base, "tool_name": "Bash", "tool_input": {"command": "curl x"}}, "u1", None)
    assert denied == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": DENY.shape}}
    assert await hook({**base, "tool_name": "Bash", "tool_input": {"command": "vfg state"}}, "u2", None) == {}
    assert await hook({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, "u3", None) == {}


# ---------------------------------------------------------------- a script another task's copy runs

BEAM_ALEX = {"instance": "beam-alex", "copy": "beam-alex", "script": "beam", "task": "T3"}


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
    g = GuardContext(repo=repo, task="T7", running=running)
    reason = await verdict(g, "Edit", file_path=os.path.join(repo, "made/main/scripts/beam/main.py"))
    assert reason == ("Refused: beam-alex (T3) runs made/main/scripts/beam/, and code a running copy uses is not "
                      "changed: vfg script new <new_name> --from beam copies it, with its action, for you to Edit; then "
                      "vfg script stop beam-alex and run the new action.")
    # a script nothing runs is let through, and not asked about again; a preset and a new script are not this rule's
    assert await verdict(g, "Edit", file_path="made/main/scripts/staff/main.py") is None
    assert await verdict(g, "Edit", file_path="made/main/scripts/staff/main.py") is None
    assert await verdict(g, "Write", file_path="made/main/presets/x.py") is None
    assert await verdict(g, "Write", file_path="made/main/scripts/fresh/main.py") is None
    assert running.calls == 2
    # the task that started the copy may fix its own script
    g = GuardContext(repo=repo, task="T3", running=Running([BEAM_ALEX]))
    assert await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py") is None
    # and when what runs cannot be told, the edit goes through
    for answer in (None, RuntimeError("no network")):
        g = GuardContext(repo=repo, task="T7", running=Running(answer))
        assert await verdict(g, "Edit", file_path="made/main/scripts/beam/main.py") is None


async def test_a_script_the_implementer_made_itself_is_its_own_to_change_while_it_runs(tmp_path):
    repo = made_repo(tmp_path)
    running = Running([{"instance": "beam2", "script": "beam2", "task": None}])
    g = GuardContext(repo=repo, task="T7", running=running)
    assert await verdict(g, "Bash", command="vfg script new beam2 --from beam --preset crater 2>&1 | head -n 5") is None
    (tmp_path / "made" / "main" / "scripts" / "beam2").mkdir()
    (tmp_path / "made" / "main" / "scripts" / "beam2" / "main.py").write_text("")
    assert await verdict(g, "Edit", file_path="made/main/scripts/beam2/main.py") is None
    assert running.calls == 0 and g.own == {"beam2"}


async def test_what_runs_is_running_json_else_vfg_state_kept_three_seconds(tmp_path, monkeypatch):
    (tmp_path / "running.json").write_text('{"copies": {"beam-alex": {"script": "beam", "task": "T3"}, "junk": "x"}}')
    vfg = FakeVfg()
    assert await guard.running_copies(str(tmp_path), vfg)() == [BEAM_ALEX]
    assert vfg.calls == []
    vfg.on("state", answer=ok('{"online": [], "instances": [{"instance": "beam-alex", "script": "beam"}, "junk"]}'))
    running = guard.running_copies(str(tmp_path / "none"), vfg)
    assert await running() == await running() == [{"instance": "beam-alex", "script": "beam"}]
    assert vfg.calls == [["state", "--json"]]
    monkeypatch.setattr(guard, "STATE_CACHE_S", 0)
    vfg.on("state", answer=bad("vfg: EC2 unreachable"))
    assert await running() is None
