import os

import pytest
from helpers import temp_dir

from orchestrator.guard import DENY, GuardContext, check_bash, decide, make_guard, refused_word, split_shell

REPO = "/home/k/everhear"
HOME = os.path.expanduser("~")  # a temp dir: conftest sets HOME


def ctx(**over) -> GuardContext:
    allowed: list[str] = []
    fields = dict(
        repo=REPO,
        max_implementers=4,
        implementer_model="haiku",
        main_thread_rule=True,
        open_task_ids=lambda: ["T7", "T9"],
        active_implementers=lambda: 0,
        on_agent_allowed=lambda t, u: allowed.append("%s:%s" % (t, u)),
    )
    fields.update(over)
    g = GuardContext(**fields)
    g.allowed = allowed
    return g


def main(tool_name, tool_input=None):
    return {"tool_name": tool_name, "tool_input": tool_input or {}, "tool_use_id": "tu1"}


def impl(tool_name, tool_input=None):
    return {"tool_name": tool_name, "tool_input": tool_input or {}, "agent_id": "a1", "tool_use_id": "tu2"}


def agent(**inp):
    return main("Agent", {"subagent_type": "implementer", "description": "T7 castle at spawn", "prompt": "do it", **inp})


def bash(command):
    return impl("Bash", {"command": command})


START = 'Start the description with the id of an open task, like "T7 castle at spawn". Open now: T7, T9.'

TABLE = [
    # the main thread orchestrates
    ("main-thread Bash", main("Bash", {"command": "vfg cmd 'time set day'"}), DENY.main_thread),
    ("main-thread Write", main("Write", {"file_path": REPO + "/scripts/x/main.py", "content": ""}), DENY.main_thread),
    ("main-thread Edit", main("Edit", {"file_path": REPO + "/scripts/x/main.py"}), DENY.main_thread),
    ("main-thread unknown tool", main("WebFetch", {"url": "x"}), DENY.main_thread),
    ("main-thread Read", main("Read", {"file_path": REPO + "/scripts/README.md"}), None),
    ("main-thread Glob", main("Glob", {"pattern": "scripts/*/README.md"}), None),
    ("main-thread Grep", main("Grep", {"pattern": "tnt", "path": REPO + "/scripts"}), None),
    ("main-thread TaskStop", main("TaskStop", {"task_id": "x"}), None),
    ("main-thread reply", main("mcp__orch__reply", {"task": "T7", "status": "done", "text": "ok"}), None),
    ("main-thread vfg place list", main("Bash", {"command": "vfg place list"}), None),
    ("main-thread vfg place list --json", main("Bash", {"command": "vfg place list --json"}), DENY.main_thread),
    ("main-thread vfg place list chained", main("Bash", {"command": "vfg place list && vfg cmd 'time set day'"}), DENY.main_thread),
    ("main-thread vfg place list in the background", main("Bash", {"command": "vfg place list", "run_in_background": True}), DENY.main_thread),
    ("main-thread vfg place save", main("Bash", {"command": "vfg place save castle --at 1 2 3 --by T7"}), DENY.main_thread),
    # Agent
    ("Agent ok", agent(), None),
    ("Agent another type", agent(subagent_type="general-purpose"), DENY.agent_type),
    ("Agent no type", agent(subagent_type=None), DENY.agent_type),
    ("Agent no task id", agent(description="castle at spawn"), START),
    ("Agent a closed task", agent(description="T3 castle"), START),
    ("Agent nothing open", agent(), "No task is open, so there is nothing to start an implementer for.",
     {"open_task_ids": lambda: []}),
    ("Agent T70 is not T7", agent(description="T70 castle"), START),
    ("Agent in the foreground", agent(run_in_background=False), DENY.foreground),
    ("Agent in the background", agent(run_in_background=True), None),
    ("Agent over the limit", agent(), "4 implementers are running; wait for a report.", {"active_implementers": lambda: 4}),
    ("Agent under the limit", agent(), None, {"active_implementers": lambda: 3}),
    ("Agent another model", agent(model="opus"), DENY.overrides),
    ("Agent the implementer model", agent(model="haiku"), None),
    ("Agent bypassing permissions", agent(mode="bypassPermissions"), DENY.overrides),
    ("Agent in a worktree", agent(isolation="worktree"), DENY.overrides),
    # implementer Bash: the server commands that are the owner's
    ("vfg cmd 'op Steve'", bash("vfg cmd 'op Steve'"), DENY.owner),
    ("vfg cmd 'execute as @a run stop'", bash("vfg cmd 'execute as @a run stop'"), DENY.owner),
    ("vfg cmd 'time set day'", bash("vfg cmd 'time set day'"), None),
    ("vfg cmd /OP Steve", bash("vfg cmd /OP Steve"), DENY.owner),
    ('vfg cmd "ban-ip x"', bash('vfg cmd "ban-ip 1.2.3.4"'), DENY.owner),
    ("vfg cmd whitelist", bash("vfg cmd 'whitelist add Bob'"), DENY.owner),
    ("vfg cmd kick after run", bash("vfg cmd 'execute at Steve run /kick Steve'"), DENY.owner),
    ("vfg cmd save-off", bash("vfg cmd save-off"), DENY.owner),
    ("vfg cmd pardon", bash("vfg cmd 'PARDON Steve'"), DENY.owner),
    ("chained after a good command", bash("vfg script run tnt_rain && vfg cmd 'deop Steve'"), DENY.owner),
    ("after a semicolon", bash("sleep 2; vfg cmd 'stop'"), DENY.owner),
    ("a flag before the command", bash("vfg cmd --quiet 'op Steve'"), DENY.owner),
    ("an env prefix", bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner),
    ("a full path", bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner),
    ("command substitution", bash('vfg cmd "$(echo op) Steve"'), DENY.expansion),
    ("a variable", bash("vfg cmd $CMD"), DENY.expansion),
    ("a dollar in single quotes is literal", bash("vfg cmd 'say costs $5'"), None),
    ("stop inside a word", bash("vfg cmd 'say stop that'"), None),
    ("run inside JSON text", bash("""vfg cmd 'tellraw @a {"text":"run stop"}'"""), None),
    ("script run is not a server command", bash("vfg script run anti_camp 15"), None),
    ("script stop is fine", bash("vfg script stop tnt_rain"), None),
    ("other commands get the shape reason", bash("ls ~"), DENY.shape),
    # shapes implementers tend to send
    ("seen: vfg cmd then echo $?", bash("""vfg cmd 'time set day'; echo "EXIT=$?\""""), DENY.shape),
    ("seen: give then echo", bash("""vfg cmd 'give Steve minecraft:diamond 1'; echo "EXIT_CODE=$?\""""), DENY.shape),
    ("seen: ps | grep; ls; find", bash("""ps -eo pid,etime,args | grep -i '[t]nt_rain'; echo "ps exit: $?"; ls -la /x/scripts/tnt_rain/ 2>&1; find /tmp -maxdepth 3 \\( -iname '*tnt_rain*' \\) 2>/dev/null | head -20"""), DENY.shape),
    ("seen: cd && ls; grep; sed", bash("""cd /x/repo/scripts && ls; ls _lib; grep -n "^def " _lib/mc.py; sed -n '/^def pos/,/^def /p' _lib/mc.py | head -30"""), DENY.shape),
    # the shapes that run
    ("sleep then logs", bash("sleep 5 && vfg script logs tnt_rain -n 20"), None),
    ("compile then run", bash("python3 -m py_compile scripts/torch_ring/main.py && vfg script run torch_ring"), None),
    ("run with --wait", bash("vfg script run --wait 30 gun_chests 4"), None),
    ("deploy all", bash("vfg script deploy"), None),
    ("deploy one", bash("vfg script deploy torch_ring"), None),
    ("list", bash("vfg script list"), None),
    ("history", bash("vfg history --source ai --since -10m"), None),
    ("two commands joined by ;", bash("vfg cmd 'time set day'; vfg cmd 'weather clear'"), None),
    ("a redirection", bash("vfg cmd 'time set day' 2>&1"), DENY.shape),
    ("to /dev/null", bash("vfg script stop tnt_rain > /dev/null"), DENY.shape),
    ("a global vfg option", bash("vfg --timeout 30 cmd 'time set day'"), DENY.shape),
    ("vfg say is not for implementers", bash("vfg say 'hi'"), DENY.shape),
    ("logs --follow never ends", bash("vfg script logs tnt_rain --follow"), DENY.follow),
    ("sleep with a variable", bash("sleep $N"), DENY.shape),
    ("a > inside quotes is no redirection", bash("""vfg cmd 'tellraw @a {"text":"a > b"}'"""), None),
    # saved places
    ("place save at coordinates", bash("vfg place save castle --at 197 68 251 --by T7"), None),
    ("place save where a player stands", bash("vfg place save home --player Steve --by T7"), None),
    ("place list", bash("vfg place list"), None),
    ("place list --json", bash("vfg place list --json"), None),
    ("place save with no name", bash("vfg place save"), DENY.shape),
    ("place forget is not for implementers", bash("vfg place forget castle"), DENY.shape),
    # implementer files
    ("Write a new script", impl("Write", {"file_path": REPO + "/scripts/torch_ring/main.py"}), None),
    ("Write _lib", impl("Write", {"file_path": REPO + "/scripts/_lib/mc.py"}), DENY.lib),
    ("Edit _lib", impl("Edit", {"file_path": REPO + "/scripts/_lib/mc.py"}), DENY.lib),
    ("Glob outside the repo", impl("Glob", {"pattern": "*", "path": "~/.ssh"}), DENY.outside),
    ("Grep outside the repo", impl("Grep", {"pattern": ".", "path": HOME + "/.vfg"}), DENY.outside),
    ("Grep with ..", impl("Grep", {"pattern": ".", "path": REPO + "/../.."}), DENY.outside),
    ("Grep inside", impl("Grep", {"pattern": "tnt", "path": REPO + "/scripts"}), None),
    ("Glob without a path", impl("Glob", {"pattern": "scripts/*/README.md"}), None),
    ("Read inside", impl("Read", {"file_path": REPO + "/scripts/tnt_rain/main.py"}), None),
    ("Read a relative path", impl("Read", {"file_path": "scripts/README.md"}), None),
    ("Read ~/.vfg/config", impl("Read", {"file_path": "~/.vfg/config"}), DENY.read_outside),
    ("Read ~/.ssh", impl("Read", {"file_path": HOME + "/.ssh/id_ed25519"}), DENY.read_outside),
    ("Read /etc", main("Read", {"file_path": "/etc/passwd"}), DENY.read_outside),
    ("Write _lib by a relative path", impl("Write", {"file_path": "scripts/_lib/mc.py"}), DENY.lib),
]


@pytest.mark.parametrize("call,want,over", [(row[1], row[2], row[3] if len(row) > 3 else {}) for row in TABLE],
                         ids=[row[0] for row in TABLE])
def test_the_guard(call, want, over):
    assert decide(call, ctx(**over)) == want


def test_server_wide_settings_refused_for_implementers_and_the_probe():
    g = ctx()
    for cmd, word in [
        ("vfg cmd 'setworldspawn'", "setworldspawn"),
        ("vfg cmd 'setworldspawn 0 64 0'", "setworldspawn"),
        ("vfg cmd '/Difficulty peaceful'", "difficulty"),
        ("vfg cmd 'execute as @a run difficulty hard'", "difficulty"),
        ("vfg cmd 'worldborder set 100'", "worldborder"),
        ("vfg cmd 'defaultgamemode creative'", "defaultgamemode"),
        ("vfg cmd 'time set day' && vfg cmd 'worldborder center 0 0'", "worldborder"),
        ("vfg cmd setworldspawn 1 2 3", "setworldspawn"),
    ]:
        assert decide(impl("Bash", {"command": cmd}), g) == DENY.server_wide(word)
        assert decide(main("Bash", {"command": cmd}), g) == DENY.main_thread  # the session runs no command
        assert decide(main("Bash", {"command": cmd}), ctx(main_thread_rule=False)) == DENY.server_wide(word)  # the probe
    assert "report" in DENY.server_wide("difficulty")
    assert decide(impl("Bash", {"command": "vfg cmd 'say difficulty is hard'"}), g) is None
    assert decide(impl("Bash", {"command": "vfg cmd 'time set day'"}), g) is None
    assert decide(impl("Bash", {"command": "vfg script run demon_lord"}), g) is None  # scripts are not refused
    assert decide(impl("Bash", {"command": "vfg cmd 'op Steve'"}), g) == DENY.owner


def test_an_implementers_background_bash_is_refused_too():
    assert decide(impl("Bash", {"command": "sleep 30", "run_in_background": True}), ctx()) == DENY.background


def test_the_probe_implementer_checks_on_the_main_thread_when_its_rule_is_off():
    g = ctx(main_thread_rule=False)
    assert decide(main("Bash", {"command": "ls ~"}), g) == DENY.shape
    assert decide(main("Bash", {"command": "vfg cmd 'op x'"}), g) == DENY.owner
    assert decide(main("Write", {"file_path": REPO + "/scripts/_probe/README.md"}), g) is None


async def test_the_hooks_output_shape_and_allowed_agent_calls_are_counted():
    g = ctx()
    hook = make_guard(g)
    base = {"session_id": "s", "transcript_path": "t", "cwd": REPO, "hook_event_name": "PreToolUse"}
    denied = await hook({**base, "tool_name": "Bash", "tool_input": {"command": "x"}, "tool_use_id": "u1"}, "u1", {"signal": None})
    assert denied == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": DENY.main_thread}}
    brief = {"subagent_type": "implementer", "description": "T9 tnt", "prompt": "p"}
    ok = await hook({**base, "tool_name": "Agent", "tool_input": brief, "tool_use_id": "u2"}, "u2", {"signal": None})
    # allowed: no permissionDecision, so the allow rules still decide; the brief gets the saved places (the fake vfg has none)
    assert ok == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "updatedInput": {**brief, "prompt": "p\n\nSAVED PLACES: none"}}}
    assert g.allowed == ["T9:u2"]


def test_a_symlinked_path_to_the_repo_counts_as_inside():
    real = temp_dir("vfg-guard-")
    os.makedirs(os.path.join(real, "scripts", "_lib"))
    link = real + "-link"
    os.symlink(real, link)
    g = ctx(repo=real)
    assert decide(impl("Read", {"file_path": link + "/scripts/x.md"}), g) is None
    assert decide(impl("Write", {"file_path": link + "/scripts/_lib/mc.py"}), g) == DENY.lib
    assert decide(impl("Grep", {"pattern": "x", "path": link}), g) is None


def test_helpers():
    assert refused_word("/Ban-IP x") == "ban-ip"
    assert refused_word("execute if entity @a run pardon x") == "pardon"
    assert refused_word("time set day") is None
    assert [c.words for c in split_shell('a \'b c\' "d \\"e\\"" f\\ g; h | i && j')] == [["a", "b c", 'd "e"', "f g"], ["h"], ["i"], ["j"]]
    assert check_bash("vfg cmd 'list' && vfg cmd 'data get entity Steve Pos'") is None
