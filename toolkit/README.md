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
vfg script new galick_ice --from galick_gun --preset replace_blocks   # into made/<VFG_SERVER>/scripts/
vfg script check                               # everything; or: vfg script check galick_ice crater
vfg script deploy                              # or: vfg script deploy castle
vfg script run spirit_bomb player=Steve        # values checked here first; deploys it and _lib first
vfg script run galick_gun player=Alex cooldown=0 as=galick-alex --task T5   # a second instance
vfg script run hello --wait                    # waits up to 30 s for it to end; --wait 10 or --wait=10
vfg script run zeus player=Steve               # every script takes key=value, checked against its VALUES
vfg script set galick-alex cooldown=0          # live: read again within 0.5 s; any other value restarts it
vfg script save galick-alex galick_no_cooldown # its values now as the defaults, into made/<VFG_SERVER>/
vfg script logs galick-alex -n 40              # --follow only under Monitor or by a person
vfg script list                                # every instance, its script, task and changed values
vfg script stop galick-alex
vfg preset save galick_ice ice_path ice_trail  # a function lifted out into made/<VFG_SERVER>/presets/
vfg state                                      # who is online and where, time, weather, what runs; --json
vfg route --task T7 < brief.txt                # the examples and presets for that brief, picked by Jev; JSON
vfg prefix                                     # the implementer's static system prompt, the same bytes each time
vfg action run command --args '{"commands": ["time set day"]}' --task T7   # one JSON line {ok, summary, …}
vfg action run teleport --args '{"player": "Steve", "to": "place", "target": "castle"}' --task T7 --timeout 40
vfg action refs --version                      # the server's version, from server.jar: 26.3
vfg action refs                                # {"version", "commands"}: the data generator's command tree
vfg place save castle --player Steve --by T7   # saved castle at 197 68 251 in overworld
vfg place save home --at -368 70 912           # --dim the_nether or the_end; overworld by default
vfg place list                                 # castle 197 68 251 overworld (T7), one per line, or none; --json, --fresh
vfg place forget castle                        # forgot castle; exit 1 if there was none
vfg usage add --component conversation --model sonnet --in 10 --out 5 --cost 0.001 --ref ab12cd34
vfg usage add --component implementer --model haiku --in 900 --out 120 --cache-read 15000 --cache-write 2000
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
`vfg place list` answers from a cache, `$VFG_HOME/places.json` (mode 600), while it is
younger than `VFG_PLACES_TTL_S` (30 s), else asks the server and rewrites it; `--fresh`
always asks. `vfg place save` and `forget` write their entry through as the server confirms
it, keeping the cache's age. When the server cannot be asked and a cache exists, however
old, `list` prints it, warns `saved places from <age> s ago: <why>` and exits 0.

## Scripts, values and instances

`lib/library.py` reads the library (`scripts/<name>/main.py`, `scripts/_presets/<name>.py`)
and this server's folder (`made/<VFG_SERVER>/scripts/` and `presets/`, gitignored) on the PC;
the router reads them through it too. A name is a script in one place only: `new`, `save`
and `preset save` refuse one that is taken in either.

- **Values are checked on the PC before anything is sent**: an unknown key, a bad enum or
  a word that is not `key=value` exits 2; a number out of range is clamped and the output
  says so. A script written the old way (a `README.md` beside it and no `VALUES`) is
  handed its words as they are.
- **An instance** is one run: the script's name, or `as=<instance>`, each with its own tag
  `vfg_<instance>`. On the box, `/opt/vfg/run/<instance>.json` says what was started
  (script, source, the command's values, task, started) and `<instance>.values.json` holds
  what `set` changed while it runs; `kit.values` reads it again every `VFG_LIVE_POLL_S`.
- **`set`** writes values marked `live` into that file; any other value stops the instance
  and starts it again with its other values and the file kept. **`save`** writes the
  command's values and the live ones as the new script's defaults.
- **`check`** exits 1 with one line per problem: `VALUES` not a literal, a default outside
  its limits, an unknown type, a value name twice, `v["key"]` read but not declared, a
  preset's value or helper without its name in front, a preset's value that starts with
  another preset's name (the two in one script would ask for one value), no first-line
  docstring, no `ASKS`, an import from `_presets` or of what the box lacks, syntax Python 3.9
  cannot read. An old-style library script (a `README.md`, no `VALUES`) is checked for its
  README's first line and its imports only.
- **`set`** on an instance this PC started is one SSH call: `run` keeps what it started in
  `~/.vfg/instances.json`, and the box checks the instance still runs that script before it
  changes anything; otherwise it asks the box first, a second call.
- **`state`** is one SSH call: `list`, each player's `Pos` and `Dimension`, `time query day`
  (26.3's day timeline) and `time query time` (the clock, for the day number), and the
  weather from an inline predicate, `{"type":"minecraft:weather_check","raining":true}`,
  which follows `weather …` within a few seconds, as the rain players see fades in. Then
  every running instance and who it touches (its `player` values).

## The router and the implementer's prompt

- **`vfg route --task T7 [--ref R] < brief`** reads the implementer's brief on stdin, and the
  task's text and context from the orchestrator's `state.json` or `inbox.jsonl`. It makes one
  card per example script and preset, ours and `made/<VFG_SERVER>/`'s, and asks Jev about all
  of them in one call through `remote/jev.py` on the box: `runs_it`, `close` or `no` per
  script, `use` or `no` per preset. It prints JSON on stdout: `{"task", "scripts":
  [{"name", "source", "verdict", "p_runs_it", "p_close", "path"}], "presets": [{"name",
  "source", "p_use", "path"}], "fallback", "ms", "tokens"}`, with a log line on stderr.
- **Picks:** every script whose `runs_it` and `close` reach `VFG_ROUTE_LOAD` (0.3), those that
  run the job first, and every preset whose `use` reaches it that no loaded script holds.
  Past `VFG_ROUTE_TIMEOUT_S` (3), or on any error, it picks by words instead: every card
  sharing a word with the job (`"fallback": true`). It writes one usage row, `--component
  jev --ref T7`, in the background.
- **`vfg prefix`** prints the part of an implementer's system prompt that never changes
  between spawns, so the provider reads it from the cache: `scripts/README.md`, the kit in
  full (`_lib/mc.py`, every file in `_lib/kit/`), then the first line and `VALUES` of every
  library preset and example script, by name. Nothing from `made/`, no date, no count.
- **`--cache-read N` and `--cache-write N`** on `vfg usage add` are the input tokens read from
  and written to the prompt cache, in two columns of `usage` that the bridge adds on start;
  until it has, the row goes in without them and one line on stderr says so.

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
`../scripts/_test/fake_places_world.py`, a small world speaking Minecraft 26.3's recorded
replies; a fake `java` stands in for the data generator. The script tests use a stand-in
kit with `values()` only; `TestRealLibrary` reads the repo's own examples and presets,
when there are any, and copies every preset into every example. Two environment knobs
exist for the tests only: `VFG_WATCH_SILENCE_S` (35) and `VFG_WATCH_UNREACHABLE_S` (60).
