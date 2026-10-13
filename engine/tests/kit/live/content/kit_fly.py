"""Something flying (the kit's live test): an orb, a block or a meteor from a player's eyes to a
place; it ends when it lands, saying where."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import fly


class KitFly(Scenario):
    class Config(ScenarioConfig):
        summary = "One flight, then it ends."
        duration_s = 20

    class Args(Args):
        at: Place = Field(None, description="where it lands")
        look: str = Field("orb", description="orb, block or meteor")
        speed: float = Field(10, description="blocks a second")
        player: str = Field("Steve", description="from whose eyes")

    def start(self, ctx):
        a = self.args
        fly(self, ctx, ctx.world.player(a.player).eye, a.at.pos, a.speed, a.look, 3, "minecraft:flame",
            "70% magma_block 30% obsidian", then="landed")

    def landed(self, ctx, at):
        self.result("landed", at=[round(c, 1) for c in at])
        self.end()
