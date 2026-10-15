"""Rocket Launcher: right-click fires the fireball ability (a ball of flame to the first mob on
your look, or where you look, then a blast that breaks blocks). Within its cooldown, a click says
how long is left.

(made/main's rocket_launcher_contact, merged into fireball per its own verdict: "a projectile to
the first hit, then a blast. The launcher becomes an Item that casts it." Its old firework-particle
look and its rings of 17 blasts were leftover defaults inherited from Spirit Bomb, never set
deliberately for it (inv_G4); one fireball, fired from here.)"""

from vfg_engine import Item, ItemConfig, Use
from vfg_engine.kit import speak


class RocketLauncher(Item):
    class Config(ItemConfig):
        summary = "Right-click to fire a fireball at the first mob on your look, or where you look (the fireball ability)."
        base = "carrot_on_a_stick"
        name = "Rocket Launcher"
        active_in = "mainhand"
        color = "red"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        got = ctx.user.cast("fireball")
        if got == "cooldown":
            speak(self, ctx, "Rocket Launcher: %.1f s" % got.remaining_s, voice="", color="gray", to=[ctx.user.name],
                  where="actionbar")
