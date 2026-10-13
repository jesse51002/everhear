"""Ice Staff: right-click the mob or block you point at, and every mob round it freezes and is
hurt; no beam (the ice_staff_freeze ability). (The old ice_staff script's item.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class IceStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click a mob or a block to freeze and hurt what is round it (the ice_staff_freeze ability)."
        base = "carrot_on_a_stick"
        name = "Ice Staff"
        active_in = "mainhand"
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        if ctx.user.cast("ice_staff_freeze") == "cooldown":
            speak(self, ctx, "Ice Staff: recharging", voice="", color="gray",
                  to=[ctx.user.name], where="actionbar")
