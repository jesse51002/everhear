# brain — every line, one call

A small always-running Python process on the owner's PC, in place of the conversation agent, the
orchestrator agent and the dispatcher's selector (the specs: Prototype_Brain, then
Prototype_Named_Tasks). It follows the feed with `vfg watch`, keeps every row in one shared
context, and answers each event with ONE `structured()` call
([`lib/py/vfg_llm.py`](../lib/py/vfg_llm.py)) on GPT-6.1 Sol with low reasoning, which routes
every part of a line and does the work in the same answer:

- **an action**, from the menu, written in full: it runs at once through `vfg action run`;
- **orchestrator**: an instruction in plain words, for anything new no action does, a change or
  fix of something that exists, or a stop. Code then makes the orchestrator's call, one small
  `structured()` call of its own, which opens a named task for a coding implementer
  (`brain/implementers/`), follows one up, or changes or stops a running copy (below);
- **talk**: answered by the reply, the one chat line every line it acts on gets.

Every event is its own asyncio task, and nothing waits on another: two players who speak 0.1 s
apart each get a call at once.

## The events

| Event | Comes from | The call |
|---|---|---|
| `LINE` | a `LINE` Jev classed `action_oriented`; or a `conversational` one Respond handed up | routes and does every part, and replies |
| `REPORT` | an implementer ending (`Implementers`' `on_report`) | once the task has taken what the implementer made, its `DESCRIPTION:` line and its action's variables (Tasks, below): the player's chat line, one record for the context (`galick_gun_styrk done: …`), the task's status, and parts when the report left something the player asked for to fix; when the call gives no record or no line, or fails, both are written in code from the task's brief, never the implementer's words; while a follow-up to the task already runs, the task stays open with the record as its result so far, and the call is told so |

There is no other call, but the orchestrator's, one for each part an event's call sends to
`orchestrator`: no model is called for what nobody asked. A game event, a system notice
and the brain's own rows wait for the next call, which reads them as NEW.

**Respond** ([`respond.py`](brain/respond.py)) takes a `conversational` line: a cheap model (DeepSeek
V4 Flash, reasoning off) on the same context that keeps up the banter and replies, or hands the
line up to the brain when it asks for something to happen or Respond is unsure. It decides first
(`reason`, `hand_up`, then `reply`), and handing up is the default unless the line is clearly pure
talk ([`gate_bench.md`](../integration/replay/gate_bench.md)). A hand-up replaces its reply, so a
player never gets two. Respond never acts on the game. A game event, a system notice and the
brain's own rows are context only. A line already in the feed when the brain
read its context, said more than two minutes before that, is history: never acted on. A line that
arrives on the watch is live, whatever its time (the box's clock may be a minute off the PC's).

## The shared context and the cache

[`context.py`](brain/context.py) keeps every feed row in seq order with its processed mark: the
lines Jev kept, with their class; the game's events; the system's notices; and the brain's own
`AI` rows, the only rows the AI side writes (`vfg say` and `vfg feed post AI`): what it said, each
action and how it went, each task opened or followed up, each task's result, each running copy
changed or stopped. Nothing an implementer writes is in it. Every call (a brain call for a line or a report, a Respond call) takes in
everything unread and marks it (`vfg context mark`), so the game's events since the last call are
its NEW rows.

**A `context` line is never shown to a model**: not in the conversation, not under
NEW, not in Respond's prompt. It stays in the feed and `vfg.db`, and in the context's rows, for a
memory that will summarise it later; the brain marks it processed as it arrives (and, at a start,
every one still unmarked), so nothing waits on it. Every `GAME` row, every `SYS` row, the brain's
own `AI` rows and the `action_oriented` and `conversational` lines are shown.

The prompt is laid out for the cache ([`call.py`](brain/call.py)):

1. **the opening**, marked: the rules ([`prompts/brain.md`](prompts/brain.md), the owner named),
   the menu, every library action in full, Minecraft's command list;
2. **the conversation**, a minute-block at a time (a run of rows sharing a minute, which never
   changes once the next minute starts), every `VFG_BRAIN_CHECKPOINT` (5) closed blocks marked,
   the last three of them: four marks in all, the opening's included;
3. **the tail**, never cached: this server's own actions (`made/<server>/actions/`, which an
   implementer may add at any moment), the world (from `vfg state --json --items`, every online
   player on one line: `- Steve at 197 68 251 in overworld: 14/20 health, 18/20 food, armor 12,
   level 5, survival; effects: speed II 0:30, night vision ∞; Steve's items, by slot: …`, what
   Minecraft did not answer left out; the running copies, the saved places, the incantations, the
   time and the weather), TASKS (below), what is new since the last read, and the event.
   Respond's tail has the same players, without their items.

One `Decision` answers every event, and its schema never changes with the actions, so nothing
before the conversation changes between calls: a part names its action, and writes it as one
`{key, value}` per field it sets, closed objects as OpenAI's schemas demand, checked here as `vfg`
checks them. The old selector's code rules that still hold are
its validators: an action not on the menu, values its action refuses (a command that places blocks
among them), an instruction that does nothing, a teleport that moves nobody, a part with no reply.
`structured()` sends a broken answer back once with why.

## Running a decision

[`act.py`](brain/act.py): the action parts run in order, as their own asyncio task, with no task
(only an implementer's work is one). Each runs as `vfg action run … --for <player>`, the action's
`player` value when it names one player, else the speaker (a report's parts: the task's player),
so its copy is named `<action>_<player>` on the box (`command_steve`, then `command_steve_2`), and
each is recorded as an `AI action -` row. A command Minecraft refuses
gets ONE retry from the action planner ([`action_planner.py`](brain/action_planner.py)) on
`VFG_PLAN_MODEL`, written with the reference of every command it used; its self-check is off
unless `VFG_PLAN_CHECK=on`.

The orchestrator parts run beside the actions: one orchestrator call each
([`orchestrator.py`](brain/orchestrator.py), [`prompts/orchestrator.md`](prompts/orchestrator.md)),
one after another in the order they come, each told what the ones before it answered. It reads the
conversation as the brain's call does, cached the same way, then the TASKS board (every task, what
it made, its copies running now, its last result), RUNNING NOW (every copy with every value of its
action, its value now, its limits, live or restart), the event and the brain's instruction; never
the menu, the commands or the world. Its answer is checked against them (a follow-up names a task
there is, an edit a copy that runs and values its action takes, a new name no library action has)
and carried out here:

- **new**: the name it gives (the thing, then whose: `frost_bow_alex`) is made free of every task,
  action, script folder and running copy, and of `cast`, with `_2`, `_3` … ([`names.py`](brain/names.py));
  the task opens, `AI task <name> | new: <description>` is posted, the reply may go, and the
  implementer starts at once, with no wait for the row's write.
- **follow_up**: the task opens again with its new brief and description, `AI task <name> |
  follow-up: <brief>` is posted, and the brief goes to its implementer, which resumes its session
  (kept in the row as soon as the implementer's CLI names it), or with none (after a restart)
  starts anew on the task's description and the follow-up.
- **edit** and **stop**: the `edit` action on the copy, or `stop` on each copy, run here with no
  task, each an `AI note` row under the copy's task, else `-` (`edited railgun_steve: cooldown=0`,
  `stopped railgun_steve`). A stop never changes a task's status.
- **reply**: nothing starts.

**Who replies.** With no orchestrator part, the brain's reply: at once when there are only talk
parts, after the actions otherwise. With one, the first orchestrator call's reply, said once the
actions, that call and its edit or stop are done, never waiting for an implementer to start or for
a later call, through `vfg say … --task <name>` when a task was opened or followed up. The brain's
own reply is held back and said only when that call fails; nothing has started then for the
orchestrator parts, so the prompt has that reply cover the actions and ask for the rest again. A
later call's reply only, its edit or stop that failed, or its failure (`say "<part>" again?`) is
a line of its own after the reply. A report's orchestrator calls write no reply. When an action, an
edit or a stop failed or was dropped in code, a short line written in code goes out instead, so a
reply never claims what did not happen; a question asked back is still said after it. An error
nobody expected never leaves a task open: a reported task is closed (on the implementer's status), a
new one whose implementer could not start, or was cut off before it started, fails, and the player
gets a line written in code; the error is logged.

## Tasks

[`tasks.py`](brain/tasks.py): one task per implementer's work, keyed by its name (`galick_gun_styrk`,
`^[a-z][a-z0-9_]{1,31}$`, kept for life), held in memory and written through to the `tasks` table of
the box's `vfg.db`, which only the brain writes. Every change sends the whole row with `vfg task set`
in the background, one task's writes in order; the box keeps the newer `updated_at` should two ever
cross. A write that fails (EC2 out of reach for a moment) is sent again after 2, 5, 15 and 30 s,
unless a newer change to the task carries the row first. A row: `name`, `description` (what the thing is now, for whom; players never see it),
`player`, `status` (`open`, `done`, `failed`), `line` (the player's words), `brief` (the latest),
`made` (`{"scripts", "actions", "presets", "copies"}`), `variables` (its action's fields, as
`vfg_actions.variables()` reads them), `session`, `result`, `created_at`, `updated_at`, `closed_at`.

- **A report** first adds what the implementer made to `made`, takes its `DESCRIPTION:` line as the
  description (with none, the orchestrator's stays), and reads `variables` from the task's action:
  the one named as the task, else the last of `made.actions` that is loaded. A changed action file
  reads them again at the next call of any kind.
- **TASKS**, in the brain's tail and Respond's: every task, oldest first, one line each,
  `- galick_gun_styrk for Styrkaeopteryx, an implementer is on it: <description, cut to 160>`, the
  status `open`, `done` or `failed` when no implementer is on it; `TASKS: none yet` with none. The
  brain never names a task: it routes, and the orchestrator finds the task a part is about.
- **A start** takes every row from `vfg context --json` and fails each one still open (`<name>
  failed: interrupted, the brain restarted while it ran`, with its `AI result` row). When the feed
  restarts, the rows read again are merged: one the memory lacks is added, one the read lacks (a box
  provisioned again) or holds older than the memory (a write that never landed) is written back, and
  for a name in both the memory wins.
- **A stop** fails each task whose implementer it stopped, and gives the writes still going out 5 s.

## Run it

```sh
vfg brain start     # brain/run, detached: pid ~/.vfg/brain/brain.pid, log ~/.vfg/brain/brain.log
vfg brain status    # running or not, and the open tasks, read from the box
vfg brain stop      # SIGTERM to the brain alone: in-flight calls finish for up to 10 s, implementers stopped, their tasks failed
```

While it stops, an implementer that ends closes its task on its own status, with a record written in
code (`castle_steve done: <its brief>`, an `AI result` row) and no model call. `vfg brain stop` signals the
brain's pid, not its process group, where the implementers' claude CLIs run: the brain interrupts
them itself; the group gets SIGKILL only after 15 s.

`run` syncs the dependencies from `uv.lock` and execs `python -m brain`. A start takes the lock,
sets aside a `tasks.json` an older brain left in `~/.vfg/brain/` (renamed
`tasks.json.retired-<YYYYmmdd-HHMMSS>`, never deleted and never read: its `T<n>` tasks are not
carried over), runs `vfg up`, sets the watchers (`join,leave,death,advancement,server`), Jev's lookout
([`prompts/lookout.md`](prompts/lookout.md)) and Jev's menu (every action's line, set again when
an action changes), loads the context and the tasks from `vfg context --json` (tried again every 5 s
while it fails), fails the tasks a crash left open, follows `vfg watch --after <seq>` from the feed's highest
seq that read gave (`last_seq`, its last object: with no row in the last 6 h, the watch still never
replays old rows as new), and greets, unless nobody is online. When the feed restarts while it runs
(`vfg watch` says `feed restarted`, or gives a seq it had passed), it reads the context again and
follows the new feed from the `last_seq` that gives. **The first start on
a new server version fetches the command references** (about 95 s, in the background, into
`~/.vfg/brain/refs/<version>/`); until they are in, a refused command is not retried.

The OpenRouter key: `OPEN_ROUTER_API` in the environment, else alone in `~/.vfg/openrouter.key`
(mode 600), as `structured()` reads it.

## Settings

From the environment, then `~/.vfg/config`:

| Key | Default |
|---|---|
| `VFG_OWNER` | none: the owner's Minecraft name, named in the opening; the owner's word wins |
| `VFG_BRAIN_MODEL`, `VFG_BRAIN_REASONING` | `openai/gpt-6.1-sol`, `low` |
| `VFG_BRAIN_PROVIDERS` | OpenRouter's choice; comma-separated providers to try first, for the brain's call |
| `VFG_RESPOND_MODEL`, `VFG_RESPOND_REASONING`, `VFG_RESPOND_PROVIDERS` | `~deepseek/deepseek-v4-flash-latest`, `off`, `parasail,wafer,phala,together,venice` (tried first, fastest first; `none`: OpenRouter's choice) |
| `VFG_PLAN_MODEL`, `VFG_PLAN_REASONING`, `VFG_PLAN_PROVIDERS`, `VFG_PLAN_CHECK` | as Respond's, and `off`: a retry is rare and has the command and Minecraft's error in hand |
| `VFG_ORCHESTRATOR_MODEL`, `VFG_ORCHESTRATOR_REASONING` | `google/gemini-3.8-flash`, `low`: it kept one thing to one task on the follow-up replay; DeepSeek V4 Flash is about 7x cheaper but lumps things together, and wants reasoning `off` |
| `VFG_ORCHESTRATOR_PROVIDERS` | OpenRouter's choice; comma-separated providers to try first, for the orchestrator's call |
| `VFG_ORCHESTRATOR_TIMEOUT_S` | `15` s per orchestrator call, its retries included; past it the brain's own reply is said |
| `VFG_BRAIN_CHECKPOINT` | `5` minute-blocks between cache marks, the orchestrator's call's too |
| `VFG_BRAIN_HISTORY_HOURS` | `6`: how much of the feed the context starts with |
| `VFG_BRAIN_TIMEOUT_S` | `20` s per model call, its retries included |
| `VFG_BRAIN_DIR` | `~/.vfg/brain` |
| `VFG_SERVER` | `main`: whose `made/<server>/actions/` are on the menu |
| `VFG_RUNNING_TTL_S` | `30` s between background fetches of the running copies; `0`: never |
| `VFG_DISPATCH_ACTION_TIMEOUT_S` | `20` s per once action, unless its class sets `timeout_s` |
| `VFG_IMPL_MODEL`, `VFG_IMPL_EFFORT`, `VFG_IMPL_MAX_TURNS` | `claude-haiku-5-5`, `low`, `40`: the implementers, on your Claude subscription |
| `VFG_IMPLEMENTER_MINUTES` | `10`: past it an implementer is stopped and its task failed |
| `VFG_IMPL_CACHE_TTL` | `5m` (or `1h`): an implementer's prompt cache; `VFG_SUBAGENT_CACHE_TTL` is read when it is empty |

## Files

In `~/.vfg/brain/`: `brain.log` (one `<ISO time> <area> <message>` per line), `brain.pid`, `lock`,
and `refs/`. The tasks are not here: they are rows of the box's `tasks` table (Tasks, above), which
`vfg route --task` and `vfg brain status` read, and a `tasks.json` from before them is set aside at
the start.

## Test it

```sh
cd brain && uv run --group dev python -m pytest --ignore=tests/implementers -q   # the brain: about 0.3 s
cd brain && uv run --group dev python -m pytest tests/implementers -q              # the implementers
```

No model, no network, no process, no sleeping: one fake `structured()` with one canned answer per
test, contexts built by hand, a fake `vfg` that records argv, fake implementers and a clock moved
by hand. `tests/test_refs.py` checks the command references against the real 26.3 command tree and
a few wiki pages.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §4 the `tasks` table, §7 the feed and its `AI` rows,
§8 the `vfg` commands it runs, §9 the brain, its orchestrator call and its tasks, §14 its actions.
