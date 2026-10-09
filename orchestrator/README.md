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
`whitelist`, `kick` …), any shell command outside the allowed `vfg` forms, reads outside the repo,
and any write but in this server's folder, `made/<VFG_SERVER>/`: the library in `scripts/` (its
`_presets/` and `_lib/` too) is read-only during play, and a write there is refused with a pointer
to `vfg script new`, which copies into the server's folder. Implementers run `vfg cmd`,
`vfg script new|run|set|save|check|stop|logs|list|deploy`, `vfg preset save`, `vfg state`,
`vfg place save|list` and `vfg history`, but never `vfg script new --library`.

When the guard allows an `Agent` call it also rewrites the brief:
[`orchestrator/brief.py`](orchestrator/brief.py) runs its `STEPS` in order, and the new brief goes
back to the CLI as the hook's `updatedInput` (the whole tool input, only the prompt changed); a step
that fails is skipped, and the call is never held up.

1. **Saved places** (`saved_places`): implementers may run `vfg place save` and `vfg place list`,
   and save the place of every build players may want to go back to. The step runs
   `vfg place list` fresh (10 s at most) and appends `SAVED PLACES: <name x y z dim (task); …>`,
   `none`, or `unavailable (<why>)`.
2. **The EXAMPLES block** ([`orchestrator/examples.py`](orchestrator/examples.py)): `vfg route
   --task T7` on the orchestrator's brief ([`orchestrator/route.py`](orchestrator/route.py); Jev
   picks the example scripts and presets for the job, or words do when Jev is slow) and
   `vfg state --json`, at once. It appends who picked them and how fast; the script that runs the
   job and those that are close; the presets to copy; every running instance with its script,
   changed values and task; who is online, where and in which dimension, the time and the
   weather; the first line of every script and preset in `made/<VFG_SERVER>/`; and then, each
   under `=== <path> ===`, whole and with no cap: every script the router loaded (those that run
   the job first), every preset it loaded, every script the brief names, and the script of every
   running instance the brief names, with that instance's values now. Whatever fails, the block
   says what is missing, and the step never takes longer than `VFG_ROUTE_TIMEOUT_S` and a second.
   The picks go into the task's record in `state.json` (`tasks.T7.implementers[]`: the
   description, the time, the picks, the files loaded), and the router's log line into the log.

## What implementers start with

Their system prompt is `prompts/implementer.md` (the ladder, where they write, the commands, the
report), then the output of `vfg prefix`: how a script and a preset are written, the kit in full,
and the first line and `VALUES` of every library preset and example script. It is read once when a
session opens (start, restart or rollover), so every implementer of a session gets the same bytes
and the second one reads them from the cache ([`orchestrator/prefix.py`](orchestrator/prefix.py)).
What changes during play is in their first message: the brief, the saved places and the EXAMPLES
block. Their cache life is `VFG_SUBAGENT_CACHE_TTL`, passed to the CLI as the flag setting
`subagentPromptCacheTtl` (`--settings`; its `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` variable would
not reach it, since the runner keeps `CLAUDE_CODE_*` out of the CLI's environment). The usage rows
carry `--cache-read` and `--cache-write`, so a cache that is not hitting shows in them.

The code is the [`orchestrator/`](orchestrator/) package, on the Python Claude Agent SDK
(`claude-agent-sdk`, pinned in `pyproject.toml`; it brings its own Claude Code CLI): `config` (the
environment, paths and the log line), `vfg` (runs the toolkit), `tasks` (task states and
`state.json`), `inbox` (follows `inbox.jsonl`), `reply` (the reply tool and the outbox), `guard`,
`brief` (what goes into an implementer's brief), `route` and `examples` (the EXAMPLES block),
`prefix` (the implementer's static system prompt), `usage` (the `usage` rows), `session` (the one
session: its options, the input queue, the message loop, the idle check, the watchdog, restarts and
the context rollover) and `main`.

## Test it

```sh
cd orchestrator && uv run pytest            # no model, no network
```

Live checks, on your subscription and not part of `uv run pytest`:
`VFG_ORCH_MODEL=haiku uv run python tests/integration.py` (the real SDK and runner against a fake
toolkit), `VFG_REPO=<temp copy of the repo> uv run python tests/probe.py` (the permission probe), and
`tests/live_box.py` against a local stand-in box with the real `vfg` (`--preflight` uses no model).
`tests/live_examples.py` runs the runner against a real 26.3 server and its stand-in
(`integration/realmc`), with real Haiku 5.5 implementers: the EXAMPLES block reaching an
implementer, the shared prefix read from the cache by the second of two implementers, a new
script saved into `made/<server>/` and passing `vfg script check`, and a library edit refused.

## Settings

From the environment (`vfg orch start` passes its own, plus `VFG_REPO` and `VFG_ORCH_DIR`); the
script library's three also from `~/.vfg/config` (`$VFG_HOME/config`), as `vfg` reads them:

| Key | Default |
|---|---|
| `VFG_REPO` | the repo this `orchestrator/` is in |
| `VFG_ORCH_DIR` | `~/.vfg/orch` |
| `VFG_ORCH_MODE` | gone: every task goes to an implementer; any other value is logged as a warning and ignored |
| `VFG_ORCH_MODEL`, `VFG_ORCH_EFFORT` | `opus`, `low` |
| `VFG_IMPL_MODEL` (or `VFG_IMPLEMENTER_MODEL`), `VFG_IMPL_EFFORT` | `claude-haiku-5-5` (Haiku 5.5, while the prototype runs on the owner's subscription), `low` |
| `VFG_ORCH_MAX_IMPLEMENTERS` | `4` running at once |
| `VFG_ORCH_MAX_CONTEXT` | `70000` input tokens before a fresh session; `0` never |
| `VFG_ORCH_STALE_MINUTES`, `VFG_IMPLEMENTER_MINUTES` | `10`, `10` |
| `VFG_SERVER` | `main`: implementers write only in `made/<VFG_SERVER>/` |
| `VFG_ROUTE_TIMEOUT_S` | `3`: `vfg route`'s limit; the EXAMPLES step takes at most this and a second |
| `VFG_SUBAGENT_CACHE_TTL` | `5m` (or `1h`): the implementers' cache life, as `subagentPromptCacheTtl` |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §9 the orchestrator, §10 implementers and the scripts
folder, §8 the `vfg` commands it uses, §4 its `usage` rows (`orchestrator`, `implementer`).
