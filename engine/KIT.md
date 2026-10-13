# The kit: helpers content shares

`vfg_engine.kit` is what content classes share, so each thing is done once: finding who a power
reaches, blowing things up, building, throwing, spawning, showing, and reading what players do
(DESIGN2 §5). Import what you use:

```python
from vfg_engine.kit import hitscan, hit, beam_line
```

**The rules every helper keeps.** A helper that acts takes the instance and `ctx` first,
`helper(self, ctx, ...)`; one that only reads takes `ctx` (`safe_spots(ctx, ...)`). It does its
work only through the content API everyone has (`self.*`, `ctx.*`, `self.state`). It never
blocks or waits: what takes time goes on timers and on the engine's block queue. What it
remembers is in `self.state.kit` (every State has it), so it is saved and survives a reload and a
restart. Each call fits well inside a content call's 50 ms: `tests/kit/test_limits.py` estimates the
heaviest on the box (their Python, plus each live read at what B measured on 26.3), all under 40 ms;
the heaviest, a 1000-block beam through 200 mobs, about 25 ms. Every helper group is also proven on a
real 26.3 server (`tests/kit/test_live.py`, the cases in `tests/kit/live/`).

**Calling back.** A helper that takes time calls your class's own method by name when it is done
(`then="landed"` calls `landed(ctx, at)`), or the methods it names (`held_use` calls `on_press`,
`on_hold`, `on_charged`, `on_release`, those you have). Its timers are the kit's own
(`kit_step`, every class has it): they go when your instance, or in an ability your cast's run,
ends.

Not in the kit, because the core does them: reading a Players arg is `players(ctx, arg)` and block
mixes and replace rules are read by `self.blocks` (`from vfg_engine import players`; WRITING.md);
`self.teleport`, `self.ignite`, `self.blocks.fill/replace/sphere`. Content that is not shared
(the `frozen` mark, an aura) is a library class, not a helper.

---

## Finding who a power reaches

**The spare rules**, the same for every finder: never the user, never a name or uuid in `spare`,
never a tamed pet (an entity with an owner), a villager, a trader, a golem, an allay, an armor
stand, a mannequin, a dropped item or an experience orb, never anything not alive (a display, a
boat), never what this instance spawned, never a dead player or one in creative or spectator.

**`what`** picks among the rest: `"everything"` (mobs and players: PvP is on, so abilities hurt
players by default), `"mobs"`, `"hostile"` (monsters only, never an animal), `"players"`, or
`"projectiles"` (arrows, tridents, fireballs still in flight, never the user's own).

Entities come from the mirror (in memory) when the reach is within `ctx.world.mirror_radius` (32)
of a player, else from a live read, `ctx.world.entities` (a long line is read in slices of 32 blocks).

A finder returns `Target`s, nearest first: `.key` (a player's name or a uuid: what `self.damage`,
`self.motion` and the helpers name it by), `.type`, `.dim`, `.pos` (its feet), `.t` (how far), `.on_head`,
`.health`, `.is_player`, `.middle`.

### `targets(self, ctx, at, radius, what="everything", spare=(), most=0, dim=None) -> list[Target]`
Everything a hit may touch within `radius` of `at` (a position, a player, an entity, a Spot); `t`
is the distance to the nearest part of the body. `most`: only the nearest that many.
```python
for t in targets(self, ctx, ctx.user, 16, "mobs"):
    self.potion("slowness", 5, 9, who=t.key)
```

### `near_line(self, ctx, a, b, width=1.5, what="everything", spare=(), most=0, dim=None) -> list[Target]`
Everything whose body comes within `width` of the line from `a` to `b`, ordered along it (`t`
from `a`), with `on_head` set when the line passes within half a block of the head.
```python
for t in near_line(self, ctx, ctx.user.eye, shot_end, 2.5):
    self.apply(t.key, "frozen", 10)
```

### `hitscan(self, ctx, length=100.0, width=1.5, what="everything", through_walls=False, spare=(), most=0) -> Shot`
A shot from the user's eyes along their look: up to `length` blocks, stopping at the first solid
block unless `through_walls` (a live `ctx.world.ray`), never at a being. A `Shot` has `.start`,
`.end` (where it stopped), `.direction`, `.length`, `.block` (the block it stopped at, if one) and
`.targets` (near_line's, along it).
```python
shot = hitscan(self, ctx, self.config.range, 1.8, through_walls=True)
hit(self, ctx, shot.targets, self.config.damage(ctx), "magic")
beam_line(self, ctx, shot.start, shot.end, "minecraft:end_rod")
```

### `hit(self, ctx, targets, amount, damage_type="minecraft:generic", kill=False, most=0, head=1.0) -> list`
Hurts each target once (a target listed twice is hurt once), nearest first, through
`self.damage`, so it is the user's hit and counted against the cast's damage. `kill`: kill
outright (`minecraft:generic_kill`). `most`: only the first that many. `head`: times the damage on
a headshot. Returns those it hurt.
```python
shot = hitscan(self, ctx, 400, 0.8, "mobs")
if hit(self, ctx, shot.targets, 150, "player_attack", most=1, head=2):
    self.message("HEADSHOT", where="actionbar")
```

---

## Blasts

### `blast(self, ctx, at, power=4.0, layout="one", spread=4.0, breaks=False, keep=0.0, spare=(), dim=None) -> list`
Blasts of `power` (TNT is 4) laid out by `layout`: `"one"`, `"plus"` (four more `spread` out),
`"ring"` (eight round it), `"rings"` (six at `spread`, ten at twice that). No blast goes off within
`keep` blocks of a player. `breaks=True` is vanilla TNT (it breaks blocks; its damage is
Minecraft's, not counted); the user, `spare`, pets, villagers and the rest of the spared get two
seconds of Resistance V first. `breaks=False` breaks nothing: a flash, a boom, damage that falls
off as a vanilla explosion's (14·power + 1 at the middle, nothing past twice the power) through
`self.damage`, and a push away from the middle. Returns the points that went off.
```python
def landed(self, ctx, at):
    blast(self, ctx, at, self.config.power, "plus", 3, breaks=True)
    self.end()
```

`blast_points(at, layout, spread)` gives a pattern's points; `blast_damage(power, distance)` what
one target takes, for a class's `damage(ctx)` formula.

### `lightning(self, ctx, at, count=1, spread=3.0, layout="scatter", keep_away=4.0, spare=(), dim=None) -> list`
`count` vanilla bolts: the first at `at`, the rest within `spread`, at random (`"scatter"`) or
evenly round it (`"ring"`). None lands within `keep_away` of the user or a spared player. A bolt's
own few points and its fire are Minecraft's; deal what the power promises with `hit`.
```python
at = ctx.user.looking_at()
for p in lightning(self, ctx, at, count=4, spread=3):
    hit(self, ctx, targets(self, ctx, p, 4), 40, "lightning_bolt")
```

### `crater(self, ctx, at, radius, leave="air", tunnel=0, path=(), load=True, dim=None) -> None`
Carves a ball of `radius` (1024 at most) through the block queue, spread over ticks, never the
bedrock floor nor a protected box, then leaves `"air"`, `"lava"` (a pool in its lowest third) or
`"fire"` (on its floor). `path`: points along a beam, each with a cube `tunnel` blocks round it
carved first. `load`: forceload what it reaches first (up to 256 chunks). Your `done(ctx)` is
called when the queue has placed it all.
```python
path = [[s + d * k for s, d in zip(shot.start, shot.direction)] for k in range(0, int(shot.length), 2)]
crater(self, ctx, shot.end, 6, tunnel=1, path=path)
def done(self, ctx): self.end()                 # the queue has placed it all
```

---

## Blocks

### `build_shape(self, ctx, at, kind="box", width=9, height=5, depth=9, blocks="minecraft:stone_bricks", facing="look", offset=(0, 0, 0), hollow=False, load=True, dim=None) -> Built`
A `"box"`, `"cylinder"`, `"dome"`, `"pyramid"`, `"staircase"`, `"staircase_down"` or `"wall"`,
the middle of its base at `at` moved by `offset` [right, up, forward]: `width` across, `depth`
forward, `height` up. `blocks` a block or a mix (`"70% stone_bricks 30% cobblestone"`); air
carves. `hollow`: air inside a one-block shell. `facing`: `"look"` (the way the player or a Spot
faces; south for a bare position) or a compass way. A box or a wall is a handful of fills at any
size; a cylinder, dome or pyramid is at most 64 across and 2000 fills (a bigger one is refused in
words). Returns `Built`: `.a`, `.b` (the box covered), `.facing`, `.dim`, `.middle`.
```python
spot = self.args.at or ctx.world.player(name)
walls = build_shape(self, ctx, spot, "box", 23, 7, 23, self.args.walls, offset=(0, 0, 6), hollow=True)
sign(self, ctx, ahead(ctx, spot, 4, up=3), ["%s's castle" % name], facing=BACK[walls.facing])
```
`compass(yaw)` gives a yaw's way; `BACK["south"]` is `"north"`.

### `sign(self, ctx, at, lines, facing="south", wood="oak", wall=True, dim=None)`
A sign at `at` showing up to four lines (each cut at 40 characters), its text facing `facing`;
`wall=False` for a standing sign.
```python
sign(self, ctx, (x, y + 4, z), ["Sniper Nest", name], facing="north")
```

### `chest(self, ctx, at, items, facing="north", dim=None)`
A chest holding `items` in order (27 at most): `"diamond 4"` (a vanilla item and how many) or
`"everhear:railgun"` (an EverHear item as the engine gives it, through `self.item_stack`).
```python
chest(self, ctx, ahead(ctx, p, 2), ["everhear:hellfire_longbow", "arrow 64", "everhear:stormcaller"])
```

---

## Movement

### `push(self, ctx, who, way="away", strength=6.0, frm=None, landing="safe") -> int`
Throws `who` (a Target, a name, a uuid, or a list of them) about `strength` blocks: `"up"` that
high, `"away"` from `frm`, `"toward"` it (never past it), or `"forward"` the way each faces. `frm`
is a point or a being (the user when None). A mob is given its velocity (`self.motion`); a player,
whose velocity 26.3 cannot set, is thrown by wind charges spawned at their feet already falling,
all bursting together (the old launch preset's plan: a ring for up, one spot behind for along;
measured live: "up 8" goes 8 up, "forward 10" 10 forward, both landing unhurt); a player in the air
or with no open ground round their feet is not thrown. A projectile is turned without a
hop. `landing`: `"safe"` (Resistance V for the flight), `"float"` (Slow Falling down), `"hurt"`.
```python
round_me = targets(self, ctx, ctx.user, self.config.radius, "everything")
push(self, ctx, round_me, "away", self.config.strength)
```

### `land_safely(self, ctx, who, at, exact=True, cushion_s=5.0, dim=None)`
Moves someone onto safe ground: on `at` itself (or one block up) when `exact` and its ground is
solid and harmless (no magma, cactus, powder snow, campfire, berries, dripstone) with room, else
the first safe spot in rings 8, 16, 32 and 64 blocks out. They get `cushion_s` of Resistance V and
Slow Falling first. Returns where they were sent, or None (not moved).
```python
spot = land_safely(self, ctx, p, self.args.to)
if spot is None:
    self.message("No safe ground there.", to=[p])
```

---

## Spawning

### `safe_spots(ctx, near, count=1, spread=8.0, dim=None) -> list`
Up to `count` spots within `spread` of `near` with solid ground under them and two blocks of air
(round a player, never on top of them; spread 0: `near` itself, a block up or down, or nothing).
Two `ctx.world.probe` reads however many it tries.
```python
behind = ahead(ctx, p, -12)
if safe_spots(ctx, behind, 1, 0):
    self.spawn("shadow", behind)
```

### `sky_spots(ctx, near, count=6, radius=14.0, height=20.0, band=0.0, open_only=True, dim=None) -> list`
`count` points up in the sky round `near` (or round each of a list): within `radius`, `height`
above, give or take `band`; with `open_only` only those in open air.
```python
for at in sky_spots(ctx, [ctx.world.player(n) for n in players(ctx, self.args.players)], 10, 7, 25):
    self.spawn("minecraft:tnt", at, nbt="{fuse:80s}")
```

### `summon_near(self, ctx, near, mob, count=1, spread=8.0, nbt=None, keep=False, cap=0, dim=None) -> list`
Spawns `count` of `mob` (an Actor's id, or `"minecraft:zombie"`) at safe spots round `near`, with
`nbt`. `keep`: left in the world when this ends by itself. `cap`: never more than that many of
this instance's `mob` alive. Returns the uuids.
```python
self.state.mobs += summon_near(self, ctx, p, "minecraft:zombie", 5, 8, cap=30,
                               nbt=mob_nbt(name="Grunt", gear="iron"))
```

### `mob_nbt(name=None, health=None, scale=None, gear=None, weapon=None, glowing=False, baby=False, no_ai=False, silent=False, invulnerable=False, extra=None) -> str`
The SNBT of a spawn: a shown name, its health, its size, a full suit of `gear` (`"iron"`,
`"diamond"`...) and a `weapon`, never dropped, the flags, and `extra` entries. It always persists.
```python
nbt = mob_nbt(name="Horde King", health=800, scale=3, gear="netherite", weapon="netherite_sword")
self.state.bodies = [self.spawn("minecraft:zombie", spot, nbt=nbt)]
```

### `boss_bar(self, ctx, bodies, name, color="red", players="@a", bar="boss", most=None) -> tuple[float, int]`
Keeps a boss bar on the summed health of `bodies` (uuids); call it from `tick()`. Full at `most`,
else at the first summed health it saw. The engine removes the bar when this ends. Returns
(summed health, bodies alive).
```python
def tick(self, ctx):
    if boss_bar(self, ctx, self.state.bodies, "Three-Headed Dragon")[1] == 0:
        self.result("the dragon is dead"); self.end()
```

### `world_mob_rule(self, ctx, mob, nbt=None, undo_nbt=None, modifiers=(), radius=48.0) -> list`
Changes each of the world's own `mob`s within `radius` of a player, once: `nbt` merged on, and
`modifiers` [(attribute, amount, operation)] as this instance's (removed when it ends). What the
engine spawned is left alone. Call it from `tick()`; `undo_world_mob_rule(self, ctx)` in `stop()`
puts `undo_nbt` back.
```python
def tick(self, ctx):
    world_mob_rule(self, ctx, "creeper", nbt="{Fuse:60s}", undo_nbt="{Fuse:30s}")
def stop(self, ctx, reason): undo_world_mob_rule(self, ctx)
```

---

## Show

### `beam_line(self, ctx, a, b, particle="minecraft:end_rod", every=1.0, spread=0.0, count=1, speed=0.0, force=True, to=None, dim=None) -> int`
Particles along a line, one every `every` blocks (200 at most: a longer line spaces them out),
seen from far off (`force`), by `to` only when given.
```python
beam_line(self, ctx, shot.start, shot.end, "minecraft:dust{color:[0.6,0.1,0.9],scale:2.0}", 1.5, 0.3, 4)
```

### `charge_frames(self, ctx, k, style="sphere", size=6.0, particle="minecraft:dust{color:[0.3,0.7,1.0],scale:2.5}", orbs=(), who=None) -> tuple`
One frame of a charge-up, `k` of the way charged: `"sphere"` (light growing overhead),
`"orbs"` (two orbs meeting ahead; `orbs` their two particles) or `"aura"`; the sound rises with
it. Call it from `on_charge`. Returns where the charge is.
```python
def on_charge(self, ctx, ev):
    charge_frames(self, ctx, ev.progress, "orbs", 5, orbs=("minecraft:dust{color:[0.2,0.4,1.0],scale:2}",
                                                          "minecraft:dust{color:[1.0,0.2,0.2],scale:2}"))
```

### `melody(self, ctx, tune, at=None, to=None, key="theme", loop=False) -> float`
A tune note by note: a name from `TUNES` (`galick_gun`, `hollow_purple`, `ice_beam`,
`spirit_bomb`, `spirit_bomb_throw`, `super_saiyan`, `boss`) or notes `[(seconds, instrument or
sound, note 0-24, volume)]`. `at`: where (the user; a player's name follows them). `to`: None,
heard round `at`; `"everyone"`; or a Players arg or names, each where they stand. One tune per
`key`: a newer one, or `melody(self, ctx, None)`, cuts it off, a record too. `loop`: again each
time it ends. Returns how long it lasts.
```python
self.state.music = melody(self, ctx, "boss", to="everyone", loop=True)
def stop(self, ctx, reason): melody(self, ctx, None)
```

### `countdown(self, ctx, seconds, text="Detonation", to="@a", then=None) -> None`
Counts down for `to`: chat at the start and at 10 and 5 minutes, 2, 1, 30 and 10 seconds left,
a title and a tick each of the last ten seconds, then your `then(ctx)`. A stop calls it off.
```python
countdown(self, ctx, self.args.countdown_s, "☢ Nuke", then="detonate")
def detonate(self, ctx):
    crater(self, ctx, self.args.at, self.args.radius)
```

### `speak(self, ctx, text, voice="Demon Lord", color="dark_red", to=None, where="chat") -> None`
A line in a voice and a colour: `[Demon Lord] You will not leave.` A voice already in brackets
(`"<The Radiant One>"`) is kept; `""` for none. `to`: a Players arg or names (everyone when None).
```python
speak(self, ctx, "Round %d. Survive." % self.state.round)
speak(self, ctx, "Kneel.", "<The Radiant One>", "gold", to=chosen, where="title")
```

### `fly(self, ctx, start, end, speed=30.0, look="orb", size=1.0, particle="minecraft:end_rod", blocks="minecraft:magma_block", then=None, dim=None) -> float`
Something seen flying straight from `start` to `end`, one step a tick: `"orb"` (a ball of
`particle`), `"block"` (a block display trailing it) or `"meteor"` (a burning cluster of `blocks`,
a block or a mix, `size` across). A show: it touches nothing; on arrival it calls your
`then(ctx, end)`, on time even when a tick comes late. Returns how long it flies.
```python
shot = hitscan(self, ctx, 80, 1.2)
end = shot.targets[0].middle if shot.targets else shot.end
fly(self, ctx, shot.start, end, 40, "block", 1, "minecraft:flame", then="landed")
```

---

## Input

### `held_use(self, ctx, ev, hold_s=0.0, repeat_s=0.0, fill=True) -> bool`
A held right-click, from the Use events you feed it: calls your `on_press(ctx)` when it goes
down, `on_hold(ctx)` every `repeat_s` while held, `on_charged(ctx)` once held `hold_s` (with
`fill`, the action bar fills meanwhile), `on_release(ctx, held_s)` when let go (no Use for
0.4 s). Returns True for the Use that pressed it.
```python
def on_use(self, ctx, ev):
    held_use(self, ctx, ev, repeat_s=0.1)
def on_hold(self, ctx): ctx.user.cast("gatling_gun")
```

### `sneak_gesture(self, ctx, ev, mode="three_taps", within=1.5, every=0.1) -> bool`
Crouching, from the Sneak and Unsneak events you feed it (listen to both): True on each crouch
(`"tap"`), on three within `within` seconds (`"three_taps"`), or on a crouch that then calls your
`on_sneaking(ctx)` every `every` s until they stand (`"held"`).
```python
def on_sneak(self, ctx, ev):
    if sneak_gesture(self, ctx, ev, "three_taps"):
        ctx.user.cast("super_saiyan")
```

### `steps(self, ctx, who, every=2.0) -> Step | None`
Call from `tick()`: a `Step` (`.pos`, `.dim`, `.fell`) when the player stands on the ground
`every` blocks from the last step, a block up or down, in another dimension, the first time, or
on landing from a fall (`fell`, when 3 or more). None in the air.
```python
s = steps(self, ctx, name, self.args.every)
if s:
    self.blocks.replace(((s.pos[0] - 10, s.pos[1] - 11, s.pos[2] - 10), (s.pos[0] + 10, s.pos[1] + 11, s.pos[2] + 10)), self.args.rules)
```

### `chunks_entered(self, ctx, who, around=0, below=32, above=40, dims=None) -> list`
Call from `tick()`: the boxes of each chunk the player is in (or within `around` chunks) that
this instance saw nobody in before, from `below` under their feet to `above` over them.
```python
for box in chunks_entered(self, ctx, name, around=1):
    self.blocks.replace(box, self.args.rules)
```

### `director(self, ctx, events, weights=None, min_s=25.0, max_s=60.0, each=True, players="@a") -> list`
A random-event clock: call it from `tick()`. Every `min_s` to `max_s` seconds a player is online,
one of `events` is picked by `weights` for them (`each`: everyone on their own clock; else one
clock, its event to one of them). Returns `[(event, player's name)]` due now; you do them.
```python
for event, p in director(self, ctx, self.args.events, self.args.weights, 25, 60):
    getattr(self, "scare_" + event)(ctx, p)
```

### Places round someone
`ahead(ctx, who, blocks, up=0.0, right=0.0)`: the point `blocks` ahead of where they stand along
the way they face (behind with a negative `blocks`). `yaw_to(frm, to)`: the (yaw, pitch) looking
from one point to another, to turn a figure to face someone. `direction(yaw, pitch)`: the unit
vector they point along.
```python
spot = ahead(ctx, p, 6)
yaw, _ = yaw_to(spot, ctx.world.player(p).pos)
self.teleport(self.spawn("god", spot), spot, yaw=yaw)
```

---

## Merged, dropped, and where the old kit went

- **launch** is `push` (one throw: a mob by its velocity, a player by wind charges).
- **meteor_fall** (the meteor's falling cluster) is `fly(look="meteor")`, the same flight as the
  fireball's.
- **room** (two blocks of air to stand in) is `safe_spots(ctx, at, 1, 0)`; **behind** is
  `ahead(ctx, p, -k)`.
- **hear** (a sound only one player hears) is the core's `self.sound(..., to=[p])`.
- **players**, **block_mix** and **replace_rules** are the core's (`players`, and the mix and
  rule readers behind `self.blocks` and the BlockMix and ReplaceRules args); `fill`, `replace`,
  `sphere` and forceloading are `self.blocks`.
- **HeldUse** is the function `held_use`, its memory in `self.state.kit`.
- **lightning's `every`** (bolts spaced in time) is dropped: no user needs it.
- Dropped with the old system: the old kit's RCON plumbing (`mc`, `loop`, `values`, `aim.mark`
  and tags), the beam clash (Jesse's call), and the particle shapes nothing in DESIGN2 asks for.
