"""Spirit Bomb: hold right-click for arm_s to arm the spirit_bomb_cast ability; let go sooner and
nothing happens (the old script's "release cancels", before its own sphere ever charges). Once
armed, spirit_bomb_cast does its own charging and firing. (The old spirit_bomb script's item: a
glinting, unbreakable carrot on a stick named Spirit Bomb, in white.)"""

from vfg_engine import Field, Item, ItemConfig, Use
from vfg_engine.kit import held_use


class SpiritBomb(Item):
    class Config(ItemConfig):
        summary = "Hold right-click for a second to arm the Spirit Bomb (the spirit_bomb_cast ability)."
        base = "carrot_on_a_stick"
        name = "Spirit Bomb"
        active_in = "mainhand"
        color = "white"
        glint = True
        unbreakable = True
        arm_s: float = Field(1, ge=0, le=10)    # how long to hold before it arms; the old "charge" value

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        held_use(self, ctx, ev, hold_s=self.config.arm_s)

    def on_charged(self, ctx):
        ctx.user.cast("spirit_bomb_cast")
