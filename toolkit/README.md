# vfg toolkit

The one command every agent on the owner's PC uses to reach the EC2 box. Python 3.9+,
standard library only.

## Install

```sh
toolkit/install.sh        # from the repo's root
```

It links `~/.local/bin/vfg` to `toolkit/vfg`, makes `~/.vfg` (mode 700) and copies
`config.example` to `~/.vfg/config` if there is none. Fill `VFG_OWNER`, `VFG_SSH_HOST`
and `VFG_SSH_KEY` there, then run `vfg up`. `config.example` lists every setting the
repo reads, with its default. Nothing is deployed to EC2: the programs in `remote/`
travel with each call. `VFG_REPO` defaults to the repo `toolkit/` is in.

## One example per command

```sh
vfg up                                         # units started if down; RCON answering
vfg watch                                      # the feed from the cursor, then follow (under Monitor)
vfg watch --tail 20                            # for a person at a terminal
vfg say 'on it — castle at spawn'              # tellraw @a with the aqua [AI] prefix, stored as an ai line
vfg say 'your base is ready' --to Steve        # a whisper, stored as "(to Steve) …"
vfg cmd 'tp @a ~ ~10 ~'                        # one RCON command; single-quote ~ and JSON
vfg history --since -10m --player Steve        # lines; also --class, --source, --until, --limit
vfg history --events --since -30m              # game events
vfg history --feed --limit 20                  # the feed's stored lines
vfg watchers set join,leave,death,server       # replaces watch.json; --players a,b --contains REGEX
vfg watchers show
vfg lookout set 'requests to build or fight'   # or: vfg lookout set - < lookout.txt
vfg lookout show
vfg orch start                                 # the dispatcher and the runner; "running pid N" for one already up
vfg orch status                                # each running/stopped, the open tasks, those the orchestrator holds
vfg orch send 'build a castle at spawn' --context 'Steve asked (#812)'   # to the dispatcher; prints T7
vfg orch hand '[{"id": "T7", "at": "…", "task": "build a castle", "context": ""}]'   # the dispatcher's
vfg orch reply T7 done 'castle built at spawn'
vfg orch stop                                  # the dispatcher first (it hands on what it holds), then the runner
vfg feed post SYS '' 'smoke'                   # low level; prints the seq
vfg script deploy                              # or: vfg script deploy castle
vfg script run hello --wait                    # deploys hello and _lib first; waits up to 30 s for it to end
vfg script run hello --wait 10                 # or --wait=10; without --wait it is checked after 2 s
vfg script run demon Steve                     # detached; refuses if it is running
vfg script logs demon -n 40                    # --follow only under Monitor or by a person
vfg script list
vfg script stop demon
vfg action run command --args '{"commands": ["time set day"]}' --task T7   # one JSON line {ok, summary, …}
vfg action run teleport --args '{"player": "Steve", "to": "place", "target": "castle"}' --task T7 --timeout 40
vfg action refs --version                      # the server's version, from server.jar: 26.3
vfg action refs                                # {"version", "commands"}: the data generator's command tree
vfg place save castle --player Steve --by T7   # saved castle at 197 68 251 in overworld
vfg place save home --at -368 70 912           # --dim the_nether or the_end; overworld by default
vfg place list                                 # castle 197 68 251 overworld (T7), one per line, or none; --json
vfg place forget castle                        # forgot castle; exit 1 if there was none
vfg usage add --component conversation --model sonnet --in 10 --out 5 --cost 0.001 --ref ab12cd34
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2
unreachable or no answer in time, 5 Minecraft not answering RCON. A failure prints
one line on stderr. `vfg --help` and `vfg <command> --help` list every option.

## The dispatcher's commands

`vfg orch send` appends to the dispatcher's inbox, `$VFG_DISPATCH_DIR/inbox.jsonl`
(default `~/.vfg/dispatch`), with an id one past the highest in it and in the
orchestrator's inbox, so ids never repeat. What the dispatcher cannot do quickly it
gives the orchestrator with `vfg orch hand`: every line in one write to
`~/.vfg/orch/inbox.jsonl`, an id already there skipped, so a retry never doubles a
task. `vfg orch start`, `stop` and `status` run both processes: the dispatcher's log is
`dispatcher.log` beside its inbox, and `stop` waits for it before stopping the runner.

`vfg action run <name>` runs `actions/<name>/handler.py` on EC2 through
`remote/action.py`, as a module with `remote/places.py` beside it and
`/opt/vfg/scripts/_lib` (`vfg script deploy`) on its path; `command` has no handler
and runs its commands over RCON, each checked as `vfg cmd` checks one. It prints one
JSON line, `{"ok": …, "summary": …}` and the action's facts, and exits 0 whenever the
action ran, even when `ok` is false; it exits 124 when `--timeout` (30 s) runs out,
and otherwise as above. `vfg action refs` runs the server jar's data generator in a
temp folder under `/tmp` on EC2 (about 10 s; 300 s at most) and prints its
`commands.json`, about 190 KB, with the version. `vfg place` keeps places in
Minecraft's command storage `everhear:places`, through the same `remote/places.py`.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests
uv run --python 3.9 --no-project python -m unittest discover -s toolkit/tests
```

No EC2 and no network: `tests/fake_ssh` runs the remote command locally,
`fake_rcon.py` speaks RCON on localhost, `fake_vfg_feed.py` stands in for the
bridge's writer (and `TestRealFeedWriter` uses `../bridge/vfg_feed.py` when it is
there), and `tests/bin/` holds fake `systemctl`, `sudo` and `journalctl`. The place
tests, and a run of the real `teleport` handler, answer RCON with
`../scripts/_test/fake_world.py`, a small world speaking Minecraft 26.3's recorded
replies; a fake `java` stands in for the data generator. Two environment knobs exist
for the tests only: `VFG_WATCH_SILENCE_S` (35) and `VFG_WATCH_UNREACHABLE_S` (60).
