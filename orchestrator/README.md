# orchestrator — the runner that does the work

One Claude Agent SDK process on the owner's PC, always running. It reads the tasks the conversation
sends, does simple ones itself and briefs implementer subagents for the rest, and answers every task
with exactly one `done` or `failed` (plus any `progress`) through `vfg orch reply`.

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

The prompts are in [`prompts/`](prompts/): `orchestrator.md`, `hybrid.md` and `direct.md` for the
main thread by mode, `implementer.md` for the subagents. `%REPO%` and `%MAX_IMPLEMENTERS%` are
filled in at start. [`orchestrator/guard.py`](orchestrator/guard.py) is a hook on every tool call:
it refuses the server commands that are the owner's (`stop`, `op`, `ban`, `whitelist`, `kick` …), any
shell command outside the allowed `vfg` forms, reads outside the repo, writes outside `scripts/` and
edits to `scripts/_lib/`.

The code is the [`orchestrator/`](orchestrator/) package, on the Python Claude Agent SDK
(`claude-agent-sdk`, pinned in `pyproject.toml`; it brings its own Claude Code CLI): `config` (the
environment, paths and the log line), `vfg` (runs the toolkit), `tasks` (task states and
`state.json`), `inbox` (follows `inbox.jsonl`), `reply` (the reply tool and the outbox), `guard`,
`usage` (the `usage` rows), `session` (the one session: its options, the input queue, the message
loop, the idle check, the watchdog, restarts and the context rollover) and `main`.

## Test it

```sh
cd orchestrator && uv run pytest            # no model, no network
```

Live checks, on your subscription and not part of `uv run pytest`:
`VFG_ORCH_MODEL=haiku uv run python tests/integration.py` (the real SDK and runner against a fake
toolkit), `VFG_REPO=<temp copy of the repo> uv run python tests/probe.py` (the permission probe), and
`tests/live_box.py` against a local stand-in box with the real `vfg` (`--preflight` uses no model).

## Settings

From the environment (`vfg orch start` passes its own, plus `VFG_REPO` and `VFG_ORCH_DIR`):

| Key | Default |
|---|---|
| `VFG_REPO` | the repo this `orchestrator/` is in |
| `VFG_ORCH_DIR` | `~/.vfg/orch` |
| `VFG_ORCH_MODE` | `hybrid`; or `implementers` (every task to a subagent), `direct` (none) |
| `VFG_ORCH_MODEL`, `VFG_ORCH_EFFORT` | `opus`, `low` |
| `VFG_IMPL_MODEL` (or `VFG_IMPLEMENTER_MODEL`), `VFG_IMPL_EFFORT` | `sonnet`, `low` |
| `VFG_ORCH_MAX_IMPLEMENTERS` | `4` running at once |
| `VFG_ORCH_MAX_CONTEXT` | `70000` input tokens before a fresh session; `0` never |
| `VFG_ORCH_STALE_MINUTES`, `VFG_IMPLEMENTER_MINUTES` | `10`, `10` |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §9 the orchestrator, §10 implementers and the scripts
folder, §8 the `vfg` commands it uses, §4 its `usage` rows (`orchestrator`, `implementer`).
