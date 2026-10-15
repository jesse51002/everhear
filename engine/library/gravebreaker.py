"""Gravebreaker: one of the six named weapons the weapon_chest hands out, a mace look that casts
an instant throw of everything round you up into the air on a right-click (the self_lift ability,
this group's own). (The old weapon_chest script's mace.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class Gravebreaker(Item):
    class Config(ItemConfig):
        summary = "Right-click to throw everything within 4 blocks of you up into the air (the self_lift ability)."
        base = "carrot_on_a_stick"
        name = "Gravebreaker"
        model = "minecraft:mace"
        active_in = "mainhand"
        color = "dark_purple"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("self_lift")
        if got == "cooldown":
            speak(self, ctx, "Gravebreaker: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
