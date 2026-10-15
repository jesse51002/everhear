"""Boom Trident: every trident a player throws explodes like a charged creeper where it lands or
hits, sparing the thrower; with crater on, it also carves a crater there. (The old boom_trident
script's trident: a named, unbreakable, loyalty-3 trident, in red, that comes back when thrown.)
spear_bomb is this with crater on, its own class with the same Config fields (library/README.md,
"A variant is its own class with its own Config")."""

from typing import Literal

from vfg_engine import Field, Item, ItemConfig, Land
from vfg_engine.kit import blast, crater


class BoomTrident(Item):
    class Config(ItemConfig):
        summary = "Every trident you throw explodes where it lands or hits."
        base = "minecraft:trident"
        name = "Boom Trident"
        active_in = "hands"
        color = "red"
        glint = True
        unbreakable = True
        enchantments = {"loyalty": 3}
        power: float = Field(6, ge=0, le=16)            # the blast's power (TNT is 4; 0: none)
        layout: Literal["one", "plus", "ring", "rings"] = "one"
        spread: float = Field(4, ge=1, le=20)           # the layout's spread, in blocks
        breaks: bool = True                             # real TNT: it breaks blocks
        crater: bool = False                            # also carve a crater where it lands (a spear bomb)
        crater_radius: float = Field(4, ge=1, le=64)
        crater_leave: Literal["air", "lava", "fire"] = "air"
        crater_tunnel: int = Field(0, ge=0, le=16)      # only matters together with a tunnel path (none here)

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        c = self.config
        blast(self, ctx, ev.pos, c.power, c.layout, c.spread, breaks=c.breaks)
        if c.crater:
            crater(self, ctx, ev.pos, c.crater_radius, leave=c.crater_leave, tunnel=c.crater_tunnel)
