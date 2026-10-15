"""Self lift: an instant throw of everything within radius of the caster up into the air --
gravity_lift centred on the caster instead of on where they look (engine/library/README.md, "A
variant is its own class with its own Config"). The Gravebreaker casts it on a right-click;
granted and bound, it is cast by voice. (The old weapon_chest script's mace, with its own
numbers: radius 4, push up 8.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import push, targets


class SelfLift(Ability):
    class Config(AbilityConfig):
        summary = "Throws everything within radius of you up into the air."
        radius: float = Field(4, ge=1, le=100)                    # how far round the caster it reaches
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        strength: float = Field(8, ge=0.5, le=40)                 # about how high it throws each one
        damage = 0                                                # lift itself deals none
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 1
        duration_s = 0                                            # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        found = targets(self, ctx, ctx.user, c.radius, c.what)
        self.sound("minecraft:entity.generic.explode", ctx.user.pos, volume=3, pitch=1.5)
        push(self, ctx, found, "up", c.strength)
