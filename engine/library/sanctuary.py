"""Sanctuary: one of the Demon Lord's rounds, started and owned by it (demon_lord.py). A golden
platform rises somewhere out from the party; reach it within the time limit and a golden apple
waits, a voice sparing you; anyone else is struck by lightning, weakened, and set on by a few
zombies.

# NEEDS: a spreadplayers-like scatter (a random point nudged onto nearby valid ground within a
# radius) is not exposed; the column's own ground height (`ctx.world.ground_y`) under the chosen
# point stands in, so the platform always lands exactly where aimed rather than a few blocks off."""

import math
import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import lightning, summon_near, speak


class Sanctuary(Scenario):
    class Config(ScenarioConfig):
        summary = ("A golden platform rises out from the party; reach it in time for a golden apple, or be "
                   "struck by lightning, weakened, and set on by a few zombies.")
        duration_s = 60
        away: float = Field(25, ge=5, le=200)       # plus level: how far out the sanctuary rises
        warn_at_s: float = Field(10, ge=1, le=60)   # the "10 seconds!" call, this long before the end
        min_time_s: float = Field(25, ge=5, le=120)
        max_time_s: float = Field(45, ge=5, le=120)
        reach: float = Field(4, ge=1, le=16)        # how close counts as "made it"
        slow_s: float = Field(15, ge=0, le=120)
        weak_s: float = Field(30, ge=0, le=120)

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how far out it rises (away + level) and how fierce "
                           "the punishment (2 + level // 3 zombies)")
        players: Players = Field("@a", description="who it judges: @a, @r, or names")

    class State(State):
        at: list = []
        dim: str = "minecraft:overworld"
        time_s: float = 0.0

    def start(self, ctx):
        a, c = self.args, self.config
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no sanctuary: %s" % ("nobody is online" if who in ("@a", "@r") else who + " is not online"),
                        players=[], spared=[], failed=True)
            self.end()
            return
        p0 = ctx.world.player(names[0])
        dim = p0.dim
        angle, dist = random.uniform(0, 2 * math.pi), c.away + a.level
        x, z = p0.pos[0] + dist * math.cos(angle), p0.pos[2] + dist * math.sin(angle)
        y = ctx.world.ground_y(x, z, dim=dim)
        if y is None:
            y = p0.pos[1]           # the column is not loaded; rise it at the party's own height instead
        at = (round(x), y, round(z))
        self.state.at, self.state.dim = list(at), dim
        self.blocks.fill((at[0] - 2, at[1] - 1, at[2] - 2), (at[0] + 2, at[1] - 1, at[2] + 2),
                        "minecraft:gold_block", dim=dim)
        self.blocks.setblock(at, "minecraft:beacon", dim=dim)
        self.blocks.fill((at[0] - 1, at[1] - 2, at[2] - 1), (at[0] + 1, at[1] - 2, at[2] + 1),
                        "minecraft:iron_block", dim=dim)
        t = max(c.min_time_s, c.max_time_s - a.level)
        self.state.time_s = t
        speak(self, ctx, "FIND THE SANCTUARY", voice="", color="gold", where="title")
        speak(self, ctx, "MINIGAME: Reach my golden sanctuary at X %d, Z %d within %d seconds. Follow the glow. "
                        "Those outside... burn." % (at[0], at[2], t))
        for name in names:
            q = ctx.world.player(name)
            self.sound("minecraft:entity.wither.spawn", q.pos, volume=0.6, pitch=0.7, to=[name], dim=q.dim)
        self.hold("weather", "thunder")
        self.after(max(0.0, t - c.warn_at_s), "warn")

    def warn(self, ctx):
        speak(self, ctx, "10 seconds!")
        self.after(min(self.config.warn_at_s, self.state.time_s), "judge")

    def judge(self, ctx):
        a, c = self.args, self.config
        spared, punished = [], []
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None or not p.alive:
                continue
            if p.dim == self.state.dim and math.dist(p.pos, self.state.at) <= c.reach:
                self.command("give %s golden_apple 1" % name)
                self.message("You are spared. Take this.", to=[name])
                spared.append(name)
            else:
                lightning(self, ctx, p.pos, count=1, dim=p.dim)
                self.potion("slowness", c.slow_s, 1, who=name)
                self.potion("weakness", c.weak_s, 0, who=name)
                summon_near(self, ctx, p, "minecraft:zombie", 2 + a.level // 3, 4, keep=True, dim=p.dim)
                punished.append(name)
        self.result("the sanctuary spared %s; %s burned" % (", ".join(spared) or "nobody", ", ".join(punished) or
                    "nobody"), players=a.players, spared=spared, punished=punished)
        self.end()
