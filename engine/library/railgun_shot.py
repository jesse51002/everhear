"""Railgun shot: an instant beam along where the caster looks, through walls, hurting everything
within 1.8 blocks of it, 100 blocks out. The Railgun item fires it on a right-click; granted and
bound, it is cast by voice. (The old railgun script's shot and the railgun action's numbers.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import beam_line, hit, hitscan


class RailgunShot(Ability):
    class Config(AbilityConfig):
        summary = "An instant beam along your look, through walls, that hurts everything it passes."
        damage: float = Field(30, ge=0, le=1000)          # to each thing the beam passes, once a shot
        damage_type: str = "minecraft:magic"
        range: float = Field(100, ge=4, le=300)            # how far the beam reaches, in blocks
        width: float = Field(1.8, ge=0.5, le=5)            # how close to the beam a body is hit
        through_walls: bool = True                         # off: it stops at the first solid block
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        particle: str = "minecraft:end_rod"
        sound: str = "minecraft:entity.firework_rocket.blast"
        pitch: float = Field(0.5, ge=0.5, le=2)
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 2
        duration_s = 0                                     # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, c.width, c.what, through_walls=c.through_walls)
        self.sound(c.sound, volume=3, pitch=c.pitch)
        hit(self, ctx, shot.targets, c.damage, c.damage_type)
        x, y, z = shot.start
        beam_line(self, ctx, (x, y - 0.2, z), shot.end, c.particle)
