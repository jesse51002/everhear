"""A sign and a chest (the kit's live test), one an EverHear item in it."""

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig
from vfg_engine.kit import chest, sign


class KitChest(Scenario):
    class Config(ScenarioConfig):
        summary = "A sign and a chest, then it ends."
        duration_s = 10

    class Args(Args):
        at: Place = Field(None, description="the chest; the sign one block east")

    def start(self, ctx):
        x, y, z = self.args.at.pos
        chest(self, ctx, (x, y, z), ["diamond 4", "arrow 64", "everhear:kit_gun"], facing="south")
        sign(self, ctx, (x + 1, y, z), ["Kit Test", "Sniper Nest", "§cRed"], facing="south", wall=False)
        self.after(1, "finish")

    def finish(self, ctx):
        self.result("placed")
        self.end()
