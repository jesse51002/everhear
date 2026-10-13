"""Nuke: a countdown in chat and titles (or at once), then a flash and a blast heard by everyone;
every mob and every survival player within radius dies, and the ground is carved away, where
chunks are loaded. A stop before zero calls it off.

made/main's destroy_everything (it was made `--from nuke`, and only added a fire phase) is this
with fire_s above 0: fire fills the air round the middle first, with a hole kept clear at it;
`players` is then spared from the kill, given fire resistance, and kept safe on a pad through the
blast, put back on it after (destroy everything: countdown_s 0, radius 30, fire_s 5, leave fire)."""

import math
from typing import Literal

from vfg_engine import Args, Field, Place, Players, Scenario, ScenarioConfig, Spot, State, UNTIL_STOPPED, players
from vfg_engine.kit import countdown, crater, hit, targets

OVERWORLD = "minecraft:overworld"


class Nuke(Scenario):
    class Config(ScenarioConfig):
        summary = ("A countdown, then a flash and a blast heard by everyone; every mob and survival player within "
                   "radius dies and the ground is carved away. fire_s above 0 (destroy everything): fire fills the "
                   "air first, and players is spared and kept safe on a pad through it.")
        duration_s = UNTIL_STOPPED                 # it ends once the crater is carved (or at once, stopped first)
        pad_block = "obsidian"                      # what the safe pad under the protected players is made of
        protect_s: float = Field(30, ge=1, le=120)  # how long their resistance and slow falling last
        fire_tick_s: float = Field(1.0, ge=0.2, le=5)      # how often the fire band is refreshed
        fire_half_max: float = Field(60, ge=1, le=256)     # the fire band's half-width never grows past this
        hole_half: float = Field(5, ge=0, le=32)    # a square this far each way at the middle is kept clear of fire
        hole_up: float = Field(3, ge=0, le=16)      # and this far up and down
        blind_s: float = Field(2, ge=0, le=20)       # how long those caught in the blast are blinded
        blind_far: float = Field(100, ge=0, le=500)  # blindness reaches this far past radius
        sounds: list[str] = ["minecraft:entity.generic.explode", "minecraft:entity.warden.sonic_boom",
                             "minecraft:entity.lightning_bolt.thunder"]   # heard by everyone, wherever they stand
        flash: str = "█" * 30                   # the title shown at the blast

    class Args(Args):
        players: Players = Field("@a", description="whose spot it goes off at (when at is empty), who the "
                                                   "countdown is said to, and, above 0 fire_s, who is spared from "
                                                   "the kill and kept safe: @a, @r or names")
        at: Place = Field("", description="where it goes off instead: a saved place, x y z, or a player (empty: "
                                          "where the first of players stands)")
        countdown_s: int = Field(600, ge=0, le=3600, description="seconds counted down in chat and titles first "
                                                                "(0: no countdown, it goes off at once); a stop "
                                                                "before zero calls it off (destroy everything: 0)")
        countdown_text: str = Field("☢ Nuke", max_length=40, description='shown as it counts down, and what '
                                                                              'calling it off says (destroy '
                                                                              'everything: "Destroy everything")')
        radius: int = Field(1000, ge=0, le=1000, description="how big the blast is, a ball this many blocks round "
                                                            "the middle: what dies and the hole, carved only where "
                                                            "chunks are loaded (0: no hole; destroy everything: 30)")
        kill: bool = Field(True, description="kill every mob and every survival player within radius as it goes "
                                             "off (players is spared instead when fire_s is above 0)")
        fire_s: float = Field(0, ge=0, le=60, description="seconds of fire filling the air round the middle "
                                                         "first, a hole kept clear at it; above 0 also spares "
                                                         "players from the kill, gives them fire resistance and a "
                                                         "pad under them, kept safe through the blast and put back "
                                                         "on it after (destroy everything: 5)")
        leave: Literal["air", "lava", "fire"] = Field("air", description="what the crater's floor is left as "
                                                                        "(destroy everything: fire)")

    class State(State):
        middle: list = []           # [x, y, z], fixed when it starts
        dim: str = OVERWORLD
        where: str = ""              # "x y z [in dim]", for messages
        protected: list = []         # players spared and kept safe (fire_s > 0 only)
        fire_start: float = 0.0
        fire_timer: str = ""
        killed: int = 0
        # done() is also called for the fire phase's own fills emptying the queue (a fire_tick, a
        # hole, each a block job of this instance too); "fire" has it ignore that. "crater" is the
        # one its own blast_phase queues; fire_s > 0 then needs one more, "pad", before it ends.
        stage: str = "fire"

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if a.at is not None:
            spot = a.at
        else:
            p = next((ctx.world.player(n) for n in names), None)
            spot = Spot(dim=p.dim, pos=tuple(p.pos), yaw=p.yaw) if p is not None else None
        if spot is None:
            self.result("no nuke: %s" % ("nobody is online" if a.players in ("@a", "@r")
                                        else a.players + " is not online"), players=names, radius=a.radius)
            self.end()
            return
        self.state.middle = list(spot.pos)
        self.state.dim = spot.dim
        where = "%d %d %d" % tuple(int(math.floor(c)) for c in spot.pos)
        if spot.dim != OVERWORLD:
            where += " in " + spot.dim.split(":")[-1]
        self.state.where = where
        self.state.protected = names if a.fire_s > 0 else []
        if a.countdown_s:
            self.result("nuke counting down at %s: radius %d, goes off in %d s" % (where, a.radius, a.countdown_s),
                        at=where, radius=a.radius, seconds=a.countdown_s)
            countdown(self, ctx, a.countdown_s, a.countdown_text, to=a.players, then="detonate")
        else:
            self.result("nuke at %s: radius %d, going off now" % (where, a.radius), at=where, radius=a.radius)
            self.detonate(ctx)

    def detonate(self, ctx):
        a = self.args
        if a.fire_s > 0:
            for name in self.state.protected:
                self.potion("minecraft:fire_resistance", a.fire_s + 5, who=name, particles=False)
            self.state.fire_start = ctx.now
            self.state.fire_timer = self.every(self.config.fire_tick_s, "fire_tick")
            self.fire_tick(ctx)                     # the first burst, at once
        else:
            self.blast_phase(ctx)

    def fire_tick(self, ctx):
        """The fire buildup round the middle, refreshed every fire_tick_s, a hole kept clear at
        it, with its look; once fire_s has passed, it goes off."""
        a, c = self.args, self.config
        mx, my, mz = self.state.middle
        cx, cy, cz = (int(math.floor(v)) for v in (mx, my, mz))
        half = min(int(a.radius), int(c.fire_half_max))
        self.blocks.fill((cx - half, cy - 1, cz - half), (cx + half, cy + 1, cz + half), "minecraft:fire",
                         replace="minecraft:air", dim=self.state.dim)
        hh, hu = int(c.hole_half), int(c.hole_up)
        self.blocks.fill((cx - hh, cy - hu, cz - hh), (cx + hh, cy + hu, cz + hh), "minecraft:air",
                         dim=self.state.dim)
        self.particle("minecraft:flame", (mx, my, mz), count=80, delta=(a.radius / 2.0, 1.0, a.radius / 2.0),
                     speed=0.02, force=True, dim=self.state.dim)
        self.particle("minecraft:large_smoke", (mx, my + 2.0, mz), count=30,
                     delta=(a.radius / 2.0, 2.0, a.radius / 2.0), speed=0.01, force=True, dim=self.state.dim)
        self.particle("minecraft:soul_fire_flame", (mx, my, mz), count=60,
                     delta=(a.radius * 0.8, 2.0, a.radius * 0.8), speed=0.03, force=True, dim=self.state.dim)
        if ctx.now - self.state.fire_start >= a.fire_s:
            self.cancel(self.state.fire_timer)
            self.protect(ctx)
            self.blast_phase(ctx)

    def protect(self, ctx):
        """The pad under the protected players, and their resistance and slow falling, so the
        blast cannot hurt them."""
        self.pad(ctx)
        for name in self.state.protected:
            self.potion("minecraft:slow_falling", self.config.protect_s, who=name, particles=False)
            self.potion("minecraft:resistance", self.config.protect_s, amplifier=4, who=name, particles=False)

    def pad(self, ctx):
        c = self.config
        cx, cy, cz = (int(math.floor(v)) for v in self.state.middle)
        self.blocks.fill((cx - 2, cy - 1, cz - 2), (cx + 2, cy - 1, cz + 2), c.pad_block, dim=self.state.dim)

    def blast_phase(self, ctx):
        """The flash, the sounds, the kill, then the crater."""
        a, c = self.args, self.config
        self.state.stage = "crater"
        middle, dim = tuple(self.state.middle), self.state.dim
        # NEEDS: a title's colour and fade timing (message() takes plain text only); the old white flash,
        # fading in 0 ticks, held 40, fading 40, is just this text shown and gone
        self.message(c.flash, to=(), where="title")
        self.message(c.flash, to=(), where="subtitle")
        far = a.radius + c.blind_far
        for p in ctx.world.players_near(middle, far, dim=dim):
            self.potion("minecraft:blindness", c.blind_s, who=p.name, particles=False)
        for name in players(ctx, "@a"):
            p = ctx.world.player(name)
            if p is None:
                continue
            for sound in c.sounds:
                self.sound(sound, at=p.pos, volume=1000, pitch=0.6, to=[name], dim=p.dim)
        self.particle("minecraft:explosion_emitter", middle, count=60, delta=(min(30.0, a.radius),) * 3,
                     force=True, dim=dim)
        if a.kill:
            got = targets(self, ctx, middle, a.radius, "everything", spare=self.state.protected, dim=dim)
            hit(self, ctx, got, 0, kill=True)
            self.state.killed = len(got)
        crater(self, ctx, middle, a.radius, leave=a.leave, load=True, dim=dim)
        if self.blocks.done:            # radius 0 (or nothing to carve): the queue never called done
            self.done(ctx)

    def done(self, ctx):                # the queue emptied: the fire phase's own fills too, ignored
        if not self.blocks.done or self.state.stage == "fire":
            return
        a = self.args
        if self.state.stage == "crater" and a.fire_s > 0:
            self.state.stage = "pad"
            self.pad(ctx)                # put it back: the crater may have carved it away
            return                       # done() comes again once it is placed; queued jobs would
                                         # else be dropped by an end() before they land
        if a.fire_s > 0:
            cx, cy, cz = (int(math.floor(v)) for v in self.state.middle)
            for name in self.state.protected:
                if ctx.world.player(name) is not None:
                    self.teleport(name, (cx + 0.5, float(cy), cz + 0.5), dim=self.state.dim)
        self.result("nuke went off at %s: radius %d, %d killed" % (self.state.where, a.radius, self.state.killed),
                    at=self.state.where, radius=a.radius, killed=self.state.killed)
        self.end()
