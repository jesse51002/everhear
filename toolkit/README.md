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
vfg script new galick_ice --from galick_gun --preset replace_blocks   # into made/<VFG_SERVER>/scripts/
vfg script check                               # everything; or: vfg script check galick_ice crater
vfg script deploy                              # or: vfg script deploy castle
vfg script run spirit_bomb player=Steve        # values checked here first; deploys it and _lib first
vfg script run galick_gun player=Alex cooldown=0 as=galick-alex --task T5   # a second instance
vfg script run hello --wait                    # waits up to 30 s for it to end; --wait 10 or --wait=10
vfg script run zeus Steve                      # a seed script takes its words as they are
vfg script set galick-alex cooldown=0          # live: read again within 0.5 s; any other value restarts it
vfg script save galick-alex galick_no_cooldown # its values now as the defaults, into made/<VFG_SERVER>/
vfg script logs galick-alex -n 40              # --follow only under Monitor or by a person
vfg script list                                # every instance, its script, task and changed values
vfg script stop galick-alex
vfg preset save galick_ice ice_path ice_trail  # a function lifted out into made/<VFG_SERVER>/presets/
vfg state                                      # who is online and where, time, weather, what runs; --json
vfg usage add --component conversation --model sonnet --in 10 --out 5 --cost 0.001 --ref ab12cd34
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2
unreachable or no answer in time, 5 Minecraft not answering RCON. A failure prints
one line on stderr. `vfg --help` and `vfg <command> --help` list every option.

## Scripts, values and instances

`lib/library.py` reads the library (`scripts/<name>/main.py`, `scripts/_presets/<name>.py`)
and this server's folder (`made/<VFG_SERVER>/scripts/` and `presets/`, gitignored) on the PC;
the router reads them through it too. A name is a script in one place only: `new`, `save`
and `preset save` refuse one that is taken in either.

- **Values are checked on the PC before anything is sent**: an unknown key, a bad enum or
  a word that is not `key=value` exits 2; a number out of range is clamped and the output
  says so. A seed script (a `README.md` and no `VALUES`) takes its words as they are.
- **An instance** is one run: the script's name, or `as=<instance>`, each with its own tag
  `vfg_<instance>`. On the box, `/opt/vfg/run/<instance>.json` says what was started
  (script, source, the command's values, task, started) and `<instance>.values.json` holds
  what `set` changed while it runs; `kit.values` reads it again every `VFG_LIVE_POLL_S`.
- **`set`** writes values marked `live` into that file; any other value stops the instance
  and starts it again with its other values and the file kept. **`save`** writes the
  command's values and the live ones as the new script's defaults.
- **`check`** exits 1 with one line per problem: `VALUES` not a literal, a default outside
  its limits, an unknown type, a value name twice, `v["key"]` read but not declared, a
  preset's value or helper without its name in front, no first-line docstring, no `ASKS`,
  an import from `_presets` or of what the box lacks, syntax Python 3.9 cannot read. A seed
  is checked for its README's first line and its imports only.
- **`state`** is one SSH call: `list`, each player's `Pos` and `Dimension`, `time query`,
  and the weather from an inline `weather_check` predicate, with every running instance and
  who it touches (its `player` values).

## Tests

```sh
python3 -m unittest discover -s toolkit/tests
uv run --python 3.9 --no-project python -m unittest discover -s toolkit/tests
```

No EC2 and no network: `tests/fake_ssh` runs the remote command locally,
`fake_rcon.py` speaks RCON on localhost, `fake_vfg_feed.py` stands in for the
bridge's writer (and `TestRealFeedWriter` uses `../bridge/vfg_feed.py` when it is
there), and `tests/bin/` holds fake `systemctl`, `sudo` and `journalctl`. The script
tests use a stand-in kit with `values()` only; `TestRealLibrary` reads the repo's own
examples and presets, when there are any, and copies every preset into every example. Two
environment knobs exist for the tests only: `VFG_WATCH_SILENCE_S` (35) and
`VFG_WATCH_UNREACHABLE_S` (60).
