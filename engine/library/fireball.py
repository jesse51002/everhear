"""Fireball: a fiery ball flies from the caster's eyes to the first mob on their look (or, with
none there, to the point they are looking at), up to `reach` blocks away. There it hits that mob
with on-fire damage, then a TNT blast goes off and breaks blocks, sparing the caster (two seconds
of Resistance V, the kit's own `blast`). The Rocket Launcher item (its own file) casts it too.

(engine/examples/fireball.py's Config and power-scaled damage, with fireball_cast's aim, flight and
numbers (reach 80, width 1.2, speed 40, a magma block, blast power 3) and made/main's hot_fireball's
(reach 100, speed 30, a ball of flame, 40 on-fire damage to the mob it finds, blast power 6) merged
in: hot_fireball's numbers are the defaults below, since it is what last ran on the box. One
fireball, not two (fireball_cast's own verdict).)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, State
from vfg_engine.kit import blast, fly, hitscan


class Fireball(Ability):
    class Config(AbilityConfig):
        summary = "A fireball flies to the first mob on your look (or where you look), hits it, then blasts there."
        reach: float = Field(100, ge=4, le=300)       # how far it may fly before it lands (fireball_cast: 80)
        width: float = Field(1.2, ge=0.5, le=5)       # how close a mob must be to the line to be its target
        speed: float = Field(30, ge=5, le=100)        # blocks a second it flies (fireball_cast: 40)
        look: Literal["orb", "block"] = "orb"         # hot_fireball's ball of flame; fireball_cast: "block"
        particle: str = "minecraft:flame"             # both old scripts' own particle
        block: str = "minecraft:magma_block"          # the block it looks like when look is "block"
        size: float = Field(1.5, ge=0.3, le=6)        # how big it looks, in blocks (fireball_cast: 1)
        hit_damage_base: float = Field(40, ge=0, le=1000)   # on-fire damage to the mob it found, scale included
        explosion_power: float = Field(6, ge=1, le=12)      # the blast's TNT power (fireball_cast: 3)
        explosion_layout: Literal["one", "plus", "ring", "rings"] = "one"
        explosion_spread: float = Field(6, ge=1, le=20)     # between blasts, in blocks (fireball_cast: 4)
        scale_per_point = 0.05
        charge_s = 0
        cooldown_s = 5

        def hit_damage(self, ctx):
            return self.hit_damage_base * (1 + self.scale_per_point * ctx.user.power())

        def damage(self, ctx):                 # required: the total, scale included. The blast's own
            return self.hit_damage(ctx)         # damage is vanilla TNT's, never counted (KIT.md, blast breaks=True)

        def duration_s(self, ctx):
            return self.reach / self.speed + 1.0

    class State(State):
        hit: str = ""                           # the mob's key the ball is bound for; "" for none found

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.reach, c.width, "mobs")           # "mobs": never a player (hot_fireball)
        self.state.hit = shot.targets[0].key if shot.targets else ""
        at = shot.targets[0].middle if shot.targets else shot.end
        self.sound("minecraft:entity.blaze.shoot", at=ctx.user.eye)
        fly(self, ctx, shot.start, at, speed=c.speed, look=c.look, size=c.size, particle=c.particle,
            blocks=c.block, then="landed")

    def landed(self, ctx, at):
        c = self.config
        if self.state.hit:
            self.damage(self.state.hit, c.hit_damage(ctx), "minecraft:on_fire")
        blast(self, ctx, at, c.explosion_power, c.explosion_layout, c.explosion_spread, breaks=True)
        self.end()
