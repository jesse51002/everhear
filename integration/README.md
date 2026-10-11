# integration — the parts together on one PC

The real bridge, Jev filter and `vfg` toolkit, plus the client mod's HTTP client and the
conversation's hooks, run together in a temporary folder that stands in for the box's `/opt/vfg`
and `/opt/mc`. No EC2, no Minecraft, no Jev account.

Stand-ins: Minecraft (console lines appended by the harness, in the shapes of
`bridge/tests/fixtures/console-26.3.log`, and `toolkit/tests/fake_rcon.py`), SSH
(`toolkit/tests/fake_ssh`, which runs the remote command locally), systemd (`toolkit/tests/bin/`),
Jev's API (`jev/tests/fake_jev.py`) and the orchestrator (`ORCH` lines posted by hand). `HOME` and
`VFG_HOME` point into the temp folder and every port is on 127.0.0.1, so nothing touches `~/.vfg`,
`~/.ssh` or any remote host.

For a real Minecraft 26.3 server instead of the fake one, with the same kind of stand-in box
around it, several side by side and Carpet's fake players: [realmc/](realmc/README.md).

## Run it

```sh
integration/run.sh            # PASS or FAIL per check and per path; non-zero on any failure
integration/run.sh --keep     # keep the temp folder and its logs
```

The box's side (bridge, Jev filter, the toolkit's remote programs) runs on Python 3.12, the box's
`/opt/vfg/python`: `run.sh` finds one with `uv python find 3.12`, else uses `python3` and says so.
Path 8 needs a JDK and the mod's compiled classes (`cd client-mod && ./gradlew build` first).

## The paths

| # | Checks |
|---|---|
| 0 | setup as the conversation does it: `vfg up`, watchers, lookout |
| 1 | `POST /v1/lines` → a `lines` row → Jev → one `LINE#seq` in `vfg watch` |
| 2 | typed chat and watched game events reach `vfg watch` |
| 3 | `vfg say` writes an `ai` line that Jev never judges or feeds |
| 4 | `vfg history` by date, source, player, class, events and feed |
| 5 | `vfg feed post` round-trips, and `vfg orch send` / `reply` |
| 6 | `usage` rows: one per Jev call, the conversation's hook, `vfg usage add` |
| 7 | killing and restarting Jev, the watch and the bridge loses and repeats nothing |
| 8 | the client mod's real `BridgeClient` and `LinePayload` against the real bridge |
| 9 | the conversation's test fixtures against what the real feed writer produces |

## Incantations, on a real server

`integration/incantations_e2e.py` is the voice-triggers spec's live test, on realmc: a server
with a fake player Steve, the real bridge against its stand-in box, and two incantations made with
`vfg action run incantation_create` ("fire ball", a scoreboard point; "ice wall", a teleport whose
player is whoever casts it). Casts and lines are posted as the mod posts them, and each check is
checked in the world or in `vfg.db`: the list and its long poll, a cast to its effect, the
duplicate window, the incantation's cooldown and the 1 s between a player's casts, another
player's phrase, the drop rule, a caster field, Layer 2 from a transcript, and the events and
feed lines. `--speech` also has the mod's `castCheck` say "Fire ball!" (Kokoro) through its real
speech pipeline and spotter against that bridge, and times the end of the phrase to the effect.

```sh
REALMC_ROOT=<a realmc .server with the jar> integration/incantations_e2e.py [--name vt] [--keep] [--speech]
```

About 25 s, most of it the server's start and stop; `--speech` adds about 15 s, the mod's
models loading. On 2026-10-10: a posted cast's effect landed 31–46 ms after the post, a
transcript cast's 39–57 ms after its line, and "Fire ball!" spoken 0.53 s after the phrase ended.

## Settings

| Key | Used for |
|---|---|
| `VFG_EC2_PYTHON` | the Python for the box's side, instead of the 3.12 `uv` finds |
| `VFG_JAVA` | the `java` for path 8; else `~/.local/share/jdk/*/bin/java`, else `java` on the PATH |

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): it checks §2–§8 end to end, and §12's start steps.
