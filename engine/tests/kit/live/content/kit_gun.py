"""A held right-click (the kit's live test): each callback leaves its own potion on the holder,
so a test can see which came."""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import held_use


class KitGun(Item):
    class Config(ItemConfig):
        summary = "Press: speed; held: haste every 0.5 s; held 1 s: jump boost; let go: glowing."
        base = "carrot_on_a_stick"
        name = "Kit Gun"
        active_in = "mainhand"

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        held_use(self, ctx, ev, hold_s=1.0, repeat_s=0.5)

    def on_press(self, ctx):
        self.potion("speed", 3, particles=False)

    def on_hold(self, ctx):
        self.potion("haste", 3, particles=False)

    def on_charged(self, ctx):
        self.potion("jump_boost", 3, particles=False)

    def on_release(self, ctx, held_s):
        self.potion("glowing", 3, particles=False)
