"""Lava rain: drops of lava and flame fall out of the sky round each player, a puff every few
seconds, until stopped. A look only: no blocks, no fire, no harm (the old lava_rain script)."""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import sky_spots


class LavaRain(Scenario):
    every = 0.1                                  # tick(): check whether the next puff is due

    class Config(ScenarioConfig):
        summary = ("Drops of lava and flame fall out of the sky round each player, a puff every few seconds, "
                   "until stopped: a look only, no blocks, no fire, no harm.")
        duration_s = UNTIL_STOPPED
        height: float = Field(15, ge=1, le=300)          # how high over each player the puffs fall from
        band: float = Field(7, ge=0, le=100)             # give or take, round height
        open_only: bool = True                            # only spots in open air
        particles: list[str] = ["minecraft:falling_lava", "minecraft:falling_lava", "minecraft:dripping_lava",
                                "minecraft:lava", "minecraft:flame"]   # one picked at random for each puff

    class Args(Args):
        players: Players = Field("@a", description="who it rains on: @a, @r or names")
        every_s: float = Field(0.4, ge=0.1, le=10, description="seconds between showers of puffs; the first falls "
                                                               "at once")
        drops: int = Field(25, ge=1, le=200, description="how many drops in each puff, spread 4 blocks wide")
        count: int = Field(6, ge=1, le=100, description="how many puffs fall round each player each time")
        radius: float = Field(14, ge=0, le=128, description="how far out the puffs can fall, in blocks")

    class State(State):
        due: float = 0.0        # ctx.now the next shower is due

    def start(self, ctx):
        a = self.args
        self.result("lava rain on %s: %d puffs of %d drops round each, every %g s" %
                    (a.players, a.count, a.drops, a.every_s), players=a.players, every=a.every_s, count=a.count)
        self.rain(ctx)           # the first falls at once

    def tick(self, ctx):
        if ctx.now >= self.state.due:
            self.rain(ctx)

    def rain(self, ctx):
        a, c = self.args, self.config
        self.state.due = ctx.now + a.every_s
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None:
                continue
            for at in sky_spots(ctx, p, a.count, a.radius, c.height, c.band, c.open_only, dim=p.dim):
                self.particle(random.choice(c.particles), at, count=a.drops, delta=(4.0, 1.0, 4.0), dim=p.dim)
