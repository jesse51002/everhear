"""Pushing (the kit's live test): the mobs round a place away from it, or a player up or forward by
wind charges at his feet."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import push, targets


class KitPush(Scenario):
    class Config(ScenarioConfig):
        summary = "One push, then it ends."
        duration_s = 10

    class Args(Args):
        at: Place = Field(None, description="where the push comes from (mobs)")
        player: str = Field("", description="a player to throw instead")
        way: str = Field("away", description="away, toward, up or forward")
        strength: float = Field(8, description="blocks")

    def start(self, ctx):
        a = self.args
        if a.player:
            n = push(self, ctx, a.player, a.way, a.strength)
        else:
            n = push(self, ctx, targets(self, ctx, a.at, 6, "mobs"), a.way, a.strength, frm=a.at)
        self.result("%d thrown" % n, thrown=n)
        self.end()
