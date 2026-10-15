"""Gatling Gun: hold right-click to rapid-fire the gatling_shot ability, a short beam through walls.
(The old railgun script run as the gatling_gun action: a glinting, unbreakable carrot on a stick
named Gatling Gun, in aqua, firing while the button is held down.)"""

from vfg_engine import Field, Item, ItemConfig, Use
from vfg_engine.kit import held_use


class GatlingGun(Item):
    class Config(ItemConfig):
        summary = "Hold right-click to rapid-fire short beams along your look (the gatling_shot ability)."
        base = "carrot_on_a_stick"
        name = "Gatling Gun"
        active_in = "mainhand"
        color = "aqua"
        glint = True
        unbreakable = True
        every_s: float = Field(0.1, ge=0.1, le=2)   # while held, a shot this often (its cooldown permitting)

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        held_use(self, ctx, ev, repeat_s=self.config.every_s)

    def on_press(self, ctx):
        ctx.user.cast("gatling_shot")

    def on_hold(self, ctx):
        ctx.user.cast("gatling_shot")
