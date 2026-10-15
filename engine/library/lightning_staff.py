"""Lightning Staff: right-click and lightning strikes the block you look at at once, hurting
everything near it (the lightning_strike ability). (The old lightning_staff script's item.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class LightningStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click and lightning strikes where you look (the lightning_strike ability)."
        base = "carrot_on_a_stick"
        name = "Lightning Staff"
        active_in = "mainhand"
        color = "yellow"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("lightning_strike")
        if got == "cooldown":
            speak(self, ctx, "Lightning Staff: %.1f s" % got.remaining_s, voice="", color="gray",
                  to=[ctx.user.name], where="actionbar")
