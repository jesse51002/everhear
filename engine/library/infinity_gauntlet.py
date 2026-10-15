"""Infinity Gauntlet: right-click and the first thing you point at is gone, in a beam of flame and
a thunder-flash (the infinity_gauntlet_shot ability), with no cooldown by default. (The old
infinity_gauntlet script's item, in gold.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class InfinityGauntlet(Item):
    class Config(ItemConfig):
        summary = "Right-click: the first thing you point at is gone, in a beam of flame (the infinity_gauntlet_shot ability)."
        base = "carrot_on_a_stick"
        name = "Infinity Gauntlet"
        active_in = "mainhand"
        color = "gold"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("infinity_gauntlet_shot")
        if got == "cooldown":
            speak(self, ctx, "Infinity Gauntlet: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name], where="actionbar")
