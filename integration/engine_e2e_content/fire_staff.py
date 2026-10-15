"""The Fire Staff, from the game engine spec ("Ticks and subscriptions"), completed: right-click
to cast Fireball; holding it adds 20% speed; while held, a few flames at the holder's hand."""

from vfg_engine import Field, Item, ItemConfig, Use


class FireStaff(Item):
    every = 0.5                          # tick while someone holds it

    class Config(ItemConfig):
        summary = "Right-click to cast Fireball. Holding it adds 20% speed."
        base = "carrot_on_a_stick"
        name = "Fire Staff"
        active_in = "mainhand"
        speed_bonus: float = Field(0.2, ge=0, le=1)

    def start(self, ctx):                # someone started holding it
        self.modifier("movement_speed", self.config.speed_bonus, "add_multiplied_total")
        self.listen(Use, "on_use")       # only this holder's uses

    def on_use(self, ctx, ev):
        ctx.user.cast("e2e_fireball")        # an ability, by name

    def tick(self, ctx):                 # every 0.5 s while held
        x, y, z = ctx.user.eye
        self.particle("minecraft:flame", (x, y - 0.4, z), count=2, delta=(0.1, 0.1, 0.1))

    def stop(self, ctx, reason):         # let go, dropped, died, reloaded
        pass                             # its modifier and listens go by themselves
