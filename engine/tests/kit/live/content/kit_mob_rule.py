"""A rule on the world's creepers (the kit's live test): a long fuse while it runs, put back at the end."""

from vfg_engine import UNTIL_STOPPED, Scenario, ScenarioConfig
from vfg_engine.kit import undo_world_mob_rule, world_mob_rule


class KitMobRule(Scenario):
    every = 0.5

    class Config(ScenarioConfig):
        summary = "Creepers get a 3 s fuse and are slowed while it runs."
        duration_s = UNTIL_STOPPED

    def tick(self, ctx):
        world_mob_rule(self, ctx, "creeper", nbt="{Fuse:60s}", undo_nbt="{Fuse:30s}",
                       modifiers=[("movement_speed", -0.5, "add_multiplied_total")])

    def stop(self, ctx, reason):
        undo_world_mob_rule(self, ctx)
