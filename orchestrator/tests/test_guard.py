import json
import os
import re

import pytest
from helpers import temp_dir

from orchestrator.guard import (DENY, VFG_FORMS, VFG_USAGE, GuardContext, Redirect, check_bash, decide, make_guard,
                                refused_word, split_shell)

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


def usage(key):
    return "Refused: send it as %s." % VFG_USAGE[key]


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
    ("main-thread vfg script list", main("Bash", {"command": "vfg script list"}), None),
    ("main-thread vfg script list chained", main("Bash", {"command": "vfg script list; vfg state"}), DENY.main_thread),
    ("main-thread vfg script set", main("Bash", {"command": "vfg script set x cooldown=0"}), DENY.main_thread),
    ("main-thread vfg state", main("Bash", {"command": "vfg state"}), DENY.main_thread),
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
    ("chained after a good command", bash("vfg action run tnt_rain --task T7 && vfg cmd 'deop Steve'"), DENY.owner),
    ("after a semicolon", bash("sleep 2; vfg cmd 'stop'"), DENY.owner),
    ("a flag before the command", bash("vfg cmd --quiet 'op Steve'"), DENY.owner),
    ("an env prefix", bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner),
    ("a full path", bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner),
    ("command substitution", bash('vfg cmd "$(echo op) Steve"'), DENY.expansion),
    ("a variable", bash("vfg cmd $CMD"), DENY.expansion),
    ("a dollar in single quotes is literal", bash("vfg cmd 'say costs $5'"), None),
    ("stop inside a word", bash("vfg cmd 'say stop that'"), None),
    ("run inside JSON text", bash("""vfg cmd 'tellraw @a {"text":"run stop"}'"""), None),
    ("an action run is not a server command", bash("vfg action run anti_camp seconds=15 --task T7"), None),
    ("script stop is fine", bash("vfg script stop tnt_rain"), None),
    ("other programs get the shape reason", bash("curl https://example.com"), DENY.shape),
    ("ls outside the repository", bash("ls ~"), DENY.outside_bash),
    # shapes implementers tend to send
    ("seen: vfg cmd then echo $?", bash("""vfg cmd 'time set day'; echo "EXIT=$?\""""), DENY.variables),
    ("seen: give then echo", bash("""vfg cmd 'give Steve minecraft:diamond 1'; echo "EXIT_CODE=$?\""""), DENY.variables),
    ("seen: ps | grep; ls; find", bash("""ps -eo pid,etime,args | grep -i '[t]nt_rain'; echo "ps exit: $?"; ls -la /x/scripts/tnt_rain/ 2>&1; find /tmp -maxdepth 3 \\( -iname '*tnt_rain*' \\) 2>/dev/null | head -20"""), DENY.ps),
    ("seen: cd && ls; grep; sed", bash("""cd /x/repo/scripts && ls; ls _lib; grep -n "^def " _lib/mc.py; sed -n '/^def pos/,/^def /p' _lib/mc.py | head -30"""), DENY.cd),
    # the shapes that run
    ("sleep then logs", bash("sleep 5 && vfg script logs tnt_rain -n 20"), None),
    ("compile then run", bash("python3 -m py_compile made/main/scripts/torch_ring/main.py && vfg action run torch_ring --task T7"), None),
    ("deploy all", bash("vfg script deploy"), None),
    ("deploy one", bash("vfg script deploy torch_ring"), None),
    ("list", bash("vfg script list"), None),
    ("history", bash("vfg history --source ai --since -10m"), None),
    ("two commands joined by ;", bash("vfg cmd 'time set day'; vfg cmd 'weather clear'"), None),
    ("2>&1", bash("vfg cmd 'time set day' 2>&1"), None),
    ("to /dev/null", bash("vfg script stop tnt_rain > /dev/null"), None),
    ("a global vfg option", bash("vfg --timeout 30 cmd 'time set day'"),
     "Refused: vfg takes no option before its command: vfg cmd '<command>', vfg action run ..., as it is."),
    ("vfg say is not for implementers", bash("vfg say 'hi'"), "Refused: vfg say is not an implementer's. These are: %s." % VFG_FORMS),
    ("logs --follow never ends", bash("vfg script logs tnt_rain --follow"), DENY.follow),
    ("sleep with a variable", bash("sleep $N"), DENY.variables),
    ("a > inside quotes is no redirection", bash("""vfg cmd 'tellraw @a {"text":"a > b"}'"""), None),
    # saved places
    ("place save at coordinates", bash("vfg place save castle --at 197 68 251 --by T7"), None),
    ("place save where a player stands", bash("vfg place save home --player Steve --by T7"), None),
    ("place list", bash("vfg place list"), None),
    ("place list --json", bash("vfg place list --json"), None),
    ("place save with no name", bash("vfg place save"), usage("place")),
    ("place forget is not for implementers", bash("vfg place forget castle"), usage("place")),
    # the script library's commands
    ("script new from a script with presets", bash("vfg script new galick_ice --from galick_gun --preset replace_blocks"), None),
    ("script new then check", bash("vfg script new storm --preset lightning && vfg script check storm"), None),
    ("script new into the library", bash("vfg script new storm --library --preset lightning"), DENY.library_flag),
    ("script new with no name", bash("vfg script new"), usage("script new")),
    ("script set", bash("vfg script set galick-alex cooldown=0 crater_radius=30"), None),
    ("script set with no value", bash("vfg script set galick-alex"), usage("script set")),
    ("script save", bash("vfg script save galick-alex galick_no_cooldown"), None),
    ("script save with one name", bash("vfg script save galick-alex"), usage("script save")),
    ("script check everything", bash("vfg script check"), None),
    ("script check some", bash("vfg script check galick_ice crater"), None),
    # actions: the only way anything runs
    ("action run with values, a copy name and a task", bash("vfg action run spirit_bomb player=Steve as=bomb_steve --task T7"), None),
    ("action run with the task first", bash("vfg action run --task T7 castle at=home"), None),
    ("action run with --task=", bash("vfg action run castle at='197 68 251' --task=T12"), None),
    ("action run with no task", bash("vfg action run castle at=home"), DENY.action_task),
    ("action run with a task that is no task id", bash("vfg action run castle at=home --task castle"), DENY.action_task),
    ("action run with nothing to run", bash("vfg action run"), usage("action run")),
    ("action run, then its logs", bash("vfg action run railgun player=Alex --task T7 && sleep 3 && vfg script logs railgun -n 40"), None),
    ("action run of edit", bash("vfg action run edit copy=railgun_2 cooldown=0 --task T7"), None),
    ("action list", bash("vfg action list"), None),
    ("action list --json", bash("vfg action list --json | head -n 50"), None),
    ("action list of something", bash("vfg action list railgun"), usage("action list")),
    ("action check", bash("vfg action check tnt_crouch"), None),
    ("action check two", bash("vfg action check a b"), usage("action check")),
    ("action check nothing", bash("vfg action check"), usage("action check")),
    ("action refs is not an implementer's", bash("vfg action refs"), usage("action")),
    ("script run is gone", bash("vfg script run --task T7 spirit_bomb player=Steve as=bomb-steve"), DENY.script_run),
    ("script run is gone, piped", bash("vfg script run x 2>&1 | head -n 5"), DENY.script_run),
    ("the command action's commands are checked as vfg cmd is",
     bash("vfg action run command commands='op Steve' --task T7"), DENY.owner),
    ("a JSON list of commands", bash("""vfg action run command 'commands=["time set day","kick Alex"]' --task T7"""), DENY.owner),
    ("a comma list of commands", bash("vfg action run command 'commands=time set day,execute as @a run ban Bob' --task T7"), DENY.owner),
    ("a server-wide command in an action", bash("vfg action run command commands='difficulty peaceful' --task T7"),
     DENY.server_wide("difficulty")),
    ("an edit to a copy's command", bash("vfg script set crouch_rule on_command='op Steve'"), DENY.owner),
    ("an owner word in other text is fine", bash("vfg action run sign text='stop here, ban the creepers' --task T7"), None),
    ("the command action's plain commands", bash("vfg action run command commands='time set day' --task T7"), None),
    ("script list --fresh", bash("vfg script list --fresh"), None),
    ("script list --json --fresh", bash("vfg script list --json --fresh"), None),
    ("script list --all", bash("vfg script list --all"), usage("script list")),
    ("preset save", bash("vfg preset save galick_ice ice_path ice_trail"), None),
    ("preset save short", bash("vfg preset save galick_ice ice_path"), usage("preset")),
    ("preset list is not a command", bash("vfg preset list"), usage("preset")),
    ("state", bash("vfg state"), None),
    ("state --json", bash("vfg state --json"), None),
    ("state with other words", bash("vfg state Steve"), usage("state")),
    ("route is the runner's", bash("vfg route --task T7"), "Refused: vfg route is not an implementer's. These are: %s." % VFG_FORMS),
    ("prefix is the runner's", bash("vfg prefix"), "Refused: vfg prefix is not an implementer's. These are: %s." % VFG_FORMS),
    ("script with no op", bash("vfg script frobnicate x"),
     "Refused: vfg script takes new|set|stop|logs|list|check|save|deploy; a script runs through its action."),
    ("a server command after script new", bash("vfg script new x --preset crater; vfg cmd 'op Steve'"), DENY.owner),
    # the pilot's nine refusals (2026-10-09), each a turn: seven now run, two say what to send
    ("pilot: echo separators, ls in the repo, 2>/dev/null",
     bash("vfg script list; echo ---; vfg state; echo ---; ls %s/made/main/scripts/ 2>/dev/null; ls %s/scripts/ice_walk/ 2>/dev/null" % (REPO, REPO)), None),
    ("pilot: echo between looks", bash("vfg state; echo ---; vfg script list; echo ---; vfg script logs galick_gun_visual -n 15"), None),
    ("pilot: history through grep and tail",
     bash("""vfg history --events 2>&1 | grep -i -E "horde|king|boss" | tail -20; vfg history --source ai 2>&1 | grep -i -E "horde|king" | tail -10"""), None),
    ("pilot: script new through head", bash("vfg script new horde_king_music --from dragon_boss --preset theme_music 2>&1 | head -40"), None),
    ("pilot: >/dev/null 2>&1", bash("sleep 2; vfg cmd 'data get entity Steve active_effects'; vfg cmd 'effect give Steve minecraft:night_vision 0 0 0'  >/dev/null 2>&1; vfg cmd 'data get entity Alex active_effects'"), None),
    ("pilot: mkdir, then state through head", bash("mkdir -p made/main/scripts/hollow_purple_staff && vfg state 2>&1 | head -40"), DENY.mkdir("main")),
    ("pilot: grep a made script", bash("""grep -n 'replace_rules":\\|^for p in loop\\|^v = values' made/main/scripts/ice_beam_staff/main.py"""), None),
    ("pilot: a for loop", bash("""for y in 60 62 64; do echo -n "y$y: "; vfg cmd "execute if block 6 $y 21 air"; done; vfg cmd 'execute positioned 12 85 10 if block ~ ~-1 ~ air'"""), DENY.loop),
    ("pilot: new, ls and grep with 2>&1",
     bash("""vfg script new galick_gun_visual --from galick_gun 2>&1; ls %s/made/main/scripts/galick_gun_visual/ 2>&1; grep -n "crater(r.end\\|explosion(r.end" %s/made/main/scripts/galick_gun_visual/main.py""" % (REPO, REPO)), None),
    ("a check chained after a set", bash("vfg script set x cooldown=0 && sleep 3 && vfg script logs x -n 40"), None),
    # pipes: into head, tail and grep, with no file after them
    ("into head -n", bash("vfg state | head -n 20"), None),
    ("into tail -n", bash("vfg script logs x -n 80 | tail -n 20"), None),
    ("|& into grep", bash("vfg state |& grep Steve"), None),
    ("into head with a file", bash("vfg state | head -n 20 /etc/passwd"), DENY.pipe),
    ("into grep with a file", bash("vfg state | grep root /etc/passwd"), DENY.pipe),
    ("into sort", bash("vfg state | sort"), DENY.pipe),
    ("into python", bash("vfg state --json | python3 -m json.tool"), DENY.pipe),
    ("into sh", bash("echo 'vfg cmd op' | sh"), DENY.pipe),
    ("an owner word piped", bash("vfg cmd 'op Steve' 2>&1 | head -n 5"), DENY.owner),
    # redirections: 2>&1, >&2 and /dev/null only
    ("2>/dev/null", bash("vfg state 2>/dev/null"), None),
    ("&>/dev/null", bash("vfg script stop x &>/dev/null"), None),
    (">&2", bash("vfg state >&2"), None),
    ("1>&2", bash("vfg state 1>&2"), None),
    ("into a file", bash("vfg state > out.txt"), DENY.redirect),
    ("appended to a file", bash("vfg state >> /tmp/x"), DENY.redirect),
    ("out of a file", bash("vfg cmd 'list' < /etc/passwd"), DENY.redirect),
    ("a heredoc", bash("cat <<EOF\nop Steve\nEOF"), DENY.redirect),
    ("2>&3", bash("vfg state 2>&3"), DENY.redirect),
    (">& a file", bash("vfg state >&out.txt"), DENY.redirect),
    ("a redirection with nothing after it", bash("vfg state >"), DENY.redirect),
    # groups, the background, variables
    ("a lone & (the background)", bash("vfg action run x --task T7 &"), DENY.group),
    ("a subshell", bash("(vfg state; vfg script list)"), DENY.group),
    ("braces", bash("{ vfg state; }"), DENY.group),
    ("echo a variable", bash('echo "$HOME"'), DENY.variables),
    ("echo $?", bash("echo $?"), DENY.variables),
    ("backticks", bash("echo `id`"), DENY.variables),
    ("NAME=value first", bash("FOO=1 vfg state"), DENY.env),
    ("NAME=value before what would be refused anyway", bash("PYTHONPATH=/opt/vfg/scripts/_lib python3 -I -c 'import mc' 2>&1"), DENY.python),
    ("NAME=value alone", bash("FOO=1"), DENY.shape),
    ("while", bash("while true; do vfg state; done"), DENY.loop),
    ("if", bash("if vfg state; then echo ok; fi"), DENY.loop),
    # echo, ls, grep, head and tail on files in the repository
    ("echo alone", bash("echo"), None),
    ("echo words", bash("echo --- done ---"), None),
    ("ls here", bash("ls"), None),
    ("ls a made folder", bash("ls -la made/main/scripts"), None),
    ("ls /etc", bash("ls /etc"), DENY.outside_bash),
    ("ls up and out", bash("ls ../.."), DENY.outside_bash),
    ("ls out through made", bash("ls made/../../"), DENY.outside_bash),
    ("ls a glob outside", bash("ls /e*"), DENY.wild),
    ("grep -rn in the library", bash("grep -rn crater scripts/_presets"), None),
    ("grep -r with no path is the repository", bash("grep -r crater"), None),
    ("grep -e twice", bash("grep -e root -e crater scripts/README.md"), None),
    ("grep -A3 -n", bash("grep -A3 -n 'def ' made/main/scripts/x/main.py"), None),
    ("grep with no file", bash("grep crater"), DENY.grep_file),
    ("grep outside", bash("grep root /etc/passwd"), DENY.outside_bash),
    ("grep ~", bash("grep TYPESAFE ~/.vfg/config"), DENY.outside_bash),
    ("grep -f", bash("grep -f /etc/passwd scripts/README.md"), DENY.pattern_file),
    ("head a made script", bash("head -n 40 made/main/scripts/x/main.py"), None),
    ("tail -20 a file", bash("tail -20 scripts/README.md"), None),
    ("tail outside", bash("tail -n 20 /var/log/messages"), DENY.outside_bash),
    ("tail -f", bash("tail -f made/main/scripts/x/main.py"), DENY.tail_follow),
    ("tail -F piped", bash("vfg script logs x -n 5 | tail -F"), DENY.tail_follow),
    # what bash expands and the guard does not follow: ~name, braces, globs (outside quotes)
    ("head ~user", bash("head ~jm/.ssh/id_ed25519"), DENY.wild),
    ("ls ~+ (the working directory)", bash("ls ~+/.."), DENY.wild),
    ("ls ~- (the last one)", bash("ls ~-"), DENY.wild),
    ("tail a=~user", bash("tail -n 5 a=~jm"), DENY.wild),
    ("grep -r a brace out", bash("grep -r . {/var/home/jm/.vfg,x}"), DENY.wild),
    ("ls a sequence brace", bash("ls /{u..w}ar"), DENY.wild),
    ("grep -rn a brace in the repository", bash("grep -rn crater {scripts,made}"), DENY.wild),
    ("echo a glob out", bash("echo ~jm/.ssh/*"), DENY.wild),
    ("echo a glob in the repository", bash("echo made/main/scripts/*"), DENY.wild),
    ("ls ? and [ ]", bash("ls /?tc /[e]tc"), DENY.wild),
    ("ls a glob in the repository", bash("ls made/*/scripts"), DENY.wild),
    ("grep an unquoted pattern", bash("grep -n def.* scripts/README.md"), DENY.wild),
    ("a glob into a piped grep", bash("vfg state | grep /var/home/jm/.vfg/c*"), DENY.wild),
    ("py_compile a glob", bash("python3 -m py_compile ~jm/x.py"), DENY.wild),
    ("sleep a glob", bash("sleep /root/*"), DENY.wild),
    ("NAME=value before ls ~user", bash("FOO=1 ls ~jm"), DENY.wild),
    ("a quoted pattern", bash("grep -n 'def .*' scripts/README.md"), None),
    ('a double-quoted pattern', bash('grep -n "a*b{c,d}" made/main/scripts/x/main.py'), None),
    ("an escaped star is a file name", bash("head -n 5 made/main/scripts/x\\*"), None),
    ("a quoted ~name is a name in the repository", bash("ls '~jm'"), None),
    ("echo quoted", bash("echo '~jm/.ssh/*' \"{a,b}\""), None),
    ("ls ~/ is followed, and outside", bash("ls ~/.ssh"), DENY.outside_bash),
    # where the rest point
    ("py_compile outside the repository", bash("python3 -m py_compile ~/.vfg/config"), DENY.outside_bash),
    ("py_compile a made script", bash("python3 -m py_compile made/main/scripts/x/main.py"), None),
    ("python3 -c", bash("python3 -c 'print(1)'"), DENY.python),
    ("python3 a script", bash("python3 made/main/scripts/x/main.py"), DENY.python),
    ("cat", bash("cat scripts/README.md"), DENY.read),
    ("sed", bash("sed -n 1,20p scripts/README.md"), DENY.read),
    ("find", bash("find . -name main.py"), DENY.find),
    ("rm", bash("rm -rf made/main"), DENY.files("main")),
    ("cp", bash("cp scripts/zeus/main.py made/main/scripts/z/main.py"), DENY.files("main")),
    ("mkdir", bash("mkdir made/main/presets"), DENY.mkdir("main")),
    ("ps", bash("ps aux"), DENY.ps),
    ("kill", bash("kill 1234"), DENY.ps),
    # implementer files: only this server's folder, never the library
    ("Write a new script in made/", impl("Write", {"file_path": REPO + "/made/main/scripts/torch_ring/main.py"}), None),
    ("Edit a made script", impl("Edit", {"file_path": REPO + "/made/main/scripts/torch_ring/main.py"}), None),
    ("Write a made preset", impl("Write", {"file_path": REPO + "/made/main/presets/ice_trail.py"}), None),
    ("Write a made action (a variant)", impl("Write", {"file_path": REPO + "/made/main/actions/sniper_rail.py"}), None),
    ("Edit a made action", impl("Edit", {"file_path": "made/main/actions/tnt_crouch.py"}), None),
    ("Write a library action", impl("Write", {"file_path": REPO + "/actions/sniper_rail.py"}), DENY.library_actions("main")),
    ("Edit a library action", impl("Edit", {"file_path": REPO + "/actions/railgun.py"}), DENY.library_actions("main")),
    ("Edit the value types", impl("Edit", {"file_path": "actions/_types.py"}), DENY.library_actions("main")),
    ("Write another server's action", impl("Write", {"file_path": REPO + "/made/other/actions/x.py"}), DENY.made_only("main")),
    ("Edit a made script by a relative path", impl("Edit", {"file_path": "made/main/scripts/x/main.py"}), None),
    ("Write a new script in scripts/", impl("Write", {"file_path": REPO + "/scripts/torch_ring/main.py"}), DENY.library("main")),
    ("Edit a library script", impl("Edit", {"file_path": REPO + "/scripts/tnt_rain/main.py"}), DENY.library("main")),
    ("Edit a library preset", impl("Edit", {"file_path": REPO + "/scripts/_presets/crater.py"}), DENY.library("main")),
    ("Write scripts/README.md", impl("Write", {"file_path": REPO + "/scripts/README.md"}), DENY.library("main")),
    ("Write _lib", impl("Write", {"file_path": REPO + "/scripts/_lib/mc.py"}), DENY.library("main")),
    ("Edit _lib", impl("Edit", {"file_path": REPO + "/scripts/_lib/mc.py"}), DENY.library("main")),
    ("Edit the kit", impl("Edit", {"file_path": REPO + "/scripts/_lib/kit/area.py"}), DENY.library("main")),
    ("Write another server's folder", impl("Write", {"file_path": REPO + "/made/other/scripts/x/main.py"}), DENY.made_only("main")),
    ("Write made/ itself", impl("Write", {"file_path": REPO + "/made/x.py"}), DENY.made_only("main")),
    ("Write out of made/ with ..", impl("Write", {"file_path": REPO + "/made/main/../../scripts/x/main.py"}), DENY.library("main")),
    ("Write elsewhere in the repo", impl("Write", {"file_path": REPO + "/toolkit/vfg"}), DENY.made_only("main")),
    ("Write /tmp", impl("Write", {"file_path": "/tmp/x.py"}), DENY.made_only("main")),
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
    ("Write _lib by a relative path", impl("Write", {"file_path": "scripts/_lib/mc.py"}), DENY.library("main")),
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
    assert decide(impl("Bash", {"command": "vfg action run demon_lord --task T7"}), g) is None  # actions are not refused
    assert decide(impl("Bash", {"command": "vfg cmd 'op Steve'"}), g) == DENY.owner


def test_an_implementers_background_bash_is_refused_too():
    assert decide(impl("Bash", {"command": "sleep 30", "run_in_background": True}), ctx()) == DENY.background


def test_the_probe_implementer_checks_on_the_main_thread_when_its_rule_is_off():
    g = ctx(main_thread_rule=False)
    assert decide(main("Bash", {"command": "ls ~"}), g) == DENY.outside_bash
    assert decide(main("Bash", {"command": "curl x"}), g) == DENY.shape
    assert decide(main("Bash", {"command": "vfg cmd 'op x'"}), g) == DENY.owner
    assert decide(main("Write", {"file_path": REPO + "/made/main/scripts/_probe/README.md"}), g) is None
    assert decide(main("Write", {"file_path": REPO + "/scripts/_probe/README.md"}), g) == DENY.library("main")


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
    assert decide(impl("Write", {"file_path": link + "/scripts/_lib/mc.py"}), g) == DENY.library("main")
    assert decide(impl("Write", {"file_path": link + "/made/main/scripts/x/main.py"}), g) is None
    assert decide(impl("Grep", {"pattern": "x", "path": link}), g) is None


def test_helpers():
    assert refused_word("/Ban-IP x") == "ban-ip"
    assert refused_word("execute if entity @a run pardon x") == "pardon"
    assert refused_word("time set day") is None
    assert [c.words for c in split_shell('a \'b c\' "d \\"e\\"" f\\ g; h | i && j')] == [["a", "b c", 'd "e"', "f g"], ["h"], ["i"], ["j"]]
    assert check_bash("vfg cmd 'list' && vfg cmd 'data get entity Steve Pos'") is None


def test_split_shell_keeps_redirections_and_pipes():
    [a, b, c] = split_shell("vfg history --events 2>&1 | grep -i 'a|b' | tail -20; echo x >/dev/null")[:3]
    assert a.words == ["vfg", "history", "--events"] and a.redirects == [Redirect("2", ">&", "1")] and not a.piped
    assert b.words == ["grep", "-i", "a|b"] and b.piped
    assert c.words == ["tail", "-20"] and c.piped
    [d] = split_shell("vfg state &> /dev/null")
    assert d.redirects == [Redirect("", "&>", "/dev/null")] and d.redirects[0].harmless()
    [e] = split_shell("vfg cmd 'say a > b' 2>/dev/null")
    assert e.words == ["vfg", "cmd", "say a > b"] and e.redirects == [Redirect("2", ">", "/dev/null")]
    [f] = split_shell("echo '2'>x")  # a quoted number is a word, not an fd
    assert f.words == ["echo", "2"] and f.redirects == [Redirect("", ">", "x")] and not f.redirects[0].harmless()
    [w] = split_shell("ls 'a*' b\\* c* ~ ~/x ~jm x~ {a,b} '{a,b}' {a} a=~ ? [x] \"~jm\" ~'jm'")
    assert w.words[1:] == ["a*", "b*", "c*", "~", "~/x", "~jm", "x~", "{a,b}", "{a,b}", "{a}", "a=~", "?", "[x]",
                           "~jm", "~jm"]
    assert w.wild == [False, False, False, True, False, False, True, True, True, False, False, True, True, True,
                      False, True]
    [g, h] = split_shell("vfg state && vfg script list || echo no")[1:]
    assert not g.piped and not h.piped
    assert [x.grouped for x in split_shell("vfg script run x & vfg state")] == [True, False]


def test_every_refusal_of_an_implementers_bash_is_one_line_saying_what_to_send():
    commands = ["curl x", "ls ~", "echo $?", "for i in 1; do vfg state; done", "vfg state > f", "vfg state | sort",
                "FOO=1 vfg state", "cd x", "cat x", "find .", "rm x", "ps", "python3 -c 1", "grep x", "grep -f a b",
                "tail -f x", "mkdir x", "vfg say hi", "vfg script set x", "vfg --x cmd y", "vfg script logs x --follow",
                "vfg script new x --library", "(vfg state)", "vfg cmd 'op Steve'", "vfg cmd $(echo hi)",
                "vfg cmd 'difficulty hard'", "vfg script run x", "vfg action run x", "vfg action refs",
                "vfg action run command commands='op Steve' --task T7",
                "head ~jm/.ssh/x", "grep -r . {/a,b}", "echo ~jm/.ssh/*"]
    for command in commands:
        reason = check_bash(command, REPO)
        assert reason and "\n" not in reason, command
        # the probe and live_box find a refusal by these words
        assert re.search(r"refused|owner.s|never ends|leave it out|literally|may not", reason, re.I), (command, reason)


# ---------------------------------------------------------------- a script a running instance uses

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


def made_repo() -> str:
    repo = temp_dir("vfg-guard-")
    for name in ("beam", "staff"):
        os.makedirs(os.path.join(repo, "made", "main", "scripts", name))
        with open(os.path.join(repo, "made", "main", "scripts", name, "main.py"), "w") as f:
            f.write('"""x"""\n')
    os.makedirs(os.path.join(repo, "made", "main", "presets"))
    os.makedirs(os.path.join(repo, "scripts", "zeus"))
    return repo


BEAM_ALEX = {"instance": "beam-alex", "script": "beam", "source": "made", "task": "T3"}


async def call_hook(g, tool_name, tool_input, agent="a1"):
    hook = make_guard(g)
    out = await hook({"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input,
                      "agent_id": agent, "tool_use_id": "u"}, "u", {"signal": None})
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason") if out else None


async def test_an_edit_to_a_made_script_another_tasks_instance_runs_is_refused_with_how_to_copy_it():
    repo = made_repo()
    running = Running([BEAM_ALEX])
    g = ctx(repo=repo, running=running, task_of=lambda agent: "T7")
    beam = os.path.join(repo, "made/main/scripts/beam/main.py")
    reason = await call_hook(g, "Edit", {"file_path": beam, "old_string": "a", "new_string": "b"})
    assert reason == DENY.running("beam", BEAM_ALEX, "main")
    assert reason == ("Refused: beam-alex (T3) runs made/main/scripts/beam/, and code a running copy uses is not "
                      "changed: vfg script new <new_name> --from beam copies it, with its action, for you to Edit; then "
                      "vfg script stop beam-alex and run the new action.")
    assert await call_hook(g, "Write", {"file_path": "made/main/scripts/beam/main.py", "content": ""}) == reason
    # another script, not running: let through, and not asked again for it
    asked = running.calls
    staff = os.path.join(repo, "made/main/scripts/staff/main.py")
    assert await call_hook(g, "Edit", {"file_path": staff}) is None
    assert await call_hook(g, "Edit", {"file_path": staff}) is None
    assert running.calls == asked + 1
    # a preset, a new script and the library are not this check's
    assert await call_hook(g, "Write", {"file_path": os.path.join(repo, "made/main/presets/x.py")}) is None
    assert await call_hook(g, "Write", {"file_path": os.path.join(repo, "made/main/scripts/fresh/main.py")}) is None
    assert await call_hook(g, "Edit", {"file_path": os.path.join(repo, "scripts/zeus/main.py")}) == DENY.library("main")
    assert running.calls == asked + 1


async def test_the_task_that_started_the_instance_may_fix_its_own_script():
    repo = made_repo()
    g = ctx(repo=repo, running=Running([BEAM_ALEX]), task_of=lambda agent: "T3")
    assert await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/beam/main.py")}) is None
    # an implementer whose task is not known is held to the rule
    g = ctx(repo=repo, running=Running([BEAM_ALEX]))
    assert (await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/beam/main.py")})
            == DENY.running("beam", BEAM_ALEX, "main"))


async def test_a_script_the_implementer_made_itself_is_its_own_to_change_while_it_runs():
    repo = made_repo()
    running = Running([{"instance": "beam2", "script": "beam2", "task": None},
                       {"instance": "fast", "script": "beam_fast", "task": "T1"}])
    g = ctx(repo=repo, running=running, task_of=lambda agent: "T7")
    assert await call_hook(g, "Bash", {"command": "vfg script new beam2 --from beam --preset crater 2>&1 | head -n 5"}) is None
    assert await call_hook(g, "Bash", {"command": "vfg script save beam-alex beam_fast"}) is None
    for name in ("beam2", "beam_fast"):  # what the commands made
        os.makedirs(os.path.join(repo, "made", "main", "scripts", name))
        open(os.path.join(repo, "made", "main", "scripts", name, "main.py"), "w").close()
    for name in ("beam2", "beam_fast"):
        assert await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/%s/main.py" % name)}) is None
    assert running.calls == 0
    # not another implementer's, though; nor a name that was taken when it asked
    assert (await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/beam2/main.py")}, agent="a2")
            == DENY.running("beam2", running.instances[0], "main"))
    assert await call_hook(g, "Bash", {"command": "vfg script new beam --from staff"}, agent="a3") is None
    running.instances = [BEAM_ALEX]
    assert (await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/beam/main.py")}, agent="a3")
            == DENY.running("beam", BEAM_ALEX, "main"))


async def test_when_what_runs_cannot_be_told_the_edit_goes_through():
    repo = made_repo()
    beam = os.path.join(repo, "made/main/scripts/beam/main.py")
    for answer in (None, RuntimeError("no network")):
        g = ctx(repo=repo, running=Running(answer), task_of=lambda agent: "T7")
        assert await call_hook(g, "Edit", {"file_path": beam}) is None
    assert await call_hook(ctx(repo=repo), "Edit", {"file_path": beam}) is None  # no check at all


async def test_running_copies_reads_running_json_and_asks_nothing():
    from helpers import use_fake_vfg, write
    from orchestrator import guard
    d = temp_dir()
    fake = use_fake_vfg(d)
    write(os.path.join(d, "running.json"), json.dumps({"fetched_at": 1, "copies": {
        "beam-alex": {"action": "beam", "script": "beam", "values": {}, "task": "T3"}, "junk": "x"}}))
    running = guard.running_copies(d)
    assert await running() == [{"action": "beam", "script": "beam", "values": {}, "task": "T3", "instance": "beam-alex",
                                "copy": "beam-alex"}]
    assert fake.calls() == []
    # and the guard refuses an edit to the script another task's copy runs, by its copy's name
    repo = made_repo()
    g = ctx(repo=repo, running=running, task_of=lambda agent: "T7")
    reason = await call_hook(g, "Edit", {"file_path": os.path.join(repo, "made/main/scripts/beam/main.py")})
    assert reason == ("Refused: beam-alex (T3) runs made/main/scripts/beam/, and code a running copy uses is not "
                      "changed: vfg script new <new_name> --from beam copies it, with its action, for you to Edit; then "
                      "vfg script stop beam-alex and run the new action.")


async def test_running_copies_without_running_json_reads_vfg_state_and_keeps_it_three_seconds(monkeypatch):
    from helpers import use_fake_vfg, write
    from orchestrator import guard
    d = temp_dir()
    fake = use_fake_vfg(d)
    os.environ["VFG_FAKE_STATE"] = os.path.join(d, "state.json")
    write(os.environ["VFG_FAKE_STATE"], json.dumps({"online": [], "instances": [BEAM_ALEX, "junk"], "rcon": None}))
    running = guard.running_copies(d)
    assert await running() == [BEAM_ALEX]
    assert await running() == [BEAM_ALEX]
    assert fake.calls() == [["state", "--json"]]
    monkeypatch.setattr(guard, "STATE_CACHE_S", 0)
    os.environ["VFG_FAKE_STATE_FAIL"] = "EC2 unreachable (fake)"
    assert await running() is None
