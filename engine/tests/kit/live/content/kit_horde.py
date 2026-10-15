"""A small horde with a boss bar (the kit's live test): zombies at safe spots round a player, never
more than three alive, a bar on their summed health."""

from vfg_engine import UNTIL_STOPPED, Args, Field, Scenario, ScenarioConfig, State
from vfg_engine.kit import boss_bar, mob_nbt, summon_near


class KitHorde(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = "Three zombies round a player, with a bar."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        player: str = Field("Steve", description="round whom")

    class State(State):
        mobs: list = []

    def start(self, ctx):
        self.tick(ctx)

    def tick(self, ctx):
        self.state.mobs = self.state.mobs + summon_near(
            self, ctx, self.args.player, "minecraft:zombie", 3, 6, cap=3,
            nbt=mob_nbt(name="Grunt", health=30, no_ai=True))
        boss_bar(self, ctx, self.state.mobs, "The Horde", "purple")
