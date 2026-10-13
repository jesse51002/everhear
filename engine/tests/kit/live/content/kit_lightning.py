"""Lightning round a place (the kit's live test), kept off a spared player."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import lightning


class KitLightning(Scenario):
    class Config(ScenarioConfig):
        summary = "Five bolts in a ring, then it ends."
        duration_s = 10

    class Args(Args):
        at: Place = Field(None, description="where")
        spare: str = Field("", description="a player no bolt lands near")

    def start(self, ctx):
        struck = lightning(self, ctx, self.args.at, count=5, spread=3, layout="ring",
                           spare=[self.args.spare] if self.args.spare else ())
        self.result("%d bolts" % len(struck), bolts=len(struck))
        self.end()
