# conversation — the AI players talk to

A Claude Code session on the owner's PC. It follows the feed (`vfg watch`, under Monitor), answers
players in chat with `vfg say`, stays quiet, or hands the work to the orchestrator with
`vfg orch send`. It never does the work itself. [`CLAUDE.md`](CLAUDE.md) is its whole brief.

## Run it

```sh
conversation/start              # a new session; it runs the start steps in CLAUDE.md
conversation/start --continue   # the last session again, after a crash
```

`start` needs `vfg` on the PATH and `VFG_OWNER` (your Minecraft name) in `~/.vfg/config` or the
environment, and refuses without it. The first time, it writes `CLAUDE.local.md` beside
`CLAUDE.md`: Claude Code loads both, and that file is the only place the owner is named. It is
gitignored, so you can add your own standing rules there.

The session runs on Sonnet at low effort, on your Claude subscription (`ANTHROPIC_API_KEY` is
unset). `--setting-sources project,local` and `--strict-mcp-config` keep your own Claude Code
settings, plugins and MCP servers out of it. Type to it at any time: what the owner types is an
order. `stop` signs the AI off and leaves the server running.

`.claude/settings.json` lets it run only `vfg` (minus `cmd`, `script`, `orch reply`, `feed` and
`usage`) and `date`, and denies edits, the web and subagents. Two hooks: `hooks/usage.py` (Stop)
records each turn's tokens with `vfg usage add`; `hooks/after-compact.sh` hands back the last 15
minutes after Claude Code compacts the conversation.

## Test it

```sh
cd conversation && python3 -m unittest test.test_usage    # the usage hook; no network
conversation/test/decide.sh [case …]                       # the decision cases, through claude -p
```

`decide.sh` runs each `test/cases/*.case` against the stand-in `test/fake-vfg/vfg` and checks the
calls it made; a model judges each case, so a failure is rerun once. It uses your subscription:
`VFG_TEST_MODEL` (default `sonnet`) and `VFG_TEST_EFFORT` (default `low`). Its owner is the
placeholder `Sunny`, and your own `CLAUDE.local.md` is left out. Output goes to `test/out/`.

## Settings

| Key | Used for |
|---|---|
| `VFG_OWNER` | the owner's Minecraft name, written into `CLAUDE.local.md` when it is missing |
| `VFG_HOME` | where `~/.vfg` is (the config, the hook's `conversation/` state) |
| `VFG_CONFIG_FILE` | another config file than `$VFG_HOME/config` |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §7 the feed it reads, §8 the `vfg` commands it runs,
§12 startup, §4 its `usage` rows (`component = conversation`).
