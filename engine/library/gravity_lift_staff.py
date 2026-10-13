"""Gravity Lift Staff: right-click to throw every mob round where you look up into the air (the
gravity_lift ability). (The old gravity_staff script's lift item: a carrot on a stick named
"Gravity Lift Staff" -- the action's own default, "Gravity Staff", was never used.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class GravityLiftStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to throw every mob round where you look up into the air (the gravity_lift ability)."
        base = "carrot_on_a_stick"
        name = "Gravity Lift Staff"
        active_in = "mainhand"
        color = "dark_purple"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        if ctx.user.cast("gravity_lift") == "cooldown":
            speak(self, ctx, "Gravity Lift Staff: recharging", voice="", color="gray",
                  to=[ctx.user.name], where="actionbar")
