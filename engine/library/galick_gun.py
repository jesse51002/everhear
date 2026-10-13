"""Galick Gun: right-click fires the galick_gun_beam ability, a beam that charges to a fanfare
then blasts through walls. (The old galick_gun script's item: a glinting, unbreakable carrot on a
stick named Galick Gun, in light purple.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class GalickGun(Item):
    class Config(ItemConfig):
        summary = "Right-click to charge and fire a beam along your look, through walls (galick_gun_beam)."
        base = "carrot_on_a_stick"
        name = "Galick Gun"
        active_in = "mainhand"
        color = "light_purple"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        # The old script fired on the first press and let go never cancelled it; cast()'s own
        # charge_s already charges and fires the same way, so one press is all this needs.
        if ctx.user.cast("galick_gun_beam") == "cooldown":
            speak(self, ctx, "Galick Gun: recharging", voice="", color="gray", to=[ctx.user.name], where="actionbar")
