"""A hitscan beam (the kit's live test): 8 magic to what is on the look line, double on the head,
stopped by a wall, drawn with beam_line."""

from vfg_engine import Ability, AbilityConfig
from vfg_engine.kit import beam_line, hit, hitscan


class KitBeam(Ability):
    class Config(AbilityConfig):
        summary = "A 40-block beam along the look: 8 magic to each, 16 on the head."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 16

    def on_cast(self, ctx, ev):
        shot = hitscan(self, ctx, 40, 1.5)
        hit(self, ctx, shot.targets, 8, "magic", head=2)
        beam_line(self, ctx, shot.start, shot.end, "minecraft:end_rod", every=1)
