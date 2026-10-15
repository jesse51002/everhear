"""Gatling shot: one short beam along where the caster looks, through walls, 24 blocks out. The
Gatling Gun item fires one every 0.1 s while its button is held; granted and bound, it is cast by
voice. (The old railgun script with the gatling_gun action's numbers: fire=hold, range 24,
cooldown 0.1, crit, arrow.shoot.)

Minecraft spaces hits on one target about half a second apart (WRITING.md, self.damage), so of
ten shots a second at one target about two land."""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import beam_line, hit, hitscan


class GatlingShot(Ability):
    class Config(AbilityConfig):
        summary = "One short beam along your look, through walls; the Gatling Gun fires it while held."
        damage: float = Field(30, ge=0, le=1000)          # to each thing the beam passes, once a shot
        damage_type: str = "minecraft:magic"
        range: float = Field(24, ge=4, le=300)             # how far the beam reaches, in blocks
        width: float = Field(1.8, ge=0.5, le=5)            # how close to the beam a body is hit
        through_walls: bool = True                         # off: it stops at the first solid block
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        particle: str = "minecraft:crit"
        sound: str = "minecraft:entity.arrow.shoot"
        pitch: float = Field(1.8, ge=0.5, le=2)
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0.1                                   # the fastest it fires
        duration_s = 0                                     # instant: the run ends with on_cast

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, c.width, c.what, through_walls=c.through_walls)
        self.sound(c.sound, volume=3, pitch=c.pitch)
        hit(self, ctx, shot.targets, c.damage, c.damage_type)
        x, y, z = shot.start
        beam_line(self, ctx, (x, y - 0.2, z), shot.end, c.particle)
