"""Lightning strike: if your look hits a block within range, lightning strikes there at once (and
more bolts scattered round it), hurting everything near the first strike. The Lightning Staff
casts it on a right-click; granted and bound, it is cast by voice. Shares its shape with zeus and
weapon_chest's Stormcaller and Thunderbrand (the kit's `lightning`). (The old lightning_staff
script and action's numbers.)

# NEEDS: the raycast stops at the first block only within its own cap (about 48 blocks today),
short of the range this staff is configured for (up to 300).
"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import hit, hitscan, lightning, targets


class LightningStrike(Ability):
    class Config(AbilityConfig):
        summary = "Strikes lightning (and more bolts round it) where you look, hurting what is near."
        damage: float = Field(40, ge=0, le=1000)                  # round the strike, once
        damage_type: str = "minecraft:lightning_bolt"
        range: float = Field(120, ge=4, le=300)                   # how far the look must hit a block
        lightning_count: int = Field(4, ge=1, le=20)
        lightning_spread: float = Field(3, ge=0, le=30)           # how far the extra bolts scatter
        lightning_layout: Literal["scatter", "ring"] = "scatter"
        around_radius: float = Field(4, ge=0, le=50)              # how far round the strike it hurts
        around_what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 1
        duration_s = 0                                            # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, 0.5, through_walls=False)
        if shot.block is None:
            self.message("No target in range", where="actionbar")
            return
        lightning(self, ctx, shot.end, c.lightning_count, c.lightning_spread, c.lightning_layout)
        hit(self, ctx, targets(self, ctx, shot.end, c.around_radius, c.around_what), c.damage, c.damage_type)
