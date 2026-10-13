"""Railgun: a right-click fires the railgun_shot ability, an instant beam through walls. Within its
cooldown a click only says it is recharging. (The old railgun script's item: a glinting,
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
        if ctx.user.cast("railgun_shot") == "cooldown":
            # NEEDS: cast() to say how long its cooldown has left (the old item showed "Railgun: 1.3 s")
            speak(self, ctx, "Railgun: recharging", voice="", color="gray", to=[ctx.user.name], where="actionbar")
