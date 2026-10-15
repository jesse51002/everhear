"""Railgun: a right-click fires the railgun_shot ability, an instant beam through walls. Within its
cooldown a click says how long is left ("Railgun: 1.3 s"). (The old railgun script's item: a glinting,
unbreakable carrot on a stick named Railgun, in aqua.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class Railgun(Item):
    class Config(ItemConfig):
        summary = "Right-click to fire a beam along your look, through walls (the railgun_shot ability)."
        base = "carrot_on_a_stick"
        name = "Railgun"
        active_in = "mainhand"
        color = "aqua"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("railgun_shot")
        if got == "cooldown":
            speak(self, ctx, "Railgun: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name], where="actionbar")
