"""Blast at look: an instant blast where the caster looks, up to range, breaking nothing. The
Doomcleaver casts it on a right-click; granted and bound, it is cast by voice. (The old
weapon_chest script's axe, with its own numbers: explosion_power 3, explosion_breaks false,
range 120 -- the kit's `blast` in place of its own explosion preset, the same shape as
gravity_lift's own hitscan-then-act.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import blast, blast_damage, hitscan


class BlastAtLook(Ability):
    class Config(AbilityConfig):
        summary = "An instant blast where you look, up to range; no blocks break."
        power: float = Field(3, ge=0, le=16)                      # the blast's power (TNT is 4)
        layout: Literal["one", "plus", "ring", "rings"] = "one"
        spread: float = Field(4, ge=1, le=20)                     # the layout's spread, in blocks
        breaks: bool = False                                      # no blocks break
        range: float = Field(120, ge=4, le=300)                   # how far the look must hit a block
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 1
        duration_s = 0                                            # instant: the run ends with on_cast

        def damage(self, ctx):
            return blast_damage(self.power, 0.0)                  # declared at its worst: the middle

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, 0.5, through_walls=False)
        if shot.block is None:
            self.message("No target in range", where="actionbar")
            return
        blast(self, ctx, shot.end, c.power, c.layout, c.spread, breaks=c.breaks)
