"""Barrier Shield: a bubble round its holder that throws out mobs (or whatever its Config names,
even flying arrows, tridents and fireballs) that come inside it, every 0.2 s, with three turning
rings of end_rod and soul fire. A player caught inside is launched straight up instead of pushed
sideways. A bubble at a place instead of a player is the place_barrier scenario (its own file): an
ability takes no args, so a fixed place needs one.

(barrier_shield/main.py, its numbers. Deflecting flying things (the old projectile_shield script)
is this with what="projectiles": barrier's own `around_what` already offered it, so projectile_shield
is not its own class (its verdict). Its own old numbers were radius 8, strength 8, ring false, every
0.1 s; set those with engine_config for that look, instead of barrier's own mob-bubble defaults.)"""

import math
from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, State, UNTIL_STOPPED
from vfg_engine.kit import push, targets

TURN_GAP = 2.0943951   # a third of a turn (2*pi/3): how far apart the three rings start


def _rings(self, ctx, at, radius, turn, dim=None):
    """Three rings of particles turning slowly round `at`, `radius` out (the bubble's wall)."""
    x, y, z = at
    for i, dz in enumerate((0.8, 0.0, -0.8)):
        particle = "minecraft:end_rod" if i % 2 == 0 else "minecraft:soul_fire_flame"
        a0 = turn * 0.3 + i * TURN_GAP
        for k in range(8):
            a = a0 + k * math.pi / 4
            self.particle(particle, (x + radius * math.cos(a), y + 1 + dz, z + radius * math.sin(a)),
                          force=True, dim=dim)


class BarrierShield(Ability):
    every = 0.2

    class Config(AbilityConfig):
        summary = "A bubble round you throws out mobs (or what this names) that come inside it."
        radius: float = Field(5, ge=1, le=128)              # how wide the bubble is, in blocks
        what: Literal["mobs", "hostile", "players", "everything", "projectiles"] = "mobs"
        strength: float = Field(4, ge=0.5, le=40)           # how hard a mob is thrown out, in blocks
        launch_strength: float = Field(10, ge=0.5, le=40)   # how high a player caught inside is launched instead
        ring: bool = True                                    # show the three turning rings of particles
        damage = 0
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED

    class State(State):
        turn: float = 0.0

    def tick(self, ctx):
        c = self.config
        found = targets(self, ctx, ctx.user, c.radius, c.what)
        mobs = [t for t in found if not t.is_player]
        people = [t for t in found if t.is_player]
        if mobs:
            push(self, ctx, mobs, "away", c.strength, frm=ctx.user)
        if people:
            push(self, ctx, people, "up", c.launch_strength, frm=ctx.user)
        if c.ring:
            _rings(self, ctx, ctx.user.pos, c.radius, self.state.turn, dim=ctx.user.dim)
        self.state.turn += 1.0
