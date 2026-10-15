"""A blast at a place (the kit's live test): breaking (vanilla TNT) or not, sparing what it names."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import blast


class KitBlast(Scenario):
    class Config(ScenarioConfig):
        summary = "One blast, then it ends."
        duration_s = 10

    class Args(Args):
        at: Place = Field(None, description="where")
        power: float = Field(4, description="how big")
        breaks: bool = Field(False, description="TNT that breaks blocks")
        layout: str = Field("one", description="one, plus, ring, rings")
        spare: str = Field("", description="a player it spares")

    def start(self, ctx):
        a = self.args
        points = blast(self, ctx, a.at, a.power, a.layout, 3, a.breaks, spare=[a.spare] if a.spare else ())
        self.result("%d blasts" % len(points), points=len(points))
        self.end()
