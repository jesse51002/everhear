# brain — the conversation agent, the router and the loops

A small always-running Python process on the owner's PC, in place of the playtest's conversation
agent and orchestrator agent and the dispatcher's selector (the specs: Prototype_Brain,
Prototype_Named_Tasks, Prototype_Brain_Loop, Prototype_Implementer_Harness, then
Prototype_Conversation_And_Router, and the game engine spec). It follows the feed with `vfg watch`,
keeps every row in one shared context, and runs three agents on it, each a `structured()` call
([`lib/py/vfg_llm.py`](../lib/py/vfg_llm.py)) on DeepSeek V4.1 Flash: the conversation agent and
the router with no reasoning, the brain's steps at low (Settings, below):

- **the conversation agent** ([`respond.py`](brain/respond.py), its class still `Respond`), for every
  line said to the AI and every implementer's report: one call that answers at once, with a reply,
  a reply and a hand-off, or silence. It has no actions and picks nothing.
- **the router** ([`router.py`](brain/router.py)), for every hand-off: one call whose whole job is
  to pick who does the work: a new brain loop, a loop already on that thing, a new implementer,
  whose brief it writes, an implementer already on it, or nowhere, with a line of its own. Code
  places the work there.
- **the brain**, the loops: an agentic loop of steps, each step one call. A step runs actions from
  the menu, the game engine's ops, written in full, together or in turn; reads another player's
  items, or an engine class's Config; hands new work off to a coding implementer with a brief in
  parts; follows a task up, or cancels its last run; and ends the loop with a reply, with silence
  or with the hand-off. When every action of a step lands, its reply goes out and the loop is done in that one step; when one
  does not, every result comes back and the next step fixes it. The orchestrator call and the retry
  planner are gone as calls of their own: what they did is tools of the loop now. Loops hold no
  turn: they run side by side, one player's too.

The implementers it starts ([`brain/implementers/`](brain/implementers/)) write engine classes in
`made/<server>/content/`, each with its test, and report back (The implementers, below).

A player's lines to the AI take turns, but a turn is only the conversation agent's call and the
router's: the next line waits for those two calls, never for a loop, and different players' turns
run at once.

## The events

| Event | Comes from | What runs |
|---|---|---|
| `LINE` | a `LINE` Jev classed `to_ai`: said to the AI | the front turn, in the player's turn: the conversation agent answers, and when it hands off, the router picks who does the work and code places it (The router, below): a new loop, which runs on its own; the line added to a loop already on that thing; an implementer, new or already on it; or nowhere |
| `REPORT` | an implementer ending (`Implementers`' `on_report`) | at once, never queued: once the task has taken what the implementer made and its `DESCRIPTION:` line (Tasks, below), the conversation agent reads the report whole and writes the player's chat line, the task's record and its status; what is left, if anything, the router places (A report, below) |

A loop's own event is the `LINE` the router sent it, or a `REPORT`: the work an implementer's report
left, which the router sent it. A line the router adds to a loop already on that thing reaches the
loop before its next step, as a `LINE ADDED TO THIS LOOP` block (Loops, below).

A `to_ai` `LINE` is one conversation agent call, and one router call when the agent hands it off
(Prototype_Addressing_The_AI: Jev passes a line only when it is said to the AI, naming it, "AI"
or "brain", or answering what the AI asked; its two classes for the AI before, `action_oriented`
and `conversational`, are one now, and a feed row with either old name is read as `to_ai`,
`context.py`). There is no other call: no model is called for what nobody asked. A game event, a system notice and the
brain's own rows wait for the next front turn or report, which reads them as NEW. A line already in
the feed when the brain read its context, said more than two minutes before that, is history: never
acted on. A line that arrives on the watch is live, whatever its time (the box's clock may be a
minute off the PC's), except the backlog `vfg watch` prints once it reconnects, which is judged by
its age, as at a start, until a line of the last two minutes arrives.

A `LINE`'s text can hold several pieces one player said in a row, joined with " … ", the ask last:
Jev's gather call joins the earlier pieces that belong to the ask (`docs/CONTRACTS.md` §5). Every
prompt says so, and to read them as one ask.

## The conversation agent

**The conversation agent** takes every line said to the AI first: DeepSeek V4.1 Flash, reasoning
off, on the same context, answering at once, with no actions of its own. It keeps up the banter. It
ends a line in one of three ways:

- **a reply**, and nothing to do: talk; a rule or a preference about the future only ("talk like a
  pirate from now on"), which holds because the brain reads the same conversation; and a question
  about work under way ("is it done yet?"), answered from WORK UNDER WAY and TASKS;
- **a hand-off**, with a short acknowledgement ("On it!", "Still on it.") or none: anything that asks
  for something now, a change to what is already going on in the world, a correction of what the AI
  just did or an addition to an ask, a rule the game itself would have to enforce ("from now on keep
  creepers away from the base"), an answer to the AI's question, a question only a look at the game
  answers, the same ask again while it is under way, and anything it is unsure of;
- **silence**: nothing needs saying and nothing needs doing.

It decides first (`reason`, which it must write, then `hand_off`, then `reply`), and handing off is
the default ([`gate_bench.md`](../integration/replay/gate_bench.md),
[`respond_bench.md`](../integration/replay/respond_bench.md)). It picks nothing, but it is shown what
is running: WORK UNDER WAY in brief, every brain loop thinking or ended a moment ago, every player's,
and TASKS one line a task. Four checks in code, after the call, none sent back:

- **Jev unnamed** (`unnamed()`): a sentence that names Jev or the filter is dropped, a report's
  reply's too;

- **the guard** (`promises()`): a reply with no hand-off that offers or promises something in the
  game, "want me to …?" or "I'll fix it", is handed off instead: a promise is kept as the hand-off's
  acknowledgement, unless it says the thing is done, and an offer is dropped. "Coming up" is a
  status, not a promise, for a player with work under way: a loop thinking, an implementer running,
  or an open task;
- **beside a hand-off**, a reply that offers, or says the thing is done, ready, gone or there
  (`claims_done()`), is dropped: nothing has happened yet;
- **the cut**: a reply over 300 characters is cut at a word to 200.

A call that fails, or runs past `VFG_RESPOND_TIMEOUT_S` (20 s), hands the line off with no reply.

## The router

**The router** ([`router.py`](brain/router.py), its rules [`prompts/router.md`](prompts/router.md))
takes every hand-off, the conversation agent's and what an implementer's report left, and picks who
does the work: one call, DeepSeek V4.1 Flash, reasoning off. Its answer, `Route`, decides first:
`reason`, ending with the choice and its target (`… so: loop L3`), then `to`, then only the field
that choice uses:

| `to` | with | the work goes to |
|---|---|---|
| `new_loop` | nothing | a new brain loop: what the engine's actions do now, a class's numbers, a stop of what runs, a question only a look at the game answers, an ask of several parts, and anything not clearly an implementer's |
| `loop` | `loop`, an id WORK UNDER WAY offers (`L3`) | the loop already on that thing, thinking or ended and still offered: a repeat, a correction, an addition, a "never mind" |
| `new_implementer` | one hand-off: the task's name, description and whole brief, as the brain writes one | a new task, its implementer started on the brief |
| `implementer` | one follow-up a task the line changes: the task under TASKS, its brief and description, or `cancel` | that task's implementer: a message into its run, or its session resumed; a cancel sends the cancel words (The ending, below) |
| `nowhere` | `reply`, or none | nobody: the line asks for nothing, or the thing is under way and the line only presses for it ("hurry up", "is it done?"); the reply, when it has one, says so |

**While an implementer is on a thing** (TASKS: `an implementer is on it`), its rules send a line
that asks that thing to change, stop or be unbound to that task, or to the loop already on it, or
nowhere when it adds nothing: never a second worker beside it, since a loop that stops or unbinds
what the implementer then starts again to test it undoes its work.

**Its prompt is laid out as a brain step's**, so it reads the same rows the same way: the opening,
marked (its rules, the owner named, the menu, the game engine's ops in full, and Minecraft's
command list, so it knows what the brain can do); the conversation in minute-blocks, the last two of
its checkpoints marked, as a step's; then the tail, never cached: the world as glanced at (every
player without their items, RUNNING NOW, INCANTATIONS and ENGINE, every class the engine has: "make
it bigger" may be a number in a class's Config, the brain's, and not an implementer's build), TASKS
in full, WORK UNDER WAY in full, and the event: `THE LINE` with the conversation agent's answer and
reason, or `THE REPORT` with what it left. Its schema is the same for every catalog, loop and task,
every property required, so the cached prefix holds; what depends on the moment is a validator.

**Sent back** by a validator, once: a reason whose last "so:" names another choice than `to`;
`loop` with an id WORK UNDER WAY does not offer (`L3`, `l3` and `3` read as one); `new_implementer`
without exactly one hand-off, or one the brain's own checks refuse ([`checks.py`](brain/checks.py),
in the same words as a step's); `implementer` with no follow-up, or one the same checks refuse (no
task named, a task twice, no brief and no cancel, a task not under TASKS); `nowhere` with a reply
over 300 characters, or one that promises work, or, for a line, says a thing is done or fixed,
since nothing is placed; and for a report's hand-off, `loop`, or more than one follow-up. **Dropped and
logged**: what the choice does not use (a loop's id on `new_loop`, a reply on a placed route, …).

**Code places it**, with no model: `new_loop`, a new loop, an asyncio task of its own; `loop`, the
line added to that loop (Loops, below), and a loop that can no longer take one (deleted, past its
window, or at the cap with no step left) gets a new loop instead, logged; `new_implementer`, the task
opened (`AI task <name> | new: …`) and its implementer started; `implementer`, each task opened again
(`AI task <name> | follow-up: …`) and its brief sent, a task gone meanwhile dropped and logged, and
a new loop instead when none was left; `nowhere`, nothing. A router call that fails, or runs past
`VFG_ROUTER_TIMEOUT_S` (30 s), places the line in a new loop.

**One line per ask: the hold.** While `VFG_ROUTER_REASONING` is `off` (`Config.hold`), the
conversation agent's line to a hand-off is held until the router has answered, and then one line is
said: the agent's when the work was placed somewhere; the router's in its place when it went nowhere
(the agent's when the router wrote none); and, when a new task would not open, code's `sorry, I
couldn't start “<the player's words>” — say it again?` in its place, since nothing was started. With
the router reasoning, nothing is held: the agent's line goes out at once, and the router's own, or
code's, after it. A reply with no hand-off always goes out at once.

## One turn per player

[`turns.py`](brain/turns.py), in front of the conversation agent and the router:

- **A live line takes its player's turn, or waits for it**, in the order the lines came.
- **At most `VFG_TURN_WAITING` (3) lines wait.** One more is turned away: marked processed, never
  answered, and that player alone is told which lines still wait, by a private `vfg say --to
  <player>` that nobody else sees: `Still on: “a meteor staff”, “and a shield”, “and a bow”. I can't
  take any more right now — wait a bit, then ask again.` Each line is cut short so the whole stays
  one chat line under 200 characters; the first five are named and the rest counted. The whisper's
  `AI say` row reads `(to <player>) …`.
- **A turn is the conversation agent's call and the router's**: it ends once the router has placed
  the line (a loop started, a line added, an implementer started or sent its follow-up), so the
  player's next line is routed knowing where this one went. It never waits for the work: a loop runs
  on its own and holds no turn, and so does an implementer.
- **An implementer's report never queues**: it goes to the conversation agent at once, beside
  whatever its player's turn is doing.
- **A turn ends when its run ends**, whether it returns, raises or is cancelled, and then waits, at
  most `VFG_TURN_SETTLE_S` (3 s), for the AI rows it wrote to come back on the watch (its chat lines,
  a whisper apart; its task rows), so the player's next turn reads what this one did. A row whose
  write failed is not waited for, and a run that raised or was cancelled skips the wait. A loop's
  rows and a report's are nobody's turn's, and never waited for. Then the next one waiting takes the
  turn.
- **A stop drops everything waiting**: a line dropped so is marked read and never answered, after a
  restart too.

## Loops

[`loops.py`](brain/loops.py) keeps every loop the router can send a line to, thinking or ended, every
player's, in memory only: a restart forgets them, as it forgets what waited for a turn.

- **A loop** has an id, `L<n>`, counted per run of the brain; its player (the speaker, or a report's
  task's player); the lines it holds, the first included; and a state: `thinking`, `ended` (saved,
  its prompt kept) or `deleted`. Each is an asyncio task of its own, so loops run side by side, two
  asks of one player too. A loop reads the world and TASKS once, at its start, and keeps them for
  every step and every take-up; what changed comes back in the results and with a line added.
- **Its budget**: `VFG_LOOP_STEPS` (10) steps for its first line and as many more for each line
  added, never past `VFG_LOOP_CAP` (20): its limit is the smaller of the cap and `VFG_LOOP_STEPS`
  times its lines. The step at the limit is the last (The limits, below). A loop that reaches the
  cap is deleted after its ending: it never takes a line again, and the next line on that thing
  starts a new loop.
- **A line added to a thinking loop** waits until its next step reads it, in a `LINE ADDED TO THIS
  LOOP` block (A step's prompt, below). When the step in flight was its ending, the ending goes out
  and the loop takes one more step anyway, for the line, the ended step shown as `ENDED <end> AND
  SENT: …`. A thinking loop takes a line however long it has run, unless the step in flight is at
  the cap: no step is left to read it, and the line gets a new loop.
- **An ended loop is saved**, and a line added within `VFG_LOOP_TAKE_UP_S` (120 s) of its creation
  takes it up again while it has a step left under the cap: a new step after its last, on the same
  prompt, every earlier step read from the cache, with the line's block. Past the window it is gone,
  as if deleted, and dropped from memory; the next line on that thing starts a new loop.
- **No line is lost**: a loop's check for a line waiting and its change to `ended` are one step for
  the event loop, with no await between them, so a line added in that instant is either read by the
  loop or takes it up.
- **A loop's clock** starts as it is created, or taken up again: its time to the reply counts from
  there, after the front turn's two calls.
- **WORK UNDER WAY** is every loop a line can be added to, oldest first, every player's. The
  router's, in full (`WORK UNDER WAY: no brain loops` with none):

  ```text
  WORK UNDER WAY (every brain loop a line can be added to, every player's, oldest first; implementers are under TASKS):
  - L1, for KrownJM, thinking, step 2 of 20, started 18:29:41 (52 s ago); lines: #58 “can you teleport me to Stirka” 18:29:40, #60 “teleport me to Stirkosaurus” 18:29:50 (added, not read yet)
  - L2, for Styrkaeopteryx, ended, saved: can take a line for 68 s more; 1 of 10 steps used, started 18:29:41 (52 s ago); lines: #34 “give me food” 18:29:40; did: gave Styrkaeopteryx 64 steak; last reply: “Here's a stack of steak!”
  ```

  The conversation agent's, brief (`WORK UNDER WAY: nothing right now` with none), each loop `—
  being done now (<s> s)`, `— done a moment ago`, `— failed a moment ago` or `— passed on to be built
  a moment ago`, with its last reply:

  ```text
  WORK UNDER WAY (what is being done for the players now, or was a moment ago):
  - KrownJM: “can you teleport me to Stirka”, then “teleport me to Stirkosaurus” — being done now (12 s)
  - Styrkaeopteryx: “give me food” — done a moment ago: “Here's a stack of steak!”
  ```

  A report's loop shows `what <task>'s report left: …` in place of its lines. Implementers are not
  listed: TASKS marks the tasks one is on. Both are from
  [`tests/test_loops.py`](tests/test_loops.py).

## The shared context and the cache

[`context.py`](brain/context.py) keeps every feed row in seq order with its processed mark: the
lines Jev kept, with their class; the game's events; the system's notices; and the brain's own
`AI` rows, the only rows the AI side writes (`vfg say` and `vfg feed post AI`): what it said, the
record of each loop, each task opened or followed up, and each task's result. Nothing an
implementer writes is in it. Every front turn and every report takes in
everything unread and marks it (`vfg context mark`), so the game's events since the last read are
its NEW rows: the conversation agent's, and those of a new loop the router starts.

**A `context` line is never shown to a model**: not in the conversation, not under NEW, in no
prompt. It stays in the feed and `vfg.db`, and in the context's rows, for a memory that will
summarise it later; the brain marks it processed as it arrives (and, at a start, every one still
unmarked), so nothing waits on it. Every `GAME` row, every `SYS` row, the brain's own `AI` rows and
the `to_ai` lines are shown.

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
   since the last read; and the event, with the conversation agent's line (`(the conversation agent
   already answered the player: “On it!”; your reply follows it — never repeat it)`, or `(the
   conversation agent said nothing)`) and the router's reason (`(the router: …)`). Marked on the
   first step;
4. **THE LOOP SO FAR**, from the second step on: one block per earlier step, what it ran and read,
   what it meant to end with and what each tool came to (`ENDED <end> AND SENT: …` for an ending that
   went out before a line was added; for a step whose call failed, that nothing of it ran and the
   line `SENT INSTEAD`); where lines were added, one block: each `LINE ADDED TO THIS LOOP: LINE #60,
   03:09:50: KrownJM said: …`, with `(the conversation agent answered: “…”)` or `(the conversation
   agent said nothing)`, and `(the router: <its reason>)`, then `ROWS SINCE THIS LOOP LAST READ:`,
   every row shown since, the added lines left out, or `nothing`. The last block marked;

then the step line, never cached: `STEP 2 of at most 10.`, the loop's limit as it is now, and on the
last step its notice. Four marks in all (`vfg_llm.MAX_MARKS`).

**The conversation and the tail are read once, at the loop's start, and kept for every step**, and
for every take-up: a row that arrives meanwhile, or a change in the world, never moves the prefix,
so each step's prompt but its step line is the next step's cached prefix, and what changed comes
back in the results and in a line's block. Gemini on OpenRouter read it so: on the replay of the
2026-10-11 session (Measured, below) a loop's first step read 96.4% of its prompt from the cache,
kept between loops, and a later step 96.6%; live on realmc every step read 95%.

The conversation agent's prompt marks the last three checkpoints; its opening is its rules and the
menu's lines, and its tail has the same players without their items, RUNNING NOW, INCANTATIONS and ENGINE
from the last reads, never waited for, no saved places, TASKS one line a task, WORK UNDER WAY in brief, what is new and the line or the
report. The router's is under The router, above.

## A step

One `Step` answers every step of every loop, and its schema never changes, not with the actions, the
step's number or the last step, so nothing before the conversation changes between calls. Every
property is required in it, its default included (`call.all_required`), so the model writes each
field rather than leave one out: on the first pilot Gemini answered a report with `end` and `status`
alone (`status` was a report's field then). Its fields:

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
  record.

**`end` is the send-if-all-land flag.** `reply`, `silent` and `handoff` say: if every action of this
step lands, send the reply, write the record, open the tasks and start their implementers, and the
loop is done. `next` says: run these, and show me the results. The reply and the record are written
up front, in the same answer, so most asks end in one step.

**What code cannot carry out, or would carry out wrongly, is sent back** by a validator, once, with
why (`structured()` asks again, at the price of the whole prompt): an action not on the menu, values
its action refuses (`check_values`), a field set twice, an instruction empty or that does nothing, a
start of the teleport scenario that moves nobody; `next` with nothing to run or read, or
with a hand-off or a follow-up; `reply` with no reply; `handoff` with neither a hand-off nor a
follow-up; a reply over 300 characters (a hard cap over the prompt's soft 200: a reply a little over 200
goes out rather than cost a second call); a reply that promises work when the step ends `reply` and
starts nothing (`respond.promises()`, its offers left out: a status of the speaker's open task
passes, and a report's loop whose follow-up already runs is not checked); a hand-off's name not
`^[a-z][a-z0-9_]{1,31}$`, `cast`, an action's name or another hand-off's, a brief with a
part left empty or no `done_when`; a follow-up that names no task, a task followed up twice, a
follow-up that does not cancel and has no brief (the hand-off's and the follow-up's checks are
[`checks.py`](brain/checks.py), the router's too); a Config to read that is no class's name
(`^[a-z][a-z0-9_]{0,63}$`); and on the last step, an ending but `reply` or
`handoff`, or a follow-up of a task not under TASKS. **While nothing of the
loop has landed in the game** (a report's loop counts as landed: its implementer built something),
a step that runs nothing may not end `reply` with a reply that says it is done (`claims_done()`),
nor end `silent`, which would leave the player with the conversation agent's "On it!" alone.

**What would have no effect is dropped and logged, never sent back**: on the last step, every action
and read it still writes (nothing after it would read them; a `next` with a reply there becomes
`reply`); a reply on `next` or `silent`, a record on `next`, and reads under an ending. A hand-off
or a follow-up under `reply` or `silent` is carried out as `handoff`.

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
`reply`, `silent` or `handoff` is the loop's last, unless a line was added meanwhile, and code sends
its ending (below), with no further call. A step that does not land, or ends `next`, sends nothing
at all, no reply, no record and no task, and its block goes into THE LOOP SO FAR: each tool with its
values, its state and its words (at most 600 characters), and what the step meant to end with,
marked NOT SENT. The next step runs only what is still to do, and writes the ending again.

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
3. **The reply**, through `vfg say … --task <name>`: a report's loop's task, else the first opened,
   else the first followed up. With none from the step, nothing is said.
4. **The record**, `AI action - | <record>`, a report's loop's too: the step's, else written in code
   from what vfg ran (`<instruction> — done`, `<instruction> — refused: …`); none when the step left
   it empty and nothing ran (talk, a question back, a lone hand-off, whose task row says it).
5. **Each implementer started** on its brief, and each follow-up sent to its task's implementer,
   which waits behind the task's running implementer, then resumes its session (with no session,
   after a restart, a new implementer starts on the task's description and the follow-up). Nothing
   waits for them to finish. One that cannot start fails its task, with its `AI result` row; a task
   cut off before its implementer had it (the brain stopped) fails by name.

Then, when a line was added while the step ran, the loop takes one more step for it; else it ends,
saved, or deleted at the cap (Loops, above).

**A hand-off whose task would not open is never promised**: the step's reply is replaced by a line
written in code, `sorry, I couldn't start “<the player's words>” — say it again?`; after what the
loop's actions landed, `done: <what landed> — but I couldn't start “…” — say it again?`; and `the
rest is on its way, but I couldn't start …` when another task started. The record says `could not
start …`.

**What the context keeps of a loop** is one short record an ending, its reply as the AI's line, and
the `AI task` rows of its hand-offs and follow-ups: never its steps, its tool calls or their results.
No row is written per action. A report's `AI result` is the
conversation agent's (A report, below), never a loop's.

## The limits, and a loop that fails

**A loop has `VFG_LOOP_STEPS` (10) steps for its first line and as many more for each line added,
never past `VFG_LOOP_CAP` (20)**; a report's loop counts the report as its first line. The rules
give the same numbers (`{grant}` and `{cap}` in [`prompts/brain.md`](prompts/brain.md), filled from
those two). The step at the limit is the last, and its step line says so: it runs no action and
reads nothing (what it still writes is dropped), and ends with a hand-off of what is left, with its brief, or a
reply of what was done and what was not, in the player's terms, offering to build the rest or asking
them to try again. The reply never mentions steps, time or limits. There is no time limit: the steps
and the step timeout, `VFG_BRAIN_TIMEOUT_S`, bound a loop.

**A step whose call fails for good** (`LlmError`: its retries and `VFG_BRAIN_TIMEOUT_S` spent, or
its answer refused twice) ends the loop's run, and so does a last step whose ending did not land.
The loop's player hears what landed, then the error, in one chat line of at most 200 characters. An
answer refused twice is never put in the check's words, which are the model's (the `brain … FAILED`
log line keeps them): `<player>: sorry, I got tangled up on that one — say it again?`, or
`<player>: done: <the instructions that landed> — then I got tangled up on the rest, say it again?`.
Any other failure (a timeout, the box out of reach) keeps its short reason: `<player>: that errored
out — <why>`, or `<player>: done: <what landed> — then that errored out: <why>`. The record of what
its steps ran goes in as `AI action -`. The loop then ends and is saved, as any other, so a line
added may take it up and fix it; or it is deleted at the cap. An error nobody expected is said once,
in the same shape, unless the reply already went out; the record of what ran goes in, the loop is
deleted, and it is logged. One before the loop began (the world read) is said the same way, with
nothing landed.

## A report

The conversation agent reads it, at once, never queued: the event holds the task's name, its player
and their words, what it is, its latest brief and the report whole, its done-when items included;
the implementer's tool calls and trace never reach a prompt. It writes, as `ReportAnswer`:

- `reason`; `hand_off`, true when a done-when item the player asked for did not land; `left`, what is
  left, the done-when items named (with no `left`, its reason; a `left` with no hand-off is
  dropped);
- `status`, `done` or `failed`, as the report says (`done` for a cancel that put things back);
- `reply`, the player's line, said with `--task <name>`: what they have now and how they use it, and
  with a hand-off what works now and that the rest is coming; with none, a line written in code from
  the player's own words (`done: …`, `sorry, that one failed: …`, `still on it: …`);
- `record`, `ice_trail_steve done: an ice trail follows Steve as he walks`, the task's name put in
  front by code when it is missing: the task's `AI result <name> | <record>` row.

The task is closed on the agent's status, or kept open with the record as its result so far while a
follow-up to it runs or its implementer is still on it, or while what is left is handed off. **What
is left goes to the router**, as a `REPORT` hand-off: usually `implementer`, the same task followed
up, its brief naming the done-when items left; `new_loop` when an action does the rest now, a loop
whose event is the report and what it left, which does it with actions or follows the task up, and
never closes it; `new_implementer`; or `nowhere`, its reply, when it has one, said after the
agent's. It is never offered a loop. A router call that fails sends `left` to the task's
implementer as a follow-up. Once the work is placed, the task closes on the agent's status, unless
it was followed up here, a follow-up to it already runs, or its implementer still does.

**The agent failing**, or an error nobody expected, closes the task in code, on the implementer's
status, with a record written in code (`<name> <status>: <its description>`) and a chat line
written in code; it stays open only while a follow-up to it runs.

## Measured

All of it before the game engine, on the action menu it replaced (scripts and their running
copies): the engine's menu has not been measured yet.

- **The 2026-10-11 session, replayed**, measured on the brain before this build: Gemini 3.8 Flash at
  low, with no conversation agent in front and no router. Its 110 brain events (one player, 80
  minutes) went through the loop on realmc, on the brain as of its fix round 5, as
  [`integration/replay/loop/report.md`](../integration/replay/loop/report.md) gives them. The brain cost **$0.86**, against the $1.18 that night's one GPT-6.1 Sol call per event cost.
  109 loops ended in one step and 1 in two or more. An ask that did not hand off had its reply 10.7 s
  after its loop started at the median and 19.4 s at p90 (37 asks). A loop's first step read 96.2% of
  its prompt from the cache (110 steps), a later step 95.8% (1).
- **Live on realmc**, on Gemini 3.8 Flash too: the brain's live checks of 2026-10-12, before this
  build ([`integration/README.md`](../integration/README.md), _The brain live on realmc_). A
  one-step ask's reply came 6.0–8.9 s after its line; a step cost a median $0.0056, its
  32–35K-token prompt 95% read from the cache.
- **The same session on DeepSeek V4.1 Flash**, through this build's code on realmc, each line a new
  loop as if the router had chosen `new_loop`, each report through the conversation agent and the
  router ([`integration/loop_replay.py`](../integration/loop_replay.py)). **At low**, the default,
  on the finished code
  ([`report-deepseek-v4.1-flash-low-final.md`](../integration/replay/loop/report-deepseek-v4.1-flash-low-final.md)):
  **$0.2673** for the steps, the agent's and the router's calls ($0.1780, $0.0270, $0.0623), against
  Gemini round 5's $0.8588 on the same events; the time to the reply 3.3 s at the median and 15.1 s
  at p90 (38 lines that did not hand off); 68 line loops in one step and 1 in two or more; 8
  answers sent back; 1 event failed; the same asks done as Gemini's on 44 of its 54; a loop's first
  step read 91.0% of its prompt from the cache (73 steps), a later one 98.6% (4). **With reasoning
  off**, on earlier code
  ([`report-deepseek-v4.1-flash-off.md`](../integration/replay/loop/report-deepseek-v4.1-flash-off.md)):
  $0.2350, 4.5 s and 13.5 s (36 lines), 61 in one step and 8 in two or more, 62 answers sent back,
  10 events failed or errored, and the same asks as Gemini's on 38 of 54.
- **Live on realmc, on DeepSeek V4.1 Flash**: the brain's live checks of 2026-10-13, on the finished
  code ([`integration/README.md`](../integration/README.md), _The brain live, on realmc_), passed
  89 of 89 for $0.082 of OpenRouter; the agent's call took a median 561 ms, the router's 733 ms and
  a step 1.5 s, and 91% of 2.0M prompt tokens came from the cache.
- **The router alone**, reasoning off, its prompt as now (router.md fix 3,
  [`integration/replay/router/report.md`](../integration/replay/router/report.md)): the 2026-10-08
  follow-ups went to the task holding their scripts 31 times in 43 (72.1%), short of the bar of 36
  in 41 (87.8%) Gemini set; the night's 7 repeated asks went to the work already on them 5 and 3
  times in two passes (7 and 6 counting `nowhere` for a pure repeat); different asks started their
  own 10 of 10 in both passes.
- **The conversation agent alone**, on the Respond bench after its fourth fix round, before the Jev
  check in code ([`integration/replay/respond_bench.md`](../integration/replay/respond_bench.md),
  _The conversation agent on DeepSeek V4.1 Flash_): every ask handed off (84 of 84 on the night's
  lines, 10 of 10 invented); no reply said alone promising or offering (0 of 47); no done-claim
  beside a hand-off (0 of 137); 0.87 s at p90 (median 0.63 s) and $0.00023 a call, against V4
  Flash's 2.49 s and $0.00086; 37 of 81 talk lines handed off too.

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
- **TASKS** in a step's tail and the router's ([`tasks.board()`](brain/tasks.py)): every task, oldest
  first, `- galick_gun_styrk, for Styrkaeopteryx, open, an implementer is on it, 21:40:02:
  <description>`, the time it was last written (with its date when not today), then `made: content
  galick_gun_styrk; bindings styrkaeopteryx.galick_gun; running now: <the engine's instances of the
  classes it made>` (`made: nothing yet` with none) and `last result: …`; `TASKS: none yet` with
  none. The router and the brain name a new task in a hand-off, and follow one up by its name. The
  conversation agent's TASKS is one line a task, `- galick_gun_styrk for Styrkaeopteryx, an
  implementer is on it: <description, cut to 160>`.
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
vfg brain stop      # SIGTERM to the brain alone: what waits for a turn dropped (its lines marked read), the saved loops forgotten, the turns, loops and reports in flight given up to 10 s, implementers stopped, their tasks failed
```

While it stops, an implementer that ends closes its task on its own status, with a record written in
code (`castle_steve done: <its description>`, an `AI result` row) and no model call. `vfg brain stop`
signals the brain's pid, not its process group, where the implementers' harnesses run: the brain
interrupts them itself; the group gets SIGKILL only after 15 s.

`run` syncs the dependencies from `uv.lock` and execs `python -m brain`. A start takes the lock,
sets aside a `tasks.json` an older brain left in `~/.vfg/brain/` (renamed
`tasks.json.retired-<YYYYmmdd-HHMMSS>`, never deleted and never read: its `T<n>` tasks are not
carried over), runs `vfg up`, sets the watchers (`join,leave,death,advancement,server`), Jev's lookout
([`prompts/lookout.md`](prompts/lookout.md)) and Jev's menu (every action's line, set again when
an action changes), loads the context and the tasks from `vfg context --json` (tried again every 5 s
while it fails), reads the engine once beside it, waited for by nobody but a router call made while it
goes, 2 s at most, so the first router call sees ENGINE and RUNNING NOW and the conversation agent
answers at once (the engine down or hung is logged in words, those parts unknown until a read lands), fails the tasks a crash left open, follows `vfg watch --after <seq>` from the feed's highest
seq that read gave (`last_seq`, its last object: with no row in the last 6 h, the watch still never
replays old rows as new), and greets, unless nobody is online. When the feed restarts while it runs
(`vfg watch` says `feed restarted`, or gives a seq it had passed), it reads the context again and
follows the new feed from the `last_seq` that gives. **The first start on a new server version
fetches the command references** (about 95 s, in the background, into `~/.vfg/brain/refs/<version>/`,
tried again every 5 minutes while it fails); until they are in, the opening says the command list is
not loaded, and a refused command's result comes without its reference. Its last line says what
it runs on: `start pid <pid>; … respond <model> <reasoning> <s> s; router <model> <reasoning> <s> s,
the hold on|no hold; brain <model> <reasoning> <s> s a step, <n> steps a line up to <cap>, taken up
within <s> s; turns: <n> waiting, <s> s settle; owner …; server …`.

**Deploy the bridge before the first start with the router**: the router's usage rows are
component `router`, which a box's `usage` CHECK from before it refuses. `vfg usage flush` stores
each row on its own, so nothing else is held up, but a refused row is skipped with a warning and
lost, until the bridge's start rebuilds the CHECK (`docs/CONTRACTS.md` §4).

The OpenRouter key: `OPEN_ROUTER_API` in the environment, else alone in the file
`VFG_OPENROUTER_KEY` names (default `~/.vfg/openrouter.key`, mode 600), as `structured()` reads it;
the implementers read it too on the `openrouter` provider, and `DEEPSEEK_API_KEY`, from the
environment only, on `deepseek` (Settings, below).

## Settings

From the environment, then `~/.vfg/config`:

| Key | Default |
|---|---|
| `VFG_OWNER` | none: the owner's Minecraft name, named in the opening; the owner's word wins |
| `VFG_RESPOND_MODEL`, `VFG_RESPOND_REASONING`, `VFG_RESPOND_PROVIDERS` | `deepseek/deepseek-v4.1-flash`, `off`, `together,coreweave,parasail`: the conversation agent (providers tried first, in that order, fallbacks allowed; `none`: OpenRouter's choice) |
| `VFG_RESPOND_TIMEOUT_S` | `20` s per conversation agent call, its retries included; past it the line is handed off with no reply |
| `VFG_ROUTER_MODEL`, `VFG_ROUTER_REASONING`, `VFG_ROUTER_PROVIDERS` | `deepseek/deepseek-v4.1-flash`, `off`, `together,coreweave,parasail`: the router; with its reasoning anything but `off`, nothing is held (the hold, The router) |
| `VFG_ROUTER_TIMEOUT_S` | `30` s per router call, its retries included, or `60` when `VFG_ROUTER_REASONING` is not `off`; set, it wins. Past it the line goes to a new loop |
| `VFG_BRAIN_MODEL`, `VFG_BRAIN_REASONING` | `deepseek/deepseek-v4.1-flash`, `low`: every step of every loop (`off`: no reasoning) |
| `VFG_BRAIN_PROVIDERS` | `together,coreweave,parasail`: tried first, in that order, fallbacks allowed, for the loop's steps, so a loop's steps stay on one host and its cache (`none`: OpenRouter's choice) |
| `VFG_BRAIN_TIMEOUT_S` | `120` s per step's call, its resend and retries included, with `VFG_BRAIN_REASONING` anything but `off`; `60` s with `off`; set, it wins |
| `VFG_LOOP_STEPS` | `10` steps for a loop's first line, and as many more for each line added; at least 1 (with 1, a loop of one line has one step, its last: a reply or a hand-off) |
| `VFG_LOOP_CAP` | `20` steps a loop at most, whatever its lines; a loop that reaches it is deleted after its ending; at least 1 |
| `VFG_LOOP_TAKE_UP_S` | `120` s from a loop's creation in which a line added takes it up again once it has ended (`0`: never) |
| `VFG_TURN_WAITING` | `3` lines that may wait for a player's turn; one more is turned away (`0`: none waits) |
| `VFG_TURN_SETTLE_S` | `3` s at most that a turn waits for the AI rows it wrote to come back on the watch before the player's next turn starts (`0`: no wait) |
| `VFG_BRAIN_CHECKPOINT` | `5` minute-blocks between cache marks, every call's |
| `VFG_BRAIN_HISTORY_HOURS` | `6`: how much of the feed the context starts with |
| `VFG_DISPATCH_ACTION_TIMEOUT_S` | `20` s per engine op, unless its action sets `timeout_s` (`engine_start` 45, `engine_bind` 40, `engine_config` 60); vfg's answer is waited for 10 s past it |
| `VFG_ENGINE_START_WAIT_S` | `15` s, 0 to 30: how long vfg's start waits for a one-off scenario's result (vfg's own) |
| `VFG_SERVER` | `main`: the server whose `made/<server>/content/` the implementers write, and a new task's name keeps clear of |
| `VFG_REPO` | this repo: `actions/`, `engine/` and `made/`, and the implementers' working directory |
| `VFG_HOME`, `VFG_BRAIN_DIR` | `~/.vfg` and `~/.vfg/brain`; from the environment only for `VFG_HOME` |
| `VFG_IMPL_HARNESS` | `claude-code`, or `opencode`: the harness an implementer runs in (`brain/implementers/harness/`) |
| `VFG_IMPL_PROVIDER` | `deepseek` when `DEEPSEEK_API_KEY` is set, else `openrouter`: DeepSeek V4.1 Flash at that provider; or `anthropic`, Haiku 5.5 on your Claude subscription, Claude Code only and untested |
| `VFG_IMPL_MODEL` | the provider's: `deepseek-flash`, `deepseek/deepseek-v4.1-flash`, `claude-haiku-5-5`; `VFG_IMPLEMENTER_MODEL` is read when it is empty |
| `VFG_IMPL_BASE_URL` | the provider's Claude-compatible address: `https://api.deepseek.com/anthropic`, `https://openrouter.ai/api`; none for `anthropic` |
| `VFG_IMPL_OR_ORDER`, `VFG_IMPL_OR_IGNORE` | `together,digitalocean,ionstream` (Together first, the fastest) and `venice,morph,relace`: on `openrouter`, the providers each request tries first and never, fallbacks allowed, so a run keeps one provider's cache; `none` empties one |
| `VFG_IMPL_EFFORT` | `high`: how hard the model thinks, on every provider and harness, `off`, `low`, `high` or `max` (`medium` is `high`; anything else stops the brain with an error). Claude Code's relay sends it as `output_config.effort` (at `off`, `thinking` disabled), OpenCode as OpenRouter's `reasoning.effort` or DeepSeek's `reasoning_effort`, the `anthropic` provider as the CLI's `--effort`. The number it reaches depends on the host: DeepSeek's own scale, and DigitalOcean and Ionstream through OpenRouter, put `low`, `high` and `max` at 50, 75 and 100 of 100; Together puts them at 25, 50 and 100 (measured 2026-10-13). Each request's reasoning tokens are on its `request` line, `reasoning=` (`~` when estimated from the thinking's text) |
| `VFG_IMPL_MAX_TURNS` | `100` model requests a run, warned at the 95th and stopped past the 100th |
| `VFG_IMPLEMENTER_MINUTES` | `20`: warned a minute before, stopped at it and its task failed. A shell command an implementer runs may take 10 minutes when it sets no timeout (a `vfg test` waiting its turn), the run's limit at most |
| `VFG_IMPL_CACHE_TTL` | `5m` (or `1h`): the `anthropic` provider's prompt cache; `VFG_SUBAGENT_CACHE_TTL` is read when it is empty |
| `VFG_OPENCODE_BIN` | `opencode` on the PATH, else `~/.opencode/bin/opencode`: OpenCode's program; from the environment only |
| `VFG_ROUTE_TIMEOUT_S` | `3` s, 0.1 to 60, that `vfg route` may take for an implementer's EXAMPLES block, which waits a second more at most |
| `VFG_TEST_BOX` | `box`: the live box's own test server, where `vfg test` runs an implementer's test in its sandbox, with an engine of its own (`docs/CONTRACTS.md` §8); or the file of a test server's settings; `none`: no test server, `vfg test` exits 3 and the check is the engine's one answer. With one, a run's looks at the live world are budgeted |
| `VFG_TEST_TIMEOUT_S` | `60` s a test may run, 5 to 600, before it is stopped |

A number that does not read, or is under its least, is logged and its default used. The
`VFG_ORCHESTRATOR_*` and `VFG_PLAN_*` keys went with the orchestrator call and the retry planner; one
left in `~/.vfg/config` is ignored. `VFG_LOOP_SECONDS` went with a loop's time limit: set, it is
logged as the settings load (`config VFG_LOOP_SECONDS='120' is no longer read: …`) and ignored.

## Files

In `~/.vfg/brain/`: `brain.log`, `brain.pid`, `lock`, `refs/` (the command references, by server
version), `implementer-system-<hash>.md` (the implementers' system prompt),
`implementer-snapshots/<task>.json` (each task's last run's snapshot, for the put-back), `pycache/`
(`py_compile`'s), and `opencode/` on the OpenCode harness. The tasks are not here: they are rows of
the box's `tasks` table (Tasks, above), which `vfg route --task` and `vfg brain status` read.

`brain.log` is one `<ISO time> <area> <message>` per line. A line's front turn and its loop, the
time left off, the ref `#<seq>` for a line and the task's name for a report:

```text
respond #812 <ms> ms $<cost>, <in> in, <n>% cached, <out> out
respond #812 Steve: 'AI, take me home and make it day' -> 'On it!', handed off (<its reason>)
router #812 <ms> ms $<cost>, <in> in, <n>% cached, <out> out
router #812 -> a new loop L1: <its reason>
front #812 Steve: agent handoff <ms> ms; router <ms> ms: new_loop L1; said agent
exec - #812 s1 #1 engine_start ok (exit 0) 412 ms: <vfg's summary, 300 characters>
action #812 s1 #1 engine_start ok: <its summary, 200 characters>
action #812 s1 #2 engine_start refused: <the engine's or Minecraft's words, 200 characters>
brain #812 s1 <ms> ms $<cost>, <in> in, <n>% cached, <out> out | in_turn: engine_start (take Steve home), engine_start (set the time to day) || end silent || record: took Steve home and set the time to day -> engine_start landed, engine_start refused (not landed)
brain #812 s2 FAILED (<ms> ms $<cost>, …): <why>
loop #812 Steve: 2 step(s), $<cost>, <n>% cached, reply at <s> s, <s> s, ended failed; L1 saved, 2 of 10 steps, lines #812
```

- `respond <ref>`: what its call spent; then the line, its reply and `handed off` with its reason,
  or for a report its status, its reply and what is left (`left: …`, or `nothing left`); `FAILED
  (<spent>; <why>): handed off`, or for a report `: the task closed in code`; `guard <ref> …` for a
  reply the guard handed off or dropped, or a sentence naming Jev dropped; a reply cut to 200.
- `router <ref>`: what its call spent; where it placed the work, `-> a new loop L1`, `-> L1, read
  next` or `-> L1, taken up`, `-> L1: it can no longer take a line, so a new loop`, `-> a new
  implementer on <task>`, `-> <task>'s implementer, a follow-up` (`a cancel`), one a task, `->
  <task>: no such task now, its follow-up dropped`, `-> <tasks>: no such task now, so a new loop`,
  `-> nowhere`, each with its reason; `FAILED (<spent>; <why>): a new loop`, or for a report `: a
  follow-up to <task> with what is left`; and `to <choice>: dropped, it would do nothing: …` for a
  field the choice does not use.
- `front #<seq> <player>`, one a front turn: `agent <handoff|reply|silent|failed> <ms> ms`, then,
  with a hand-off, `router <ms> ms: <to> <loop id|task|->` (`router FAILED <ms> ms, so new_loop
  L4` when it failed; `<what it said>, so <where it went>` when code placed it elsewhere), and `said
  <agent|router|code|nothing>`, the lines said, in order.
- `brain <ref> s<n>`, one a step: what its call spent (`$<cost>+` when an attempt in flight was cut
  off, its cost unknown), what it did (`<order>: <action> (<instruction>), …`, `read <player>`, `read
  the Config of <class>`, `hand off <name>`, `follow up <name>`, `cancel <name>`, or `nothing run`; then `|| end <ending>` and its
  reply and record), and what came of it (`<action> <state>, …`; `read <player>'s items`, or
  `read <player>'s items: refused`; `follow up <task>: refused`; and `(not landed)` when it did
  not); `FAILED (<spent>): <why>` for a call that failed for good.
- `step end <ending>: dropped, it would do nothing: …`: a field the step's answer set that its
  ending ignores, the last step's actions and reads (`(the last step runs and reads nothing)`), or
  `end reply (it hands off: end handoff)`.
- `exec - <ref> s<n> #<i>`, one an action: vfg's run, its kind, its exit and how long it took.
- `action <ref> s<n> #<i>`, one an action: how vfg's run went (`ok`, `refused`, `partial`, `timeout`,
  `not_run`) and its summary.
- `loop <ref> <player|nobody>`, one a run of a loop (its start, or a take-up): its steps, its cost,
  how much of its prompts the cache read, the seconds from the run's start to its reply (`no reply`
  with none), its seconds in all, and how it ended: `reply`, `silent`, `handoff`, `failed`, `cut
  off` (the brain stopped) or `broke` (an error nobody expected); then the loop's id, `saved` or
  `deleted`, its steps of its limit and the lines it holds. `loop L1 never read #<seq> …` for a line
  a loop that broke or was cut off never read.
- `turn`: a line that waits for its turn, and one turned away; once a line that waited takes its
  turn, how long it waited (`turn #36 Steve waited 4.2 s for their turn`; one that took its turn at
  once logs nothing); and the AI rows a turn did not see come back within the settle.
- `task`: a task started, followed up, kept open or failed, and the hand-offs that did not open,
  the router's included.
- `world`: a state or engine read that failed, or took over its wait.
- `stop`: what the stop dropped, each line it marked read, and the saved loops it forgot (`forgot 2
  saved loop(s)`).
- `config`: a number that does not read, and a key no longer read.
- An implementer's: `implementer <task> …` (its run started or resumed, each tool call, what it
  says, how it ended, its report and what it made), `request <task> …` (each model request: its
  tokens in, cached, written and out, its size and price), `denied <task> …` (each call the guard
  refused, at the run's end), `brief` (each part's size), `route` (the router's picks and what was
  loaded), `prefix`, `guard`, `putback` and `usage` (the run's row).

## Test it

```sh
cd brain && uv run --group dev python -m pytest --ignore=tests/implementers -q   # the brain: a few seconds
cd brain && uv run --group dev python -m pytest tests/implementers -q              # the implementers
```

No model, no network, no process, no sleeping: one fake `structured()` with canned answers by output
class (`Step`, the brain's; `Response` and `ReportAnswer`, the conversation agent's; `Route`, the
router's; a list of them for several calls), contexts built by hand, a fake `vfg` that records argv,
the menu loaded from a copy of `actions/`, a fake ENGINE, fake implementers and a clock moved by
hand. `tests/test_turns.py` holds the turns to their order and the limit; `tests/test_loops.py` holds the loops to their budget, their take-up window, the cap
and WORK UNDER WAY; `tests/test_router.py` and `tests/test_respond.py` hold the two calls' answers,
their checks and their prompts; `tests/test_refs.py` checks the command references against the real
26.3 command tree and a few wiki pages; `tests/test_engine.py` the game engine's part of the world,
its bindings among the incantations, its Config read and rewrite, and its actions.

The implementers': HOME a temporary folder, no `VFG_*` setting or provider key from outside, a fake
`vfg` and a fake harness whose runs give scripted events, each tool call put through the guard;
both adapters against fakes of the Claude Code client and of `opencode serve` (the OpenCode
plugin's JavaScript under node, when node is there).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §4 the `tasks` table and `usage`, §7 the feed and its
`AI` rows, §8 the `vfg` commands it runs, §9 the brain, the conversation agent, the router, its
loops, its turns and its tasks, §10 the implementers it starts, §14 its actions.
