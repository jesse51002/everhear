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

## Part 2: the reads, ops and events added for the library and the kit

### Reads on demand (McIo, a tight loop on ge-mc: the round trips and the server's own time)

| Read | How | Cost |
|---|---|---|
| `probe(dim, points, test)` | the points into `everhear:io pr` (50 a command), `function everhear:io/probe` (a macro call per point: `execute in … positioned x y z if block …`), one `data get` | 1 point 0.09 ms, 10 0.22 ms, 100 2.0 ms (~20 µs a point) |
| `ground_y(dim, x, z)` | `function everhear:io/ground {d,x,z}`: `positioned over motion_blocking_no_leaves`, a marker's `Pos[1]`, returned; -99999 (None) for a chunk not loaded | 0.06 ms |
| `ray(dim, origin, direction, length)` | `function everhear:io/ray {…}`: half a block a step from the origin, blocks only, the face found to 1/32; `through_blocks` sends nothing | 10 blocks 0.2 ms, 50 0.26, 250 0.45, 500 0.68, 1000 1.3 ms (2000 steps: no recursion limit met) |
| `entities(dim, center, radius, types)` | `everhear:io en`, `function everhear:io/entities` (an entity compound each), one `data get` | 60 husks in 32 blocks 2.5 ms (~40 µs an entity, read and parsed); 2 in 5 blocks 0.19 ms |
| `loaded(dim, x, z)` | `execute in D if loaded X 0 Z` | 0.03 ms |
| `mspt()` | `tick query`'s "Average time per tick", read at most once a second | 0.08 ms uncached |
| `world_id()` | `everhear:meta world` (two random numbers made the first time the pack loads in a world; kept across reloads and restarts, new in a fresh world), read again after a reconnect | 0.03 ms, then cached |

### Ops

| Op | 26.3 command | Replies seen | Cost (flush of one) |
|---|---|---|---|
| Fill | `[execute in D run] fill x1 y1 z1 x2 y2 z2 <block> [replace <filter>] [strict \| keep \| destroy \| hollow \| outline]`; strict goes only with replace (26.3 has no "destroy strict"); over 32,768 blocks refused before sending | `Successfully filled N block(s)` · `No blocks were filled` (ok: nothing to change) · `That position is not loaded` · `That position is out of this world!` · `Too many blocks in the specified area (maximum 32768, but specified N)` · `Unknown block type …` | 27 blocks 0.04 ms; 32,768 blocks 8.6 ms (2.3 ms when nothing changes) |
| SetBlock | `setblock x y z <block> [strict \| keep \| destroy]` | `Changed the block at x, y, z` · `Could not set the block` (ok when it is that block already; with keep, "something is there") | 0.03 ms |
| Forceload | `forceload add\|remove x1 z1 x2 z2`; over 256 chunks refused | `Marked chunk [4, 4] in … to be force loaded` · `No chunks were marked…` · `Unmarked chunk…` | — |
| Motion | `data merge entity <uuid> {Motion:[xd,yd,zd]}`; a player refused (see launching) | `Modified entity data of Husk` · `Nothing changed…` (ok) · `Unable to modify player data`. A NoAI mob has no physics: Motion does nothing to it | 0.04 ms |
| EntityData | `data merge entity <target> {…}` (UUID refused) | as Motion | 0.03 ms |
| Bossbar | `bossbar add <id> "<name>"` and `bossbar set <id> name …` (made if missing, renamed if there), `set … color/max/value/players`, several players by a moment's tag (`tag P add everhear.bb`, `players @a[tag=everhear.bb]`, tag removed); `bossbar remove <id>`. An id without a namespace is `everhear:` | `Created custom bossbar [Ice Queen]` · `A bossbar already exists…` · `Custom bossbar […] has changed value to 150` · `… now has 1 player(s): Steve` · `… no longer has any players` · `No bossbar exists with the ID …` · `Nothing changed. That's already the color…` | 0.02 ms a command |
| StopSound | `stopsound <player\|@a> [* <sound>]` | `Stopped all sounds` · `Stopped sound '…'` · `No player was found` | 0.02 ms |
| Spawnpoint | `[execute in D run] spawnpoint <player> x y z` | `Set spawn point to 0, 85, 0 [0.0, 0.0] in minecraft:overworld for Steve` | 0.05 ms |
| Ignite | `function everhear:ignite {t, f, k}` (below) | `… returned 1` · `… returned 0` | a mob 0.07 ms, a player 0.6 ms |
| Sound `to` | `playsound <name> master <player> …`, one per player (`@a` when none) | `Played sound … to Steve` · `No player was found` | 0.04 ms |
| Particle `force`/`to` | `particle … force\|normal <player\|@a>`, one per player | `Displaying particle …` · `No player was found` | 0.04 ms |

### Events added

| Fact | How known |
|---|---|
| An arrow keeps its bow as `weapon` (with the bow's custom_data), `inGround: 1b` once stuck, `inBlockState: "minecraft:grass_block"` (a plain string), `Owner`. A trident keeps its own stack as `item` and has `DealtDamage: 1b` once it struck (it was 1b after landing in grass too). A tamed wolf has `Owner`. | measured (Carpet's Steve shooting) |
| An arrow that hits an entity is still there when the ranged hit's advancement reward runs, within a few blocks of its target; it is gone right after. | measured (land with hit "entity" and the arrow's uuid) |
| A ranged hit's damage is not direct (`is_direct` false: the direct entity is the projectile): `hit_ranged`. Primed TNT with `owner` set credits its blast to that player, so each entity it hurts is that player's ranged hit too. | measured; the `owner` key from the bytecode and a summon |
| `minecraft.mined:<block>` (the criterion names the block with dots: `minecraft.mined:minecraft.sponge`) counts a block broken in survival, not in creative; it says nothing of where. | measured (a sponge mined by Carpet's `attack continuous`, 1.2 s) |
| `LastDeathLocation: {dimension, pos: [I; x, y, z]}` is in a player's data after a death (a Carpet player's too, when it comes back). | measured |
| The datapack's tick with nothing to do: 0.03 ms; with 4 watched kinds: 0.03 ms; with 20 of a player's arrows in flight: 0.12 ms (each read once a tick until it comes down). A hit's far search (`@e` with `on attacker`, 126 entities loaded): 0.16 ms. | measured |

### Setting things on fire

| Fact | How known |
|---|---|
| A mob takes `Fire` ticks: `data merge entity <uuid> {Fire:200s}`. A player cannot: `Unable to modify player data`. | measured |
| A fire block at a player's feet does not light them for about a second: a player's `Fire` rests at -20 and goes up 1 a tick in fire (2 ticks: -18, 1 damage), and Minecraft lights it for 8 s only when it reaches 0. | measured |
| `/damage … by <a mob holding a Fire Aspect sword>` does not light anyone (an enchantment's attack effects belong to a real attack). A 0-damage burning arrow does nothing. | measured |
| A player's body and saddle slots take no item (`No targets accepted item…`), so an enchantment's tick effect cannot be hidden on them. | measured |
| **Lava for two ticks at the feet lights a player at once**, for 15 s, with 4 damage. With one second of Fire Resistance the 4 damage is not taken (lava lights whatever is not fire-immune by its type, Fire Resistance or not), and the `burning_time` attribute scales the 15 s: a modifier of seconds/15 − 1 (`add_multiplied_base`) gave exactly 60, 300 and 801 ticks for 3, 15 and 40 s. Burning then hurts from the second second (3 s: 2 health). This is Ignite for a player (`everhear:ignite`), with the modifier `everhear:ignite` taken off two ticks later. | measured |
| A fire on a player cannot be cut short by the engine (no command puts a player out); Ignite sets the length once. | not measured: water or powder snow would, and the engine does not place them |

### For the kit: launching a player, a breaking blast, lightning

| Fact | How known |
|---|---|
| **Players take no Motion** (`Unable to modify player data`). **Wind charges are the reliable launch**: one set off on the ground at the feet (summoned 0.6 above, `Motion [0,-1,0]`, it bursts on the ground the next tick) lifts a player +6 blocks; 2 in a ring of 0.4: +17; 4: +56; 8 (ring 0.5): +150, and no fall damage was taken within the 4 s watched (a wind charge jump spares the fall above where it left from). The push is the explosion's knockback, which the client applies to itself, so it works for a real player as for Carpet's. The old preset `scripts/_presets/launch.py` has the measured model (1.22 × (1 − d / 2.4) along the line from the burst to the eyes, 2.4 blocks' reach) for charges staged behind a player to throw them along the ground. | measured |
| Levitation XXI for 1 s lifts +19 blocks but the fall then cost 15 health; `/damage … at` a point under the feet gives no lift (knockback is along the ground only). Riding an invisible armor stand given Motion carries a player too (the old preset's "ride"); not measured here. | measured / not measured |
| **A breaking explosion**: `summon tnt x y z {fuse:0s,explosion_power:P,owner:[I;…]}` goes off on the next tick; in dirt it broke 243 blocks at P 4 (vanilla TNT), 1208 at 8, 3044 at 12. A creeper `{Fuse:0s,ignited:1b,ExplosionRadius:R}` matches TNT at P = R (3: 126, 8: 1178; `powered:1b` doubles it: R 4 → 1165). In stone (resistance 6) the same TNT broke 46 and 67. `owner` credits the damage to that player; `block_state` sets what the TNT looks like. | measured |
| **Lightning**: `summon lightning_bolt x y z` hurts everything within about 3 blocks (husks at 0.5, 1.5, 2.5: 5 lightning damage each, the nearest set alight for 160 ticks; at 3.5 and beyond: nothing) and lit 3 fire blocks. A bolt reads no NBT (`readAdditionalSaveData` is empty), so it cannot be made visual-only or credited to anyone: content that counts its damage deals its own `Damage(by=…)` and keeps the bolt for the look (with the spared kept 4 blocks away). | measured; bytecode (`LightningBolt`) |
| The gamerules for fire are `fire_spread_radius_around_player` (128 here); there is no `do_fire_tick` in 26.3. | measured |

## Not measured

- **Death on a real client.** A Carpet fake player that is killed leaves (`Steve lost connection:
  Steve died`) with its `deathCount` up, so the pack reports `death` and `respawn` in one tick when
  it comes back (measured). A real client stays on the death screen, where `@a` selects it
  (bytecode): `death` would come the tick after, `respawn` when it respawns. Whether effects and
  `/attribute` modifiers survive a real death is the spec's TODO.
- **Load.** Every cost above is an idle world; a busy server's ticks delay RCON commands (they run
  between ticks), and twenty players are an estimate.
- **TCP_QUICKACK on the box.** Amazon Linux's kernel has it (Linux 2.4.4+); not run there yet.
