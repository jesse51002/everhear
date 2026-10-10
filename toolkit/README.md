# vfg toolkit

The one command every agent on the owner's PC uses to reach the EC2 box. It runs under `uv`
(its first lines say so, PEP 723) on Python 3.12 with pydantic, which the actions need; a
command that does not load them starts as fast as before. Run as `python3 vfg` where pydantic
is missing, it starts itself again under `uv`. On the box, every program runs on the box's
Python 3.12, `/opt/vfg/python/bin/python3`.

## Install

```sh
toolkit/install.sh        # from the repo's root
```

It links `~/.local/bin/vfg` to `toolkit/vfg`, makes `~/.vfg` (mode 700) and copies
`config.example` to `~/.vfg/config` if there is none. Fill `VFG_OWNER`, `VFG_SSH_HOST`
and `VFG_SSH_KEY` there, then run `vfg up`. `config.example` lists every setting the
repo reads, with its default. Nothing is deployed to EC2 for vfg itself: the programs in
`remote/` travel with each call, with the libraries they use from `scripts/_lib/`
(`instances.py`, `places.py`). `VFG_REPO` defaults to the repo `toolkit/` is in.

## One example per command

```sh
vfg up                                         # units started if down; RCON answering
vfg watch                                      # the feed from the cursor, then follow (under Monitor)
vfg watch --tail 20                            # for a person at a terminal
vfg say 'on it — castle at spawn'              # tellraw @a with the aqua [AI] prefix, stored as an ai line
vfg say 'your base is ready' --to Steve        # a whisper, stored as "(to Steve) …"
vfg cmd 'tp @a ~ ~10 ~'                        # the command action, one command; single-quote ~ and JSON
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
vfg action run railgun player=Steve --task T5  # one JSON line; the copy is "railgun"
vfg action run railgun player=Alex cooldown=0 --task T6   # a second copy, "railgun_2"
vfg action run railgun player=Alex as=alex_gun # a copy named as given (refused while one runs)
vfg action run edit copy=railgun_2 cooldown=5  # what vfg script set runs
vfg action list                                # every action, ours and the server's: name — its line; --json
vfg action check tnt_crouch                    # what is wrong with it, or ok
vfg script new galick_ice --from galick_gun --preset replace_blocks   # the script and its action, made/<VFG_SERVER>/
vfg script check                               # everything; or: vfg script check galick_ice crater
vfg script deploy                              # or: vfg script deploy castle
vfg script set railgun_2 cooldown=0            # the edit action: live in 0.5 s, any other value restarts it
vfg script stop railgun_2                      # the stop action: everything it made goes
vfg script list                                # the running copies, from ~/.vfg/running.json; --json, --fresh
vfg script logs railgun_2 -n 40                # --follow only under Monitor or by a person
vfg script save railgun_2 rail_no_cooldown     # a script with VALUES: its values now as the defaults
vfg preset save galick_ice ice_path ice_trail  # a function lifted out into made/<VFG_SERVER>/presets/
vfg made save                                  # made/<VFG_SERVER>/ into $VFG_MADE_ARCHIVE/<today>/
vfg made restore 2026-10-09                    # and back
vfg state                                      # who is online and where, time, weather, what runs; --json
vfg route --task T7 < brief.txt                # the examples and presets for that brief, picked by Jev; JSON
vfg prefix                                     # the implementer's static system prompt, the same bytes each time
vfg action refs --version                      # the server's version, from server.jar: 26.3
vfg action refs                                # {"version", "commands"}: the data generator's command tree
vfg place save castle --player Steve --by T7   # the save_place action: saved castle at 197 68 251 in overworld
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

`vfg action run <action> key=value … [as=<copy>] [--task T7]` loads the actions
(`lib/py/vfg_actions.py`, the one loader), checks the values against the action's class (an
unknown or fixed key, or a value its field refuses, exits 2; a number out of range is clamped
and a note says so; a saved place against the places cache, fetched again only when a place
check fails on a stale one), then makes one SSH call: `_lib` and the action's script deployed,
a copy started, waited for. It prints ONE JSON line, `{"ok", "copy", "action", "running",
"summary", "facts", "notes", "error"?}`, whatever happens, and exits 0 when ok. A once action
is waited for up to its `timeout_s`, else `VFG_DISPATCH_ACTION_TIMEOUT_S` (20 s), and answers
with its script's `result()`; past it, `"running": true` and "still running as <copy>": it is
never run again. An until-stopped action answers with its first `result()`, or "running as
<copy>" after a 2 s check. A script that exits non-zero is `"ok": false`, its error its last
words. `vfg cmd`, `vfg script set`, `vfg script stop` and `vfg place save` are shortcuts for
the `command`, `edit`, `stop` and `save_place` actions and print their summaries. `vfg action refs` runs the server jar's data generator in a
temp folder under `/tmp` on EC2 (about 10 s; 300 s at most) and prints its
`commands.json`, about 190 KB, with the version. `vfg place` keeps places in
Minecraft's command storage `everhear:places`, through `scripts/_lib/places.py`.
`vfg place list` answers from a cache, `$VFG_HOME/places.json` (mode 600), while it is
younger than `VFG_PLACES_TTL_S` (30 s), else asks the server and rewrites it; `--fresh`
always asks. `vfg place save` and `forget` write their entry through as the server confirms
it, keeping the cache's age. When the server cannot be asked and a cache exists, however
old, `list` prints it, warns `saved places from <age> s ago: <why>` and exits 0.

## Actions, copies and the scripts

`lib/library.py` reads the library (`scripts/<name>/main.py`, `scripts/_presets/<name>.py`)
and this server's folder (`made/<VFG_SERVER>/scripts/`, `presets/` and `actions/`, gitignored)
on the PC; the router reads them through it too. A name is a script in one place only: `new`,
`save` and `preset save` refuse one that is taken in either, and `new` one an action has.

- **A copy** is one run of an action, named on the box under `run/copies.lock`: `as=` when
  given, else the action's name, else the next free `<action>_2`, `_3` …. Its files in
  `/opt/vfg/run/`: `<copy>.pid`, `<copy>.log`, `<copy>.json` (its action, script, every value
  as checked, the Live fields, runs, task, started), `<copy>.values.json` (live edits, read
  again by the kit every `VFG_LIVE_POLL_S`) and `<copy>.result.json` (what `result()` wrote
  last). Everything it makes carries the tag `vfg_<copy>`, which a stop and its own end
  remove. `scripts/_lib/instances.py` starts, stops and edits copies, for vfg and for the
  `stop` and `edit` scripts.
- **An edit** is checked here against the copy's own action and its values in
  `running.json`; the `edit` script gets the full new set, what changed and whether every
  change is live. Live: written to the copy's live file. Otherwise: the copy restarts under
  its name with every other value kept.
- **`~/.vfg/running.json`**, `{"fetched_at", "copies": {copy: {"action", "script", "values",
  "live", "task", "started", "runs"}}}`, written atomically under `running.lock`: every run,
  edit and stop through vfg writes it through; `vfg script list --fresh` (and `vfg state`)
  fetch it from the box, which drops a copy that ended by itself, keeping one written through
  after the fetch began. `vfg script list` reads it, fetching only when it never was.
- **`new`** writes the script ported to its action (no `VALUES`; `v = values()`) and
  `made/<VFG_SERVER>/actions/<name>.py`: a class inheriting the values class of every preset
  the script holds that has one (`preset("name")`), with a field for every other value, from
  the source script's action (descriptions, takes, runs, defaults, fixed) or its `VALUES`. Its
  line on the menu, and any description it could not carry, are TODOs for the implementer.
- **`check`** exits 1 with one line per problem: `VALUES` not a literal, a default outside
  its limits, an unknown type, a value name twice, `v["key"]` read but not declared (in a
  ported script, not a field of its actions), a preset's value or helper without its name in
  front, a preset's value that starts with another preset's name, no first-line docstring,
  no `ASKS`, an import from `_presets` or of what the box lacks; and for each action over a
  script, its TODOs, an action the loader left out, a made script no action runs. An
  old-style library script (a `README.md`, no `VALUES`) is checked for its README's first
  line and its imports only.
- **`state`** is one SSH call: `list`, each player's `Pos` and `Dimension`, `time query day`
  (26.3's day timeline) and `time query time` (the clock, for the day number), and the
  weather from an inline predicate, `{"type":"minecraft:weather_check","raining":true}`,
  which follows `weather …` within a few seconds, as the rain players see fades in. Then
  every running copy (`"instances"` in `--json`, each with its `copy` and `action`), the
  values that differ from its action's and who it touches (its Player and Players values).

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
  full (`_lib/mc.py`, every file in `_lib/kit/`), then every library preset and example
  script by name: a preset's first line and values (its values class as written, else its
  `VALUES`), a script's first line only, since the brief has every action in full. Nothing
  from `made/`, no date, no count. `vfg route` gives a script card the fields of the actions
  over it.
- **`--cache-read N` and `--cache-write N`** on `vfg usage add` are the input tokens read from
  and written to the prompt cache, in two columns of `usage` that the bridge adds on start;
  until it has, the row goes in without them and one line on stderr says so.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests      # vfg itself starts again under uv
```

No EC2 and no network: `tests/fake_ssh` runs the remote command locally,
`fake_rcon.py` speaks RCON on localhost, `fake_vfg_feed.py` stands in for the
bridge's writer (and `TestRealFeedWriter` uses `../bridge/vfg_feed.py` when it is
there), and `tests/bin/` holds fake `systemctl`, `sudo` and `journalctl`; the fake box's
Python is the one running the tests. `test_copies.py` runs actions as copies on the real
kit, with the stand-in `command`, `edit`, `stop` and `save_place` actions and scripts of
`tests/standins.py` and a few of its own. The place tests answer RCON with
`../scripts/_test/fake_places_world.py`, a small world speaking Minecraft 26.3's recorded
replies; a fake `java` stands in for the data generator. The library tests use a stand-in
kit with `values()` only; `TestRealLibrary` reads the repo's own examples and presets,
when there are any, and copies every preset into every example. Two environment knobs
exist for the tests only: `VFG_WATCH_SILENCE_S` (35) and `VFG_WATCH_UNREACHABLE_S` (60).
