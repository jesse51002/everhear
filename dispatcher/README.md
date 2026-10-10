# dispatcher — every task's first stop

A small always-running Python process on the owner's PC. Every task the conversation sends
(`vfg orch send`) lands in its inbox. The complexity selector picks the [actions](../actions/README.md)
that do it and what only the orchestrator can do; the action planner fills each pick in, checking
any command it writes against the server's own command reference first; the executor runs it with
`vfg action run`. Each action's report goes to the conversation as it finishes (`progress`, the
last one `done`); what no action does goes to the orchestrator with `vfg orch hand`, collected for
10 s; a pick nothing can fix is dropped and the conversation told why. Every model call is
`structured()` from [`lib/py/`](../lib/py/vfg_llm.py), reasoning off.

## What it chooses from

- **Every action**, the library's (`actions/`) and this server's (`made/<VFG_SERVER>/actions/`),
  loaded by [`vfg_actions`](../lib/py/vfg_actions.py) and kept in memory. Before each ask the files
  are looked at again and any that changed reloaded, so an action an implementer saved a moment ago
  is on the menu at the next ask, with no restart. The selector sees each action's name and its
  docstring's first line; the planner sees the one picked in full and answers with the values it
  `takes` (`planner_model`), every other keeping its default. Any action with a `Command` field
  (`command`'s `commands`) is drafted, checked against the references and approved first.
- **Every running copy**, one line each: its name, its action, the values that differ from the
  action's defaults and the task that started it, read from `~/.vfg/running.json`. A change to one
  is `edit`: the planner sees the copy it names in full, every value with its setting now, and
  answers with the values to change (`edit_model`). Ending one is `stop`. A background fetch,
  `vfg script list --json --fresh` every `VFG_RUNNING_TTL_S`, drops the copies that ended by
  themselves; no ask waits on it.
- **The saved places**, from `vfg place list` as each task starts.

The executor runs `vfg action run <action> key=value … --task T<n>` (a list as JSON) and reads its
one JSON line: `ok`, `summary`, the copy's name, `running`, the facts and vfg's notes. A once action
past its timeout answers that it is still running, and is never run again. A task opening with
`Fix` goes to the orchestrator whole, with no model call; anything else is the selector's.

## Set it up

Once: put your OpenRouter API key alone in `~/.vfg/openrouter.key`, with mode 600.
`VFG_OPENROUTER_KEY` in `~/.vfg/config` can point at another file. `OPEN_ROUTER_API` in the
environment wins over both.

```sh
(umask 077 && read -rsp 'OpenRouter key: ' key && echo && printf '%s\n' "$key" > ~/.vfg/openrouter.key)
```

The actions' scripts run on the box, which `vfg script deploy` puts there with the kit. Without
them `vfg action run` cannot run an action, and the task goes to the orchestrator.

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
the actions with no `Command` field work and the rest go to the orchestrator; tasks that start
once the references are in use them as usual. If the fetch fails, it carries on the same way and
tries again every 5 minutes.

## Test it

```sh
cd dispatcher && uv run pytest                          # no model, no network: a fake structured() and a fake vfg
uv run --project dispatcher python integration/dispatcher_e2e.py   # the real dispatcher, toolkit and model, a fake box
```

The tests load the repo's own actions, from a copy of `actions/` beside a stub of each script, and
fake `vfg action run`'s JSON line.

The end-to-end run is the spec's smoke test with no EC2: the real `vfg` through
`toolkit/tests/fake_ssh`, a fake Minecraft world over RCON, `OPEN_ROUTER_API` from the
environment or the repo's `.env`. Each full run costs well under a cent.

## Replay

```sh
cd dispatcher && uv run python -m dispatcher.replay [--model <id>] [--limit N] [--only T3,T9] [--jobs 8] \
                                                    [--eval labels.jsonl] [--out results.jsonl]
```

Sends the playtest's tasks from `~/.vfg/orch/inbox.jsonl` (read only) through the complexity
selector alone, with every action on the menu and nothing running, and prints each selection, its
cost and time, then the totals. It acts on nothing. It is how a cheaper model or a prompt change is
judged before a live run.

With `--eval` it replays only the tasks the file labels and scores each against its label: the
route (exact, or any route the label accepts), each part (did it reach its action, or the
orchestrator), and the work lost (a part that went nowhere, or the orchestrator's part given only
to an action). The labels are kept outside the repo, in `~/.vfg/dispatch/eval/`.
`python -m dispatcher.label --from <old labels> --to <new labels>` labels the same tasks again for
the menu as it is now, with a stronger model through `structured()`: it prints its cost estimate
and the OpenRouter credit left first, and runs only under `--max-cost` (default $10).

| Date | Labels | Selector | Agreement | Lost | Time p50 / p90 | Cost |
|---|---|---|---|---|---|---|
| 2026-10-09 | `selector.jsonl`: by hand, for the six actions | DeepSeek V4 Flash, reasoning off | 91% of routes | 0 | 0.8 s | $0.02 |
| 2026-10-10 | `selector-actions-2026-10-10.jsonl`: Claude Opus 5.5 at high reasoning, for the 67 actions, all 172 read by hand and 20 changed | GPT-6.1 Sol, reasoning low, OpenRouter's providers | 168/172 routes (97.7%); 146/172 the label's own route (84.9%); 183/192 parts (95.3%) | 0 tasks, 0 parts | 2.0 s / 3.2 s | $1.78 |

Both over the 172 tasks of the 2026-10-08 playtest, with nothing running and no saved place, so
`edit` and `stop` are never the answer. On 2026-10-10 every disagreement was the selector handing
the whole task to the orchestrator when an action could do it, or part of it: never a wrong
action, never the orchestrator's work given to an action. Before this prompt's paragraph on the
context, the same run agreed on 151/172 routes: the playtest's contexts tell the orchestrator how
to build each ask, and the selector took that as more than any action does.

## Settings

From the environment, then `~/.vfg/config`:

| Key | Default |
|---|---|
| `OPEN_ROUTER_API`, else the file `VFG_OPENROUTER_KEY` names | `~/.vfg/openrouter.key` (mode 600; only `structured()` reads it) |
| `VFG_DISPATCH_DIR` | `~/.vfg/dispatch`: `inbox.jsonl`, `state.json`, `dispatcher.log`, `refs/` |
| `VFG_SERVER` | `main`: whose `made/<server>/actions/` are on the menu beside the library's |
| `VFG_RUNNING_TTL_S` | `30` s between background fetches of the running copies (`vfg script list --json --fresh`); `0`: never |
| `VFG_DISPATCH_MODEL` | `~deepseek/deepseek-v4-flash-latest` |
| `VFG_DISPATCH_PROVIDERS` | `parasail,wafer,phala,together,venice`: OpenRouter providers tried first, in order, for every model call (the replay's: no lost work); `none` lets OpenRouter choose |
| `VFG_DISPATCH_RETRIES` | `1` retry per failed action |
| `VFG_DISPATCH_WINDOW_S` | `10` s before handed tasks go to the orchestrator together |
| `VFG_DISPATCH_ACTION_TIMEOUT_S` | `20` s per once action, which `vfg action run` keeps; a class may set its own `timeout_s` (`teleport`: 60); the dispatcher waits 5 s past it |
| `VFG_DISPATCH_MODEL_TIMEOUT_S` | `10` s per model call (selector, planner, replay), its retries included; past it the task, or that pick, is handed on |
| `VFG_DISPATCH_REASONING` | `off`; or an effort, `low`, `medium` or `high`, for a model that cannot think without it (Sonnet 5.5 on OpenRouter) |
| `VFG_ORCH_STALE_MINUTES` | `10`: a task that waited longer is failed as expired, never run |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md) §14, and §8 for the `vfg` commands it runs.
