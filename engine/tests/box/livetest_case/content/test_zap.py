"""A tiny ability for the livetest runner's live test: the caster zaps every living thing within 2
blocks of where he looks, 4 damage; 10 s cooldown."""
from vfg_engine import Ability, AbilityConfig


class TestZap(Ability):
    class Config(AbilityConfig):
        summary = "Zaps every living thing within 2 blocks of where the caster looks: 4 damage."
        damage = 4
        duration_s = 1
        charge_s = 0
        cooldown_s = 10
        scale_per_point = 0

    def on_cast(self, ctx, ev):
        for e in ctx.world.entities_near(ctx.user.looking_at(), 2):
            if e.health is not None:
                self.damage(e.uuid, self.config.damage, "minecraft:magic")
        self.end()
