"""Ice Beam Staff: right-click fires the ice_beam ability, an instant freezing beam through
walls. (The old ice_beam script's item: a glinting, unbreakable carrot on a stick named Ice Beam
Staff, in aqua.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class IceBeamStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to fire a freezing beam along your look, through walls (the ice_beam ability)."
        base = "carrot_on_a_stick"
        name = "Ice Beam Staff"
        active_in = "mainhand"
        color = "aqua"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        if ctx.user.cast("ice_beam") == "cooldown":
            speak(self, ctx, "Ice Beam Staff: recharging", voice="", color="gray", to=[ctx.user.name], where="actionbar")
