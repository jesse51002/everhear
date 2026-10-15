"""Zeus: every trident a player throws calls lightning down where it lands or hits, in any
weather, never within 4 blocks of the thrower; a bolt's own damage is Minecraft's, uncounted.
Granted directly, no staff: he throws his own trident, EverHear or vanilla. Shares its shape with
lightning_staff's lightning_strike and weapon_chest's Stormcaller (the kit's `lightning`). (The
old zeus script and action's numbers.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, Land, UNTIL_STOPPED
from vfg_engine.kit import lightning


class Zeus(Ability):
    class Config(AbilityConfig):
        summary = "Every trident you throw calls lightning down where it lands or hits."
        lightning_count: int = Field(1, ge=1, le=20)
        lightning_spread: float = Field(3, ge=0, le=30)           # how far the extra bolts scatter
        lightning_layout: Literal["scatter", "ring"] = "scatter"
        damage = 0                                                # a bolt's own damage is Minecraft's
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED                                # passive: it just listens

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        if not (ev.projectile_type or "").endswith("trident"):
            return
        c = self.config
        lightning(self, ctx, ev.pos, c.lightning_count, c.lightning_spread, c.lightning_layout, dim=ev.dim)
