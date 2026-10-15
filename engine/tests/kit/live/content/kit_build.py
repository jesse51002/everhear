"""A shape built by the queue (the kit's live test); it ends when it is built, saying where."""

from vfg_engine import UNTIL_STOPPED, Args, Field, Place, Scenario, ScenarioConfig, State
from vfg_engine.kit import build_shape


class KitBuild(Scenario):
    class Config(ScenarioConfig):
        summary = "Builds one shape, ends when it is built."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        at: Place = Field(None, description="the middle of its base")
        kind: str = Field("box", description="the shape")
        width: int = Field(7, description="across")
        height: int = Field(5, description="up")
        depth: int = Field(7, description="forward")
        blocks: str = Field("stone_bricks", description="a block or a mix")
        hollow: bool = Field(False, description="air inside")

    class State(State):
        box: list = []

    def start(self, ctx):
        a = self.args
        built = build_shape(self, ctx, a.at, a.kind, a.width, a.height, a.depth, a.blocks, facing="north",
                            hollow=a.hollow)
        self.state.box = list(built.a) + list(built.b)

    def done(self, ctx):
        self.result("built", box=self.state.box)
        self.end()
