"""Summoning Staff: right-click and zombies appear at safe spots where you look (the
summoning_staff_summon ability). (The old wither_wand script, run as the summoning_staff action:
summon_mob zombie, item "Summoning Staff"; its own class beside wither_wand, the same few lines,
library/README.md.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class SummoningStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to summon zombies at safe spots where you look (the summoning_staff_summon ability)."
        base = "carrot_on_a_stick"
        name = "Summoning Staff"
        active_in = "mainhand"
        color = "dark_purple"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("summoning_staff_summon")
        if got == "cooldown":
            speak(self, ctx, "Summoning Staff: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
