"""Demon Lord: the game master that runs round after round of mob waves, boss fights and
minigames, escalating or easing off by how players fare, until stopped (the old
`demon_lord`/`actions/demon_lord.py`, `runs = "until_stopped"`).

Each round: a supply chest beside every player, an arm-up wait, then one of 13 events on a
16-round rotation (every 4th may instead be a boss, unless the rotation lands on a minigame; see
`Config.rotation`), harder or easier next round by how many died. The seven "wave" kinds (webs,
zombies, TNT, lava, skeletons, creepers, phantoms) and the four bosses (gravekeeper.py,
spider_queen.py, ravager_warlord.py, hollow_king.py) are spawned by this instance itself, so it can
poll `self.spawned` and recall a round stuck without a kill, same as the old script's
`wait_until_over`. The six minigames (sanctuary, blood_moon, lightning_storm, closing_ring,
defend_gregor, parkour) are each their own Scenario, started and owned (`self.start_scenario`).

# NEEDS (see each site below):
- a way for an owning scenario to end one it started, or to know when it ended on its own: each
  `run_<minigame>` instead schedules its own `round_cleared` with a timer shaped like that
  scenario's own worst-case length, and the arena kept round the party is started once, at a flat
  radius, rather than re-tightening every round as the old `arena(30 + 2 x level)` did
  (`ensure_arena`).
- reading a player's equipment and inventory, so a supply chest could skip what is already carried
  (`supply`, below): every chest is the same full set regardless.
- setting a player's game mode: no self.* verb does it (`gather`, below; `self.command` is the
  escape hatch, as `god_visits.py` already uses it for `give`/`clear`)."""

import random

from vfg_engine import Args, Death, Field, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import boss_bar, chest, mob_nbt, safe_spots, sky_spots, speak

ROTATION = [0, 1, 7, 11, 10, 12, 2, 9, 4, 11, 8, 5, 10, 12, 3, 6]
BOSS_CYCLE = ["gravekeeper", "spider_queen", "ravager_warlord", "hollow_king"]
MINIGAME_KINDS = (7, 8, 9, 10, 11, 12)          # sanctuary, lightning_storm, defend_gregor, parkour,
                                                # closing_ring, blood_moon (tick() leaves these to their own timer)
WAVE_LINES = ["My children of the web hunger.", "The dead rise to greet you.",
             "Fire from the heavens! Look up.", "Feel the warmth of my domain...",
             "My archers have you surrounded.", "Sssssomething is behind you.",
             "The phantoms of your sleepless nights return."]
WAVE_SUBS = ["webs", "the dead rise", "look up", "heat", "archers", "ssss", "phantoms"]


class DemonLord(Scenario):
    every = 3        # tick(): poll a wave or boss round's own self.spawned for a kill, or a recall

    class Config(ScenarioConfig):
        summary = ("A game master that runs round after round: a wave, a boss (every 4th, unless the "
                   "rotation lands on a minigame), or one of six minigames, harder or easier by how players "
                   "fared, until stopped.")
        duration_s = UNTIL_STOPPED
        rotation: list[int] = list(ROTATION)              # the kind per round, repeating every 16
        boss_every: int = Field(4, ge=1, le=16)             # every this many rounds may be a boss
        boss_cycle: list[str] = list(BOSS_CYCLE)
        boss_hp_base: float = Field(40, ge=1, le=10000)
        boss_hp_per_level_player: float = Field(12, ge=0, le=1000)
        break_s: float = Field(60, ge=0, le=600)             # the break between rounds (Live in the old action)
        arm_s: float = Field(20, ge=1, le=120)               # supply, then this long before the event
        recall_after_s: float = Field(30, ge=5, le=300)      # a wave or boss round, no kill this long: recalled
        gather_radius: float = Field(40, ge=5, le=200)
        arena_radius: float = Field(50, ge=10, le=500)       # see the module NEEDS: flat, not per-round
        level_up_clean: float = Field(1.5, ge=0, le=10)      # a round nobody died in
        level_up: float = Field(1.0, ge=0, le=10)            # under 0.7 deaths a player
        level_down: float = Field(0.5, ge=0, le=10)          # 0.7 deaths a player or more
        level_down_hard: float = Field(2.0, ge=0, le=10)     # 1.5 deaths a player or more
        supply_diamond_level: int = Field(6, ge=0, le=1000)
        supply_totem_level: int = Field(8, ge=0, le=1000)
        wave_gap_s: float = Field(0.15, ge=0.05, le=2)       # between one player's spawns and the next's

    class Args(Args):
        round: int = Field(1, ge=1, le=1000, description="the round it starts at; the rotation repeats every "
                           "16: rounds 12 and 16 are a boss, 4 and 8 a minigame (closing_ring, defend_gregor)")
        level: float = Field(0, ge=0, le=1000, description="the difficulty it starts at (0: the round's own); "
                             "it rises after a round nobody died in, falls after many deaths")

    class State(State):
        round: int = 1
        level: float = 0.0
        phase: str = "idle"          # idle, gathering, arming, running, scoring, break
        kind: int = -1
        is_boss: bool = False
        boss_id: str = ""
        boss_name: str = ""
        boss_uuid: str = ""
        arena_started: bool = False
        last_count: int = -1
        last_change: float = 0.0
        round_deaths: int = 0
        recalled: bool = False

    # ---- start, and the top of the loop ----------------------------------------------------

    def start(self, ctx):
        self.state.round = self.args.round
        self.state.level = self.args.level or float(self.args.round)
        self.listen(Death, "on_death")
        self.result("the Demon Lord begins at round %d, level %g" % (self.state.round, self.state.level),
                    round=self.state.round, level=self.state.level)
        self.after(0.1, "await_players")

    def on_death(self, ctx, ev):
        if self.state.phase in ("arming", "running"):
            self.state.round_deaths += 1

    def await_players(self, ctx):
        self.state.phase = "idle"
        online = players(ctx, "@a")
        if not online:
            self.after(30, "await_players")
            return
        if not any((p := ctx.world.player(n)) is not None and p.alive for n in online):
            self.after(3, "await_players")
            return
        self.ensure_arena(ctx)
        self.gather(ctx)
        self.state.phase = "gathering"
        self.after(0.5, "after_gather")

    def ensure_arena(self, ctx):
        if not self.state.arena_started:
            self.start_scenario("arena", players="@a", shape="radius", radius=self.config.arena_radius, message="")
            self.state.arena_started = True
            self.message("The arena seals around you...", where="actionbar")

    def gather(self, ctx):
        """Bring stragglers to the group, and everyone to survival (the old gather())."""
        self.command("gamemode survival @a")
        names = [n for n in players(ctx, "@a") if ctx.world.player(n) is not None]
        if len(names) < 2:
            return
        pos = {n: ctx.world.player(n).pos for n in names}
        r = self.config.gather_radius

        def near(n):
            return sum(1 for m in names if abs(pos[n][0] - pos[m][0]) + abs(pos[n][2] - pos[m][2]) < r)

        anchor = max(names, key=near)
        a, anchor_dim = pos[anchor], ctx.world.player(anchor).dim
        for n in names:
            if abs(pos[n][0] - a[0]) + abs(pos[n][2] - a[2]) > r:
                self.teleport(n, a, dim=anchor_dim)
                self.message("The Demon Lord drags you back to your allies.", to=[n])

    # ---- supply, the arm-up wait, and picking the event --------------------------------------

    def after_gather(self, ctx):
        self.state.round_deaths = 0
        names = [n for n in players(ctx, "@a") if (p := ctx.world.player(n)) is not None and p.alive]
        for n in names:
            p = ctx.world.player(n)
            if p.on_ground:
                self.spawnpoint(n, p.pos)          # respawn near the event (the old RESPAWN_HERE)
            self.supply(ctx, n, int(self.state.level))
        speak(self, ctx, "A supply chest has dropped beside you. Arm up - %d seconds." % int(self.config.arm_s))
        self.state.phase = "arming"
        self.after(self.config.arm_s, "pick_event")

    def supply(self, ctx, name, level):
        c = self.config
        p = ctx.world.player(name)
        if p is None:
            return
        mat = "diamond" if level >= c.supply_diamond_level else "iron"
        items = ["%s_helmet" % mat, "%s_chestplate" % mat, "%s_leggings" % mat, "%s_boots" % mat,
                "%s_sword" % mat, "shield", "bow", "%s_axe" % mat, "arrow 24", "cooked_beef 12",
                "golden_apple %d" % (1 + level // 4), "torch 16"]
        if level >= c.supply_totem_level:
            items.append("totem_of_undying 1")
        at = (p.pos[0] + 1, p.pos[1], p.pos[2])        # the old setblock's own "~1 ~ ~": always east
        chest(self, ctx, at, items, dim=p.dim)
        self.sound("minecraft:block.chest.open", at, volume=1, to=[name], dim=p.dim)
        self.particle("minecraft:totem_of_undying", (at[0], at[1] + 1, at[2]), count=60, delta=(0.3, 0.6, 0.3),
                     speed=0.2, to=[name], dim=p.dim)
        self.message("Supply chest right next to you (east, +X)!", to=[name], where="actionbar")

    def pick_event(self, ctx):
        names = [n for n in players(ctx, "@a") if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            self.after(0.1, "await_players")           # everyone died while arming: wait, then try again
            return
        self.state.phase = "running"
        kind = self.config.rotation[(self.state.round - 1) % len(self.config.rotation)]
        self.state.kind = kind
        self.state.is_boss = False
        self.state.boss_uuid = ""
        self.state.last_count, self.state.last_change = -1, ctx.now
        if kind == 11:
            self.run_closing_ring(ctx)
        elif kind == 12:
            self.run_blood_moon(ctx)
        elif kind == 10:
            self.run_parkour(ctx)
        elif kind == 9:
            self.run_defend_gregor(ctx)
        elif kind == 7:
            self.run_sanctuary(ctx)
        elif kind == 8:
            self.run_lightning_storm(ctx)
        else:
            self.command("weather %s" % ("rain" if kind in (0, 4, 6) else "clear"))
            if self.state.round % self.config.boss_every == 0:
                self.run_boss(ctx)
            else:
                self.run_wave(ctx, kind)

    # ---- the six minigames: started and owned, a shadow timer standing in for their own result -

    def run_sanctuary(self, ctx):
        level = max(1, int(self.state.level))
        self.start_scenario("sanctuary", players="@a", level=level)
        self.after(max(25, 45 - level) + 3, "round_cleared")

    def run_blood_moon(self, ctx):
        self.start_scenario("blood_moon", players="@a", level=max(1, int(self.state.level)))
        self.after(6 * 7 + 3, "round_cleared")

    def run_lightning_storm(self, ctx):
        level = max(1, int(self.state.level))
        self.start_scenario("lightning_storm", players="@a", level=level)
        self.after((10 + level) * 2 + 3, "round_cleared")

    def run_closing_ring(self, ctx):
        self.start_scenario("closing_ring", players="@a", level=max(1, int(self.state.level)))
        self.after(5 + 60 + 20 + 3, "round_cleared")

    def run_defend_gregor(self, ctx):
        level = max(1, int(self.state.level))
        self.start_scenario("defend_gregor", players="@a", level=level)
        self.after(12 + 60 + 5 * level + 5, "round_cleared")

    def run_parkour(self, ctx):
        level = max(1, int(self.state.level))
        self.start_scenario("parkour", players="@a", level=level)
        self.after(90 + 5 * level + 5, "round_cleared")

    # ---- the seven waves, spawned by this instance itself -------------------------------------

    def run_wave(self, ctx, kind):
        names = [n for n in players(ctx, "@a") if (p := ctx.world.player(n)) is not None and p.alive]
        speak(self, ctx, WAVE_LINES[kind] + " Round %d." % self.state.round, voice="", color="dark_red",
             where="title")
        speak(self, ctx, WAVE_SUBS[kind], voice="", color="gray", where="subtitle")
        self.after(5, "wave_spawn", kind, names)

    def wave_spawn(self, ctx, kind, names):
        for i, name in enumerate(names):
            self.after(i * self.config.wave_gap_s, "wave_one", name, kind)

    def wave_one(self, ctx, name, kind):
        p = ctx.world.player(name)
        if p is None or not p.alive:
            return
        dim = p.dim
        n = min(1 + max(1, int(self.state.level)) // 2, 6)
        if kind == 0:
            for _ in range(12):
                at = (p.pos[0] + random.choice([-1, 1]) * random.randint(3, 8), p.pos[1],
                     p.pos[2] + random.choice([-1, 1]) * random.randint(3, 8))
                self.blocks.fill(at, at, "minecraft:cobweb", mode="keep", dim=dim)
            self.command("give %s shears 1" % name)
            for spot in safe_spots(ctx, p, n, 4, dim=dim):
                self.spawn(random.choice(["minecraft:spider", "minecraft:cave_spider"]), spot, dim=dim)
        elif kind == 1:
            for spot in safe_spots(ctx, p, n * 2, 8, dim=dim):
                self.spawn(random.choice(["minecraft:zombie", "minecraft:zombie", "minecraft:husk"]), spot,
                          dim=dim, nbt=mob_nbt(baby=random.random() < 0.15))
        elif kind == 2:
            for _ in range(n):
                at = (p.pos[0] + random.choice([-1, 1]) * random.randint(4, 9), p.pos[1] + 20,
                     p.pos[2] + random.choice([-1, 1]) * random.randint(4, 9))
                self.spawn("minecraft:tnt", at, dim=dim, nbt="{fuse:70}")
        elif kind == 3:
            for _ in range(n):
                at = (p.pos[0] + random.choice([-1, 1]) * random.randint(3, 7), p.pos[1] + 6,
                     p.pos[2] + random.choice([-1, 1]) * random.randint(3, 7))
                self.blocks.fill(at, at, "minecraft:lava", mode="keep", dim=dim)
            self.command("give %s water_bucket 1" % name)
            for spot in sky_spots(ctx, p, max(1, n // 4), radius=5, height=3, dim=dim):
                self.spawn("minecraft:blaze", spot, dim=dim)
        elif kind == 4:
            for spot in safe_spots(ctx, p, n, 9, dim=dim):
                self.spawn(random.choice(["minecraft:skeleton", "minecraft:skeleton", "minecraft:stray"]), spot,
                          dim=dim)
            for spot in safe_spots(ctx, p, 1, 9, dim=dim):
                self.spawn("minecraft:witch", spot, dim=dim)
        elif kind == 5:
            powered = self.state.level > 7
            for spot in safe_spots(ctx, p, max(1, n // 2), 6, dim=dim):
                self.spawn("minecraft:creeper", spot, dim=dim,
                          nbt="{powered:1b}" if powered and random.random() < 0.3 else None)
            self.spawn("minecraft:tnt", (p.pos[0] + 6, p.pos[1] + 20, p.pos[2] + 6), dim=dim, nbt="{fuse:70}")
        elif kind == 6:
            for spot in sky_spots(ctx, p, max(2, n // 2), radius=6, height=12, dim=dim):
                self.spawn("minecraft:phantom", spot, dim=dim)

    # ---- the boss, spawned by this instance itself (its own boss bar: kit.boss_bar in tick) ---

    def run_boss(self, ctx):
        names = [n for n in players(ctx, "@a") if (p := ctx.world.player(n)) is not None and p.alive]
        idx = (self.state.round // self.config.boss_every - 1) % len(self.config.boss_cycle)
        boss_id = self.config.boss_cycle[idx]
        self.state.boss_id, self.state.boss_name = boss_id, boss_id.replace("_", " ").title()
        self.state.is_boss = True
        hp = self.config.boss_hp_base + self.config.boss_hp_per_level_player * self.state.level * max(1, len(names))
        speak(self, ctx, "Prepare yourselves. Supplies have fallen... you will need them. %s awakens in 15 "
                        "seconds." % self.state.boss_name)
        speak(self, ctx, self.state.boss_name.upper(), voice="", color="dark_red", where="title")
        speak(self, ctx, "awakens in 15 seconds", voice="", color="gray", where="subtitle")
        for name in names:
            p = ctx.world.player(name)
            self.sound("minecraft:entity.ender_dragon.growl", p.pos, volume=0.6, pitch=0.7, to=[name], dim=p.dim)
        for name in names:
            self.supply(ctx, name, int(self.state.level))
        self.after(15, "boss_rise", hp, names)

    def boss_rise(self, ctx, hp, names):
        alive = [n for n in names if (p := ctx.world.player(n)) is not None and p.alive]
        if not alive:
            self.round_cleared(ctx)
            return
        target = ctx.world.player(random.choice(alive))
        # NEEDS: an Actor's health is its Config's alone (Actors take no Args); a scenario cannot
        # override it when it spawns one, so this overrides it the way boss_fight.py's own
        # _override_nbt already does, relying on Minecraft keeping the later of two same-named
        # nbt keys in one summon (not proven live in this stage; see boss_fight.py's own NEEDS).
        nbt = '{Health:%gf,attributes:[{id:"minecraft:max_health",base:%g}]}' % (hp, hp)
        self.state.boss_uuid = self.spawn(self.state.boss_id, target.pos, dim=target.dim, nbt=nbt)
        self.command("weather thunder")
        self.command("time set midnight")
        level = max(1, int(self.state.level))
        for name in alive:
            p = ctx.world.player(name)
            for spot in safe_spots(ctx, p, level // 4 + 1, 8, dim=p.dim):
                self.spawn(random.choice(["minecraft:zombie", "minecraft:skeleton", "minecraft:spider"]), spot,
                          dim=p.dim)

    # ---- waiting for a wave or a boss to clear, and recalling a stuck one ----------------------

    def tick(self, ctx):
        if self.state.phase != "running" or self.state.kind in MINIGAME_KINDS:
            return            # a minigame round: its own shadow timer calls round_cleared instead
        if self.state.is_boss and self.state.boss_uuid:
            # NEEDS: no way to remove just this bar mid-life (only "when this ends", WRITING.md);
            # between fights it is left showing the last fight's own end (0 health, 0 alive).
            boss_bar(self, ctx, [self.state.boss_uuid], self.state.boss_name)
        n = len(self.spawned)
        if n > 0:
            if self.state.last_count < 0 or n < self.state.last_count:
                self.state.last_count, self.state.last_change = n, ctx.now
            elif ctx.now - self.state.last_change > self.config.recall_after_s:
                self.recall(ctx)
        elif self.state.last_count > 0:
            self.round_cleared(ctx)                            # it had mobs; now it has none: cleared
        elif ctx.now - self.state.last_change > self.config.recall_after_s:
            self.recall(ctx)                                    # never had any (no room, or all dead already)

    def recall(self, ctx):
        speak(self, ctx, "Cowards hide and my servants wander lost. I recall them... for now.")
        for uuid in list(self.spawned):
            self.remove(uuid)
        self.state.recalled = True
        self.round_cleared(ctx)

    def round_cleared(self, ctx):
        if self.state.phase != "running":
            return      # a minigame's shadow timer firing after an early finish or recall already moved on
        if self.state.is_boss and not self.state.recalled:
            speak(self, ctx, "%s has fallen... impressive, mortals. Enjoy your break." % self.state.boss_name)
        self.state.recalled = False
        self.state.phase = "scoring"
        self.score_round(ctx)

    # ---- scoring: the next round's level, then the break --------------------------------------

    def score_round(self, ctx):
        c = self.config
        names = players(ctx, "@a")
        d, n_players = self.state.round_deaths, max(1, len(names))
        ratio = d / n_players
        if ratio >= 1.5:
            self.state.level = max(1.0, self.state.level - c.level_down_hard)
            speak(self, ctx, "%d deaths... you are too fragile. I will hold back. A little." % d)
            for name in names:
                if (p := ctx.world.player(name)) is not None and p.alive:
                    self.supply(ctx, name, int(self.state.level))
        elif ratio >= 0.7:
            self.state.level = max(1.0, self.state.level - c.level_down)
        elif d == 0:
            self.state.level += c.level_up_clean
            speak(self, ctx, "Not a single death? Then I stop holding back.")
        else:
            self.state.level += c.level_up
        self.state.round += 1
        self.state.phase = "break"
        self.after(c.break_s, "await_players")
