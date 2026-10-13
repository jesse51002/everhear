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
vfg up                                         # units started if down (minecraft, vfg-bridge, vfg-jev, vfg-engine); RCON answering
vfg watch                                      # the feed from the cursor, then follow; one at a time, the newest wins
vfg watch --tail 20                            # for a person at a terminal
vfg watch --after 812                          # for the brain: from seq 813 on; no cursor, no lock, no valve
vfg say 'on it — castle at spawn'              # tellraw @a with the aqua [AI] prefix, stored as an ai line, fed as AI say
vfg say 'your base is ready' --to Steve --task base_steve   # a whisper, stored as "(to Steve) …"; fed as AI say base_steve
vfg cmd 'tp @a ~ ~10 ~'                        # the command action, one command; single-quote ~ and JSON
vfg history --since -10m --player Steve        # lines; also --class, --source, --until, --limit
vfg history --events --since -30m              # game events
vfg history --feed --limit 20                  # the feed's stored lines
vfg context --json                             # the brain's context: the feed rows of the last 6 h, oldest first, then the tasks
vfg context mark 812 813 815                   # their processed_at set to now where it was not; prints 3
vfg watchers set join,leave,death,server       # replaces watch.json; --players a,b --contains REGEX
vfg watchers show
vfg lookout set 'requests to build or fight'   # or: vfg lookout set - < lookout.txt
vfg lookout show
vfg jev menu set - < menu.md                   # the brain's action menu, which Jev reads; vfg jev menu show
vfg brain start                                # brain/run, detached; "running pid N" when it is up already
vfg brain status                               # running or stopped, and the open tasks, read from the box
vfg brain stop                                 # SIGTERM, SIGKILL after 15 s
vfg task set '{"name": "castle_steve", "description": "A stone castle at spawn for Steve", "player": "Steve"}'   # added castle_steve
vfg task set - < row.json                      # the brain's whole row on stdin: updated castle_steve
vfg task list                                  # castle_steve open Steve: A stone castle at spawn for Steve; --open, --name, --json
vfg feed post SYS '' 'smoke'                   # low level; prints the seq
vfg feed post AI 'action -' 'command: give Steve diamond_pickaxe — ok'   # the brain's own records: say, action, task, result, note
vfg action run railgun player=Steve            # one JSON line; the copy is "railgun"
vfg action run railgun player=Alex cooldown=0  # a second copy, "railgun_2"
vfg action run railgun player=Steve --for Steve   # the brain's own copy, "railgun_steve", then "railgun_steve_2"
vfg action run railgun player=Alex as=frost_rail_alex --task frost_rail_alex   # a task's copy, named as given (refused while one runs)
vfg action run edit copy=railgun_2 cooldown=5  # what vfg script set runs
vfg action list                                # every action, ours and the server's: name — its line; --json
vfg action check tnt_crouch                    # what is wrong with it, or ok
vfg action show nuke                           # one action in full, as a brief gives it; exit 1 with the names near it
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
vfg route --task castle_steve < brief.txt      # the examples, presets and actions for that brief, picked by Jev; JSON
vfg prefix                                     # the implementer's static system prompt, the same bytes each time
vfg test hot_fireball_steve                    # its test on the test server (VFG_TEST_BOX): a line per check; -n N, --timeout S
vfg action refs --version                      # the server's version, from server.jar: 26.3
vfg action refs                                # {"version", "commands"}: the data generator's command tree
vfg place save castle --player Steve --by castle_steve   # the save_place action: saved castle at 197 68 251 in overworld
vfg place save home --at -368 70 912           # --dim the_nether or the_end; overworld by default
vfg place list                                 # castle 197 68 251 overworld (castle_steve), one per line, or none; --json, --fresh
vfg place forget castle                        # forgot castle; exit 1 if there was none
vfg usage add --component brain --model google/gemini-3.8-flash --in 9000 --out 120 --cost 0.0016 --ref '#812 s1'
vfg usage add --component implementer --model haiku --in 900 --out 120 --cache-read 15000 --cache-write 2000
vfg usage add --component implementer --model deepseek-flash --in 4000 --out 900 --cache-read 310000 --largest 104322
vfg usage flush                                # the queued rows, every structured() call's among them; none, nothing sent
vfg engine status                              # the game engine: its tick, players, instances, classes; exit 6 when it is not running
vfg engine reload fireball                     # made/<VFG_SERVER>/content/ deployed, fireball.py reloaded: what loaded, what failed
vfg engine grant Steve fireball                # also revoke, give/take, apply/clear, start/stop, spawn, bind/unbind
vfg engine config get fireball                 # its Config exactly as written, with its file and lines
vfg engine config set fireball < config.py     # the whole new Config class: spliced in, checked, deployed, reloaded
vfg engine config undo fireball                # its last kept Config back
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2
unreachable or no answer in time, 5 Minecraft not answering RCON, 6 the game engine not
running. A failure prints one line on stderr. `vfg --help` and `vfg <command> --help` list every option.

## The brain's commands

- **`vfg brain start|stop|status`** runs `brain/run` detached, in a process group of its own,
  with `VFG_BRAIN=1` and `VFG_HOME` in its environment; its pid in `~/.vfg/brain/brain.pid`
  and its output appended to `brain.log` there. `start` when it runs already prints its pid
  and succeeds; one that exits within 3 s did not start. `stop` sends SIGTERM, then SIGKILL
  after 15 s. `status` says running or stopped and lists the open tasks, oldest first, read from
  the box's `tasks` table (`remote/tasks.py`, 10 s at most, or `--timeout`), one `open
  castle_steve Steve: <its description, cut to 120>` line each (`-` for no player), or `open
  none`; when the read fails it warns and prints `open   ? (<why>)`. It exits 1 when the brain is
  not running, whatever the read did.
- **`vfg task set '<json>'|-`** writes one of the brain's tasks, the `tasks` table in the box's
  `vfg.db`, which only the brain writes (the bridge creates it). The JSON is one object of the
  table's columns, `name`, `description`, `player`, `status`, `line`, `brief`, `made`,
  `variables`, `session`, `result`, `created_at`, `updated_at` and `closed_at` (`-` reads it
  from stdin), checked here first: an unknown key, a name that is not a task's, a status other
  than `open`, `done` or `failed`, an empty description, a bad player, `made` other than an object
  of `scripts`, `actions`, `presets` and `copies` lists, `variables` other than a list of objects,
  or a time `parse_when` cannot read, exits 2 with no SSH call; times go on as ISO UTC with
  milliseconds. One SSH call then adds the task (`added <name>`), or changes only the keys given
  (`updated <name>`), or keeps the row when the given `updated_at` is older than the stored one
  (`kept <name>: a newer write is there`, exit 0). A new task needs a description and never takes
  a library action's name; the box refuses either with exit 1, as it does when `vfg.db` has no
  `tasks` table yet (`restart vfg-bridge, which adds it`). `name` and `created_at` never change;
  `closed_at`, when not given, is set as the status leaves `open` and cleared as it opens again.
- **`vfg task list [--open] [--name NAME] [--json]`** prints one `<name> <status> <player|->:
  <description, cut to 120>` line per task, oldest created first, or `none`; `--json` one JSON
  list of the rows, `made` and `variables` decoded.
- **A task's name** is `^[a-z][a-z0-9_]{1,31}$` (`galick_gun_styrk`). `cast`, the bridge's task
  for an incantation's copies, is refused (exit 2) everywhere a task is given: `vfg task set` and
  `list --name`, `--task` on `vfg action run`, `vfg say` and `vfg route`, and `vfg place save
  --by`.
- **`vfg say "<text>" [--to P] [--task NAME]`** sends the tellraw, stores the text as an `ai`
  line and appends the feed's `AI say <name|->` row through the box's `vfg_feed.py`, all in
  one SSH call. What the brain records otherwise goes through `vfg feed post AI "<kind>
  <name|->" "<text>"`, the kind one of `say`, `action`, `task`, `result` and `note`, the second
  field a task's name or `-`.
- **`vfg context [--since <iso|-6h>] [--json]`** prints the feed rows since then, the last
  `VFG_BRAIN_HISTORY_HOURS` (6) by default, oldest first; `--json` one `{"seq", "at", "line",
  "processed_at"}` object per line, then `{"tasks": [row, …]}`, every row of the box's `tasks`
  table as `vfg task list --json` gives them, then `{"last_seq": N}`, in one SSH call. **`vfg
  context mark <seq> …`** sets `processed_at` to now on those rows where it is not set, in one
  call and one transaction, and prints how many. The bridge adds the column, and the `tasks`
  table, on start; until it has, both exit 1 saying so, a `--json` read for the table too.
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

`vfg action run <action> key=value … [as=<copy> | --for <player>] [--task <name>]` loads the actions
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
  given; else its stem, `<action>_<player>` for `--for <player>` (the player lowercased: the
  brain's own copies, `railgun_steve`) or the action's name, and the stem itself, else the next
  free `<stem>_2`, `_3` …. `as=` and `--for` together exit 2, and `--for` names no copy of `edit`,
  `stop` or an incantation action. `--task <name>` is the task that started it, its `VFG_TASK`;
  with none the copy has no task. Its files in
  `/opt/vfg/run/`: `<copy>.pid`, `<copy>.log`, `<copy>.json` (its action, script, every value
  as checked, the Live fields, runs, task (a task's name, null, or `cast` for an incantation's),
  started), `<copy>.values.json` (live edits, read
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
  after the fetch began. `vfg script list` reads it, fetching only when it never was, one
  `railgun_steve — railgun, player=Steve cooldown=0` line per copy: the action when it is not the
  copy's name, and the task when it is not the copy's name either, so a task's own copy,
  `galick_gun_styrk`, shows neither (`vfg state` writes its copies the same way).
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
    values='{"commands": ["effect give Steve speed 10 2"]}' cooldown_s=5
vfg action run incantation_edit player=Steve phrase='fire ball' cooldown_s=10   # or values='{…}', aliases='fireball,fire bowl'
vfg action run incantation_edit player=Steve phrase='beam me' values.length=80 values.player=   # a value a word of its own
vfg action run incantation_edit player=Steve phrase='ice beam' everyday_aliases='ice cream,i scream'   # everyday_aliases= for none
vfg action run incantation_remove player=Steve phrase='fire ball'
vfg incantation list                        # Steve: "fire ball" → command {…} cooldown 5s; aliases fireball, fire bowl
vfg incantation list --player Steve --json  # what Steve can cast, his own and @a's: [{player, phrase, action, values, cooldown_s, aliases, everyday_aliases}]
```

An incantation is a word or a phrase that fires an action the moment its player says it, with
no model on the path (Specs/2026-10-10-Prototype_Voice_Triggers; one word since
Specs/2026-10-11-Prototype_Fast_Casts). The box keeps them in `/opt/vfg/incantations.json`
(`scripts/_lib/incantations.py`'s store), which the bridge reads as players cast. The three
actions change it through one script, `scripts/incantations/`, once vfg has checked everything
here (`run_incantation`):

- **Create** makes one entry per named player, `@a` as one, its phrase normalised. It refuses
  binding `edit`, `stop` or an `incantation_*` action. The bound action's values are checked as
  a run of it checks them (a clamp is a note, a place against the cache); every player field
  left out or empty is a caster field, checked with a stand-in name, stored empty and filled
  by the bridge with whoever casts. The entry keeps what was asked, `asked` (the values given,
  caster fields apart), beside `values`, every value a cast runs with. The aliases are the
  code's (the phrase joined, compound words split) and one `structured()` call's, 24 at most:
  `VFG_INCANT_ALIAS_MODEL`, by default Gemini 3.8 Flash at light thinking (`low`), OpenRouter
  picking the host, 20 s at most; `off` skips it, and a failure is a note. Its prompt is
  `INCANT_ALIAS_PROMPT` in `vfg`: the incantation, each word's pronunciation (CMUdict's, in
  ARPAbet; for a word it lacks, "not in the dictionary (a made-up word)" and the kit's guess from
  its letters: `incantations.pronunciation`), five rules (the same sounds in the same order with
  one or two near ones changed, the same length, written as a recognizer writes, never the
  phrase or a synonym, the likeliest first) and exactly 20 variants (`INCANT_ALIAS_COUNT`), each
  with its ARPAbet sounds. Then the code checks them (`incantations.checked_variants`) before
  `merge_aliases`: the phrase itself, repeats, one 3 speech sounds longer than the phrase or
  more, each sound weighed as Layer 2 weighs one put in (an H 0.25, an "uh" 0.5, any other 1),
  so a made-up word keeps respellings with H and "uh" sounds ("come may hum may uh"), and one
  more than 0.35 from it in sound (`sound_distance`: the weighted edit distance of their
  phonemes, Layer 2's costs, over the phrase's count) are dropped, and so are those under 4
  speech sounds or past the 24, as before. What was dropped goes in the line's
  `facts.alias_call.dropped` (`"call me hammer ha: 3 speech sounds longer"`), never in a note.
  On 2026-10-12 (ten phrases, three runs) it made 20 every time, at a median 6.7 s and at most
  9.0 s (12.3 s in a later run), $0.0036 a create; 18 to 21 were kept for most phrases, 14 to
  18 for kamehameha (CMUdict says it in 8 sounds, with no H: 9 to 12 before the length was
  weighed) and 2 or 3 for heal (one-syllable variants are under 4 sounds). It also says whether the phrase is everyday talk,
  which is a note (`"heal" is everyday talk, so it will fire whenever it is said in
  conversation.`), and none when the call is off or fails. And it flags each variant that is a
  common phrase of its own, said meaning something else (`common_phrase`: "ice cream", "i
  scream", "nice beam" and "eyes beam" for ice beam, never "ice been", "juicy stake" or
  "destroyed everything"): those kept among the aliases, never the code's own, are the entry's
  `everyday_aliases`, which the bridge leaves out of what the mod's keyword spotter listens for
  (`GET /v1/incantations`), so ordinary talk does not cast; Layer 2 and the Jev cast check still
  use them, as speech to text writes "Ice cream." for "ice beam". The store summary names them
  (`(everyday talk, not spotted: ice cream, i scream)`); none when the call is off or fails. A
  stray flag on a mishearing ("hi step") only takes it from the spotter. No word list backs it
  up: in Google Books "destroyed everything" is 23 times as common as "eyes beam". The call is
  made while one SSH call reads the store and the bound script's stamps: the bind warnings (a
  short phrase, with a second word suggested for one word; one that sounds like, or starts
  like, another of the player's, a one-word phrase found as Layer 2 finds it) and a replaced
  one are notes, and the bound script and `_lib` are deployed when their stamps say so, as a
  run would.
- **Edit** reads each player's entry from the box (never the cache), by its player and phrase,
  and changes what is given: the values, checked against its action over the stored ones (a
  player field set empty becomes a caster's, one given a name stops being one) and sent, with
  what was asked, as only the keys given (and the caster fields only when they change), which
  the box merges over what the store holds as it writes, so two edits of different values both
  land; the cooldown; the aliases, normalised, capped and bind-checked again, keeping the
  everyday marks still among them; `everyday_aliases=`, the marks replaced whole, each one of
  its aliases after the edit (one that is not is left out, a note), empty for none. It is not
  in the action's `takes`, so the brain is not shown it: it is set by hand. An entry from
  before the marks has none.
- **Values as words**: `values='{…}'` takes a JSON object, and `values.<name>=<value>` one value,
  a word of its own (Claude Code's permission check refused a JSON object of two keys or more in
  one word in `dontAsk`; the guard still asks for the words form). A word is read once the bound action is known: a field that takes text keeps it as
  it is (`values.title=1` is the text "1"), any other reads it as JSON (6, true, `["say hi"]`),
  else as its field reads words (`a,b`).
- **Following the action**: after a `vfg action run` of an action that went well (it has just
  deployed the action's script as it is here), each binding of it is made again from what was
  asked over the action's defaults as they are now (`refresh_incantations`), so a fix to the
  action reaches the phrase. It is decided from the cache alone, with no SSH while every binding
  there holds what it would be made; when one would change, the store is read and each binding
  that changes is updated, its values, `live` and `asked` replaced whole (a value the action no
  longer has goes), a note each. With no cache a note says they were not checked (`vfg
  incantation list --fresh`). `vfg action check` writes nothing to the box. A binding made
  before `asked` was kept stays as it is.
- **Remove** removes each player's entry; one nobody had is `"ok": false`.
- **`vfg incantation list`** answers from `$VFG_HOME/incantations.json`, `{"fetched_at",
  "version", "incantations": [entries]}`, while it is younger than `VFG_INCANT_TTL_S` (30 s),
  else fetches the store (`remote/incantations.py`, one SSH call); `--fresh` always fetches,
  and a failed fetch with any cache prints it and warns. Every incantation run writes through.
  Values show as they differ from the action's own, caster fields as `player=<caster>`, and
  the everyday aliases after the aliases (`; everyday, not spotted: ice cream, i scream`).

## The game engine

The game engine (Specs/2026-10-12-Prototype_Game_Engine) is one process on the box, `vfg-engine`,
that runs abilities, items, marks, actors and scenarios written as Python classes. `vfg engine
<op>` sends one of its control ops through `remote/engine.py`, which speaks its control socket,
`<VFG_REMOTE_VFG>/engine/engine.sock`, through its folder when that path is longer than a socket's address
holds, as a realmc stand-in's is (`engine/vfg_engine/server.py` has the protocol: one
connection a request, one JSON line in with `timeout_s`, one out), in one SSH call. Each prints the
reply in words, or with `--json` as it came; exit 0 when the engine did it, 1 when it refused (its
error on stderr), 2 for bad arguments (refused here, with no SSH call), 4 when it took the request
and did not answer in time (it says whether it may still do it), 6 when it is not running.

```sh
vfg engine status                              # engine running since …; tick 4821 at 10 a second; p50 1.2 ms, p95 3.4 ms; players; instances by kind; classes; slow
vfg engine classes                             # ability   fireball  Burns for burn_s seconds, …  (made/main/content/fireball.py)
vfg engine overview --json                     # {"ok", "classes", "instances"}: both in one SSH call, what the brain shows as ENGINE
vfg engine meta [fireball]                     # one table: kind, id, damage@0, damage@10, damage@20, the required fields, own: name=value …
vfg engine world                               # mob_griefing = false (it was true); held by scenario:horde#1: false at priority 10 since …
vfg engine instances [--kind ability] [--user Steve]   # instance kind id user started ends
vfg engine log [-n 40]                         # <time> <level> <message> {its fields}
vfg engine reload [fireball …]                 # deployed made/main/content: …; loaded: …; refused <file> (<Class>): <check> failed: …; switched: N; stopped …
vfg engine grant Steve fireball                # granted fireball to Steve; revoke: took fireball away from Steve
vfg engine give Alex fire_staff [--base B]     # gave Alex fire_staff (a base that is not the class's is refused); take: took every …
vfg engine apply Steve giant [--seconds 30]    # put giant on Steve for 30 s; clear: took giant off Steve
vfg engine start horde [waves=3 …]             # started horde as scenario:horde#1; its args each JSON when they read as JSON
vfg engine stop horde | --instance scenario:horde#1    # stopped horde (scenario:horde#1)
vfg engine spawn ice_queen --at 10 64 -20 | --near Steve   # spawned ice_queen as <uuid>
vfg engine bind Steve 'fire ball' fireball [--by TASK]     # "fire ball" for Steve now casts fireball; aliases fireball; notes on stderr
vfg engine unbind Steve 'fire ball'            # removed Steve's incantation "fire ball"
vfg engine incantations [--player Steve]       # Steve: "fire ball" → ability fireball (engine) cooldown 5s; aliases fireball
vfg engine config get|set|undo fireball        # below
```

- **Content** is written on this PC in `made/<VFG_SERVER>/content/<file>.py` (gitignored, as
  `made/` is) and deployed whole to `/opt/vfg/made/<server>/content/`: the folder packed as a
  script's is (no caches, no dotfiles), unpacked beside it on the box and swapped in under
  `run/deploy.lock`, left as it is when every file is the same. `reload` deploys first, then sends
  `reload` with the named files' paths there (none named: every content dir), and prints what
  loaded, each check that failed and why (the old version keeps running), how many running instances
  switched and which stopped; exit 1 when a check failed.
- **`bind`** is an action incantation's checks and aliases (`run_incantation`): the phrase
  normalised, one player and never `@a`, the alias call (20 variants, the code's checks, the
  everyday ones), in a thread while one SSH call reads the engine's classes (an ability it has not
  is refused before the bind), the player's engine bindings and the action incantations' store;
  then the bind warnings against every other phrase of the player's, both kinds, and the everyday
  note, as notes. An action incantation with the same phrase is a note, either way round. The
  binding goes into the cache as the engine's reply has it stored (its `entry`: its aliases as kept
  and the ability's cooldown).
- **`overview`** asks the engine for its classes and its instances in one SSH call (two requests in
  one remote/engine.py run) and prints both, or with `--json` `{"ok": true, "classes": [...],
  "instances": [...]}`, as the `classes` and `instances` ops give them. One the engine refused is
  left out, `ok` false, `error` naming it (`instances: …`), the other kept, exit 1. The brain's
  ENGINE part is one `vfg engine overview --json` (brain/brain/world.py).
- **`config get <class>`** prints the class's Config exactly as written on this PC, `made/main/
  content/fireball.py, lines 10 to 33: the Config of Fireball, as written`, then its lines; the
  engine says which file and class (its `config_get`), and a box that runs another version than
  this PC holds is a warning. **`config set <class>`** takes the whole new Config class on stdin, at
  any indentation: the engine's `engine/vfg_engine/rewrite.py` (standard library, loaded by its
  path) splices it into exactly the old one's lines and runs the static checks (it parses, the same
  fields and formulas, nothing outside the Config changed, and only values: every formula's code and
  the `class Config` line as they were, each value a literal or `Field(<literals>)`); a failed one
  is exit 1 with the checks' own words, several numbered `(1) …; (2) …`, and nothing is sent. Then the old file is kept, the new one written, deployed and reloaded under
  `$VFG_HOME/engine.lock`; when the engine refuses it (a reload check: in bounds, damage at three
  powers, imports), or is down, or does not answer, this PC's file is put back and deployed again,
  and the error is the answer, saying so. A Config already as written changes nothing (`"changed":
  []`). **`config undo <class>`** splices the Config of that class's last kept version back into
  the file as it is now, checked, deployed and reloaded the same way, and moves the version to
  `undone/`, so the next undo goes one further back. The kept versions are
  `made/<server>/content/.history/<file>/<YYYYmmdd-HHMMSS-ffffff>-<Class>.py`, the whole file
  before each change, and `.history/log.jsonl` one line a change or a refusal (`at`, `op`, `class`,
  `class_name`, `file`, `ok`, `kept` or `error`, `by`); a deploy skips dot folders, so none reaches
  the box.
- **The menu's engine actions** (`actions/engine_*.py`, each naming its op in `engine`: grant,
  revoke, give, start, stop, bind, unbind and config) run through `vfg action run` as any action, so the
  brain and the implementers run them so: `run_named` sends an action whose class names an
  `engine` op (`vfg_actions.engine_op`) to `run_engine_action`, which checks its values as any
  action's and runs the op as `vfg engine` does: one JSON line, `copy` empty and `running` false,
  its `facts` the engine's reply, a refusal `"ok": false` with the engine's error. `engine_config`'s
  `config` is Python source (`Annotated[str, Kind("python")]`): vfg_actions takes it whole, its
  lines and comments kept, where any other text field is one line. An incantation never fires an
  engine action (`incantation_create action=engine_grant` is exit 2: an ability is bound with
  `engine_bind`), and `as=` names no copy of one.
- **`vfg incantation list`** lists the engine's bindings beside the action incantations: the
  incantations program reads the engine's export, `/opt/vfg/engine/incantations.json`, in the same
  SSH call, and the cache keeps it as `"engine"`. A binding's line is `Steve: "fire ball" → ability
  fireball (engine) cooldown 5s; aliases …`, and in `--json` `{"player", "phrase", "action": null,
  "engine": true, "ability", "values": {}, "cooldown_s", "aliases", "everyday_aliases"}`. `engine
  bind` and `unbind` write through to the cache.

## The router and the implementer's prompt

- **`vfg route [--task NAME] [--ref REF] < brief`** reads the implementer's brief on stdin, and
  with `--task` the task's description (else its brief) and `<player> said: <line>` from the box's
  `tasks` table, in one SSH call of at most 1.5 s (`VFG_ROUTE_TIMEOUT_S` if that is less), which
  comes out of the router's budget. A task the box does not have warns `<name> is not a task on
  the box — routing on the brief alone`, a read that fails `cannot read <name> from the box
  (<why>) — routing on the brief alone`, and both route on. It makes one
  card per example script, preset and action, ours and `made/<VFG_SERVER>/`'s (an action's card
  is its menu line and the names of its values, from the actions cache), and asks Jev about all
  of them in one call through `remote/jev.py` on the box: `runs_it`, `close` or `no` per
  script, `use` or `no` per preset, `needed` or `no` per action. It prints JSON on stdout:
  `{"task", "scripts": [{"name", "source", "verdict", "p_runs_it", "p_close", "path"}],
  "presets": [{"name", "source", "p_use", "path"}], "actions": [{"name", "source", "p_needed",
  "path"}], "fallback", "ms", "tokens"}`, with `"held"` and `"cut"` when there are any, and a
  log line on stderr.
- **Picks:** at most 3 scripts of those whose `runs_it` and `close` reach `VFG_ROUTE_LOAD` (0.3),
  those that run the job first, then by how sure Jev is; every preset whose `use` reaches it
  that no loaded script holds; at most 7 actions whose `needed` reaches it, by `needed`. What
  the caps leave out is listed under `"cut"`. Past `VFG_ROUTE_TIMEOUT_S` (3), or on any error,
  it picks by words instead (`lib/menu.py`'s words): every script or preset sharing a word with
  the job, and the actions sharing most, those it names first (`"fallback": true`). It writes
  one usage row, `--component jev --ref <REF>` (by default the task's name), in the background.
- **`vfg prefix`** prints the part of an implementer's system prompt that never changes
  between spawns, so the provider reads it from the cache: `scripts/README.md`, the kit in
  full (`_lib/mc.py`, every file in `_lib/kit/`), then every library preset and example
  script by name: a preset's first line and its values class as written, a script's first
  line only; then every library action's menu line, by name, as `vfg action list` prints it.
  Nothing from `made/`, no date, no count. `vfg route` gives a script card the fields of the
  actions over it.
- **`vfg action show <name>`** prints one action in full, the text a brief gives each action it
  holds in full (`lib/py/vfg_actions.py`'s `describe()`, kept in the actions cache and read
  through `lib/menu.py`, which the brain loads by path too): its line, its script, how it runs,
  and every value with its type, limits, default and description. The cache is built again
  first when an action file is newer. A name that is not an action exits 1 with the names near
  it; one that did not load, with why.
- **`--cache-read N` and `--cache-write N`** on `vfg usage add` are the input tokens read from
  and written to the prompt cache, in two columns of `usage` that the bridge adds on start;
  until it has, the row goes in without them and one line on stderr says so. **`--largest N`**
  is a run's largest request in tokens (an implementer's), in the column `largest_tokens`, the
  same way.
- **`vfg usage flush`** sends `usage.pending.jsonl` and writes no row of its own. Every
  `structured()` call (`lib/py/vfg_llm.py`) appends its row there under `usage.pending.lock`, in
  the format `vfg usage add` queues its own, plus `ok`, `error` and `ms`, the bridge's added
  columns (a box without them gets the rest, and one line on stderr says so); `cost_usd` is null
  for a call cut off with an attempt in flight. The brain runs it after its calls, never two at
  once, and vfg after an incantation's alias call (component `alias`, the phrase its ref), each
  in its own session, nobody waiting.
- **The queue's folder** is `VFG_USAGE_QUEUE` from the environment, else `$VFG_HOME`; `vfg usage`
  and `structured()` read it alike. The replays (`integration/replay/`) point it at their own
  output, so their rows never reach the box unless sent on purpose: `VFG_USAGE_QUEUE=OUT vfg usage
  flush`.

## An ability's test, on the test server

- **`vfg test <name> [-n N] [--timeout S]`** runs a task's test, `made/<VFG_SERVER>/tests/<name>.py`,
  or a library script's, `scripts/<name>/test.py`, on the **test server**: a second Minecraft
  server with Carpet's fake players, never the live one. First it gives the test server what
  the test needs, each folder only when its stamp there is another (`scripts.py`'s `stale`, then
  `deploy`): the kit, the task's made script, and the scripts of the actions the test names and of
  the task's incantations, which it copies from the live server's store (those it bound, `by`,
  and those on its action). Then `remote/test.py` runs the test there, one at a time, with the
  actions it names as the actions cache holds them (for `t.run`). It prints one line per check,
  `PASS`, `FAIL` or `CAN'T TELL` with the numbers that decided it, each copy's last `-n` (15) log
  lines when one failed, and `RESULT: …`; exit 0 when none failed, else 1 with why on stderr. Past
  `--timeout` (`VFG_TEST_TIMEOUT_S`, 60) it is stopped, its cleanup run. A task with no test gets
  the skeleton (exit 1), as `vfg script new` writes it. The helpers are the kit's
  (`scripts/_lib/kit/test.py`; its docstring is the guide): a stage at y 296, 3000 blocks out,
  cleared after.
- **The test server is a second box**: `VFG_TEST_BOX` names a file of its settings, `KEY=value`
  lines as `~/.vfg/config` has them, of which `VFG_SSH`, `VFG_SSH_HOST`, `VFG_SSH_USER`,
  `VFG_SSH_KEY`, `VFG_REMOTE_VFG` and `VFG_REMOTE_MC` are read over the live box's. On realmc it
  is the paired server's own config: `integration/realmc/up ih-b-test`, then
  `VFG_TEST_BOX=$REALMC_ROOT/ih-b-test/home/.vfg/config`. A second instance on the EC2 box needs
  only `VFG_REMOTE_VFG` and `VFG_REMOTE_MC`. None set, or the live box itself: exit 3.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests      # from the repo's root; about 15 s
```

No EC2 and no network, and where `python3` has no pydantic or httpx the run starts itself again
under `uv`, as vfg does. `tests/harness.py` makes each test a box in a temp folder: the PC (`HOME`,
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
- `test_copies.py`: `vfg action run` and its copies (`--for` among them), edits, stops, `running.json`, script save.
- `test_task.py`: `vfg task set` and `list` on the box's `tasks` table, `vfg context --json`'s
  tasks, and a box without the table.
- `test_test.py`: `vfg test` against a second box, its skeleton, `remote/test.py`.
- `test_route.py`: the router, `remote/jev.py` against `fake_jev_route.py`, `vfg prefix`, `vfg action show`,
  `vfg usage add`'s added columns.
- `test_library.py`: `vfg script new` and `check`, `vfg preset save`, five real presets in one script.
- `test_incantations.py`: the incantation actions writing the box's store through their real
  script, the alias call (its prompt, the code's checks) and its everyday aliases (kept,
  edited, set by hand), `values.<name>=`
  words, a binding following its action after a run, and `vfg incantation list` with its cache;
  one box for a class.
- `test_engine.py`: every `vfg engine` op against `fake_engine.py`, the engine's control socket
  served in this process, its replies shaped as `engine/vfg_engine/ops.py`'s: the reads, the ops,
  the content's deploy and reload, a Config set, refused and put back, undone, engine bindings in
  `vfg incantation list`, and the menu's engine actions through `vfg action run`; a Config is held to
  the engine's own `engine/vfg_engine/rewrite.py`. `TestRealEngine` runs the
  same against the engine itself, `python -m vfg_engine --fake-io` (no Minecraft), and skips, saying
  why, when it cannot start.
- `test_e2e.py`: a handful with vfg as a process, through `tests/fake_ssh`, which runs the remote
  command here with bash: a command and its SSH options, closed descriptors, and watches
  following the feed.

`tests/route_eval/` is an eval, not a test: it calls Jev for real. Two environment knobs exist for
the tests only: `VFG_WATCH_SILENCE_S` (35) and `VFG_WATCH_UNREACHABLE_S` (60).
