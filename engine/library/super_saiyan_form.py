"""Super Saiyan form: for a while, Speed III, Strength II and Jump Boost II (blue, what the action
last ran), hovering through a gravity modifier while not sneaking (crouch to sink back down; look
up past pitch -30 for a burst of levitation), with a continuous aura and an actionbar countdown.
Powering down (super_saiyan applying it again with seconds=0) ends it with 5 s of Slow Falling so
the fall does not hurt.

Levels 1, 2 and 3 (super_saiyan/main.py's other `level` values) had no amplifiers on record beyond
blue's, the one that ran (inv_G2); only blue is kept, rather than guessing the others' numbers."""

from vfg_engine import Field, Mark, MarkConfig, State
from vfg_engine.kit import charge_frames


class SuperSaiyanForm(Mark):
    every = 0.2

    class Config(MarkConfig):
        summary = "Speed, strength and jump boost, hovering while not sneaking, and an aura, for a while."
        duration_s = 60
        damage = 0
        on_death = "end"
        speed_amp: int = Field(2, ge=0, le=4)                # Speed III
        strength_amp: int = Field(1, ge=0, le=4)             # Strength II
        jump_amp: int = Field(1, ge=0, le=4)                 # Jump Boost II
        aura_size: float = Field(5, ge=1, le=20)
        levitation_pitch: float = Field(-30, ge=-90, le=0)   # look up past this for a burst of levitation

    class State(State):
        floating: bool = False
        ends_at: float = 0.0

    def start(self, ctx):
        self.state.ends_at = ctx.now + self.config.duration_s
        self.keep_potion("minecraft:speed", self.config.speed_amp)
        self.keep_potion("minecraft:strength", self.config.strength_amp)
        self.keep_potion("minecraft:jump_boost", self.config.jump_amp)

    def tick(self, ctx):
        c = self.config
        charge_frames(self, ctx, 1.0, "aura", c.aura_size)
        floating = not ctx.user.sneaking
        if floating != self.state.floating:
            self.modifier("gravity", -1 if floating else None, "add_multiplied_total")
            self.state.floating = floating
        if ctx.user.pitch < c.levitation_pitch:
            self.potion("minecraft:levitation", 1.0, 0, particles=False)
        if getattr(ctx.user, "is_player", False):
            left = max(0.0, self.state.ends_at - ctx.now)
            self.message("§bSuper Saiyan §7%ds" % round(left), where="actionbar")

    def stop(self, ctx, reason):
        self.potion("minecraft:slow_falling", 5.0, 0)
        # NEEDS: clearing a potion instantly: the engine never clears one (WRITING.md), so speed,
        # strength, jump boost and any levitation from the last burst fade out over a few more
        # seconds instead of ending the moment the form does
