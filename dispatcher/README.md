# dispatcher — every task's first stop

A small always-running Python process on the owner's PC. Every task the conversation sends
(`vfg orch send`) lands in its inbox. The complexity selector picks the [actions](../actions/README.md)
that do it and what only the orchestrator can do; the action planner turns each pick into the exact
action, checking a `command` against the server's own command reference first; the executor runs
it with `vfg action run`. Each action's report goes to the conversation as it finishes (`progress`,
the last one `done`); what no action does goes to the orchestrator with `vfg orch hand`, collected
for 10 s; a pick nothing can fix is dropped and the conversation told why. Every model call is
`structured()` from [`lib/py/`](../lib/py/vfg_llm.py), reasoning off.

## Set it up

Once: put your OpenRouter API key alone in `~/.vfg/openrouter.key`, with mode 600.
`VFG_OPENROUTER_KEY` in `~/.vfg/config` can point at another file. `OPEN_ROUTER_API` in the
environment wins over both.

```sh
(umask 077 && read -rsp 'OpenRouter key: ' key && echo && printf '%s\n' "$key" > ~/.vfg/openrouter.key)
```

The handlers (`teleport`, `summon_around`) run on the box with `scripts/_lib/mc.py`, which
`vfg script deploy` puts there; the playtest box already has it. Without it they exit 3 and the
task goes to the orchestrator.

## Run it

```sh
vfg orch start      # runs dispatcher/run beside the orchestrator runner; its log is dispatch/dispatcher.log
vfg orch status     # both, and the open tasks
vfg orch stop       # the dispatcher first: it hands on everything unfinished, then exits
```

`run` is `uv run --project dispatcher python -m dispatcher`.

**The first start on a new server version takes about 95 s** to fetch the command references
(`vfg action refs`, then the wiki's command pages, one a second) into `refs/<version>/`. Later
starts read them from there. Tasks are read from the start all the same: during the first fetch
the other actions work and `command` asks go to the orchestrator; tasks that start once the
references are in use `command` as usual. If the fetch fails, it carries on the same way and
tries again every 5 minutes.

## Test it

```sh
cd dispatcher && uv run pytest                          # no model, no network: a fake structured() and a fake vfg
uv run --project dispatcher python integration/dispatcher_e2e.py   # the real dispatcher, toolkit and model, a fake box
```

The end-to-end run is the spec's smoke test with no EC2: the real `vfg` through
`toolkit/tests/fake_ssh`, a fake Minecraft world over RCON, `OPEN_ROUTER_API` from the
environment or the repo's `.env`. Each full run costs well under a cent.

## Replay

```sh
cd dispatcher && uv run python -m dispatcher.replay [--model <id>] [--limit N] [--only T3,T9] [--jobs 8] [--eval labels.jsonl]
```

Sends the playtest's tasks from `~/.vfg/orch/inbox.jsonl` (read only) through the complexity
selector alone and prints each selection, its cost and time, then the totals. It acts on nothing.
It is how a cheaper model or a prompt change is judged before a live run.

## Settings

From the environment, then `~/.vfg/config`:

| Key | Default |
|---|---|
| `OPEN_ROUTER_API`, else the file `VFG_OPENROUTER_KEY` names | `~/.vfg/openrouter.key` (mode 600; only `structured()` reads it) |
| `VFG_DISPATCH_DIR` | `~/.vfg/dispatch`: `inbox.jsonl`, `state.json`, `dispatcher.log`, `refs/` |
| `VFG_DISPATCH_MODEL` | `~deepseek/deepseek-v4-flash-latest` |
| `VFG_DISPATCH_PROVIDERS` | `parasail,wafer,phala,together,venice`: OpenRouter providers tried first, in order, for every model call (the replay's: no lost work); `none` lets OpenRouter choose |
| `VFG_DISPATCH_RETRIES` | `1` retry per failed action |
| `VFG_DISPATCH_WINDOW_S` | `10` s before handed tasks go to the orchestrator together |
| `VFG_DISPATCH_ACTION_TIMEOUT_S` | `20` s per action; a class may set its own (`teleport`: 60) |
| `VFG_DISPATCH_MODEL_TIMEOUT_S` | `10` s per model call (selector, planner, replay), its retries included; past it the task, or that pick, is handed on |
| `VFG_DISPATCH_PLAN_MODEL`, `VFG_DISPATCH_PLAN_PROVIDERS`, `VFG_DISPATCH_PLAN_REASONING` | the action planner's own; each defaults to the selector's (`VFG_DISPATCH_MODEL`, `_PROVIDERS`, `_REASONING`) |
| `VFG_DISPATCH_REASONING` | `off`; or an effort, `low`, `medium` or `high`, for a model that cannot think without it (Sonnet 5.5 on OpenRouter) |
| `VFG_ORCH_STALE_MINUTES` | `10`: a task that waited longer is failed as expired, never run |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md) §14, and §8 for the `vfg` commands it runs.
