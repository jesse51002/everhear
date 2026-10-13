"""Gravity crush: slams every mob round the spot you look at to the ground and pins it there,
frozen, flyers too: it is re-teleported to the ground's surface every 0.1 s for freeze_s. The
Gravity Crush Staff casts it on a right-click; granted and bound, it is cast by voice. (The old
gravity_staff script's crush mode and the gravity_crush action's numbers; pull and push are
dropped, like the old push preset's wind-up for them: no action ever used them.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, State
from vfg_engine.kit import hitscan, targets


class GravityCrush(Ability):
    every = 0.1                                       # pin: re-teleport what it crushed to the ground

    class Config(AbilityConfig):
        summary = "Crushes every mob round where you look to the ground and pins it there, frozen."
        range: float = Field(30, ge=4, le=300)                    # how far he can aim, in blocks
        around_radius: float = Field(40, ge=1, le=100)            # how far round the spot it reaches
        around_what: Literal["everything", "mobs", "hostile", "players"] = "mobs"
        freeze_s: float = Field(5, ge=1, le=120)                  # how long it is pinned and frozen
        damage = 0                                                # crush itself deals none
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0

        def duration_s(self, ctx):
            return self.freeze_s

    class State(State):
        pinned: list[str] = []          # keys (names or uuids) still being held down this cast

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, 0.5, through_walls=False)
        self.sound("minecraft:entity.warden.sonic_boom", shot.end, volume=4, pitch=0.5)
        found = targets(self, ctx, shot.end, c.around_radius, c.around_what)
        self.state.pinned = [t.key for t in found]
        for t in found:
            self.apply(t.key, "frozen", seconds=c.freeze_s)

    def tick(self, ctx):
        for key in self.state.pinned:
            snap = ctx.world.player(key) or ctx.world.get(key)
            if snap is None:
                continue
            x, y, z = snap.pos
            gy = ctx.world.ground_y(x, z)
            if gy is not None:
                self.teleport(key, (x, gy, z))
