"""Galick Gun's beam: charges for charge_s to a rising fanfare, then an instant beam along the
caster's look, through walls, hurting everything near it; it carves a tunnel to a crater at its
end and a small blast there. The Galick Gun item fires it on a right-click; granted and bound, it
is cast by voice. (The old galick_gun script and action's numbers. Two casters' beams used to
clash and push toward the weaker one; that is dropped, Jesse's call: KIT.md, "Merged, dropped...".)
"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, State
from vfg_engine.kit import beam_line, blast, charge_frames, crater, hit, hitscan, melody


class GalickGunBeam(Ability):
    class Config(AbilityConfig):
        summary = "Charges, then an instant beam along your look, through walls: it hurts what it passes and carves a crater at its end."
        damage: float = Field(60, ge=0, le=1000)           # to each thing the beam passes, once a shot
        damage_type: str = "minecraft:player_attack"
        length: float = Field(150, ge=4, le=1000)
        # near_line's width is how close a body must come to the line; the old script's own
        # "width" field (3) was twice that ("within 1.5 of its line", galick_gun/main.py's does).
        width: float = Field(1.5, ge=0.25, le=10)
        through_walls: bool = True
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        particle: str = "minecraft:dust{color:[0.6,0.1,0.9],scale:2.0}"
        charge_size: float = Field(1, ge=0.5, le=20)        # how far apart the two merging orbs start
        crater_radius: int = Field(6, ge=0, le=64)
        tunnel: int = Field(1, ge=0, le=8)                  # the carved cube's half-width, every 2 blocks along the beam
        blast_power: float = Field(3, ge=0, le=20)
        charge_s = 1.5                                      # the fanfare's own length: the run ends exactly as it finishes
        cooldown_s = 0
        scale_per_point = 0
        duration_s = 0                                      # instant once fired: the beam, crater and blast are all synchronous

    class State(State):
        tuned: bool = False                                 # the fanfare has started

    def on_charge(self, ctx, ev):
        c = self.config
        charge_frames(self, ctx, ev.progress, "orbs", c.charge_size, particle=c.particle)
        if not self.state.tuned:
            melody(self, ctx, "galick_gun")
            self.state.tuned = True

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.length, c.width, c.what, through_walls=c.through_walls)
        hit(self, ctx, shot.targets, c.damage, c.damage_type)
        beam_line(self, ctx, shot.start, shot.end, c.particle)
        n = int(shot.length)
        path = [[s + d * k for s, d in zip(shot.start, shot.direction)] for k in range(0, n + 1, 2)]
        crater(self, ctx, shot.end, c.crater_radius, leave="air", tunnel=c.tunnel, path=path)
        blast(self, ctx, shot.end, c.blast_power, "one", breaks=False)
