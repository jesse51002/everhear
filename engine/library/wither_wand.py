"""Wither Wand: right-click and withers appear at safe spots where you look (the
wither_wand_summon ability). (The old wither_wand script's item: a glinting, unbreakable carrot
on a stick named Wither Wand, in dark purple.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class WitherWand(Item):
    class Config(ItemConfig):
        summary = "Right-click to summon withers at safe spots where you look (the wither_wand_summon ability)."
        base = "carrot_on_a_stick"
        name = "Wither Wand"
        active_in = "mainhand"
        color = "dark_purple"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        if ctx.user.cast("wither_wand_summon") == "cooldown":
            # NEEDS: cast() to say how long its cooldown has left (railgun.py has the same note)
            speak(self, ctx, "Wither Wand: recharging", voice="", color="gray", to=[ctx.user.name], where="actionbar")
