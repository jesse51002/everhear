"""Meteor: one or more giant flaming meteors fall slantwise out of the sky onto the ground near
each player and blast a crater where each lands. A meteor shower (the old meteor_shower script,
and meteor_staff's fall) is this with count 8, radius 40."""

import math
import random
from typing import Literal

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import blast, fly, sky_spots

OVERWORLD = "minecraft:overworld"


class Meteor(Scenario):
    class Config(ScenarioConfig):
        summary = ("count meteors fall out of the sky round each player and blast a crater where each lands "
                   "(breaks false: a flash that only hurts and throws back, nothing broken). A meteor shower: "
                   "count 8, radius 40.")
        duration_s = 120                                  # the most a fall and its blasts may take
        height: float = Field(100, ge=1, le=300)          # how high each starts above the ground it falls on
        slant: float = Field(25, ge=0, le=128)            # how far off to a random side it starts
        fall_s: float = Field(4.8, ge=0.5, le=30)         # how long the fall takes
        blocks: str = "magma_block blackstone obsidian"   # what the falling cluster is made of
        layout: Literal["one", "plus", "ring", "rings"] = "rings"     # the blast's layout where it lands
        spread: float = Field(4, ge=0, le=64)             # the blast layout's spread
        keep: float = Field(7.5, ge=0, le=64)             # no blast lands within this of a player
        scan_height: float = Field(20, ge=1, le=300)      # how high to look, round each player, for open sky
        scan_band: float = 0                               # give or take, round scan_height
        gap_s: float = Field(0.1, ge=0.1, le=2)           # between queuing one meteor's fall and the next's

    class Args(Args):
        players: Players = Field("@a", description="who it falls on: @a, @r or names; count meteors fall round "
                                                   "each, all at once")
        count: int = Field(1, ge=1, le=100, description="how many meteors fall round each player (a meteor "
                                                        "shower: 8)")
        radius: float = Field(7, ge=0, le=128, description="how far out they can land, in blocks (a meteor "
                                                           "shower: 40)")
        size: float = Field(10, ge=1, le=24, description="how wide each meteor's falling cluster is")
        power: float = Field(4, ge=0, le=16, description="the blast's power where it lands (TNT is 4; 0: no "
                                                         "blast at all)")
        breaks: bool = Field(True, description="the blast breaks blocks (a crater); false: it only hurts and "
                                               "throws back, nothing broken")

    class State(State):
        names: list = []           # who it fell for, for the result
        left: int = 0              # meteors still falling or waiting to
        landed: list = []          # [x, y, z] of each one that landed
        flights: dict = {}         # "x,y,z" (a landing spot) -> its dimension, while it falls

    def start(self, ctx):
        a, c = self.args, self.config
        names = players(ctx, a.players)
        feet = [p for p in (ctx.world.player(n) for n in names) if p is not None]
        if not feet:
            self.result("no meteor fell: %s" % ("nobody is online" if a.players in ("@a", "@r")
                                                else a.players + " is not online"), players=[], meteors=0)
            self.end()
            return
        self.state.names = [p.name for p in feet]
        spots = [(at[0], at[2], p.dim) for p in feet
                for at in sky_spots(ctx, p, a.count, a.radius, c.scan_height, c.scan_band, True, dim=p.dim)]
        if not spots:
            self.result("no meteor fell: no open sky over %s" % ", ".join(self.state.names),
                        players=self.state.names, meteors=0)
            self.end()
            return
        self.state.left = len(spots)
        for i, (x, z, dim) in enumerate(spots):
            self.after(i * c.gap_s, "drop", x, z, dim)

    def drop(self, ctx, x, z, dim):
        """Where one meteor falls: the ground under (x, z), the fall starting height above it,
        slanted a random way to one side."""
        c, a = self.config, self.args
        ground = ctx.world.ground_y(x, z, dim=dim)
        if ground is None:                      # no ground there (not loaded, or over the void): it skips
            self.one_done(ctx)
            return
        land = (float(x), float(ground), float(z))
        angle = random.uniform(0, 2 * math.pi)
        start = (land[0] - c.slant * math.cos(angle), land[1] + c.height, land[2] - c.slant * math.sin(angle))
        key = "%.2f,%.2f,%.2f" % land
        self.state.flights = {**self.state.flights, key: dim}
        speed = max(1.0, math.dist(start, land) / max(0.1, c.fall_s))
        fly(self, ctx, start, land, speed=speed, look="meteor", size=a.size, blocks=c.blocks, then="landed", dim=dim)

    def landed(self, ctx, end):
        c, a = self.config, self.args
        key = "%.2f,%.2f,%.2f" % tuple(end)
        flights = dict(self.state.flights)
        dim = flights.pop(key, OVERWORLD)
        self.state.flights = flights
        blast(self, ctx, end, a.power, c.layout, c.spread, a.breaks, c.keep, dim=dim)
        self.state.landed = self.state.landed + [[int(math.floor(v)) for v in end]]
        self.one_done(ctx)

    def one_done(self, ctx):
        self.state.left -= 1
        if self.state.left <= 0:
            self.finish(ctx)

    def finish(self, ctx):
        names, landed = self.state.names, self.state.landed
        if not landed:
            self.result("no meteor fell: no open sky over %s" % ", ".join(names), players=names, meteors=0)
            self.end()
            return
        at = ["%d %d %d" % tuple(p) for p in landed]
        extra = ": " + "; ".join(at) if len(at) <= 3 else ""
        self.result("%d meteor%s fell on %s, size %g%s" %
                    (len(at), "" if len(at) == 1 else "s", ", ".join(names), self.args.size, extra),
                    players=names, meteors=len(at), at=at)
        self.end()
