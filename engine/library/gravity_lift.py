"""Gravity lift: throws every mob round the spot you look at up into the air. The Gravity Lift
Staff casts it on a right-click; granted and bound, it is cast by voice. (The old gravity_staff
script's lift mode and the gravity_lift action's numbers; pull and push are dropped, like
gravity_crush: no action ever used them.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import hitscan, push, targets


class GravityLift(Ability):
    class Config(AbilityConfig):
        summary = "Throws every mob round where you look up into the air."
        range: float = Field(30, ge=4, le=300)                    # how far he can aim, in blocks
        around_radius: float = Field(40, ge=1, le=100)            # how far round the spot it reaches
        around_what: Literal["everything", "mobs", "hostile", "players"] = "mobs"
        push_strength: float = Field(30, ge=0.5, le=40)           # about how high it throws each one
        damage = 0                                                # lift itself deals none
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 0                                            # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, 0.5, through_walls=False)
        self.sound("minecraft:entity.generic.explode", shot.end, volume=3, pitch=1.5)
        found = targets(self, ctx, shot.end, c.around_radius, c.around_what)
        push(self, ctx, found, "up", c.push_strength)
