# brain — every event, a loop of steps

A small always-running Python process on the owner's PC (the specs: Prototype_Brain,
Prototype_Named_Tasks, Prototype_Brain_Loop, Prototype_Implementer_Harness and the game engine
spec). It follows the feed with `vfg watch`, keeps every row in one shared context, and runs two
agents on it:

- **the brain**, for a line that asks for something to happen and for an implementer's report: an
  agentic loop of steps, each step one `structured()` call
  ([`lib/py/vfg_llm.py`](../lib/py/vfg_llm.py)) on Gemini 3.8 Flash with low reasoning. A step runs
  actions from the menu, the game engine's ops, written in full, together or in turn; reads another
  player's items, or an engine class's Config; hands new work off to a coding implementer with a
  brief in parts; follows a task up, or cancels its last run; and ends the loop with a reply, with
  silence or with the hand-off. When every action of a step lands, its reply goes out and the loop
  is done in that one step; when one does not, every result comes back and the next step fixes it.
- **Respond** ([`respond.py`](brain/respond.py)), the cheap mode, for a conversational line: it
  answers, or hands the line up to the brain.

The implementers it starts ([`brain/implementers/`](brain/implementers/)) write engine classes in
`made/<server>/content/`, each with its test, and report back (The implementers, below).

A player's lines take turns, whichever agent takes them: one thing runs for a player at a time, the
next line waits for it, and different players' turns run at once.

## The events

| Event | Comes from | What runs |
|---|---|---|
| `LINE` | a `LINE` Jev classed `action_oriented`; or a `conversational` one Respond handed up | a loop, in the player's turn |
| `REPORT` | an implementer ending (`Implementers`' `on_report`) | once the task has taken what the implementer made and its `DESCRIPTION:` line (Tasks, below): a loop in the task's player's turn, at the front of their queue, which reads the report whole and writes the player's chat line, the task's record and its status, and may act on it (A report's loop, below) |

A conversational `LINE` is one Respond call, and a loop when Respond hands it up. There is no other
call: no model is called for what nobody asked. A game event, a system notice and the brain's own
rows wait for the next loop or Respond call, which reads them as NEW.

A `LINE`'s text can hold several pieces one player said in a row, joined with " … ", the ask last:
Jev's gather call joins the earlier pieces that belong to the ask (`docs/CONTRACTS.md` §5). Both
prompts say so, and to read them as one ask.

**Respond** takes a `conversational` line: a cheap model (DeepSeek V4 Flash, reasoning off) on the
same context that keeps up the banter and replies, or hands the line up to the brain, whose loop
then runs it in the same turn. It answers talk, and a rule or a preference about the future only
("talk like a pirate from now on"), which holds because the brain reads the same conversation. It
hands up anything that asks for something now, a change to what is already going on in the world, a
rule the game itself would have to enforce ("from now on keep creepers away from the base"), an
answer to the AI's question, and anything it is unsure of. It decides first (`reason`, which it must
write, then `hand_up`, then `reply`), and handing up is the default
([`gate_bench.md`](../integration/replay/gate_bench.md),
[`respond_bench.md`](../integration/replay/respond_bench.md)). A hand-up replaces its reply, so a
player never gets two. Respond never acts on the game, and never promises or offers anything there:
a reply that still does, "want me to …?" or "I'll fix it", is handed up in code instead of said
(`promises()`). A call that fails, or runs past `VFG_RESPOND_TIMEOUT_S` (20 s), hands the line up
too. A game event, a system notice and the brain's own rows are context only.

A line already in the feed when the brain read its context, said more than two minutes before that,
is history: never acted on. A line that arrives on the watch is live, whatever its time (the box's
clock may be a minute off the PC's), except the backlog `vfg watch` prints once it reconnects, which
is judged by its age, as at a start, until a line of the last two minutes arrives.

## One turn per player

[`turns.py`](brain/turns.py), in front of both agents:

- **A live line takes its player's turn, or waits for it**, in the order the lines came.
- **At most `VFG_TURN_WAITING` (3) lines wait.** One more is turned away: marked processed, never
  answered, and that player alone is told which lines still wait, by a private `vfg say --to
  <player>` that nobody else sees: `Still on: “a meteor staff”, “and a shield”, “and a bow”. I can't
  take any more right now — wait a bit, then ask again.` Each line is cut short so the whole stays
  one chat line under 200 characters; the first five are named and the rest counted. The whisper's
  `AI say` row reads `(to <player>) …`.
- **A conversational line's turn is Respond's**; when Respond hands it up, the brain's loop runs in
  that same turn, with what Respond read.
- **A hand-off ends the turn as soon as the implementer is started**: the implementer runs on its
  own and holds nobody's turn.
- **An implementer's report waits for its task's player's turn too, at the front of their queue**,
  behind only the reports already there. It is never turned away and does not count toward the
  three. A task with no player has a lane of its own.
- **A turn ends when its run ends**, whether it returns, raises or is cancelled, and then waits, at
  most `VFG_TURN_SETTLE_S` (3 s), for the AI rows it wrote to come back on the watch (its chat lines,
  a whisper apart; its record; its task rows), so the player's next loop reads what this one did. A
  row whose write failed is not waited for, and a run that raised or was cancelled skips the wait.
  Then the next one waiting takes the turn.
- **A turn's clock** starts as it begins: a loop's limits and its time to the reply count from
  there, Respond's call and the world read included.
- **A stop drops everything waiting**: a line dropped so is marked read and never answered, after a
  restart too, and a report dropped so closes its task in code.

## The shared context and the cache

[`context.py`](brain/context.py) keeps every feed row in seq order with its processed mark: the
lines Jev kept, with their class; the game's events; the system's notices; and the brain's own
`AI` rows, the only rows the AI side writes (`vfg say` and `vfg feed post AI`): what it said, the one
record of each loop, each task opened or followed up, and each task's result. Nothing an implementer
writes is in it. Every loop, at its start, and every Respond call takes in everything unread and
marks it (`vfg context mark`), so the game's events since the last read are its NEW rows.

**A `context` line is never shown to a model**: not in the conversation, not under NEW, not in
Respond's prompt. It stays in the feed and `vfg.db`, and in the context's rows, for a memory that
will summarise it later; the brain marks it processed as it arrives (and, at a start, every one
still unmarked), so nothing waits on it. Every `GAME` row, every `SYS` row, the brain's own `AI`
rows and the `action_oriented` and `conversational` lines are shown.

A step's prompt is laid out for the cache ([`call.py`](brain/call.py)):

1. **the opening**, marked: the rules ([`prompts/brain.md`](prompts/brain.md), the owner named, its
   `# TODO` lines left out: `call.rules_text`), the menu, every action in full (its docstring and
   each field it fills with its type, limits, default and description) and Minecraft's command
   list, less the commands that place or remove blocks (the library's command scenario's `BLOCKED`,
   `engine/library/command.py`: building is a scenario's) and the owner's, which the engine refuses
   (`engine/vfg_engine/mc/commands.py`'s `REFUSED`; both read as text by
   `actions.hidden_commands()`): the same on every step of every loop while the
   actions and the references stay as they are;
2. **the conversation**, a minute-block at a time (a run of rows sharing a minute, which never
   changes once the next minute starts), every `VFG_BRAIN_CHECKPOINT` (5) closed blocks marked, the
   last two of them;
3. **the tail**, the event's own: THE WORLD NOW ([`world.py`](brain/world.py)), every online player
   on one line from `vfg state --json --items`, `- Steve at 197 68 251 in overworld: 14/20 health,
   18/20 food, armor 12, level 5, survival; effects: speed II 0:30, night vision ∞; Steve's items, by
   slot: …`, the items of the event's player alone, the speaker or a report's task's player, and what
   Minecraft did not answer left out; the time and the weather; then the game engine's part, RUNNING
   NOW, SAVED PLACES, INCANTATIONS and ENGINE (The game engine, below); TASKS (below); what is new
   since the last read; and the event. Marked on the first step;
4. **THE LOOP SO FAR**, from the second step on: one block per earlier step, what it ran and read,
   what it meant to end with and what each tool came to, the last block marked;

then the step line, never cached: `STEP 2 of at most 10; 6 s of 120 used.`, and on the last step its
notice. Four marks in all (`vfg_llm.MAX_MARKS`).

**The conversation and the tail are read once, at the loop's start, and kept for every step**: a row
that arrives meanwhile, or a change in the world, never moves the prefix, so each step's prompt but
its step line is the next step's cached prefix, and what changed comes back in the results.

Respond's prompt is its rules, the owner and the menu's lines, then the conversation, the last
three checkpoints marked; its tail has the same players without their items, RUNNING NOW,
INCANTATIONS and ENGINE from the last reads, never waited for, no saved places, and TASKS one line
a task.

## A step

One `Step` answers every step of every loop, and its schema never changes, not with the actions, the
step's number or the last step, so nothing before the conversation changes between calls. Every
property is required in it, its default included (`call.all_required`), so the model writes each
field rather than leave one out: on the first pilot Gemini answered a report with `end` and `status`
alone. Its fields:

- `actions`, each `{to, instruction, action}`: an action on the menu, one plain sentence of what it
  does and for whom, and its values written in full, one `{key, value}` per field it sets (closed
  objects, as OpenAI's schemas demand; a scenario's or a mark's args one `args.<name>` key each,
  which code gathers into one `args` object, and an `args` value written as text holding a JSON
  object is read as that object: `ActBase.values`); and `order`, `together` or `in_turn`;
- `read_items`: players whose items to read; `read_configs`: engine classes whose Config to read,
  as written, before an `engine_config` rewrites it;
- `hand_offs`, each a new task's `name` and `description` and its brief in parts: `words` (the
  player's own), `picture` (how it behaves, looks, sounds and feels), `assumed` (what the player
  takes for granted), `cast` (how it is cast, and for whom) and `done_when` (the checklist the
  implementer checks);
- `follow_ups`, each a task under TASKS by its name, with its `brief` (what to change) and its new
  `description` (empty: it stays as it was), or `cancel`;
- `end`, `next`, `reply`, `silent` or `handoff`; `reply`, the chat line; `record`, the loop's one
  record; `status`, a report's loop's `done` or `failed`.

**`end` is the send-if-all-land flag.** `reply`, `silent` and `handoff` say: if every action of this
step lands, send the reply, write the record, open the tasks and start their implementers, and the
loop is done. `next` says: run these, and show me the results. The reply and the record are written
up front, in the same answer, so most asks end in one step.

**What code cannot carry out, or would carry out wrongly, is sent back** by a validator, once, with
why (`structured()` asks again, at the price of the whole prompt): an action not on the menu, values
its action refuses (`check_values`), a field set twice, an instruction empty or that does nothing, a
start of the teleport scenario that moves nobody; `next` with nothing to run or read, or with a
hand-off or a follow-up; `reply` with no reply; `handoff` with neither a hand-off nor a follow-up; a
reply over 300 characters (a hard cap over the prompt's soft 200: a reply a little over 200 goes out
rather than cost a second call); a reply that promises work when the step ends `reply` and starts
nothing (`respond.promises()`, its offers left out: a status of the speaker's open task passes, and
a report's loop whose follow-up already runs is not checked); a hand-off's name not
`^[a-z][a-z0-9_]{1,31}$`, `cast`, an action's name or another hand-off's, a brief with a part left
empty or no `done_when`; a follow-up that names no task, a task followed up twice, a follow-up that
does not cancel and has no brief; a Config to read that is no class's name
(`^[a-z][a-z0-9_]{0,63}$`); and on the last step, any action or read (`read_configs` too), an ending
but `reply` or `handoff`, or a follow-up of a task not under TASKS.

**What would have no effect is dropped and logged, never sent back**: a status outside a report's
loop or on `next`, a reply on `next` or `silent`, a record on `next`, and reads under an ending (but
on the last step). A hand-off or a follow-up under `reply` or `silent` is carried out as `handoff`.

## Running a step

[`act.py`](brain/act.py), code, no model:

1. **The checks that need no model**: each follow-up's task must exist. A refusal is a result,
   never an exception: the next step reads it.
2. **The actions**: `together` all at once; `in_turn` one after another, stopping at the first that
   does not land, the rest `not run`. The same action with the same values twice in a step runs
   once. Each is one of the game engine's ops, run as `vfg action run <action> key=value …` with no
   task (only an implementer's work is one). Its values are checked first, as vfg checks them; a
   scenario's or a mark's args go to the engine, which checks them against the class and refuses in
   words. An `engine_config` of a class whose Config no earlier step of the loop read is refused
   here. Every action that reached the engine, landed or not, makes the next loop read the engine
   afresh.
3. **The reads**: the player's items by slot, from a `vfg state --json --items` fetched for that read
   and waited for up to 15 s (two reads, two fetches), its own fetch's answer and never a state
   another fetch left from before the step's actions; the name matched in any case. A fetch that
   fails is refused (nothing read, try again), and so are Minecraft not answering and a player not
   online. A Config read is `vfg engine config get <class>`, never kept from before: its file and
   lines and the class as written, shown to the next step whole (at most 8000 characters), its
   lines kept, and whose (the library's, or this server's version over it); the engine not running,
   a class it has not, and a class neither the library's nor in `made/<server>/content/` are refused.

**An action lands when it ran without an error**: vfg checked its values, the engine did it (and
Minecraft did not refuse a command a scenario sent), and it did not say it changed nothing. Each tool
comes to one of:

- `landed`: its summary (a scenario that goes on says its instance, which `engine_stop` takes) and
  the numbers vfg clamped;
- `changed nothing`: it ran, but its facts say it changed nothing (`changed` empty: an
  `engine_config` to the Config as it already is). Not landed, so a reply written up front ("fixed
  it") never goes out on it;
- `refused`: it did not happen. Refused before it ran, here (a value, a Config not read first, an
  action no longer on the menu) or by vfg (a value or a key, `vfg refused it before it ran: …`); a
  player not online; or the engine's error, and for a command a scenario sent Minecraft's, with the
  reference of every command it used (the args whose type ENGINE gives as a command): this server's
  syntax and the wiki's Java Edition notes ([`refs.py`](brain/refs.py)), at most 6000 characters;
- `did not run`: vfg could not run it (not configured, the box out of reach, Minecraft or the game
  engine not running: vfg's exit 3 to 6; vfg itself missing). Nothing of it happened, and the
  prompt says to run it once more;
- `failed`: it timed out, or partly happened. It may have happened, and is never run blindly again;
- `not run`: in turn, after one that did not land;
- `read`: a player's items, or a Config as written.

Nothing is retried in code: a refused command goes back to the next step, which fixes it.

**A step lands when every action landed and no tool was refused.** A step that lands and ends
`reply`, `silent` or `handoff` is the loop's last, and code sends its ending (below), with no further
call. A step that does not land, or ends `next`, sends nothing at all, no reply, no record and no
task, and its block goes into THE LOOP SO FAR: each tool with its values, its state and its words (at
most 600 characters), and what the step meant to end with, marked NOT SENT. The next step runs only
what is still to do, and writes the ending again.

## The game engine

Everything that happens in the game is an engine class (the game engine spec,
[`engine/WRITING.md`](../engine/WRITING.md)): an ability (granted, then cast), an item (held), a
mark (applied, for a while), an actor (a body a scenario spawns) or a scenario (started, with its
args); one class a file, the library's `engine/library/<id>.py` and this server's
`made/<server>/content/<id>.py`, one version of each for everyone, run by one process on the box.
The brain reaches it through reads and its menu, and nothing else:

- **THE WORLD NOW's engine part** ([`world.py`](brain/world.py)): one `vfg engine overview --json`,
  the engine's classes, instances, places and incantations in one SSH call, fetched at a loop's
  start beside `vfg state --json --items` and waited for up to 3 s (past that the last good read is
  shown, and the fetch finishes for the next call); each read is shared while younger than 2 s, and
  an action that reached the engine makes the next loop read it afresh (`engine_changed()`).
  RUNNING NOW is every instance the engine runs, scenarios first: its name (what `engine_stop`
  takes for a scenario's), its kind and class, whose it is (`granted to Steve`, `held by Steve`,
  `on Steve`, a scenario's `for the world`, an actor's `in the world`) and when it ends, in UTC; at
  most 40, the rest counted. SAVED PLACES are the engine's own, per world: `castle 197 68 251
  overworld (castle_steve)`, a place's name, where, and the task that saved it. INCANTATIONS is each
  binding, `- "fire ball" for Steve: casts fireball`, or `starts meteor {"at":"caster"}`: the bridge
  casts a phrase it hears in the engine with no loop, and its `game, cast` row is context. ENGINE is
  every class by kind, scenarios first, its summary cut to 120 characters, and under a scenario and
  a mark its args, one `args.count: whole number 1..100, default 10: how many fall` line each. While
  vfg says exit 6 each part says the engine is not running; a read that fails keeps the last good
  parts, and a part with none, or one the engine refused, reads `unknown`, with why, and a log line.
- **The menu** is the engine's eleven actions (`actions/engine_*.py`, each naming its op in
  `engine`: [`actions/README.md`](../actions/README.md)): `engine_start` (a scenario and its args)
  and `engine_stop` (a scenario, every run of it, or one instance), `engine_give` and `engine_take`
  (an item), `engine_grant` and `engine_revoke` (an ability), `engine_apply` (a mark, its args and
  seconds) and `engine_clear`, `engine_bind` (a player's phrase to an ability, or to a scenario with
  set args, `caster` for a Player or Place arg filled by whoever casts it; `cooldown_s` for a
  scenario's; its aliases made as it is bound) and `engine_unbind`, and `engine_config`. Each runs
  as `vfg action run` runs any action, and vfg sends its op to the engine. A start waits for a
  one-off scenario's result (vfg's `VFG_ENGINE_START_WAIT_S`, 15 s) and answers with it; one that
  goes on answers that it is running as its instance. The prompt names the everyday scenarios by id:
  `command` (raw server commands, `args.commands`), `teleport`, `save_place` and `summon_around`. A
  grant and its bind never go in one step (the prompt's rule).
- **A Config rewritten** (the spec's "rewritten by the brain"): `read_configs` first, ending `next`;
  then `engine_config` with `class_id` and `config`, the whole class as Python, as read, comments
  and all. An `engine_config` of a class whose Config no earlier step of the loop read is refused at
  the tool, before it runs. vfg splices it into the class's file on this PC, checks it, deploys and
  reloads it; a refusal comes back with the check that failed, the old version still running, and
  one already as written comes back `changed nothing`. A library class's first rewrite copies its
  file into `made/<server>/content/`, which then runs in the library's place, one version for
  everyone (`toolkit/README.md`, `config set`).

## The ending

[`loop.py`](brain/loop.py), once the last step landed, in this order:

1. **Each hand-off's task opens.** Its name, which the class it makes takes too, is made free of
   every task, action and engine class (those ENGINE lists, and the files in `engine/library/` and
   `made/<server>/content/`), and of `cast`, with `_2`, `_3` … ([`names.py`](brain/names.py)). Its
   brief is rendered from its parts under `THE PLAYER'S WORDS`, `THE WHOLE PICTURE`, `WHAT THE
   PLAYER TAKES FOR GRANTED`, `HOW IT IS CAST, AND FOR WHOM` and `DONE WHEN` (one `- ` item a line),
   and kept as the task's `brief`. `AI task <name> | new: <description>` is posted.
2. **Each follow-up's task opens again** with its new brief and description, and `AI task <name> |
   follow-up: <brief, cut to 160>` is posted. **A cancel** ("never mind") is a follow-up whose brief
   is the cancel words of Prototype_Implementer_Harness, written by code: `CANCELLED: the player said
   never mind. Stop building and put everything back the way it was before the run this cancels,
   then report Cancelled: and what you put back.` The description stays, and the row says
   `follow-up: never mind — put back what its last run built`. Stopping what runs (`engine_stop`,
   `engine_revoke`, `engine_clear`, `engine_take`) is an action, and changes no task.
3. **A report's loop**: its `AI result <name> | <record>` row, and the task closed on the step's
   status (else the implementer's), or kept open with the record as its result so far while a
   follow-up to it runs, one this loop sent included.
4. **The reply**, through `vfg say … --task <name>`: the reported task, else the first opened, else
   the first followed up. A report's loop always says one: with none from the step, a line written in
   code from the player's own words (`done: …`, `sorry, that one failed: …`, `still on it: …`).
5. **A line's loop: the record**, `AI action - | <record>`: the step's, else written in code from
   what vfg ran (`<instruction> — done`, `<instruction> — refused: …`); none when the step left it
   empty and nothing ran (talk, a question back, a lone hand-off, whose task row says it).
6. **Each implementer started** on its brief, and each follow-up sent to its task's implementer
   (The implementers, below). Nothing waits for them to finish, and the turn ends. One that cannot
   start fails its task, with its `AI result` row; a task cut off before its implementer had it (the
   brain stopped) fails by name.

**A hand-off whose task would not open is never promised**: the step's reply is replaced by a line
written in code, `sorry, I couldn't start “<the player's words>” — say it again?`; after what the
loop's actions landed, `done: <what landed> — but I couldn't start “…” — say it again?`; and `the
rest is on its way, but I couldn't start …` when another task started. The record says `could not
start …`.

**What the context keeps of a loop** is one short record, its reply as the AI's line, the `AI task`
rows of its hand-offs and follow-ups, and a report's `AI result`: never its steps, its tool calls or
their results. No row is written per action.

## The limits, and a loop that fails

**At most `VFG_LOOP_STEPS` (10) steps and `VFG_LOOP_SECONDS` (120 s) a loop.** The step that starts
at the 10th, or once 120 s have passed since its turn began, is the last, and its step line says so:
it runs no action and reads nothing, and ends with a hand-off of what is left, with its brief, or a
reply of what was done and what was not, in the player's terms, offering to build the rest or asking
them to try again. The reply never mentions steps, time or limits. A step's call and its actions may
run past the 120 s.

**A step whose call fails for good** (`LlmError`: its retries and `VFG_BRAIN_TIMEOUT_S` spent, or
its answer refused twice) ends the loop, and so does a last step whose ending did not land. A line's
player hears what landed, then the error, in one chat line of at most 200 characters. An answer
refused twice is never put in the check's words, which are the model's (the `brain … FAILED` log
line keeps them): `<player>: sorry, I got tangled up on that one — say it again?`, or `<player>:
done: <the instructions that landed> — then I got tangled up on the rest, say it again?`. Any other
failure (a timeout, the box out of reach) keeps its short reason: `<player>: that errored out —
<why>`, or `<player>: done: <what landed> — then that errored out: <why>`. The record of what its
steps ran goes in as `AI action -`. A report's task is closed on the implementer's status (kept open
while a follow-up to it runs), with a record written in code, `<name> <status>: <its description>`,
then `; then <what ran>`, and its player hears a line written in code. An error nobody expected in a
line's loop is said once, in the same shape, unless the reply already went out; the record of what
ran goes in, and it is logged. One before the loop began (the world read) is said the same way, with
nothing landed. A report's task never stays open for it.

## A report's loop

The event holds the task's name, its player and their words, what it is, its latest brief and the
report whole, its done-when items included; the implementer's tool calls and trace never reach a
prompt. The step writes the player's line, the record (`ice_trail_steve done: an ice trail follows
Steve as he walks`) and the status (`done` for a cancel that put things back). When a done-when item
the player asked for did not land, it follows the task up in the same step, its brief naming what is
left, and ends `handoff`, which keeps the task open. When a follow-up to the task already runs, the
event says so: the task stays open, with the record as its result so far.

## Tasks

[`tasks.py`](brain/tasks.py): one task per implementer's work, keyed by its name (`galick_gun_styrk`,
`^[a-z][a-z0-9_]{1,31}$`, kept for life), held in memory and written through to the `tasks` table of
the box's `vfg.db`, which only the brain writes. Every change sends the whole row with `vfg task set`
in the background, one task's writes in order; the box keeps the newer `updated_at` should two ever
cross. A write that fails (EC2 out of reach for a moment) is sent again after 2, 5, 15 and 30 s,
unless a newer change to the task carries the row first. A row: `name`, `description` (what the
thing is, for whom; players never see it), `player`, `status` (`open`, `done`, `failed`), `line`
(the player's words), `brief` (the latest: a hand-off's rendered parts, a follow-up's brief, or the
cancel words), `made` (`{"content", "bindings", "grants"}`: the ids of the classes it wrote in
`made/<server>/content/`, the engine's ids of the bindings it made, `steve.frost_bolt`, and its
grants, `Steve:frost_staff_steve`; each name once, added at each report and never taken out),
`variables` (null on every task the brain opens: a class's numbers are its Config), `session`,
`result`, `created_at`, `updated_at`, `closed_at`.

- **A report** sets `made` to what the implementer says the task has made now (what its guard
  recorded over every run, checked against the disk and the engine: The implementers, below), in
  place of what the row had, as a deploy mirrors the folder: a class removed, a binding undone or a
  grant revoked leaves it (`tasks.mirror_made`; a report with nothing known, None, changes nothing).
  It takes its `DESCRIPTION:` line as the description (with none, the one the brain wrote stays).
- **TASKS** in the brain's tail ([`tasks.board()`](brain/tasks.py)): every task, oldest first, `-
  galick_gun_styrk, for Styrkaeopteryx, open, an implementer is on it, 21:40:02: <description>`, the
  time it was last written (with its date when not today), then `made: content galick_gun_styrk;
  bindings styrkaeopteryx.galick_gun; running now: <the engine's instances of the classes it made>`
  (`made: nothing yet` with none) and `last result: …`; `TASKS: none yet` with none. The brain names
  a new task in its hand-off, and follows one up by its name there. Respond's TASKS is one line a
  task, `- galick_gun_styrk for Styrkaeopteryx, an implementer is on it: <description, cut to 160>`.
- **A start** takes every row from `vfg context --json` and fails each one still open (`<name>
  failed: interrupted, the brain restarted while it ran`, with its `AI result` row). When the feed
  restarts, the rows read again are merged: one the memory lacks is added, one the read lacks (a box
  provisioned again) or holds older than the memory (a write that never landed) is written back, and
  for a name in both the memory wins.
- **A stop** fails each task whose implementer it stopped, and gives the writes still going out 5 s.

## The implementers

[`implementers/runner.py`](brain/implementers/runner.py): a task's implementer is one run at a time,
in the harness `VFG_IMPL_HARNESS` names (Claude Code or OpenCode: `implementers/harness/`), on
DeepSeek V4.1 Flash unless the settings say otherwise. It is the main agent, with nothing around
it: it writes engine classes and their tests, sets up what the job needs through `vfg engine`,
checks it, and ends with its report, which reaches the brain as a `REPORT` with what the task made.
The brain never waits for it.

- **Its system prompt**: [`prompts/implementer.md`](prompts/implementer.md), the server's name
  filled in, then `vfg prefix`: `engine/WRITING.md`, `engine/KIT.md`, and every library class's
  kind, first line, Config and Args as written ([`prefix.py`](brain/implementers/prefix.py)). It is
  read once and written to `implementer-system-<hash>.md` in the brain's folder, so every
  implementer's prompt starts with the same bytes; without `vfg prefix`, the rules alone.
- **Its first message** ([`brief.py`](brain/implementers/brief.py)): the brain's brief; `YOUR TASK:
  <name>`, the name its class (`made/<server>/content/<name>.py`, another it needs `<name>_<what>`),
  its test (`made/<server>/tests/<name>.py`) and every bind it sends (`--by <name>`) take; SAVED
  PLACES (`vfg engine places --json`); and the EXAMPLES block
  ([`examples.py`](brain/implementers/examples.py)): the classes `vfg route` picks for the job (at
  most three, by Jev, or by words when Jev is slow; the ones that run the job first, with how to use
  them as they are), whole, a made one followed by its test; the task's own test; every class the
  brief names; what the engine runs; who is online, where, the time and the weather; and every
  class this server's implementers made, by first line. The route, the state and the instances are
  read at once, for at most `VFG_ROUTE_TIMEOUT_S` and a second; what fails is said or left out, and
  the brief still goes.
- **The prompt's ladder**: use what exists (start a scenario with its args, grant, give, apply,
  bind, or one `vfg cmd`); else write one new class from the closest, importing only `vfg_engine`
  and its kit, and `vfg engine reload` it; a variant with other numbers only is the brain's Config
  change, named in the report. Every number is in the class's Config, the same for everyone, and
  where and for whom are a scenario's Args.
- **The guard** ([`guard.py`](brain/implementers/guard.py)): one check before every tool call,
  whatever the harness, the harness's own permission checks off. An implementer reads and searches
  only `engine/` and `made/<server>/` (Read, Glob, Grep; `ls`, `cat`, `sed -n 'A,Bp'`, `grep`,
  `head`, `tail`); writes only `made/<server>/content/<id>.py` and `made/<server>/tests/<id>.py`,
  with Write and Edit, never a class of a library class's id (it would run in the library's place
  for everyone); and runs `vfg engine` (`reload`; the reads; `start`, `stop`, `grant`,
  `revoke`, `give`, `take`, `apply`, `clear`, `bind`, `unbind`) in the forms its prompt gives, `vfg
  cmd`, `vfg test`, `vfg state`, `vfg history`, `python3 -m py_compile`, `sleep` and `echo`, joined
  with `&&` or `;` and piped only into `head`, `tail` or `grep`. Refused, each with one line saying
  what to send instead: `vfg engine config set|undo` (the brain's), `vfg engine spawn`, `@a` or `@r`
  for a player, a bind without `--by <its task>`, an owner's command (the engine's `REFUSED`) or a
  server-wide setting (`setworldspawn`, `defaultgamemode`, `difficulty`, `worldborder`) in a `vfg
  cmd` or in a scenario's command args, loops, variables, redirections into files, the background
  and the web. `vfg test` runs at most four times a run, and never again with nothing changed since
  that test's last run (comments, docstrings and formatting are no change); a look at the live world
  (a `vfg cmd` of `data get`, `list`, `scoreboard players get`, or an `execute` with no `run`) at
  most three times a run when there is a test server, and never once the tests are spent.
- **What it made**: the guard records each class file it writes, each binding it sends (the
  engine's id, `steve.fire_ball`) and each grant (`Steve:fireball`), over the task's runs; a task
  this process did not start begins from its row's `made` (the follow-up passes it), so the record
  covers every run. At the report they are checked against the disk and the engine (`vfg engine
  incantations`, `vfg engine instances --kind ability`), and what is there is what the task has.
- **Its test**: `made/<server>/tests/<task>.py`, a `test(t)` with one check per DONE WHEN item,
  which `vfg test <task>` writes a skeleton of when there is none, and runs on the test server
  (`VFG_TEST_BOX`): livetest's own engine with the library, this server's content and the bindings
  the task made, on a stage high in the sky with fake players
  ([`engine/README.md`](../engine/README.md), _Tests of content: livetest_). With no test server,
  `vfg test` exits 3, and the check is the engine's answer to one start, grant, give or apply.
- **Its report**: its last message, first word `Done`, `Failed` or `Cancelled` (the task's `done`,
  `failed`, and `done` for a cancel), a line per DONE WHEN item, then `DESCRIPTION:`, which code
  takes off (at most 600 characters) for the task's description. An error, a crash, its turns used
  up or a stop at its limit is `failed`; a run that ends on a summary of its conversation instead of
  a report is resumed once for it.
- **Follow-ups**: one that comes while the run is live goes into it as `MESSAGE from the brain: …`,
  and the run's one report covers it; after the run it resumes the task's session, which the task's
  row keeps (`session`), so a restart keeps it too; follow-ups queued behind a run make one run.
  With no session to resume, a new implementer starts on the task's description and the follow-up.
- **Never mind, and the limits**: a cancel goes into a live run as the cancel words, or resumes the
  last run's session with them (with no session, code alone puts back); one for a task whose first
  run has not begun reports `Cancelled` with nothing made. Near its limit (the 95th of
  `VFG_IMPL_MAX_TURNS`' 100 model requests, or a minute before `VFG_IMPLEMENTER_MINUTES`' 10) a run
  gets the `LIMIT:` words once; at the limit it is stopped and reported `failed` from what the guard
  saw: what it made and changed, and its last five commands with their errors.
- **The put-back** ([`putback.py`](brain/implementers/putback.py)): before each call the guard lets
  through, the run's snapshot keeps what the call is about to change: a made file's bytes, or that
  it was not there; a binding as the engine lists it; whether the player had the ability. After a
  cancel, a stop at the limit, or a warned run that did not report done, code makes each what it
  was: each file written back or removed, one `vfg engine reload` when a class file was among them
  (a class that goes with its file takes its instances with it), each binding the run made unbound
  and each it replaced or unbound bound again as it was (its aliases, everyday aliases, cooldown
  and `--by`, with `--aliases`: no alias call), each grant it made revoked and each it revoked
  granted again. Its line, `Code put back: …`, ends the report, and what it could not put back makes
  the report `failed`. A scenario the run started, an item it gave, a mark it applied and a place it
  saved are the implementer's own to undo. The snapshot is kept until the task's next run, in memory
  and in `implementer-snapshots/<task>.json`, so a cancel after the run, or after a restart, puts
  back from it.
- **Usage**: one `vfg usage add` row a run, its ref the task's name, each model request priced at
  its own size and time (`implementers/config.py`'s `PRICES`).

## Run it

```sh
vfg brain start     # brain/run, detached: pid ~/.vfg/brain/brain.pid, log ~/.vfg/brain/brain.log
vfg brain status    # running or not, and the open tasks, read from the box
vfg brain stop      # SIGTERM to the brain alone: what waits for a turn dropped (its lines marked read), the loops in flight given up to 10 s, implementers stopped, their tasks failed
```

While it stops, an implementer that ends, or a report the stop dropped while it waited for its turn,
closes its task on its own status, with a record written in code (`castle_steve done: <its
description>`, an `AI result` row) and no model call. `vfg brain stop` signals the brain's pid, not
its process group, where the implementers' harnesses run: the brain interrupts them itself; the
group gets SIGKILL only after 15 s.

`run` syncs the dependencies from `uv.lock` and execs `python -m brain`. A start takes the lock,
sets aside any `tasks.json` in `~/.vfg/brain/` (renamed `tasks.json.retired-<YYYYmmdd-HHMMSS>`, never
deleted and never read), runs `vfg up`, sets the watchers (`join,leave,death,advancement,server`),
Jev's lookout ([`prompts/lookout.md`](prompts/lookout.md)) and Jev's menu (every action's line, set
again when an action changes), loads the context and the tasks from `vfg context --json` (tried
again every 5 s while it fails), fails the tasks a crash left open, follows `vfg watch --after
<seq>` from the feed's highest seq that read gave (`last_seq`, its last object: with no row in the
last 6 h, the watch still never replays old rows as new), and greets, unless nobody is online. When
the feed restarts while it runs (`vfg watch` says `feed restarted`, or gives a seq it had passed), it
reads the context again and follows the new feed from the `last_seq` that gives. **The first start
on a new server version fetches the command references** (about 95 s, in the background, into
`~/.vfg/brain/refs/<version>/`, tried again every 5 minutes while it fails); until they are in, the
opening says the command list is not loaded, and a refused command's result comes without its
reference.

The OpenRouter key: `OPEN_ROUTER_API` in the environment, else alone in the file
`VFG_OPENROUTER_KEY` names (default `~/.vfg/openrouter.key`, mode 600), as `structured()` reads it;
the implementers read it too on the `openrouter` provider, and `DEEPSEEK_API_KEY`, from the
environment only, on `deepseek` (Settings, below).

## Settings

From the environment, then `~/.vfg/config`:

| Key | Default |
|---|---|
| `VFG_OWNER` | none: the owner's Minecraft name, named in the opening; the owner's word wins |
| `VFG_BRAIN_MODEL`, `VFG_BRAIN_REASONING` | `google/gemini-3.8-flash`, `low`: every step of the loop |
| `VFG_BRAIN_PROVIDERS` | OpenRouter's choice; comma-separated providers to try first, for the loop's steps (`none`: OpenRouter's choice) |
| `VFG_LOOP_STEPS` | `10` steps a loop at most, the last ending it; at least 1 (with 1, the only step is the last: a reply or a hand-off) |
| `VFG_LOOP_SECONDS` | `120`: a step that starts once this many seconds have passed since its turn began is the last |
| `VFG_TURN_WAITING` | `3` lines that may wait for a player's turn; one more is turned away (`0`: none waits) |
| `VFG_TURN_SETTLE_S` | `3` s at most that a turn waits for the AI rows it wrote to come back on the watch before the player's next turn starts (`0`: no wait) |
| `VFG_RESPOND_MODEL`, `VFG_RESPOND_REASONING`, `VFG_RESPOND_PROVIDERS` | `~deepseek/deepseek-v4-flash-latest`, `off`, `parasail,wafer,phala,together,venice` (tried first, fastest first; `none`: OpenRouter's choice) |
| `VFG_RESPOND_TIMEOUT_S` | `20` s per Respond call, its retries included; past it the line is handed up |
| `VFG_BRAIN_CHECKPOINT` | `5` minute-blocks between cache marks, the loop's and Respond's |
| `VFG_BRAIN_HISTORY_HOURS` | `6`: how much of the feed the context starts with |
| `VFG_BRAIN_TIMEOUT_S` | `45` s per step's call, its resend and retries included: a step on Gemini 3.8 Flash low thinks first, and at 20 s two of the first pilot's timed out |
| `VFG_DISPATCH_ACTION_TIMEOUT_S` | `20` s per engine op, unless its action sets `timeout_s` (`engine_start` 45, `engine_bind` 40, `engine_config` 60); vfg's answer is waited for 10 s past it |
| `VFG_ENGINE_START_WAIT_S` | `15` s, 0 to 30: how long vfg's start waits for a one-off scenario's result (vfg's own) |
| `VFG_SERVER` | `main`: the server whose `made/<server>/content/` the implementers write, and a new task's name keeps clear of |
| `VFG_REPO` | this repo: `actions/`, `engine/` and `made/`, and the implementers' working directory |
| `VFG_HOME`, `VFG_BRAIN_DIR` | `~/.vfg` and `~/.vfg/brain`; from the environment only for `VFG_HOME` |
| `VFG_IMPL_HARNESS` | `claude-code`, or `opencode`: the harness an implementer runs in (`brain/implementers/harness/`) |
| `VFG_IMPL_PROVIDER` | `deepseek` when `DEEPSEEK_API_KEY` is set, else `openrouter`: DeepSeek V4.1 Flash at that provider; or `anthropic`, Haiku 5.5 on your Claude subscription, Claude Code only and untested |
| `VFG_IMPL_MODEL` | the provider's: `deepseek-flash`, `deepseek/deepseek-v4.1-flash`, `claude-haiku-5-5`; `VFG_IMPLEMENTER_MODEL` is read when it is empty |
| `VFG_IMPL_BASE_URL` | the provider's Claude-compatible address: `https://api.deepseek.com/anthropic`, `https://openrouter.ai/api`; none for `anthropic` |
| `VFG_IMPL_OR_ORDER`, `VFG_IMPL_OR_IGNORE` | `digitalocean,ionstream,together` and `venice,morph,relace`: on `openrouter`, the providers each request tries first and never, fallbacks allowed, so a run keeps one provider's cache; `none` empties one |
| `VFG_IMPL_EFFORT`, `VFG_IMPL_MAX_TURNS` | `low` (the `anthropic` provider's only), `100` model requests a run, warned at the 95th and stopped past the 100th |
| `VFG_IMPLEMENTER_MINUTES` | `10`: warned a minute before, stopped at it and its task failed |
| `VFG_IMPL_CACHE_TTL` | `5m` (or `1h`): the `anthropic` provider's prompt cache; `VFG_SUBAGENT_CACHE_TTL` is read when it is empty |
| `VFG_OPENCODE_BIN` | `opencode` on the PATH, else `~/.opencode/bin/opencode`: OpenCode's program; from the environment only |
| `VFG_ROUTE_TIMEOUT_S` | `3` s, 0.1 to 60, that `vfg route` may take for an implementer's EXAMPLES block, which waits a second more at most |
| `VFG_TEST_BOX` | none: the file of the test server's settings, where `vfg test` runs an implementer's test; with one, a run's looks at the live world are counted; with none, `vfg test` exits 3 and the check is the engine's one answer |
| `VFG_TEST_TIMEOUT_S` | `60` s a test may run, 5 to 600, before it is stopped |

A brain setting whose number does not read, or is under its least, is logged and its default used;
an implementer setting's is its default.

## Files

In `~/.vfg/brain/`: `brain.log`, `brain.pid`, `lock`, `refs/` (the command references, by server
version), `implementer-system-<hash>.md` (the implementers' system prompt),
`implementer-snapshots/<task>.json` (each task's last run's snapshot, for the put-back), `pycache/`
(`py_compile`'s), and `opencode/` on the OpenCode harness. The tasks are not here: they are rows of
the box's `tasks` table (Tasks, above), which `vfg route --task` and `vfg brain status` read.

`brain.log` is one `<ISO time> <area> <message>` per line. A loop's lines, the time left off, its ref
`#<seq>` for a line and the task's name for a report:

```text
exec - #812 s1 #1 engine_start ok (exit 0) 412 ms: <vfg's summary, 300 characters>
action #812 s1 #1 engine_start ok: <its summary, 200 characters>
action #812 s1 #2 engine_start refused: <the engine's or Minecraft's words, 200 characters>
brain #812 s1 <ms> ms $<cost>, <in> in, <n>% cached, <out> out | in_turn: engine_start (take Steve home), engine_start (set the time to day) || end silent || record: took Steve home and set the time to day -> engine_start landed, engine_start refused (not landed)
brain #812 s2 FAILED (<ms> ms $<cost>, …): <why>
loop #812 Steve: 2 step(s), $<cost>, <n>% cached, reply at <s> s, <s> s, ended failed
```

- `brain <ref> s<n>`, one a step: what its call spent (`$<cost>+` when an attempt in flight was cut
  off, its cost unknown), what it did (`<order>: <action> (<instruction>), …`, `read <player>`, `read
  the Config of <class>`, `hand off <name>`, `follow up <name>`, `cancel <name>`, or `nothing run`;
  then `|| end <ending>` and its reply, record and status), and what came of it (`<action> <state>,
  …`; `read <player>'s items`, or `read <player>'s items: refused`; `follow up <task>: refused`; and
  `(not landed)` when it did not); `FAILED (<spent>): <why>` for a call that failed for good.
- `step end <ending>: dropped, it would do nothing: …`: a field the step's answer set that its
  ending ignores, or `end reply (it hands off: end handoff)`.
- `exec - <ref> s<n> #<i>`, one an action: vfg's run, its kind, its exit and how long it took.
- `action <ref> s<n> #<i>`, one an action: how vfg's run went (`ok`, `refused`, `partial`, `timeout`,
  `not_run`) and its summary.
- `loop <ref> <player|nobody>`, one a loop: its steps, its cost, how much of its prompts the cache
  read, the seconds from its turn's start to its reply (`no reply` with none), its seconds in all,
  and how it ended: `reply`, `silent`, `handoff`, `failed`, `cut off` (the brain stopped) or `broke`
  (an error nobody expected).
- `respond #<seq>`: what its call spent; `handed up: <why>`, or the line and its reply.
- `turn`: a line that waits for its turn, one turned away, a report first in line; once a line or a
  report that waited takes its turn, how long it waited (`turn #36 Steve waited 4.2 s for their
  turn`, `turn report galick_gun_styrk waited 4.2 s for their turn`; one that took its turn at once
  logs nothing); and the AI rows a turn did not see come back within the settle.
- `task`: a task started, followed up, kept open or failed, and the hand-offs that did not open.
- `world`: a state or engine read that failed, or took over its wait.
- `stop`: what the stop dropped, each line it marked read.
- An implementer's: `implementer <task> …` (its run started or resumed, each tool call, what it
  says, how it ended, its report and what it made), `request <task> …` (each model request: its
  tokens in, cached, written and out, its size and price), `denied <task> …` (each call the guard
  refused, at the run's end), `brief` (each part's size), `route` (the router's picks and what was
  loaded), `prefix`, `guard`, `putback` and `usage` (the run's row).

## Test it

```sh
cd brain && uv run --group dev python -m pytest --ignore=tests/implementers -q   # the brain: about 1 s
cd brain && uv run --group dev python -m pytest tests/implementers -q              # the implementers: about 6 s
```

The brain's: no model, no network, no process, no sleeping: one fake `structured()` with canned
answers (a list of them for a loop's steps), contexts built by hand, a fake `vfg` that records argv,
the menu loaded from a copy of `actions/`, a fake ENGINE, fake implementers and a clock moved by
hand. `tests/test_turns.py` holds the turns to their order, the limit and the front of the queue;
`tests/test_refs.py` checks the command references against the real 26.3 command tree and a few wiki
pages; `tests/test_engine.py` the game engine's part of the world, its bindings among the
incantations, its Config read and rewrite, and its actions.

The implementers': HOME a temporary folder, no `VFG_*` setting or provider key from outside, a fake
`vfg` and a fake harness whose runs give scripted events, each tool call put through the guard;
both adapters against fakes of the Claude Code client and of `opencode serve` (the OpenCode
plugin's JavaScript under node, when node is there).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §4 the `tasks` table and `usage`, §7 the feed and its
`AI` rows, §8 the `vfg` commands it runs, §9 the brain, its loop, its turns and its tasks, §10 the
implementers it starts, §14 its actions.
