"""Place Barrier: the same bubble as Barrier Shield, but at a saved place instead of round a
player: a scenario, since an ability takes no args and a fixed place needs one. Stopped to take it
down. (barrier_shield/main.py, at="".)"""

import math
from typing import Literal

from vfg_engine import Args, Field, Place, Scenario, ScenarioConfig, State, UNTIL_STOPPED
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


class PlaceBarrier(Scenario):
    every = 0.2

    class Config(ScenarioConfig):
        summary = "A bubble at a place throws out mobs (or what its args name) that come inside it."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        at: Place = Field("", description="where the bubble stands: a saved place, x y z, or a player")
        radius: float = Field(5, ge=1, le=128, description="how wide the bubble is, in blocks")
        what: Literal["mobs", "hostile", "players", "everything", "projectiles"] = Field(
            "mobs", description="what it throws out: mobs, hostile, players, everything, or flying projectiles")
        strength: float = Field(4, ge=0.5, le=40, description="how hard a mob is thrown out, in blocks")
        launch_strength: float = Field(
            10, ge=0.5, le=40, description="how high a player caught inside is launched instead")
        ring: bool = Field(True, description="show the three turning rings of particles")

    class State(State):
        turn: float = 0.0

    def start(self, ctx):
        if self.args.at is None:
            self.result("no barrier: no place given", failed=True)
            self.end()

    def tick(self, ctx):
        a = self.args
        if a.at is None:
            return
        found = targets(self, ctx, a.at.pos, a.radius, a.what, dim=a.at.dim)
        mobs = [t for t in found if not t.is_player]
        people = [t for t in found if t.is_player]
        if mobs:
            push(self, ctx, mobs, "away", a.strength, frm=a.at.pos)
        if people:
            push(self, ctx, people, "up", a.launch_strength, frm=a.at.pos)
        if a.ring:
            _rings(self, ctx, a.at.pos, a.radius, self.state.turn, dim=a.at.dim)
        self.state.turn += 1.0
