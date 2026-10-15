"""Meteor strike: a meteor made of blocks falls out of the sky onto the first block the caster
looks at, up to range blocks away, landing in a breaking blast. The Meteor Staff item fires it on
a right-click; granted and bound, it is cast by voice. (The old meteor_staff script's fall and
blast, folded into one ability; its fall and blast are the same kit helpers, `fly` and `blast`, as
the library's `meteor` scenario elsewhere, DESIGN2 "meteor_staff's fall shares meteor's helper".)"""

import math
import random
from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import blast, fly, speak


class MeteorStrike(Ability):
    class Config(AbilityConfig):
        summary = "A meteor falls where you look, up to range blocks away, then explodes."
        range: float = Field(120, ge=4, le=400)         # how far away the block you look at may be
        size: float = Field(4, ge=1, le=24)             # the falling cluster, blocks across
        blocks: str = "minecraft:magma_block"           # a block or a mix it is made of
        height: float = Field(60, ge=10, le=300)        # how high above the target it starts
        slant: float = Field(15, ge=0, le=128)          # how far off to a random side it starts
        fall_s: float = Field(2.5, ge=0.5, le=30)       # how long the fall takes
        power: float = Field(4, ge=0, le=16)            # the blast's power (TNT is 4)
        layout: Literal["one", "plus", "ring", "rings"] = "plus"
        spread: float = Field(3, ge=1, le=20)
        breaks: bool = True                              # real TNT: it breaks blocks
        damage = 0                 # declared: it is a breaking blast, vanilla's damage, uncounted here
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 5

        def duration_s(self, ctx):
            return self.fall_s + 2     # a safety net: landed() ends the run as soon as it lands

    def on_cast(self, ctx, ev):
        c = self.config
        user = ctx.user
        look = ctx.world.ray(user.eye, user.facing, c.range)
        if look.kind != "block":
            speak(self, ctx, "No target in range", voice="", color="gray", to=[user.name], where="actionbar")
            self.end()
            return
        bx, by, bz = look.block
        land = (bx + 0.5, by + 1.0, bz + 0.5)
        a = random.uniform(0, 2 * math.pi)
        start = (land[0] - c.slant * math.cos(a), land[1] + c.height, land[2] - c.slant * math.sin(a))
        dist = math.dist(start, land)
        speed = dist / c.fall_s if c.fall_s > 0 else dist
        fly(self, ctx, start, land, speed, "meteor", c.size, blocks=c.blocks, then="landed")

    def landed(self, ctx, at):
        c = self.config
        blast(self, ctx, at, c.power, c.layout, c.spread, breaks=c.breaks)
        self.end()
