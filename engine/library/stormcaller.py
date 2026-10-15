"""Stormcaller: one of the six named weapons the weapon_chest hands out. Every trident it throws
calls lightning down where it lands or hits, the same shape as zeus and lightning_staff's
lightning_strike (the kit's `lightning`), and also deals its own damage round the strike -- zeus's
bolt alone is Minecraft's and uncounted, this one declares and deals damage there too. (The old
weapon_chest script's trident, with its own numbers: lightning_count 1, 10 damage round, radius 4,
damage_type lightning_bolt.)"""

from typing import Literal

from vfg_engine import Field, Item, ItemConfig, Land
from vfg_engine.kit import hit, lightning, targets


class Stormcaller(Item):
    class Config(ItemConfig):
        summary = "Every trident you throw calls lightning down where it lands or hits, hurting what is near."
        base = "minecraft:trident"
        name = "Stormcaller"
        active_in = "hands"
        color = "yellow"
        glint = True
        unbreakable = True
        enchantments = {"loyalty": 3, "impaling": 5}
        lightning_count: int = Field(1, ge=1, le=20)
        lightning_spread: float = Field(3, ge=0, le=30)            # how far the extra bolts scatter
        lightning_layout: Literal["scatter", "ring"] = "scatter"
        damage_amount: float = Field(10, ge=0, le=1000)            # round the strike, once
        damage_type: str = "minecraft:lightning_bolt"
        around_radius: float = Field(4, ge=0, le=50)               # how far round the strike it hurts

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        if not (ev.projectile_type or "").endswith("trident"):
            return
        c = self.config
        lightning(self, ctx, ev.pos, c.lightning_count, c.lightning_spread, c.lightning_layout, dim=ev.dim)
        hit(self, ctx, targets(self, ctx, ev.pos, c.around_radius, "everything", dim=ev.dim),
            c.damage_amount, c.damage_type)
