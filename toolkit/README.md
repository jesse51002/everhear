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
vfg watch                                      # the feed from the cursor, then follow; one at a time, the newest wins
vfg watch --tail 20                            # for a person at a terminal
vfg watch --after 812                          # for the brain: from seq 813 on; no cursor, no lock, no valve
vfg say 'on it — castle at spawn'              # tellraw @a with the aqua [AI] prefix, stored as an ai line, fed as AI say
vfg say 'your base is ready' --to Steve --task T7   # a whisper, stored as "(to Steve) …"; fed as AI say T7
vfg cmd 'tp @a ~ ~10 ~'                        # the command action, one command; single-quote ~ and JSON
vfg history --since -10m --player Steve        # lines; also --class, --source, --until, --limit
vfg history --events --since -30m              # game events
vfg history --feed --limit 20                  # the feed's stored lines
vfg context --json                             # the brain's context: the feed rows of the last 6 h, oldest first
vfg context mark 812 813 815                   # their processed_at set to now where it was not; prints 3
vfg watchers set join,leave,death,server       # replaces watch.json; --players a,b --contains REGEX
vfg watchers show
vfg lookout set 'requests to build or fight'   # or: vfg lookout set - < lookout.txt
vfg lookout show
vfg jev menu set - < menu.md                   # the brain's action menu, which Jev reads; vfg jev menu show
vfg brain start                                # brain/run, detached; "running pid N" when it is up already
vfg brain status                               # running or stopped, and the open tasks in ~/.vfg/brain/tasks.json
vfg brain stop                                 # SIGTERM, SIGKILL after 15 s
vfg feed post SYS '' 'smoke'                   # low level; prints the seq
vfg feed post AI 'action T7' 'give Steve diamond_pickaxe — ok'   # the brain's own records: say, action, task, result, note
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
vfg script logs railgun_2 -n 40                # --follow only for a person or code that reads it as it comes
vfg script save railgun_2 rail_no_cooldown     # the copy's values kept as an action: made/<VFG_SERVER>/actions/
vfg preset save galick_ice ice_path ice_trail  # a function lifted out into made/<VFG_SERVER>/presets/
vfg made save                                  # made/<VFG_SERVER>/ into a new $VFG_MADE_ARCHIVE/2026-10-10-142301/
vfg made restore 2026-10-10                    # and back: that day's latest, or one save by its full name
vfg state                                      # who is online and where, time, weather, what runs; --json
vfg state --json --items                       # and each player's items, health, food, armor, level, mode, effects
vfg route --task T7 < brief.txt                # the examples and presets for that brief, picked by Jev; JSON
vfg prefix                                     # the implementer's static system prompt, the same bytes each time
vfg action refs --version                      # the server's version, from server.jar: 26.3
vfg action refs                                # {"version", "commands"}: the data generator's command tree
vfg place save castle --player Steve --by T7   # the save_place action: saved castle at 197 68 251 in overworld
vfg place save home --at -368 70 912           # --dim the_nether or the_end; overworld by default
vfg place list                                 # castle 197 68 251 overworld (T7), one per line, or none; --json, --fresh
vfg place forget castle                        # forgot castle; exit 1 if there was none
vfg usage add --component brain --model openai/gpt-6.1-sol --in 9000 --out 120 --cost 0.0016 --ref T7
vfg usage add --component implementer --model haiku --in 900 --out 120 --cache-read 15000 --cache-write 2000
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2
unreachable or no answer in time, 5 Minecraft not answering RCON. A failure prints
one line on stderr. `vfg --help` and `vfg <command> --help` list every option.

## The brain's commands

- **`vfg brain start|stop|status`** runs `brain/run` detached, in a process group of its own,
  with `VFG_BRAIN=1` and `VFG_HOME` in its environment; its pid in `~/.vfg/brain/brain.pid`
  and its output appended to `brain.log` there. `start` when it runs already prints its pid
  and succeeds; one that exits within 3 s did not start. `stop` sends SIGTERM, then SIGKILL
  after 15 s. `status` says running or stopped and lists the open tasks of `tasks.json`, which
  only the brain writes, one `open   T183 Steve: <its brief>` line each; it exits 1 when the
  brain is not running.
- **`vfg say "<text>" [--to P] [--task T7]`** sends the tellraw, stores the text as an `ai`
  line and appends the feed's `AI say <task|->` row through the box's `vfg_feed.py`, all in
  one SSH call. What the brain records otherwise goes through `vfg feed post AI "<kind>
  <task|->" "<text>"`, the kind one of `say`, `action`, `task`, `result` and `note`.
- **`vfg context [--since <iso|-6h>] [--json]`** prints the feed rows since then, the last
  `VFG_BRAIN_HISTORY_HOURS` (6) by default, oldest first; `--json` one `{"seq", "at", "line",
  "processed_at"}` object per line. **`vfg context mark <seq> …`** sets `processed_at` to now
  on those rows where it is not set, in one call and one transaction, and prints how many. The
  bridge adds the column on start; until it has, both exit 1 saying so.
- **`vfg watch --after <seq>`** prints the feed from seq+1, then follows; it reads and writes no
  cursor, takes no lock (a person's `vfg watch` runs beside it), has no valve and skips no line
  however far behind it starts: no `VFG_WATCH_CATCHUP`.
- **`vfg state --json --items`** adds to each online player `"items"`: Minecraft's replies to
  `data get entity <p> Inventory`, its `equipment` (asked only when it has any) and
  `SelectedItemSlot`, which the brain's `listing.py` turns into slot names. And the player's
  state, in the same SSH call, read from `data get entity <p>` `Health`, `foodLevel`, `XpLevel`,
  `playerGameType` and `active_effects`, and `attribute <p> minecraft:armor get` and
  `minecraft:max_health get`: `"health"` (14.5), `"max_health"` (20.0), `"food"` (18),
  `"xp_level"` (5), `"armor"` (12.0, the armor points worn), `"game_mode"` (`survival`,
  `creative`, `adventure` or `spectator`) and `"effects"` (`[{"id": "minecraft:speed",
  "amplifier": 1, "duration": 600}]`, the amplifier 0 for level I, the duration in ticks, -1
  for infinite; `[]` when Minecraft says there is none). Each is `null` when Minecraft's reply
  could not be read: never a guess. Without `--json`, a `player` row each: `Steve: 14.5/20
  health, 18/20 food, armor 12, level 5, survival; effects: speed 2 0:30, night_vision ∞`.
- **`vfg jev menu set|show`** writes or prints `/opt/vfg/menu.md`, the brain's action menu,
  which Jev reads after `lookout.md`; it may be empty.

## Actions and places

`vfg action run <action> key=value … [as=<copy>] [--task T7]` loads the actions
(`lib/py/vfg_actions.py`, the one loader), checks the values against the action's class (an
unknown or fixed key, or a value its field refuses, exits 2; a number out of range is clamped
and a note says so; a saved place against the places cache, fetched again only when a place
check fails on a stale one), then makes one SSH call: a copy started and waited for, when EC2
has `_lib` and the action's script as this repo does (a deploy leaves in each folder the stamp
it was sent with, `.vfg-stamp`, a hash of its files, and the run sends only the stamps);
otherwise EC2 starts nothing and asks for them, and a second call deploys them with the run. It prints ONE JSON line, `{"ok", "copy", "action", "running",
"summary", "facts", "notes", "error"?}`, whatever happens, and exits 0 when ok. A once action
is waited for up to its `timeout_s`, else `VFG_DISPATCH_ACTION_TIMEOUT_S` (20 s), and answers
with its script's `result()`; past it, `"running": true` and "still running as <copy>": it is
never run again. An until-stopped action answers with its first `result()`, or "running as
<copy>" after a 2 s check. A script that exits non-zero is `"ok": false`, its error what it
failed with: the error it crashed on (`run/<copy>.error`, which `mc.py` writes), else the
`result()` it refused with, else the last line it printed. `vfg cmd`, `vfg script set`, `vfg script stop` and `vfg place save` are shortcuts for
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
- **`new`** writes the script (`v = values()`) and `made/<VFG_SERVER>/actions/<name>.py`: a
  class inheriting the values class of every preset the script holds (`preset("name")`), with
  a field for every other value, from the source script's action (descriptions, takes, runs,
  defaults, fixed); from nothing, a player. Its line on the menu is a TODO for the implementer.
- **`save <copy> <new_name>`** keeps a running copy's values as an action of its own,
  `made/<VFG_SERVER>/actions/<new_name>.py` over the same script, the values that differ from
  its action's as its `defaults`: a variant of a library action, or a made action written out
  (made actions do not import each other). Its line on the menu is a TODO.
- **`preset save <script> <function> <name>`** lifts the function, the private helpers and
  constants it uses and the imports they need into `made/<VFG_SERVER>/presets/<name>.py`, with
  its values class: a field for each value it reads, renamed to the preset's prefix, written
  as the script's action has it, description included (a TODO where there is none).
- **`check`** exits 1 with one line per problem: `v["key"]` read but not a field of the
  script's actions (or of the preset's values class), a script no action runs, a preset's
  value or helper without its name in front, a preset's value that starts with another
  preset's name, a preset's value with no description (or a TODO), a type that is not one of
  `actions/_types.py`'s, a values class that does not load, no first-line docstring, no
  `ASKS`, an import from `_presets` or of what the box lacks; and for each action over a
  script, its TODOs and an action the loader left out.
- **`state`** is one SSH call: `list`, each player's `Pos` and `Dimension` (with `--items`, their
  items and state, above), `time query day`
  (26.3's day timeline) and `time query time` (the clock, for the day number), and the
  weather from an inline predicate, `{"type":"minecraft:weather_check","raining":true}`,
  which follows `weather …` within a few seconds, as the rain players see fades in. Then
  every running copy (`"instances"` in `--json`, each with its `copy` and `action`), the
  values that differ from its action's and who it touches (its Player and Players values).

## Incantations

```sh
vfg action run incantation_create player=Steve phrase='fire ball' action=command \
    values='{"commands": ["effect give Steve speed 10 2"]}' cooldown_s=5 --task T7
vfg action run incantation_edit player=Steve phrase='fire ball' cooldown_s=10   # or values='{…}', aliases='fireball,fire bowl'
vfg action run incantation_remove player=Steve phrase='fire ball'
vfg incantation list                        # Steve: "fire ball" → command {…} cooldown 5s; aliases fireball, fire bowl
vfg incantation list --player Steve --json  # what Steve can cast, his own and @a's: [{player, phrase, action, values, cooldown_s, aliases}]
```

An incantation is a phrase of two words or more that fires an action the moment its player
says it, with no model on the path (Specs/2026-10-10-Prototype_Voice_Triggers). The box keeps
them in `/opt/vfg/incantations.json` (`scripts/_lib/incantations.py`'s store), which the bridge
reads as players cast. The three actions change it through one script, `scripts/incantations/`,
once vfg has checked everything here (`run_incantation`):

- **Create** makes one entry per named player, `@a` as one, its phrase normalised. It refuses
  binding `edit`, `stop` or an `incantation_*` action. The bound action's values are checked as
  a run of it checks them (a clamp is a note, a place against the cache); every player field
  left out or empty is a caster field, checked with a stand-in name, stored empty and filled
  by the bridge with whoever casts. The aliases are the code's (the phrase joined, compound
  words split) and one `structured()` call's, 12 at most: `VFG_INCANT_ALIAS_MODEL`, by default
  the dispatcher's planner's model and providers, reasoning off, 8 s at most (about 1 s and
  $0.00005); `off` skips it, and a failure is a note. Its prompt is `INCANT_ALIAS_PROMPT` in
  `vfg`. The call is made while one SSH call reads the store and the bound script's stamps: the
  bind warnings (a short phrase; one that sounds like, or starts like, another of the player's)
  and a replaced one are notes, and the bound script and `_lib` are deployed when their stamps
  say so, as a run would.
- **Edit** reads each player's entry from the box (never the cache), by its player and phrase,
  and changes what is given: the values, checked against its action over the stored ones (a
  player field set empty becomes a caster's, one given a name stops being one) and sent as only
  the keys given, which the box merges over what the store holds as it writes, so two edits of
  different values both land; the cooldown; the aliases, normalised, capped and bind-checked
  again.
- **Remove** removes each player's entry; one nobody had is `"ok": false`.
- **`vfg incantation list`** answers from `$VFG_HOME/incantations.json`, `{"fetched_at",
  "version", "incantations": [entries]}`, while it is younger than `VFG_INCANT_TTL_S` (30 s),
  else fetches the store (`remote/incantations.py`, one SSH call); `--fresh` always fetches,
  and a failed fetch with any cache prints it and warns. Every incantation run writes through.
  Values show as they differ from the action's own, caster fields as `player=<caster>`.

## The router and the implementer's prompt

- **`vfg route --task T7 [--ref R] < brief`** reads the implementer's brief on stdin, and the
  task's brief and the player's words from the brain's `~/.vfg/brain/tasks.json`. It makes one
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
  script by name: a preset's first line and its values class as written, a script's first
  line only, since the brief has every action in full. Nothing
  from `made/`, no date, no count. `vfg route` gives a script card the fields of the actions
  over it.
- **`--cache-read N` and `--cache-write N`** on `vfg usage add` are the input tokens read from
  and written to the prompt cache, in two columns of `usage` that the bridge adds on start;
  until it has, the row goes in without them and one line on stderr says so.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests      # from the repo's root; about 7 s
```

No EC2 and no network, and where `python3` has no pydantic the run starts itself again under
`uv`, as vfg does. `tests/harness.py` makes each test a box in a temp folder: the PC (`HOME`,
`VFG_HOME`, a repo) and the EC2 box (`vfg.db`, `feed.log`, `tests/fake_vfg_feed.py` as the
feed's writer). vfg's `main()` runs in the test's process, and so does every remote program it
sends: the harness replaces `Ssh`'s one subprocess call and nothing else, and gives the program
the box's environment, none of the PC's. A test class shares one `fake_rcon.py`, Minecraft's RCON
in-process on localhost; a copy an action starts is a real process on the real kit, from the
stand-in actions of `tests/standins.py`. vfg's own waits are short in the tests (the 2 s check
0.2 s, `vfg brain start`'s 3 s check 0.1 s), a copy ends with no exit grace (`VFG_EXIT_GRACE_S=0`
on the box) and is waited for in 5 ms steps, and each part of a remote program is compiled once.
No test reaches a model: the box's config turns the incantation alias model off unless a test
names one, with `vfg_llm` mocked, and no OpenRouter key passes in.

- `test_vfg.py`: every command at its edge, with `fake_places_world.py` (26.3's recorded
  replies) for places, a fake `java` for the data generator and `tests/bin/`'s fake
  `systemctl`, `sudo` and `journalctl` for `vfg up`; `TestRealFeedWriter` runs the bridge's
  `vfg_feed.py` in place of the fake.
- `test_copies.py`: `vfg action run` and its copies, edits, stops, `running.json`, script save.
- `test_route.py`: the router, `remote/jev.py` against `fake_jev_route.py`, `vfg prefix`.
- `test_library.py`: `vfg script new` and `check`, `vfg preset save`, five real presets in one script.
- `test_incantations.py`: the incantation actions writing the box's store through their real
  script, the alias call, and `vfg incantation list` with its cache; one box for a class.
- `test_e2e.py`: a handful with vfg as a process, through `tests/fake_ssh`, which runs the remote
  command here with bash: a command and its SSH options, closed descriptors, and watches
  following the feed.

`tests/route_eval/` is an eval, not a test: it calls Jev for real. Two environment knobs exist for
the tests only: `VFG_WATCH_SILENCE_S` (35) and `VFG_WATCH_UNREACHABLE_S` (60).
