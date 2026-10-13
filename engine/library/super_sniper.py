"""Super Sniper: right-click to fire a long hitscan shot (the sniper_shot ability), stopping at the
first solid block, double damage on the head; crouch while holding it to zoom the scope in (slowed,
as a scope narrows the view), standing or putting it away takes the zoom off. Within its cooldown a
click only says it is recharging. (The old sniper_rifle script's item, with its zoom.)"""

from vfg_engine import Field, Item, ItemConfig, Sneak, Unsneak, Use
from vfg_engine.kit import speak


class SuperSniper(Item):
    class Config(ItemConfig):
        summary = "Right-click to fire a long shot, double on the head; crouch to zoom (the sniper_shot ability)."
        base = "carrot_on_a_stick"
        name = "Super Sniper"
        active_in = "mainhand"
        color = "aqua"
        glint = True
        unbreakable = True
        # how far crouching with it zooms in (0: no zoom); each step is 15% slower, as a scope
        # narrows the view (the old zoom's slowness amplifier, as a movement_speed modifier instead)
        zoom: int = Field(4, ge=0, le=7)

    def start(self, ctx):
        self.listen(Use, "on_use")
        self.listen(Sneak, "on_sneak")
        self.listen(Unsneak, "on_unsneak")

    def on_use(self, ctx, ev):
        if ctx.user.cast("sniper_shot") == "cooldown":
            # NEEDS: cast() to say how long its cooldown has left (the old item showed "Super Sniper: 0.4 s")
            speak(self, ctx, "Super Sniper: recharging", voice="", color="gray", to=[ctx.user.name], where="actionbar")

    def on_sneak(self, ctx, ev):
        if self.config.zoom:
            self.modifier("movement_speed", -0.15 * self.config.zoom, "add_multiplied_total")

    def on_unsneak(self, ctx, ev):
        self.modifier("movement_speed", None)
