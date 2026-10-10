# orchestrator — the runner that does the work

One Claude Agent SDK process on the owner's PC, always running. It reads the tasks the dispatcher
hands on (`vfg orch hand`), briefs an implementer subagent for every one of them and runs no command
on the server itself, and answers every task with exactly one `done` or `failed` (plus any
`progress`) through `vfg orch reply`.

## Run it

```sh
vfg orch start      # runs orchestrator/run detached; prints "running pid N" if it already is
vfg orch status     # running or stopped, then the open tasks
vfg orch stop       # SIGTERM, then SIGKILL after 15 s; open tasks are failed first
```

`run` brings the dependencies in line with `uv.lock` (`uv sync`, a no-op once they are there), then
execs `python -m orchestrator` from this folder's `.venv`, so the pid `vfg orch start` records is the
runner itself. It needs [uv](https://docs.astral.sh/uv/), which fetches Python 3.12 if there is
none. Its log is `~/.vfg/orch/runner.log`; the inbox, state and lock are beside it. It runs on the
Claude subscription Claude Code is logged in with, and refuses to start if the CLI would use an API key.

The prompts are in [`prompts/`](prompts/): `orchestrator.md` for the main thread, `implementer.md`
for the subagents. `%REPO%`, `%MAX_IMPLEMENTERS%` and `%SERVER%` (`VFG_SERVER`) are filled in at start.
[`orchestrator/guard.py`](orchestrator/guard.py) is a hook on every tool call: it keeps the main
thread to orchestrating (its commands are `vfg script list`, to keep work from colliding, and
`vfg place list`), and refuses the server commands that are the owner's (`stop`, `op`, `ban`,
`whitelist`, `kick` …) in a `vfg cmd` and in the commands an action run or an edit gives (any
value of a key naming commands), any shell command outside the allowed forms, reads outside the
repo, and any write but in this server's folder, `made/<VFG_SERVER>/`: the library in `scripts/`
(its `_presets/` and `_lib/` too) and in `actions/` is read-only during play, and a write there is
refused with a pointer to `vfg script new`, which copies a script into the server's folder and
writes its action, or to a variant in `made/<VFG_SERVER>/actions/`. Implementers run
`vfg action run <action> key=value … [as=<copy>] --task T<n>` (the task is required),
`vfg action list` and `vfg action check`, `vfg cmd`, `vfg script new|set|save|check|stop|logs|list|deploy`,
`vfg preset save`, `vfg state`, `vfg place save|list` and `vfg history`, but never
`vfg script new --library`; `vfg script run` is gone, and refused with a pointer to
`vfg action run`. Commands are joined with `&&`
or `;`, with `2>&1`, `>&2` or `>/dev/null`, and piped into `head -n N`, `tail -n N` or `grep`; and
`echo`, `sleep N`, `python3 -m py_compile`, and `ls`, `grep`, `head` and `tail` on files in the
repo. Never `$( )`, backticks or variables, loops, `( )`, a lone `&`, a redirection into or out of
a file, or another program: each refusal is one line saying what to send instead (`mkdir` points
at `vfg script new`, `cat` and `sed` at Read, `find` at Glob, `ps` at `vfg script list`), so the
next call runs. And an implementer may not Edit or Write a script in `made/<VFG_SERVER>/scripts/`
that a copy another task started is running (`$VFG_HOME/running.json`, which vfg writes through
on every run, edit and stop; else `vfg state --json`, kept 3 s; asked only for a script the
implementer did not make itself): new code is a new script, `vfg script new <new_name> --from <it>`.
A script it made with `vfg script new` or `vfg script save`, or whose copy its own task started,
it fixes freely; when what runs cannot be told, the edit goes.

When the guard allows an `Agent` call it also rewrites the brief:
[`orchestrator/brief.py`](orchestrator/brief.py) runs its `STEPS` in order, and the new brief goes
back to the CLI as the hook's `updatedInput` (the whole tool input, only the prompt changed); a step
that fails is skipped, and the call is never held up.

1. **Saved places** (`saved_places`): implementers may run `vfg place save` and `vfg place list`,
   and save the place of every build players may want to go back to. The step runs
   `vfg place list` fresh (10 s at most) and appends `SAVED PLACES: <name x y z dim (task); …>`,
   `none`, `unavailable (<why>)`, or, when this `vfg` has no `place` command (it comes with the
   dispatcher's work), `off`, telling the implementer to save no place whatever the brief says.
2. **The EXAMPLES block** ([`orchestrator/examples.py`](orchestrator/examples.py)): `vfg route
   --task T7` on the orchestrator's brief ([`orchestrator/route.py`](orchestrator/route.py); Jev
   picks the example scripts and presets for the job, or words do when Jev is slow),
   `vfg state --json` and the actions cache, at once. It appends who picked them and how fast; the
   script that runs the job, with the actions over it, and those that are close; the presets to
   copy, and those Jev picked that a loaded script already holds, with the script they are in;
   every running copy from `$VFG_HOME/running.json` (else the state's list) with its action, the
   values that differ from the action's and its task; who is online, where and in which dimension,
   the time and the weather, and a line saying to act on that rather than ask again; the first line
   of every script and preset in `made/<VFG_SERVER>/`; then **ACTIONS**
   ([`orchestrator/actions.py`](orchestrator/actions.py)): every action, the library's by name then
   this server's, whole and uncapped, as the actions cache (`$VFG_HOME/actions.json`, kept by
   `lib/py/vfg_actions.py`, built again when an action file is newer) has it as the implementer
   starts: its line, its script, how it runs, and every value with its type, limits, default or
   fixed value and description, those it takes marked `*`, and any action file that did not load;
   and then, each under `=== <path> ===`, whole and with no cap: every script the router loaded
   (those that run the job first), every preset it loaded, every script the brief names, and the
   script of every running copy the brief names (by its name, its action's or its script's), with
   the values it runs with (and, when another task started it, the `vfg script new <new_name> --from
   <script>` that copies it to be changed). Whatever fails, the block says what is missing; the
   router and the state get `VFG_ROUTE_TIMEOUT_S` and a second, the actions cache 10 s (a fresh one
   is a file read; the runner reads it once as it starts, so the first brief does not rebuild it).
   The picks go into the task's record in `state.json` (`tasks.T7.implementers[]`: the
   description, the time, the picks, the files loaded, how many actions), and the router's log
   line into the log.

## What implementers start with

Their system prompt is `prompts/implementer.md` (actions, the ladder of the actions spec — run an
action, edit a running copy, a new script and its action, a new preset — what they make, where
they write, the commands, the report), then the output of `vfg prefix`: how a script and a preset
are written, the kit in full, and the library's presets and example scripts by first line. It is
read once when a session opens (start, restart or rollover), so every implementer of a session
gets the same bytes and the second one reads them from the cache
([`orchestrator/prefix.py`](orchestrator/prefix.py)). What changes during play is in their first
message: the brief, the saved places, the EXAMPLES block and every action. The actions are there
and not in the system prompt so that an action made mid-game changes only the next brief, never the
cached prefix: an implementer writes its first message to the cache on its first call and reads it
on every call after. Their cache life is `VFG_SUBAGENT_CACHE_TTL`, passed to the CLI as the flag setting
`subagentPromptCacheTtl` (`--settings`; its `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` variable would
not reach it, since the runner keeps `CLAUDE_CODE_*` out of the CLI's environment). The usage rows
carry `--cache-read` and `--cache-write`, so a cache that is not hitting shows in them. The SDK
counts usage per model, so when the session and its implementers share one (Haiku 5.5 by default),
the main thread's own calls, seen in the stream, are the `orchestrator` row and the rest the
`implementer` row, the SDK's cost split between them by each side's tokens at Anthropic's price
ratios ([`orchestrator/usage.py`](orchestrator/usage.py)).

The code is the [`orchestrator/`](orchestrator/) package, on the Python Claude Agent SDK
(`claude-agent-sdk`, pinned in `pyproject.toml`; it brings its own Claude Code CLI): `config` (the
environment, paths and the log line), `vfg` (runs the toolkit), `tasks` (task states and
`state.json`), `inbox` (follows `inbox.jsonl`), `reply` (the reply tool and the outbox), `guard`,
`brief` (what goes into an implementer's brief), `route` and `examples` (the EXAMPLES block),
`actions` (every action and the running copies, from vfg's caches),
`prefix` (the implementer's static system prompt), `usage` (the `usage` rows), `session` (the one
session: its options, the input queue, the message loop, the idle check, the watchdog, restarts and
the context rollover) and `main`.

## Test it

```sh
cd orchestrator && uv run pytest            # no model, no network
```

Live checks, on your subscription and not part of `uv run pytest`:
`uv run python tests/integration.py` (the real SDK and runner against a fake
toolkit), `VFG_REPO=<temp copy of the repo> uv run python tests/probe.py` (the permission probe), and
`tests/live_box.py` against a local stand-in box with the real `vfg` (`--preflight` uses no model).
`tests/live_examples.py` runs the runner against a real 26.3 server and its stand-in
(`integration/realmc`), with real Haiku 5.5 implementers: the EXAMPLES block reaching an
implementer, the shared prefix read from the cache by the second of two implementers, a new
script and its action saved into `made/<server>/` and passing their checks, and a library edit refused.

## Settings

From the environment (`vfg orch start` passes its own, plus `VFG_REPO` and `VFG_ORCH_DIR`); the
script library's three also from `~/.vfg/config` (`$VFG_HOME/config`), as `vfg` reads them:

| Key | Default |
|---|---|
| `VFG_REPO` | the repo this `orchestrator/` is in |
| `VFG_ORCH_DIR` | `~/.vfg/orch` |
| `VFG_ORCH_MODE` | gone: every task goes to an implementer; any other value is logged as a warning and ignored |
| `VFG_ORCH_MODEL`, `VFG_ORCH_EFFORT` | `claude-haiku-5-5` (Haiku 5.5, as every agent), `low` |
| `VFG_IMPL_MODEL` (or `VFG_IMPLEMENTER_MODEL`), `VFG_IMPL_EFFORT` | `claude-haiku-5-5`, `low` |
| `VFG_ORCH_MAX_IMPLEMENTERS` | `4` running at once |
| `VFG_ORCH_MAX_CONTEXT` | `70000` input tokens before a fresh session; `0` never |
| `VFG_ORCH_STALE_MINUTES`, `VFG_IMPLEMENTER_MINUTES` | `10`, `10` |
| `VFG_SERVER` | `main`: implementers write only in `made/<VFG_SERVER>/` |
| `VFG_HOME` | `~/.vfg`: where vfg keeps `actions.json` and `running.json`, which every brief is read from |
| `VFG_ROUTE_TIMEOUT_S` | `3`: `vfg route`'s limit; the EXAMPLES step takes at most this and a second |
| `VFG_SUBAGENT_CACHE_TTL` | `5m` (or `1h`): the implementers' cache life, as `subagentPromptCacheTtl` |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §9 the orchestrator, §10 implementers and the scripts
folder, §8 the `vfg` commands it uses, §4 its `usage` rows (`orchestrator`, `implementer`).
