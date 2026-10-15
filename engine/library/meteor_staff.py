"""Meteor Staff: right-click and a meteor falls from the sky onto the block you look at, up to
reach blocks away, then explodes (the meteor_strike ability; named apart from the library's
`meteor` scenario elsewhere, which shares its fall and blast helpers). (The old meteor_staff
script's item: a glinting, unbreakable carrot on a stick made to look like a blaze rod, named
Meteor Staff, in gold.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class MeteorStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to drop a meteor where you look (the meteor_strike ability)."
        base = "carrot_on_a_stick"
        name = "Meteor Staff"
        active_in = "mainhand"
        model = "minecraft:blaze_rod"
        color = "gold"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("meteor_strike")
        if got == "cooldown":
            speak(self, ctx, "Meteor Staff: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
