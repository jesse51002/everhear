"""Horror: ash that only each player sees, drifting round them, and every min_s to max_s seconds of
play a scare at random: a sound behind them, a shadow (Herobrine) watching, a fake creeper that
hisses and vanishes, a whisper, or the dark; and, from the old herobrine, a door creaking, a fake
"Herobrine joined the game", torches near them put out, a "behind you" sign, or far lightning. Until
stopped.

It is herobrine and horror_mode merged into one: the same director, with herobrine's five extra
events added to the built-in five, and horror_mode's ash kept as haze (off: herobrine's own look).
A stop takes away whatever is still standing; the signs and the missing torches stay, as both old
scripts left them."""

import math
import random
from typing import Literal

from vfg_engine import Args, Field, Player, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import ahead, compass, director, lightning, safe_spots, sign, yaw_to

EventName = Literal["sound", "shadow", "mob", "message", "dark", "door", "fake_join", "torches", "sign", "lightning"]

EVENTS = {"sound": "scare_sound", "shadow": "scare_shadow", "mob": "scare_mob", "message": "scare_message",
         "dark": "scare_dark", "door": "scare_door", "fake_join": "scare_fake_join", "torches": "scare_torches",
         "sign": "scare_sign", "lightning": "scare_lightning"}


class Horror(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = ("Ash drifting round each player (haze) and a scare every min_s to max_s seconds: a sound "
                   "behind them, a shadow watching, a fake creeper, a whisper, the dark; also herobrine's door, "
                   "fake join message, missing torches, a sign, or far lightning.")
        duration_s = UNTIL_STOPPED
        ash_every_s: float = Field(2, ge=0.5, le=10)             # how often the haze puffs
        ash_count: int = Field(60, ge=0, le=200)                 # ash particles each puff
        white_ash_count: int = Field(20, ge=0, le=200)           # white ash particles each puff
        ash_spread: tuple[float, float, float] = (6.0, 3.0, 6.0)   # how far it scatters: right, up, forward
        sounds: list[str] = [
            "minecraft:entity.zombie.step", "minecraft:block.wooden_door.open", "minecraft:ambient.cave",
            "minecraft:entity.enderman.stare", "minecraft:entity.warden.heartbeat", "minecraft:entity.ghast.ambient"]
        sound_dist: float = Field(3, ge=1, le=32)                # how far behind "sound" plays
        shadow_tries: tuple[float, ...] = (12.0, 8.0, 5.0)       # how far behind the shadow tries, nearest room wins
        shadow_min_s: float = Field(3, ge=0.5, le=30)            # how long it lingers, at least
        shadow_max_s: float = Field(6, ge=0.5, le=30)            # at most
        mob_dist: float = Field(2, ge=1, le=16)                  # how far behind the fake creeper stands
        mob_hiss_s: float = Field(1.5, ge=0.2, le=10)            # how long it hisses before it vanishes
        dark_s: float = Field(8, ge=1, le=60)                    # how long the darkness lasts
        door_dist: float = Field(3, ge=1, le=16)                 # how far behind the door creaks
        door_close_s: float = Field(2, ge=0.5, le=10)            # how long before it shuts again
        fake_join_s: float = Field(6, ge=1, le=30)               # how long before "Herobrine left the game"
        torches_radius: int = Field(8, ge=1, le=32)              # how far round them torches go out
        torches_up: int = Field(4, ge=0, le=16)
        torches_down: int = Field(3, ge=0, le=16)
        sign_dist: float = Field(2, ge=1, le=16)                 # how far behind the sign appears
        lightning_dist: float = Field(18, ge=1, le=64)           # how far behind the lightning lands

    class Args(Args):
        players: Players = Field("@a", description="who it scares: @a, @r, or names separated by commas")
        events: list[EventName] = Field(
            ["sound", "shadow", "mob", "message", "dark"], min_length=1, max_length=10,
            description="what a scare does, one at random: sound (behind them), shadow (a figure watching), mob "
                       "(a fake creeper), message (a whisper), dark; also herobrine's door (creaks open and "
                       "shut), fake_join (\"Herobrine joined the game\"), torches (go out near them), sign "
                       "(\"behind you\"), lightning (far behind)")
        weights: list[float] = Field([4, 1, 1, 2, 1], min_length=1, max_length=10,
                                     description="how often each event in events comes, the same order (a "
                                                "missing weight is 1, 0 never)")
        min_s: float = Field(25, ge=1, le=3600, description="the shortest wait before a scare, in seconds a "
                                                           "player is online")
        max_s: float = Field(60, ge=1, le=3600, description="the longest wait before a scare, in seconds (under "
                                                           "min_s: min_s)")
        each: bool = Field(True, description="each player gets scares on their own clock; off: one clock for "
                                            "all, each scare to one player at random")
        messages: list[str] = Field(
            ["i see you", "turn around", "you are not alone", "why are you still here", "behind you"],
            max_length=20, description="what the message and whisper events say, one at random")
        no_dark: list[Player] = Field([], max_length=20, description="players the dark never falls on; the "
                                                                     "rest of the horror stays")
        haze: bool = Field(True, description="ash drifts round each player, seen only by them, every "
                                             "ash_every_s seconds")

    class State(State):
        next_ash: float = 0.0

    def tick(self, ctx):
        if self.args.haze:
            self.haze(ctx)
        for event, p in director(self, ctx, self.args.events, self.args.weights, self.args.min_s, self.args.max_s,
                                 self.args.each, self.args.players):
            method = EVENTS.get(event)
            if method:
                getattr(self, method)(ctx, p)

    # ---- the ash ----------------------------------------------------------------------------

    def haze(self, ctx):
        if ctx.now < self.state.next_ash:
            return
        self.state.next_ash = ctx.now + self.config.ash_every_s
        for p in players(ctx, self.args.players):
            pl = ctx.world.player(p)
            if pl is None:
                continue
            at = (pl.pos[0], pl.pos[1] + 1, pl.pos[2])
            spread = self.config.ash_spread
            if self.config.ash_count:
                self.particle("minecraft:ash", at, count=self.config.ash_count, delta=spread, to=[p], dim=pl.dim)
            if self.config.white_ash_count:
                self.particle("minecraft:white_ash", at, count=self.config.white_ash_count, delta=spread, to=[p],
                              dim=pl.dim)

    # ---- the scares ---------------------------------------------------------------------------

    def scare_sound(self, ctx, p):
        pl = ctx.world.player(p)
        at = ahead(ctx, p, -self.config.sound_dist)
        if pl is None or at is None:
            return
        self.sound(random.choice(self.config.sounds), at, volume=1.0, pitch=random.uniform(0.5, 0.8), to=[p],
                  dim=pl.dim)

    def scare_shadow(self, ctx, p):
        pl = ctx.world.player(p)
        if pl is None:
            return
        at = None
        for k in self.config.shadow_tries:
            spot = ahead(ctx, p, -k)
            if spot is not None and safe_spots(ctx, spot, 1, 0, dim=pl.dim):
                at = spot
                break
        if at is None:
            return
        yaw, _ = yaw_to(at, pl.pos)
        uuid = self.spawn("shadow", at, dim=pl.dim, nbt="{Rotation:[%.1ff,0f]}" % yaw)
        self.sound("minecraft:entity.warden.heartbeat", at, volume=1.0, pitch=0.6, to=[p], dim=pl.dim)
        self.after(random.uniform(self.config.shadow_min_s, self.config.shadow_max_s), "end_shadow", uuid)

    def end_shadow(self, ctx, uuid):
        if uuid in self.spawned:
            self.remove(uuid)

    def scare_mob(self, ctx, p):
        pl = ctx.world.player(p)
        at = ahead(ctx, p, -self.config.mob_dist) if pl is not None else None
        if at is None or not safe_spots(ctx, at, 1, 0, dim=pl.dim):
            return
        yaw, _ = yaw_to(at, pl.pos)
        uuid = self.spawn("fake_creeper", at, dim=pl.dim, nbt="{Rotation:[%.1ff,0f]}" % yaw)
        self.sound("minecraft:entity.creeper.primed", at, volume=1.0, pitch=1.0, to=[p], dim=pl.dim)
        self.after(self.config.mob_hiss_s, "pop_creeper", uuid)

    def pop_creeper(self, ctx, uuid):
        if uuid in self.spawned:
            e = ctx.world.get(uuid)
            if e is not None:
                self.particle("minecraft:poof", e.pos, count=20, delta=(0.3, 0.6, 0.3), speed=0.02, dim=e.dim)
            self.remove(uuid)

    def scare_message(self, ctx, p):
        if self.args.messages:
            self.message("§4%s" % random.choice(self.args.messages), to=[p])

    def scare_dark(self, ctx, p):
        if p in self.args.no_dark:
            return
        pl = ctx.world.player(p)
        # NEEDS: clearing one potion, to lift the dark at once from a player added to no_dark mid-way;
        # the engine never clears a potion (WRITING.md), so it simply runs its course (dark_s).
        self.potion("darkness", self.config.dark_s, 0, who=p, particles=False)
        at = ahead(ctx, p, -2)
        if at is not None and pl is not None:
            self.sound("minecraft:entity.warden.heartbeat", at, volume=1.0, pitch=0.5, to=[p], dim=pl.dim)

    def scare_door(self, ctx, p):
        pl = ctx.world.player(p)
        at = ahead(ctx, p, -self.config.door_dist)
        if pl is None or at is None:
            return
        self.sound("minecraft:block.wooden_door.open", at, volume=1.0, pitch=0.7, to=[p], dim=pl.dim)
        self.after(self.config.door_close_s, "door_close", p, list(at), pl.dim)

    def door_close(self, ctx, p, at, dim):
        self.sound("minecraft:block.wooden_door.close", tuple(at), volume=1.0, pitch=0.7, to=[p], dim=dim)

    def scare_fake_join(self, ctx, p):
        self.message("§eHerobrine joined the game", to=())
        self.after(self.config.fake_join_s, "fake_leave")

    def fake_leave(self, ctx):
        self.message("§eHerobrine left the game", to=())

    def scare_torches(self, ctx, p):
        pl = ctx.world.player(p)
        if pl is None:
            return
        x, y, z = (math.floor(c) for c in pl.pos)
        r, up, down = self.config.torches_radius, self.config.torches_up, self.config.torches_down
        self.blocks.replace(((x - r, y - down, z - r), (x + r, y + up, z + r)), "torch > air; wall_torch > air",
                            dim=pl.dim)

    def scare_sign(self, ctx, p):
        pl = ctx.world.player(p)
        at = ahead(ctx, p, -self.config.sign_dist)
        if pl is None or at is None or not safe_spots(ctx, at, 1, 0, dim=pl.dim):
            return
        # a standing sign, as the old prank placed it (no wall behind it to hang on); its text
        # faces the way the player was facing, so it reads right once they turn round to look at it
        sign(self, ctx, at, ["", "behind you", "", ""], facing=compass(pl.yaw), wall=False, dim=pl.dim)

    def scare_lightning(self, ctx, p):
        pl = ctx.world.player(p)
        at = ahead(ctx, p, -self.config.lightning_dist)
        if pl is not None and at is not None:
            lightning(self, ctx, at, count=1, spread=0.0, keep_away=0.0, dim=pl.dim)
