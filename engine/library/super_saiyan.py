"""Super Saiyan: three crouches within 1.5 s, or a right-click on the Super Saiyan star (its own
file), power you up (or down again) into Super Saiyan form (the super_saiyan_form mark, its own
file): the riff, a 1.5 s aura charge, then the form for a while. (super_saiyan/main.py, its gesture
and its item.)"""

from vfg_engine import Ability, AbilityConfig, Field, Sneak, State, Unsneak
from vfg_engine.kit import charge_frames, melody, sneak_gesture


class SuperSaiyan(Ability):
    class Config(AbilityConfig):
        summary = "Three crouches, or the Super Saiyan star, power you up (or down) into Super Saiyan form."
        seconds: float = Field(60, ge=5, le=3600)     # how long the form lasts once you power up
        damage = 0
        scale_per_point = 0
        charge_s = 1.5
        cooldown_s = 0
        cooldown_from = "end"
        duration_s = 0                                # instant: the toggle happens in on_cast

    class State(State):
        played: bool = False                          # the riff: once a cast, not every charge tick

    def start(self, ctx):
        self.listen(Sneak, "on_sneak")
        self.listen(Unsneak, "on_sneak")

    def on_sneak(self, ctx, ev):
        if sneak_gesture(self, ctx, ev, "three_taps"):
            ctx.user.cast("super_saiyan")

    def on_charge(self, ctx, ev):
        charge_frames(self, ctx, ev.progress, "aura", 5)
        if not self.state.played:
            self.state.played = True
            melody(self, ctx, "super_saiyan")

    def on_cast(self, ctx, ev):
        who = ctx.user
        if "super_saiyan_form" in who.marks():
            self.apply(who.key, "super_saiyan_form", seconds=0)   # power down: ends at once
            # NEEDS: a cast that skips its own ability's charge_s: powering down still waits the
            # same 1.5 s and shows the aura charge again, instead of being instant
        else:
            self.apply(who.key, "super_saiyan_form", seconds=self.config.seconds)
        self.end()
