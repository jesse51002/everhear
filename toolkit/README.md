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
vfg orch start                                 # prints "running pid N" if it already is
vfg orch status                                # running/stopped, then the open tasks
vfg orch send 'build a castle at spawn' --context 'Steve asked (#812)'
vfg orch reply T7 done 'castle built at spawn'
vfg orch stop
vfg feed post SYS '' 'smoke'                   # low level; prints the seq
vfg script deploy                              # or: vfg script deploy castle
vfg script run hello --wait                    # deploys hello and _lib first; waits up to 30 s for it to end
vfg script run hello --wait 10                 # or --wait=10; without --wait it is checked after 2 s
vfg script run demon Steve                     # detached; refuses if it is running
vfg script logs demon -n 40                    # --follow only under Monitor or by a person
vfg script list
vfg script stop demon
vfg usage add --component conversation --model sonnet --in 10 --out 5 --cost 0.001 --ref ab12cd34
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2
unreachable or no answer in time, 5 Minecraft not answering RCON. A failure prints
one line on stderr. `vfg --help` and `vfg <command> --help` list every option.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests
uv run --python 3.9 --no-project python -m unittest discover -s toolkit/tests
```

No EC2 and no network: `tests/fake_ssh` runs the remote command locally,
`fake_rcon.py` speaks RCON on localhost, `fake_vfg_feed.py` stands in for the
bridge's writer (and `TestRealFeedWriter` uses `../bridge/vfg_feed.py` when it is
there), and `tests/bin/` holds fake `systemctl`, `sudo` and `journalctl`. Two
environment knobs exist for the tests only: `VFG_WATCH_SILENCE_S` (35) and
`VFG_WATCH_UNREACHABLE_S` (60).
