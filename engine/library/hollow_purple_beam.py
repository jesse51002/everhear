"""Hollow Purple's beam: a blue and a red orb merge for charge_s to a falling tune, then an
instant 1000-block beam along the caster's look, through walls, killing everything near it and
within around_radius of where it ends; it carves a tunnel to a crater there. The Hollow Purple
item fires it on a right-click; granted and bound, it is cast by voice. (The old hollow_purple
script and action's numbers. It used to clash with galick_gun's beam; dropped, Jesse's call:
KIT.md, "Merged, dropped...". It declares damage honestly as a kill: hit(kill=True) deals the
engine's own flat generic_kill amount, not damage_amount.)"""

from vfg_engine import Ability, AbilityConfig, Field, State
from vfg_engine.kit import beam_line, charge_frames, crater, hit, hitscan, melody, targets


class HollowPurpleBeam(Ability):
    class Config(AbilityConfig):
        summary = "Charges, then an instant beam along your look, through walls, that kills everything near it and round its end."
        # near_line's width is how close a body must come to the line; the old script's own
        # "width" field (6) was twice that ("within 3 of its line", hollow_purple/main.py's does).
        length: float = Field(1000, ge=4, le=1000)
        width: float = Field(3, ge=0.25, le=20)
        through_walls: bool = True
        around_radius: float = Field(15, ge=0, le=64)       # also killed, round where the beam ends
        particle: str = "minecraft:dust{color:[0.4,0.0,0.6],scale:2.5}"
        charge_size: float = Field(5, ge=0.5, le=20)
        crater_radius: int = Field(7, ge=0, le=64)
        tunnel: int = Field(2, ge=0, le=8)                  # the carved cube's half-width, every 3 blocks along the beam
        charge_s = 0.5
        cooldown_s = 0
        scale_per_point = 0
        duration_s = 3                                      # the merge tune outlasts the 0.5 s charge by about this long

        def damage(self, ctx):
            return 1_000_000.0                              # hit(kill=True) deals generic_kill: an outright kill

    class State(State):
        tuned: bool = False

    def on_charge(self, ctx, ev):
        charge_frames(self, ctx, ev.progress, "orbs", self.config.charge_size,
                      orbs=("minecraft:dust{color:[0.2,0.4,1.0],scale:2}", "minecraft:dust{color:[1.0,0.2,0.2],scale:2}"))
        if not self.state.tuned:
            melody(self, ctx, "hollow_purple")
            self.state.tuned = True

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.length, c.width, "everything", through_walls=c.through_walls)
        near = targets(self, ctx, shot.end, c.around_radius, "everything")
        hit(self, ctx, shot.targets + near, c.damage(ctx), kill=True)
        beam_line(self, ctx, shot.start, shot.end, c.particle)
        n = int(shot.length)
        path = [[s + d * k for s, d in zip(shot.start, shot.direction)] for k in range(0, n + 1, 3)]
        crater(self, ctx, shot.end, c.crater_radius, leave="air", tunnel=c.tunnel, path=path)
