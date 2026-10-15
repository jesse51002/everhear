"""Infinity gauntlet shot: the first thing along where the caster looks is gone, in a beam of
flame, with an explosion flash and a thunderclap where it hit. The Infinity Gauntlet item fires it
on a right-click; granted and bound, it is cast by voice. (The old infinity_gauntlet script's shot
and the infinity_gauntlet action's numbers: damage_kill is on, so damage_amount does nothing unless
it is turned off.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import beam_line, hit, hitscan


class InfinityGauntletShot(Ability):
    class Config(AbilityConfig):
        summary = "The first thing along your look is gone, in a beam of flame."
        damage_amount: float = Field(100, ge=0, le=1000)    # ignored while kill is on (it is, by default)
        damage_type: str = "minecraft:lightning_bolt"
        kill: bool = True                                   # kill outright instead of damage_amount
        range: float = Field(80, ge=4, le=300)              # how far it reaches, in blocks
        width: float = Field(1.5, ge=0.5, le=5)             # how close to the line a body is hit
        through_walls: bool = False                        # it stops at the first solid block
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        most: int = Field(1, ge=0, le=1000)                 # only the nearest this many (1: the first only; 0: all)
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0                                      # no cooldown by default
        duration_s = 0                                      # instant: the run ends with on_cast

        def damage(self, ctx):
            # kill=True deals the kit's generic_kill (1,000,000, through hit()): declared honestly,
            # so a kill is never flagged as damage drift.
            return 1_000_000.0 if self.kill else self.damage_amount

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, c.width, c.what, through_walls=c.through_walls)
        self.sound("minecraft:entity.blaze.shoot", volume=3, pitch=0.6)
        end = shot.targets[0].middle if shot.targets else shot.end
        x, y, z = shot.start
        beam_line(self, ctx, (x, y - 0.2, z), end, "minecraft:flame", every=3.0, spread=0.5, count=20)
        if hit(self, ctx, shot.targets, c.damage_amount, c.damage_type, kill=c.kill, most=c.most):
            self.particle("minecraft:explosion_emitter", end)
            self.particle("minecraft:flame", end, count=150, delta=(1.5, 1.5, 1.5), speed=0.1)
            self.sound("minecraft:entity.lightning_bolt.thunder", end, volume=2)
