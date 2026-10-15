"""A tiny one-shot scenario for the livetest runner's live test: a 3x3x3 stone cube at a place,
its result said, then it ends: a build that ends inside its start's wait."""
from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig


class TestPillar(Scenario):
    class Config(ScenarioConfig):
        summary = "Builds a 3x3x3 stone cube at a place, then ends."
        duration_s = 10

    class Args(Args):
        at: Place = Field("", description="where the cube's bottom middle is")

    def start(self, ctx):
        x, y, z = (int(v // 1) for v in self.args.at.pos)
        self.blocks.fill((x - 1, y, z - 1), (x + 1, y + 2, z + 1), "stone")

    def done(self, ctx):
        self.result("built a stone cube", blocks=27)
        self.end()
