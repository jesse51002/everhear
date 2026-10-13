"""Gravity Crush Staff: right-click to crush every mob round where you look to the ground and
pin it there (the gravity_crush ability). (The old gravity_staff script's crush item: a carrot on
a stick named "Gravity Crush Staff" -- the action's own default, "Gravity Staff", was never used.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class GravityCrushStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to crush every mob round where you look to the ground (the gravity_crush ability)."
        base = "carrot_on_a_stick"
        name = "Gravity Crush Staff"
        active_in = "mainhand"
        color = "dark_purple"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        if ctx.user.cast("gravity_crush") == "cooldown":
            speak(self, ctx, "Gravity Crush Staff: recharging", voice="", color="gray",
                  to=[ctx.user.name], where="actionbar")
