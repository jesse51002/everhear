# brain — every line, one call

A small always-running Python process on the owner's PC, in place of the conversation agent, the
orchestrator and the dispatcher's selector (the spec: Prototype_Brain). It follows the feed with
`vfg watch`, keeps every row in one shared context, and answers each event with ONE
`structured()` call ([`lib/py/vfg_llm.py`](../lib/py/vfg_llm.py)) on GPT-6.1 Sol with low
reasoning, which routes every part of a line and does the work in the same answer:

- **an action**, from the menu, written in full: it runs at once through `vfg action run`;
- **implementer**: a brief, from which code starts a coding implementer (`brain/implementers/`),
  or a follow-up to a task's own implementer;
- **talk**: answered by the reply, the one chat line every line it acts on gets.

Every event is its own asyncio task, and nothing waits on another: two players who speak 0.1 s
apart each get a call at once.

## The events

| Event | Comes from | The call |
|---|---|---|
| `LINE` | a `LINE` Jev classed `action_oriented`; or a `conversational` one Respond handed up | routes and does every part, and replies |
| `REPORT` | an implementer ending (`Implementers`' `on_report`) | the player's chat line, one record for the context (`T183 done: …`), the task's status; when the call gives no record or no line, or fails, both are written in code from the task's brief, never the implementer's words; while a follow-up to the task already runs, the task stays open with the record as its result so far, and the call is told so |
| `BATCH` | the timer: `VFG_BRAIN_BATCH_S` (60 s) with no read of any kind and something unread | usually nothing; acts where something clearly needs it |

**Respond** ([`respond.py`](brain/respond.py)) takes a `conversational` line: a cheap model (Gemini
3.8 Flash, low) on the same context that keeps up the banter and replies, or hands the line up to
the brain when it asks for something to happen or Respond is unsure. A hand-up replaces its reply,
so a player never gets two. Respond never acts on the game. A `context` line, a game event, a
system notice and the brain's own rows are context only. A line already in the feed when the brain
read its context, said more than two minutes before that, is history: never acted on. A line that
arrives on the watch is live, whatever its time (the box's clock may be a minute off the PC's).

## The shared context and the cache

[`context.py`](brain/context.py) keeps every feed row in seq order with its processed mark: the
lines Jev kept, with their class; the game's events; the system's notices; and the brain's own
`AI` rows, the only rows the AI side writes (`vfg say` and `vfg feed post AI`): what it said, each
action and how it went, each task opened or followed up, each task's result. Nothing an implementer
writes is in it. A live read (a brain call for a line, a Respond call) takes in everything unread
and marks it (`vfg context mark`); the batch catches up what no live read took.

The prompt is laid out for the cache ([`call.py`](brain/call.py)):

1. **the opening**, marked: the rules ([`prompts/brain.md`](prompts/brain.md), the owner named),
   the menu, every library action in full, Minecraft's command list;
2. **the conversation**, a minute-block at a time (a run of rows sharing a minute, which never
   changes once the next minute starts), every `VFG_BRAIN_CHECKPOINT` (5) closed blocks marked,
   the last three of them: four marks in all, the opening's included;
3. **the tail**, never cached: this server's own actions (`made/<server>/actions/`, which an
   implementer may add at any moment), the world (every online player's place and items by slot
   from `vfg state --json --items`, the running copies, the saved places, the incantations, the
   time and the weather), the open tasks, what is new since the last read, and the event.

One `Decision` answers every event, and its schema never changes with the actions, so nothing
before the conversation changes between calls: a part names its action, and writes it as one
`{key, value}` per field it sets, closed objects as OpenAI's schemas demand, checked here as `vfg`
checks them. The old selector's code rules that still hold are
its validators: an action not on the menu, values its action refuses (a command that places blocks
among them), an instruction that does nothing, a teleport that moves nobody, a part with no reply.
`structured()` sends a broken answer back once with why.

## Running a decision

[`act.py`](brain/act.py): the action parts run in order under the line's task id
(`vfg action run … --task T<n>`), each recorded as an `AI action` row. A command Minecraft refuses
gets ONE retry from the action planner ([`action_planner.py`](brain/action_planner.py)) on
`VFG_PLAN_MODEL`, written with the reference of every command it used; its self-check is off
unless `VFG_PLAN_CHECK=on`. Implementer parts start at once: a new one gets its own `T<n>`, a
`tasks.json` entry and an `AI task` row; one naming a task goes to that task's implementer, which
resumes its session (kept in `tasks.json` as soon as the implementer's CLI names it), or with no
session saved starts anew on the task's brief and the follow-up. The
reply is said at once when there are only implementer and talk parts, and after the actions
otherwise; when an action failed or was dropped in code, a short line written in code goes out
instead, so a reply never claims what did not happen. An error nobody expected never leaves a task
open: the line's task, or the reported one, is closed (failed, or on the implementer's status)
and the player gets a line written in code; the error is logged.

## Run it

```sh
vfg brain start     # brain/run, detached: pid ~/.vfg/brain/brain.pid, log ~/.vfg/brain/brain.log
vfg brain status    # running or not, and the open tasks
vfg brain stop      # SIGTERM to the brain alone: in-flight calls finish for up to 10 s, implementers stopped, their tasks failed
```

While it stops, an implementer that ends closes its task on its own status, with a record written in
code (`T7 done: <its brief>`, an `AI result` row) and no model call. `vfg brain stop` signals the
brain's pid, not its process group, where the implementers' claude CLIs run: the brain interrupts
them itself; the group gets SIGKILL only after 15 s.

`run` syncs the dependencies from `uv.lock` and execs `python -m brain`. A start takes the lock,
runs `vfg up`, sets the watchers (`join,leave,death,advancement,server`), Jev's lookout
([`prompts/lookout.md`](prompts/lookout.md)) and Jev's menu (every action's line, set again when
an action changes), loads the context from `vfg context --json` (tried again every 5 s while it
fails), fails the tasks a crash left open, follows `vfg watch --after <seq>` from the feed's highest
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
| `VFG_RESPOND_MODEL`, `VFG_RESPOND_REASONING` | `google/gemini-3.8-flash`, `low` |
| `VFG_PLAN_MODEL`, `VFG_PLAN_REASONING`, `VFG_PLAN_CHECK` | `google/gemini-3.8-flash`, `low`, `off` |
| `VFG_BRAIN_BATCH_S` | `60` s with no read of any kind, and something unread, before a batch call |
| `VFG_BRAIN_CHECKPOINT` | `5` minute-blocks between cache marks |
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

In `~/.vfg/brain/`: `tasks.json` (`{"next", "tasks": {"T183": {"id", "at", "player", "line",
"task", "status", "result", "session", "closed_at"}}}`, written whole, by the brain only; `vfg
route --task` and `vfg brain status` read it), `brain.log` (one `<ISO time> <area> <message>` per
line), `brain.pid`, `lock`, and `refs/`.

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

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §7 the feed and its `AI` rows, §8 the `vfg` commands
it runs.
