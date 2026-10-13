# Minecraft 26.3, as the engine's connection relies on it

The facts `vfg_engine/mc/` and the datapack (`engine/datapack/`) are built on, each marked how it
is known:

- **measured**: seen on a realmc 26.3 server (the box's own `server.jar`, Fabric + Carpet) on
  2026-10-12/13, server `ge-mc`; most are pinned by `engine/tests/mc/test_mc_live.py`;
- **bytecode**: read from the 26.3 jar, which ships unobfuscated (`javap -c` on the class named);
- **not measured**: believed, with the reason, and what would show it.

Costs are this PC's (12 cores, other servers running, an idle world with two fake players); the
box will differ. `engine/tests/mc/bench_mc.py` measures them again.

## RCON

| Fact | How known |
|---|---|
| Every connection is answered from one shared buffer (`RconConsoleSource`): two commands running at once get each other's output. Every client holds the flock `/tmp/vfg-rcon-<port>.lock` from send to the end of the reply. | measured (2026-10-12, before this build) |
| **Closing a socket does not stop its command.** A command whose exchange was cut still runs, and its output lands in the next command's reply, on any connection. So an interrupted exchange is finished on the same socket before the lock is let go: an end marker is sent and the raw bytes read until its answer's exact bytes appear, which also puts the framing back in step. | measured: a cut `data get` (38 KB) glued onto the next `time query` on a new connection. With the recovery: 2000 commands cut at random moments (20–600 µs), 845 interrupted, **0 wrong replies, 0 dropped connections**, no waits over 50 ms |
| A SIGALRM (the engine's cut) cannot be held off an exchange by blocking it on the main thread: the kernel hands a process's timer signal to any thread that has not blocked it, and Python runs the handler on the main thread at its next bytecode, even right after a `recv()` returned and before its bytes were kept. The reader therefore keeps every byte in a buffer the moment it can, and the recovery does not need the lost ones. | measured (a cut after `recv`, with a server thread in the process) |
| The reader (`RconClient.run`) makes a new `BufferedInputStream` and reads at most 1460 bytes per packet, and **closes the connection when a read is not exactly one packet** (`length != read - 4`). Never pipeline; a command over 1446 bytes kills the connection. | bytecode; the over-long command measured (the server closed the socket) |
| A reply is cut into packets of **4096 UTF-16 units** (not bytes: 4096 `é` came as 8093 bytes). A packet shorter than that is the last. | measured |
| A packet of a type other than 2 (command) and 3 (login) is answered `Unknown request <hex>` with its own id, after the reply in progress: the end marker after a packet of exactly 4096 units. | measured (`Unknown request 0`, after 10 packets of a 38 KB reply) |
| **TCP_QUICKACK after every recv removes the ~40 ms per extra packet** (the client's delayed ACK held up the server's next packet). 38 KB: 42 ms → 0.2–0.45 ms. Set after send only: no change (41 ms). | measured |
| RCON commands run on the server's main thread between ticks. A simple command: 0.02–0.035 ms in a tight loop; **0.5 ms at the engine's 10 Hz** (an idle server's wake-up, on this PC). | measured |
| Commands inside a function send nothing back; `function X` answers `Running function X`, and `Running function XFunction X returned N` when it ends in `return`. So every read is a function, then one `data get`. | measured |
| `data get` on an entity saves the **whole** entity to read any path: a player 0.04–0.05 ms, 0.34–0.39 ms with every recipe unlocked (the recipe book is in it). | measured |
| `datapack list` names a pack copied into `datapacks/` as enabled after the next `reload`. | measured |

## The datapack's 26.3 forms

| Fact | How known |
|---|---|
| The jar's `version.json` has `data_major 121, data_minor 0`; `pack.mcmeta` takes `min_format`/`max_format` `[121, 0]`. | measured (loads) |
| A loot condition names its kind under `type` (not `condition`); an entity predicate's parts are keyed `minecraft:entity_type`, `minecraft:flags`; a trigger's `entity` is **one** loot condition, not a bare predicate or a list. | measured (the server refused every other form, in its log) |
| An entity's saved data has **no `id`**: its type cannot be read as text. The pack numbers types by tag bits. | measured (`Found no elements matching id`) |
| 161 entity types, 1286 blocks, 1657 items (air aside). | the jar's data generator (`--reports`) |
| `@e` and `@n` select only living entities (`Entity::isAlive`); `@a`, `@p`, `@r`, `@s` select the dead too. | bytecode (`EntitySelectorParser.parseSelector`) |
| A dying mob stays selectable by its UUID for its death animation (~1 s). | measured (remove "succeeded" twice until it checked for the dead) |
| An advancement's reward runs at once, as the player, inside the damage: the victim's `HurtTime` is 10, the `damage_taken` statistic already counted, `execute on attacker` already set. | measured (hit, hurt) |
| Death runs **before** the hurt trigger: a killing blow gives the kill's reward, then the hit's. | measured (kill n=4, hit n=5) |
| `damage_taken` is the health lost ×10, after armour and absorption. On hard, a mob's damage to a player is ×1.5 (`damage Steve 2 minecraft:mob_attack by <husk>` took 3.0). | measured |
| On hard, **explosion damage to a player is ×1.5 whoever caused it**: `damage Alex 4 minecraft:explosion by Steve` and `… minecraft:player_explosion by Steve` took 6.0, `… minecraft:generic by Steve` and `… minecraft:on_fire by Steve` 4.0 (vanilla's `scaling: always` for the explosion types). So `self.explode`'s damage reaches players 1.5 times on the box. | measured (2026-10-13, `engine_e2e.py` step 5b) |
| A target hurt in the last 10 game ticks takes a new hurt only when it is bigger, and then only the difference: Fireball's last burn (1.05, `on_fire`) and its explosion (8.4) sent in one tick took 1.05 + 7.35 (so the engine sends one tick's damage on one target by one dealer as one op). | measured (2026-10-13) |
| About a second after joining, a player refused `minecraft:explosion` and `player_explosion` damage ("Target is invulnerable to the given damage type") that it took a few seconds later; `generic` landed 2.5 s after the join. | measured once; when the protection ends is not measured |
| An entity teleported into a chunk with no ticket (its forceload just removed, no player near) is no longer selectable within a second, so the snapshot loses it and the engine takes it as gone; a player arriving loads it back within a second, and the entity is there as it was. | measured (`engine_e2e.py` step 7b) |
| A player's data has `last_hurt_by_mob` and `ticks_since_last_hurt_by_mob` (0 in the tick it was set); there is no `HurtByTimestamp` any more. | measured; bytecode (`LivingEntity` constants) |
| Item entities have a `Health` of their own (`5s`); only living things save `attributes`. | measured |
| A duplicate key in SNBT: the last wins (`Tags:["x"],Tags:["y"]` → `["y"]`). | measured |
| A use of carrot_on_a_stick adds exactly 1 to `minecraft.used:minecraft.carrot_on_a_stick` (Carpet's `use once`). Warped fungus on a stick the same, from the off hand too. | measured |
| A base with a `consumable` component reports through `using_item` once per tick while held (Carpet `use continuous`: 21 in 1 s) and never for Carpet's `use once` (0). | measured (not used, see bases.py) |
| Zombies burn at noon even with `NoAI`; the tests use husks, or a zombie wearing a helmet. | measured |
| Copying many values: `data modify … append from <path>[]…` appends every match. | measured (inventory ids) |
| Teleporting into the nether for the first time held the server ~1.1 s (chunk generation). | measured |

## Each op's command and its replies

Every reply here was seen on 26.3; `vfg_engine/mc/commands.py` reads them, and
`test_mc_units.py` holds them.

| Op | Command | Replies (ok → error words) |
|---|---|---|
| AddModifier | `attribute <t> <attr> modifier add <everhear:id> <amount> <op>` | `Added modifier …` · `Modifier … is already present on attribute …` → "already present" · `No entity was found` → "no entity found" · `Can't find element 'x' of type 'minecraft:attribute'` → "no attribute x" |
| RemoveModifier | `attribute <t> <attr> modifier remove <everhear:id>` | `Removed modifier …` · `Attribute … has no modifier …` → "no modifier with that id (not present)" |
| GiveEffect | `effect give <t> <effect> <seconds\|infinite> <amp> <hideParticles>` (whole seconds: the command takes no ticks) | `Applied effect …` · `Unable to apply this effect (target is either immune to effects, or has something stronger)` → "has something stronger" |
| GiveItem | `give <p> <base>[custom_data={everhear:{item:"<id>"}},item_name="<name>",<components>] <n>` | `Gave 1 [Fire Staff] to Steve` · `No player was found` → "no player found" · `Unknown item …` → "no such item" |
| TakeItem | `clear <p> *[custom_data~{everhear:{item:"<id>"}}]` | `Removed N item(s) from player …` · `No items were found on player …` (ok: they carry none) |
| Summon | `summon <type> <x y z> {<nbt>,Tags:[…],UUID:[I;…]}`, `execute in <dim> run …` off the overworld | `Summoned new Husk` (or its name) · `Unable to summon entity due to duplicate UUIDs` → "an entity with that uuid already exists" |
| Remove | `function everhear:remove {u:"<uuid>"}` | `… returned 1` · `… returned 0` → "no such entity (gone already, dying, or a player…)" |
| Damage | `damage <t> <amount> <type> [by <who>]` | `Applied 3.0 damage to Husk` · `Target is invulnerable to the given damage type` (also within 10 ticks of the last hurt) → "invulnerable to it…" |
| Particle | `particle <name> <x y z> <dx dy dz> <speed> <count>` | `Displaying particle …` · `The particle was not visible for anybody` (ok) · `Unknown particle …` → "no such particle" |
| Sound | `playsound <name> master @a <x y z> <volume> <pitch>` | `Played sound … to Steve` · `The sound is too far away to be heard` (ok). An unknown sound id is not checked: `Played sound minecraft:nope to Steve` |
| Message | `tellraw <p\|@a> "<text>"`; `title <p> actionbar\|title\|subtitle "<text>"`, one per player | tellraw: `` (ok), `No player was found`; title: `Showing new title for Steve` |
| Teleport | `tp <t> <x y z> [<yaw> <pitch>]` (`~` for one not given), `execute in <dim> run …` off the overworld | `Teleported Steve to …` · `No entity was found` |
| Gamerule | `gamerule <name> <value>` | `Game rule mob_griefing is now set to false` · `… is already set to 3` · `Incorrect argument for command…<--[HERE]` · `Invalid boolean: …` |
| read_gamerule | `gamerule <name>` | `Game rule mob_griefing is currently set to true` |
| read_modifiers | `data get entity <t> attributes` (only `everhear:*` kept) | the attributes list |
| Command | as given; the owner's commands (`stop`, `op`, `ban`, `whitelist`, …) refused | as Minecraft answers |

`execute in <dim> run <cmd>` passes the inner command's error through (`Unable to summon …`,
`The particle was not visible …`); `execute at <player> run …` with no such player answers ``
(measured before this build).

## What it costs (measured, two players)

| What | Server's time (tight loop) | At 10 Hz, wall |
|---|---|---|
| Snapshot function, 2 players, looking at the floor 3 blocks on | 0.18–0.20 ms | |
| … looking at open sky (the whole 48-block ray) | 0.35–0.39 ms | |
| One raycast: 48 blocks of sky / 3 blocks to the floor | 0.15 ms / 0.06–0.10 ms | |
| Snapshot read (544 chars for 2 players) | 0.04 ms | |
| The tick's I/O: events + snapshot (one function, one read) | | 2.3–2.9 ms median (separate reads: 2.6–2.7) |
| … plus a flush of 10 ops | | 4.5–4.9 ms median, p95 5.4–7.7 |
| Flush, per op, in a batch of 100 | 0.03–0.04 ms | 0.14 ms in a batch of 10 |
| A page of 12 events / a backlog of 120 | | 0.5–1.7 ms / 4.8–10 ms |
| Mirror pass, 69 entities within 32 blocks, two players among them (the pass skips players) | 1.7 ms (function) + 0.4 ms (18.5 KB read) | 7 ms with parsing |
| Parsing in Python | ~0.1 ms per KB | |
| The cut's recovery (an interrupted exchange) | one round trip | |

**Twenty players (an estimate, from the above):** the snapshot function costs the server about
0.1–0.25 ms per player (0.04 for its data, up to 0.15 for its ray, the rest for the compound), plus
0.3 ms for each player with a large recipe book: 2–5 ms a snapshot, up to ~11 ms with every book
full, so 2–11% of the server's main thread at 10 Hz. The read is ~270 characters a player, 5.4 KB
(two packets; no extra cost with TCP_QUICKACK), parsed in ~0.6 ms. The mirror costs ~25 µs per
entity a pass: with ~600 entities near twenty players a pass is ~15 ms, so its budget (a tenth of
the server's time) slows it to about 0.15 s. Levers: `VFG_ENGINE_RAY_BLOCKS` (48),
`VFG_ENGINE_MIRROR_RADIUS` (32), `VFG_ENGINE_MIRROR_S` (0.1), `VFG_ENGINE_MIRROR_BUDGET` (0.1).

## Not measured

- **Death on a real client.** A Carpet fake player that is killed leaves (`Steve lost connection:
  Steve died`) with its `deathCount` up, so the pack reports `death` and `respawn` in one tick when
  it comes back (measured). A real client stays on the death screen, where `@a` selects it
  (bytecode): `death` would come the tick after, `respawn` when it respawns. Whether effects and
  `/attribute` modifiers survive a real death is the spec's TODO.
- **Load.** Every cost above is an idle world; a busy server's ticks delay RCON commands (they run
  between ticks), and twenty players are an estimate.
- **TCP_QUICKACK on the box.** Amazon Linux's kernel has it (Linux 2.4.4+); not run there yet.
