"""A crater (the kit's live test), carved by the block queue; it ends when the queue is done."""

from vfg_engine import UNTIL_STOPPED, Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import crater


class KitCrater(Scenario):
    class Config(ScenarioConfig):
        summary = "Carves a crater, ends when it is carved."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        at: Place = Field(None, description="its middle")
        radius: int = Field(3, description="how big")
        leave: str = Field("air", description="air, lava or fire")

    def start(self, ctx):
        crater(self, ctx, self.args.at, self.args.radius, self.args.leave)

    def done(self, ctx):
        self.result("carved")
        self.end()
