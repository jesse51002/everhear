"""Boom Bow: every arrow a player shoots from it explodes where it lands or hits, sparing the
shooter, with an optional mushroom-cloud of particles over the blast. (The old boom_bow script's
bow: a glinting, unbreakable bow named Boom Bow, in aqua.)"""

from typing import Literal

from vfg_engine import Field, Item, ItemConfig, Land
from vfg_engine.kit import blast


class BoomBow(Item):
    class Config(ItemConfig):
        summary = "Every arrow you shoot explodes where it lands or hits."
        base = "minecraft:bow"
        name = "Boom Bow"
        active_in = "hands"
        color = "aqua"
        glint = True
        unbreakable = True
        power: float = Field(8, ge=0, le=16)            # the blast's power (TNT is 4; 0: none)
        layout: Literal["one", "plus", "ring", "rings"] = "one"
        spread: float = Field(8, ge=1, le=20)           # the layout's spread, in blocks
        breaks: bool = True                             # real TNT: it breaks blocks
        cloud: bool = False                             # a mushroom-cloud of particles over the blast

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        c = self.config
        blast(self, ctx, ev.pos, c.power, c.layout, c.spread, breaks=c.breaks)
        if c.cloud:
            x, y, z = ev.pos
            self.particle("minecraft:explosion_emitter", (x, y + 8, z), count=20, delta=(3, 6, 3), force=True)
        # NEEDS: self.remove only takes what this instance itself spawned; the landed arrow is
        # Minecraft's own and cannot be removed here (the old script killed it by uuid). It is
        # left to despawn naturally instead of being picked up and reused.
