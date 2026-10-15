"""Doomcleaver: one of the six named weapons the weapon_chest hands out, an axe look that casts
an instant blast where you look on a right-click (the blast_at_look ability, this group's own).
(The old weapon_chest script's axe.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class Doomcleaver(Item):
    class Config(ItemConfig):
        summary = "Right-click for a power-3 blast where you look, breaking nothing (the blast_at_look ability)."
        base = "carrot_on_a_stick"
        name = "Doomcleaver"
        model = "minecraft:netherite_axe"
        active_in = "mainhand"
        color = "dark_red"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("blast_at_look")
        if got == "cooldown":
            speak(self, ctx, "Doomcleaver: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
