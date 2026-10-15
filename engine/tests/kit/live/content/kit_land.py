"""Landing safely (the kit's live test)."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import land_safely


class KitLand(Scenario):
    class Config(ScenarioConfig):
        summary = "Moves a player onto safe ground at a place, then ends."
        duration_s = 10

    class Args(Args):
        player: str = Field("Steve", description="who")
        at: Place = Field(None, description="where")

    def start(self, ctx):
        spot = land_safely(self, ctx, self.args.player, self.args.at)
        self.result("landed" if spot else "no safe ground", spot=list(spot) if spot else None)
        self.end()
