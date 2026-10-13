"""Frost servants: every `every_s`, calls frost servants round the Ice Queen's body, never more
than a cap alive at once. Passive: granted to her at spawn, alongside ice_blast."""

from vfg_engine import Ability, AbilityConfig, Field, State, UNTIL_STOPPED
from vfg_engine.kit import summon_near


class FrostServants(Ability):
    class Config(AbilityConfig):
        summary = "Every every_s, calls frost servants round her body, never over a cap alive at once."
        every_s: float = Field(8, ge=1, le=120)   # the old boss_every
        count: int = Field(3, ge=1, le=20)        # the old summon_count
        cap: int = Field(6, ge=1, le=60)          # the old summon_cap
        spread: float = Field(4, ge=1, le=32)     # the old summon_spread
        damage = 0
        duration_s = UNTIL_STOPPED                # passive: runs for as long as it is granted
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    every = 1

    class State(State):
        due: float = 0.0

    def start(self, ctx):
        self.state.due = ctx.now + self.config.every_s

    def tick(self, ctx):
        if ctx.now < self.state.due:
            return
        c = self.config
        self.state.due = ctx.now + c.every_s
        if summon_near(self, ctx, ctx.user, "frost_servant", c.count, c.spread, cap=c.cap):
            self.sound("minecraft:entity.evoker.prepare_summon", volume=3, pitch=1.4)
