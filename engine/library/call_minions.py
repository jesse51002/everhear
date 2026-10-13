"""Call minions: every `every_s`, summons more of a minion mob round the boss's own body, never
more than a cap alive at once. Passive: granted to the Horde King at spawn (ActorConfig.abilities),
it calls on its own while its body lives, the way its enrage buff runs on its own too, with no cast
needed. (A triggered ability's on_cast only has that one cast's own fresh State - WRITING.md - so a
cap remembered across many calls needs a passive ability's persistent one instead.)"""

from vfg_engine import Ability, AbilityConfig, EntityType, Field, State, UNTIL_STOPPED
from vfg_engine.kit import summon_near


class CallMinions(Ability):
    class Config(AbilityConfig):
        summary = "Every every_s, calls more minions round its body, never over a cap alive at once."
        minion: EntityType = "minecraft:zombie"
        every_s: float = Field(10, ge=1, le=120)   # the old boss_every
        count: int = Field(3, ge=1, le=20)         # how many each call brings
        cap: int = Field(9, ge=1, le=60)           # never more of them alive at once (the old 3x boss_minions)
        spread: float = Field(6, ge=1, le=32)
        damage = 0
        duration_s = UNTIL_STOPPED                 # passive: runs for as long as it is granted
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
        if summon_near(self, ctx, ctx.user, c.minion, c.count, c.spread, cap=c.cap):
            self.sound("minecraft:entity.evoker.prepare_summon", volume=3)
