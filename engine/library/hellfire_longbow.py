"""Hellfire Longbow: one of the six named weapons the weapon_chest hands out. Every arrow it
shoots explodes where it lands or hits, a small blast that breaks nothing, sparing the shooter.
(The old weapon_chest script's bow, boom_bow's own mechanic with its own numbers -- power 3,
breaks false: engine/library/README.md, "A variant is its own class with its own Config.")"""

from typing import Literal

from vfg_engine import Field, Item, ItemConfig, Land
from vfg_engine.kit import blast


class HellfireLongbow(Item):
    class Config(ItemConfig):
        summary = "Every arrow you shoot explodes where it lands or hits; no blocks break."
        base = "minecraft:bow"
        name = "Hellfire Longbow"
        active_in = "hands"
        color = "red"
        glint = True
        unbreakable = True
        enchantments = {"power": 5, "infinity": 1, "flame": 1}
        power: float = Field(3, ge=0, le=16)            # the blast's power (TNT is 4; 0: none)
        layout: Literal["one", "plus", "ring", "rings"] = "one"
        spread: float = Field(8, ge=1, le=20)           # the layout's spread, in blocks
        breaks: bool = False                            # no blocks break

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        c = self.config
        blast(self, ctx, ev.pos, c.power, c.layout, c.spread, breaks=c.breaks, dim=ev.dim)
