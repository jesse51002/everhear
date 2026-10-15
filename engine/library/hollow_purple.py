"""Hollow Purple: right-click fires the hollow_purple_beam ability, a blue-and-red charge that
merges into a long killing beam. (The old hollow_purple script's item: a glinting, unbreakable
carrot on a stick named Hollow Purple, in dark purple.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class HollowPurple(Item):
    class Config(ItemConfig):
        summary = "Right-click to charge and fire a long killing beam along your look, through walls (hollow_purple_beam)."
        base = "carrot_on_a_stick"
        name = "Hollow Purple"
        active_in = "mainhand"
        color = "dark_purple"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("hollow_purple_beam")
        if got == "cooldown":
            speak(self, ctx, "Hollow Purple: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name], where="actionbar")
