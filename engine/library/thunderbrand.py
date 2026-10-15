"""Thunderbrand: one of the six named weapons the weapon_chest hands out, a sword look over
lightning_staff's own mechanic -- right-click casts the same lightning_strike ability (engine/
library/README.md, "A variant is its own class with its own Config"). (The old weapon_chest
script's sword.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class Thunderbrand(Item):
    class Config(ItemConfig):
        summary = "Right-click for lightning plus damage where you look (the lightning_strike ability)."
        base = "carrot_on_a_stick"
        name = "Thunderbrand"
        model = "minecraft:netherite_sword"
        active_in = "mainhand"
        color = "yellow"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("lightning_strike")
        if got == "cooldown":
            speak(self, ctx, "Thunderbrand: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
