"""Giant: twice as big while it lasts (the game engine spec's marks scene). Its size is its own
modifier, everhear:mark/giant, which Minecraft multiplies with any other on minecraft:scale."""

from vfg_engine import Field, Mark, MarkConfig


class Giant(Mark):
    class Config(MarkConfig):
        summary = "Twice as big for a while."
        duration_s = 30
        damage = 0
        on_death = "end"
        on_milk = "keep"
        factor: float = Field(2.0, ge=0.0625, le=16)

    def start(self, ctx):
        self.modifier("scale", self.config.factor - 1, "add_multiplied_total")
