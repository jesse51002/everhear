# The EverHear datapack: the engine's sensors

A folder of JSON and command files in the world's `datapacks/` folder, which vanilla 26.3 loads by
itself (the game engine spec, "The datapack"). It holds no game logic. It notices what happens in
the game, numbers it into one queue, and builds what the engine reads each tick. It is generic: it
reports EverHear item ids from `custom_data={everhear:{item:"<id>"}}`, so a new item never changes
it. The engine's side is `vfg_engine.mc` (McIo); the 26.3 facts both rely on are in
[../MINECRAFT.md](../MINECRAFT.md).

```text
everhear/
  pack.mcmeta                          min_format/max_format [121, 0]: 26.3's data_major/minor (its version.json)
  data/minecraft/tags/function/        load -> everhear:load, tick -> everhear:tick
  data/everhear/
    function/load, tick, meta          objectives, the queue, the version and tables; every tick's checks
    function/ev/*                      the detectors, and ev/push (the queue's one way in)
    function/io/*                      what the engine calls: events, take, snap, player, tick, mirror, entity
    function/ray/*                     each player's raycast
    function/remove, remove_one        Remove: gone without loot or a death message
    function/type/entity, type/block   an entity's type / the block here as a number   (generated)
    advancement/hit, kill              player_hurt_entity / player_killed_entity, type bits (generated)
    advancement/hurt, milk             entity_hurt_player / consume_item of a milk bucket
    predicate/sneaking                 entity flags is_sneaking
    tags/entity_type/bit/0..7          one tag per bit of a type's index                 (generated)
    tags/block/bit/0..10               one tag per bit of a block's index                (generated)
    tags/block/ray_through             blocks the ray passes through
    tags/entity_type/ray_ignore        entities the ray passes through
  ../generate.py                       writes the generated files from the server's registries
```

**Install:** copy `everhear/` to `<world>/datapacks/everhear` and run `reload` (a pack copied
there is enabled on the next reload or start). `McIo.open()` checks it is enabled and that
`everhear:meta version` is the version the engine reads (1), and says what to do when not.

**A new Minecraft version:** run the server jar's data generator and `generate.py` on its
registries (the commands are in its docstring), check the pack format against the jar's
`version.json`, run the live tests, and raise `VERSION` in both `generate.py` and
`vfg_engine/mc/datapack.py` when anything the engine reads changed.

## Numbers instead of names

Commands cannot write an entity's type, a block, or a player's name as text. So every entity type
and every block has an index (1-based, the registry's ids sorted; 0 is "not known"), each bit of
the index is a tag, and a function adds up the bits (8 tests for 161 types, 11 for 1286 blocks).
The tables are in `everhear:meta types` and `everhear:meta blocks`, which the engine reads at open.
An advancement cannot run a test on the entity it saw, so `hit` and `kill` have one criterion per
bit and one for its inverse, each pair required: the reward runs once every bit is known and reads
which were granted. Players and entities are named by UUID (four ints); the engine turns a
player's UUID into a name with `list uuids`.

## The queue

`everhear:events q` is a list of events, oldest first; `everhear.q` holds the counters:

| Score | What |
|---|---|
| `#n` | the last number given (it only goes up; a fresh world starts again at 0) |
| `#len` | how many wait |
| `#over` | how many a full queue turned away, ever |
| `#max` | how many it holds: 2048 |

Every event goes in through `ev/push`, which adds its number (`n`), the game time (`t`) and the
player's UUID (`u`) to `{k, d}`. A full queue does not add it and counts it in `#over`; the engine
reads `#over` with every page and logs what was turned away, so nothing is dropped in silence.

An event stays until the engine says it has it. `function everhear:io/events {upto:N}` takes off
the front every event numbered up to N (the last one the engine read), then copies the next page
of at most 12 into `everhear:out ev` with `len`, `last` (= `#n`) and `over`. So an event read is
taken off on the engine's next read, and one read twice (a reconnect between the function and its
`data get`) is deduped by its number. When `#n` is behind N the world was replaced (or the pack
reset): nothing is taken, and the engine starts counting again from there.

`function everhear:io/tick {upto:N}` is the engine's one read a tick: the page above and the
snapshot below, both into `everhear:out`, read with one `data get storage everhear:out`.

## Events

Each event is `{n, k, u, t, d}`: number, kind, the player's UUID, the game time, and what `d`
carries below. The engine hands it over as `RawEvent(n, kind, player, data, game_tick)`, with
UUIDs as hyphenated strings (a player's as their name) and type numbers as ids, and the player's
own UUID added as `data["uuid"]`.

| Kind | How it is noticed | `data` |
|---|---|---|
| `use` | each tick, `minecraft.used:minecraft.carrot_on_a_stick` / `…warped_fungus_on_a_stick` above 0: the used stack is the main hand's if it is that base, else the off hand's; it is an event only when that stack has an EverHear id | `item`, `slot` (`mainhand`/`offhand`), `count` (uses since the last tick: 1, unless something clicks every tick) |
| `hit` | advancement `player_hurt_entity`, direct damage only (`is_direct`: melee, a sweep, or a `damage … by <player>`), at once. The target is the entity near the player (16 blocks), attacked by them (`on attacker`) and hurt this tick (`HurtTime` 10) that no hit this tick reported yet (a sweep: one each) | `target` (uuid, or a player's name), `target_type`, `item` (the EverHear id in the main hand, if any) |
| `hurt` | advancement `entity_hurt_player`, at once | `amount` (health lost, after armour and absorption: the `damage_taken` statistic), `absorbed` (what absorption hearts took), `attacker` and `attacker_type` only when this damage set the attacker (`ticks_since_last_hurt_by_mob` is 0) |
| `kill` | advancement `player_killed_entity`, at once | `target_type`; `target` only for a player killed (a dead player is still selected by `@a`); `item` |
| `death` | each tick, `deathCount` above 0 | — |
| `respawn` | each tick, a player marked dead who is alive again (`@e`, unlike `@a`, never selects the dead) | — |
| `milk` | advancement `consume_item` of a milk bucket, at once | — |

What 26.3 cannot give, or this pack does not:

- **A killed mob's UUID.** It is dead before any reward runs, and `@e` selects only the living, so
  `kill` of a mob, and the killing blow's `hit` (which comes after its `kill`: Minecraft runs the
  death first), carry no target. The engine knows its own mobs die from `gone`.
- **The damage of a hit**, and **the damage type of a hurt**: no statistic or selector has them.
- **A right-click with other bases.** Only the two "on a stick" items count a click (see
  `vfg_engine/bases.py`). A `consumable` component makes any base report through `using_item`,
  but only while held, never for a click shorter than a game tick, and the player is slowed
  meanwhile: measured, not used.
- **Who threw a potion or shot an arrow at a mob**: `hit` is melee only (`is_direct`).
- **A Carpet fake player's death, on time.** A killed fake player leaves the server instead of
  dying; its `deathCount` still goes up, so `death` and `respawn` come together when it comes back.
  A real client stays dead on the death screen, where `@a` still selects it: `death` the tick after
  it died, `respawn` when it respawns (not measured: no real client on the test server).
- **Several hurts in one tick** are separate events, each read at once; the engine's `Damage` with
  `by` a player is that player's `hit` too (Minecraft counts it as theirs).

Left-clicks in the air, sneaking and items held are not events here: the engine works out `sneak`,
`held`, `unheld` from the snapshot.

## The snapshot

`function everhear:io/snap` builds `everhear:out snap = {time, p: [...], e: [...]}`: a compound per
online player (`everhear:io/player`, as and at the player), and one per tracked entity that exists
(the engine sets `everhear:io track`, a list of `{u:"<uuid>"}`, when it changes).

A player's compound reads the player's data once (each read of an entity's data saves all of it):

| Field | What |
|---|---|
| `u`, `d` | UUID, dimension |
| `p`, `e` | feet, eyes (a marker summoned at `anchored eyes`, so a sneak or a scale is right) |
| `r` | rotation `[yaw, pitch]` |
| `h`, `mh` | health, max health (the attribute) |
| `g`, `s` | game mode (0 survival … 3 spectator), sneaking (predicate `everhear:sneaking`) |
| `fx` | `active_effects` as Minecraft keeps them, `hidden_effect` chains included |
| `hd` | the EverHear id in each of `mainhand`, `offhand`, `head`, `chest`, `legs`, `feet` |
| `inv` | the EverHear ids in the 36 inventory slots (the main hand's slot among them) |
| `l` | the raycast, below |

An entity's compound (`everhear:io/entity`): `u`, `y` (type index), `d`, `p`, `h` (living things
only: an item's `Health` is its own), `g` (tags), `c` (custom name). The engine leaves out any
whose health is 0: a dying mob stays in the world, selectable by its UUID, for a second.

**The raycast** (`everhear:ray/start`), from the eyes along the look, half a block a step, stops at
the first block not in `#everhear:ray_through` (air, light, water, grass, flowers, fire, vines… a
block is taken as a full cube) or the first entity whose hitbox it comes within 0.25 of (not
`#everhear:ray_ignore`: markers, displays, items, orbs, projectiles), at most `#rmax`/32 blocks (48;
McIo sets it from `VFG_ENGINE_RAY_BLOCKS`). `l = {k, d, p?, b?, e?, y?}`: kind (`block`, `entity`,
`none`), distance in 1/32 of a block, the point (a block's: just inside it, its face found to
1/32 by halving the last step four times; an entity's: the ray's point), the block's index, or
the entity's UUID and type index. A step costs about 1 µs of the server's time.

## The mirror

`function everhear:io/mirror {r:R}` writes `everhear:mirror {time, e: [...]}`: an entity compound
for every entity within R blocks of any player, each once (a tag for the pass), but not players (they
are the snapshot's, and reading a player saves all its data, its recipe book too) or markers. McIo's mirror thread runs it every 0.1 s, slower when a pass would take more than a
tenth of the server's time.

## Remove

`function everhear:remove {u:"<uuid>"}` removes an entity and leaves nothing: its custom name and
owner go first (a named or tamed mob's death is announced), it is moved 1024 blocks under the world,
where whatever it drops is gone on its first tick, and killed there. It returns 1, or 0 when there
is no such entity, it is already dying, or it is a player (never removed).

## Storage and scores, all of them

| Where | What |
|---|---|
| `everhear:meta` | `version`, `mc`, `types`, `blocks` |
| `everhear:events q` | the queue |
| `everhear:out` | `ev` (the page), `snap` (the snapshot): what the engine reads |
| `everhear:mirror` | the mirror's pass |
| `everhear:io` | `track` (the engine's), and the functions' scratch: `new`, `tmp`, `cur`, `ent`, `tq` |
| `everhear.q` | `#n`, `#len`, `#over`, `#max`, `#upto`, `#h` |
| `everhear.t` | scratch (`#y`, `#b`, `#rs`, `#rhit`, `#hits`, `#ago`) and `#rmax` |
| `everhear.use_c`, `everhear.use_w` | uses of the two bases (`minecraft.used:…`) |
| `everhear.deaths`, `everhear.dead` | `deathCount`; dead now |
| `everhear.dmg`, `everhear.abs` | `damage_taken`, `damage_absorbed` since the last hurt |
| tags | `everhear.hit` (hit this tick), `everhear.me`, `everhear.ray_self`, `everhear.m` (the pass's): all short-lived |
