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

`run` installs the dependencies with Bun the first time (`bun install --frozen-lockfile`), then
runs `src/main.ts`. Its log is `~/.vfg/orch/runner.log`; the inbox, state and lock are beside it.
It runs on the Claude subscription Claude Code is logged in with, and refuses to start if the CLI
would use an API key.

The prompts are in [`prompts/`](prompts/): `orchestrator.md`, `hybrid.md` and `direct.md` for the
main thread by mode, `implementer.md` for the subagents. `%REPO%` and `%MAX_IMPLEMENTERS%` are
filled in at start. `src/guard.ts` is a hook on every tool call: it refuses the server commands
that are the owner's (`stop`, `op`, `ban`, `whitelist`, `kick` …), any shell command outside the
allowed `vfg` forms, reads outside the repo, writes outside `scripts/` and edits to `scripts/_lib/`.

## Test it

```sh
cd orchestrator && bun install && bun test            # no model, no network
```

Live checks, on your subscription and not part of `bun test`:
`VFG_ORCH_MODEL=haiku bun run test/integration.ts` (the real SDK and runner against a fake
toolkit), `VFG_REPO=<temp copy of the repo> bun run test/probe.ts` (the permission probe), and
`test/live_box.py` against a local stand-in box with the real `vfg` (`--preflight` uses no model).

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
