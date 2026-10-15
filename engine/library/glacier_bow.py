"""Glacier Bow: one of the six named weapons the weapon_chest hands out. Every arrow it shoots
freezes everything within its radius where it lands or hits, through the shared `frozen` mark
(ice_beam's and ice_staff's group). (The old weapon_chest script's bow, the freeze preset with its
own numbers -- radius 4, 6 s.)"""

from vfg_engine import Field, Item, ItemConfig, Land
from vfg_engine.kit import targets


class GlacierBow(Item):
    class Config(ItemConfig):
        summary = "Every arrow you shoot freezes everything near where it lands or hits."
        base = "minecraft:bow"
        name = "Glacier Bow"
        active_in = "hands"
        color = "aqua"
        glint = True
        unbreakable = True
        enchantments = {"power": 5, "infinity": 1}
        radius: float = Field(4, ge=0, le=50)           # how far round the point it freezes
        freeze_s: float = Field(6, ge=1, le=120)

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        c = self.config
        for t in targets(self, ctx, ev.pos, c.radius, "everything", dim=ev.dim):
            # frozen is ice_beam's and ice_staff's group's Mark, named by id alone (library
            # README: "name another class by its id instead").
            self.apply(t.key, "frozen", seconds=c.freeze_s)
