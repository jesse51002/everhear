"""Death Gaze: while you sneak, a beam along your look, up to the first wall, touches everything
close to it, killing it outright by default (or hurting it, by Config), with an end_rod line along
the beam. Letting go of sneak stops it. (death_gaze/main.py, its sneak trigger and its numbers.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field, Sneak, UNTIL_STOPPED, Unsneak
from vfg_engine.kit import beam_line, hit, hitscan, sneak_gesture


class DeathGaze(Ability):
    class Config(AbilityConfig):
        summary = "While you sneak, a beam along your look up to the first wall kills (or hurts) what is close to it."
        range: float = Field(60, ge=4, le=300)          # how far the beam reaches, in blocks
        width: float = Field(1.3, ge=0.5, le=5)         # how close to the beam a body is touched
        every_s: float = Field(0.1, ge=0.05, le=1)      # a pass this often while crouched
        what: Literal["everything", "mobs", "hostile", "players"] = "mobs"
        kill: bool = True                                # kill outright; false: damage_amount instead
        damage_amount: float = Field(20, ge=0, le=1000)   # to each thing the beam touches, one pass (kill false)
        damage_type: str = "minecraft:magic"
        most: int = Field(0, ge=0, le=50)                # only the nearest this many (0: all)
        head: float = Field(1, ge=0.5, le=5)             # times the damage on a headshot
        particle: str = "minecraft:end_rod"
        spare: tuple[str, ...] = ()                       # names the beam never touches
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED                        # passive: it runs the whole time it is granted

        def damage(self, ctx):
            # a kill: no declared number would ever be enough. Else Minecraft lands at most about
            # two hits a second on one target (self.damage spaces hits 0.5 s apart, WRITING.md), so
            # the per-second total is twice one pass's damage_amount
            return 1_000_000.0 if self.kill else self.damage_amount * 2

    def start(self, ctx):
        self.listen(Sneak, "on_sneak")
        self.listen(Unsneak, "on_sneak")

    def on_sneak(self, ctx, ev):
        sneak_gesture(self, ctx, ev, "held", every=self.config.every_s)

    def on_sneaking(self, ctx):
        c = self.config
        shot = hitscan(self, ctx, c.range, c.width, c.what, through_walls=False, spare=c.spare)
        hit(self, ctx, shot.targets, c.damage_amount, c.damage_type, kill=c.kill, most=c.most, head=c.head)
        beam_line(self, ctx, shot.start, shot.end, c.particle)
