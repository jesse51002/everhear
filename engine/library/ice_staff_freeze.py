"""Ice staff freeze: locks onto the nearest mob in a narrow cone where you look, up to lock_range,
or else the block you look at, and freezes and hurts every mob round that point; no beam. The Ice
Staff casts it on a right-click; granted and bound, it is cast by voice. (The old ice_staff
script's lock_on, around, freeze and damage_hits presets, and the ice_staff action's numbers.)

# NEEDS: a lock-on target behind a wall is not ruled out past the ray's own cap (about 48 blocks
today; lock_range reaches up to 500): line of sight that far is not something the engine reads yet.
"""

import math
from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import hit, hitscan, targets


class IceStaffFreeze(Ability):
    class Config(AbilityConfig):
        summary = "Freezes and hurts every mob round the mob or block you point at; no beam."
        damage: float = Field(200, ge=0, le=1000)                 # to each mob round the point, once
        damage_type: str = "minecraft:magic"
        lock_range: float = Field(300, ge=4, le=500)              # how far it looks for a mob to lock onto
        lock_cone: float = Field(6, ge=1, le=45)                  # how far off the look a lock may be, in degrees
        lock_players: bool = False                                # players can be locked onto too
        around_radius: float = Field(15, ge=1, le=100)            # how far round the point it reaches
        around_what: Literal["everything", "mobs", "hostile", "players"] = "mobs"
        freeze_s: float = Field(5, ge=1, le=120)                  # how long it freezes what is round the point
        blind: bool = False                                       # blind it too while frozen
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 1
        duration_s = 0                                            # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        at = self._lock(ctx, c) or hitscan(self, ctx, c.lock_range, 0.0, through_walls=False).end
        self.sound("minecraft:block.glass.break", at, volume=3, pitch=0.5)
        self.particle("minecraft:snowflake", at, count=400, delta=(4, 1, 4), speed=0.05, force=True)
        found = targets(self, ctx, at, c.around_radius, c.around_what)
        for t in found:
            self.apply(t.key, "frozen", seconds=c.freeze_s, blind=c.blind)
        hit(self, ctx, found, c.damage, c.damage_type)

    def _lock(self, ctx, c):
        """The nearest mob (or player, with lock_players) within lock_cone degrees of the look, up
        to lock_range, nearest first; its position, or None when there is none."""
        limit = math.cos(math.radians(c.lock_cone))
        user = ctx.user
        for t in targets(self, ctx, user.eye, c.lock_range, "everything" if c.lock_players else "mobs"):
            v = tuple(t.middle[i] - user.eye[i] for i in range(3))
            d = math.sqrt(sum(x * x for x in v)) or 1.0
            if sum(v[i] * user.facing[i] for i in range(3)) / d >= limit:
                return t.pos
        return None
