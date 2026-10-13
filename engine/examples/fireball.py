"""Fireball, from the game engine spec ("Abilities and their config"), completed so that what it
deals is what its damage formula adds up: the point the caster looks at burns everyone within
`radius` for `burn_s` seconds (burn_per_s_base, scaled, each second), then explodes
(explosion_base, scaled). One target in the radius the whole time takes exactly damage(ctx)."""

from vfg_engine import Ability, AbilityConfig, Field, State


class Fireball(Ability):
    class Config(AbilityConfig):
        summary = "Burns for burn_s seconds, then explodes in a radius; both scale with power."
        burn_per_s_base: float = Field(1, ge=0, le=5)
        burn_s: float = Field(3, ge=0, le=10)
        explosion_base: float = Field(8, ge=0, le=30)
        radius: float = Field(3, ge=1, le=8)
        scale_per_point: float = 0.05
        charge_s = 0
        cooldown_s = 5

        def scale(self, ctx):
            return 1 + self.scale_per_point * ctx.user.power()

        def burn_per_s(self, ctx):
            return self.burn_per_s_base * self.scale(ctx)

        def explosion(self, ctx):
            return self.explosion_base * self.scale(ctx)

        def damage(self, ctx):                 # required: the total, scale included
            return self.burn_per_s(ctx) * self.burn_s + self.explosion(ctx)

        def duration_s(self, ctx):
            return self.burn_s + 1

    class State(State):
        at: list[float] = []                   # where it landed
        burn_left: float = 0.0                 # seconds of burning still to deal

    def on_cast(self, ctx, ev):
        self.state.at = list(ctx.user.looking_at())
        self.state.burn_left = self.config.burn_s
        self.sound("minecraft:entity.blaze.shoot", at=ctx.user.eye)
        if self.config.burn_s > 0:
            self.every(1, "burn")
        else:
            self.boom(ctx)

    def burn(self, ctx):
        part = min(1.0, self.state.burn_left)
        for target in self.targets(ctx):
            self.damage(target, self.config.burn_per_s(ctx) * part, "minecraft:on_fire")
        self.particle("minecraft:flame", self.state.at, count=20, delta=(1, 0.5, 1), speed=0.02)
        self.state.burn_left -= part
        if self.state.burn_left <= 1e-9:
            self.boom(ctx)

    def boom(self, ctx):
        self.explode(at=self.state.at, radius=self.config.radius,
                     damage=self.config.explosion(ctx))
        self.end()                             # dealt in full; the run need not wait for its end

    def targets(self, ctx):
        """Everyone within the radius of where it landed: players (the caster too, as an
        explosion's self-damage is on) and living entities."""
        at, r = self.state.at, self.config.radius
        return ctx.world.players_near(at, r) + [e for e in ctx.world.entities_near(at, r) if e.health is not None]
