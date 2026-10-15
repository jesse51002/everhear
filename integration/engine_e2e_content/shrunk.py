"""Shrunk: three quarters of the size while it lasts (the game engine spec's marks scene). When
it ends early, only everhear:mark/shrunk goes, and whatever else is on minecraft:scale (Giant's
x2) comes out exact. Milk cures it."""

from vfg_engine import Field, Mark, MarkConfig


class Shrunk(Mark):
    class Config(MarkConfig):
        summary = "Three quarters of the size for a while; milk cures it."
        duration_s = 10
        damage = 0
        on_death = "end"
        on_milk = "end"
        factor: float = Field(0.75, ge=0.0625, le=16)

    def start(self, ctx):
        self.modifier("scale", self.config.factor - 1, "add_multiplied_total")
