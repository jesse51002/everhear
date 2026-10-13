"""Sniper shot: a long hitscan shot along where the caster looks, stopping at the first solid
block, hurting every mob within its width, double on a headshot. The Super Sniper item fires it on
a right-click, from crouching eyes when zoomed; granted and bound, it is cast by voice. (The old
sniper_rifle script's shot and the sniper_rifle action's numbers.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import beam_line, hit, hitscan


class SniperShot(Ability):
    class Config(AbilityConfig):
        summary = "A long hitscan shot along your look, stopping at the first solid block, double damage on the head."
        damage_amount: float = Field(150, ge=0, le=1000)   # to each mob the shot passes, once a shot
        damage_type: str = "minecraft:player_attack"
        range: float = Field(400, ge=8, le=500)             # how far the shot reaches, in blocks
        width: float = Field(0.8, ge=0.5, le=5)             # how close to the line a body is hit
        through_walls: bool = False                        # it stops at the first solid block
        what: Literal["everything", "mobs", "hostile", "players"] = "mobs"
        head: float = Field(2, ge=1, le=10)                 # times the damage on a headshot
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0.5
        # long enough for the two beam trails below to draw, a tick apart: on a long, clear shot
        # (the live test's 54.6 ms) the hit, the ray and both trails together ran past the engine's
        # 50 ms cut in one call, so the trails are drawn the tick after, same as ice_beam's tune.
        duration_s = 0.2

        def damage(self, ctx):
            return self.damage_amount * self.head           # declared at its worst: a headshot

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.range, c.width, c.what, through_walls=c.through_walls)
        self.sound("minecraft:entity.firework_rocket.blast_far", volume=4, pitch=0.7)
        self.sound("minecraft:item.trident.thunder", volume=3, pitch=1.6)
        hits = hit(self, ctx, shot.targets, c.damage_amount, c.damage_type, head=c.head)
        if any(t.on_head for t in hits):
            self.message("HEADSHOT", where="actionbar")
        x, y, z = shot.start
        ex, ey, ez = shot.end
        self.after(0.0, "draw_trail", x, y - 0.15, z, ex, ey, ez, "minecraft:end_rod", 8.0, 6)
        self.after(0.1, "draw_trail", x, y - 0.15, z, ex, ey, ez, "minecraft:electric_spark", 16.0, 4)

    def draw_trail(self, ctx, x, y, z, ex, ey, ez, particle, every, count):
        beam_line(self, ctx, (x, y, z), (ex, ey, ez), particle, every=every, spread=0.1, count=count)
