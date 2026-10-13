"""Frozen: cannot move or attack for a while; blinded too, if asked. Slowness and weakness (and
blindness) are given through self.keep_potion, so they wear off by themselves even if the mark is
cleared early, and need no cleanup. A non-player bearer also gets the vanilla frozen-in-ice look
(and its frost damage) through TicksFrozen, refreshed while it lasts. (The old freeze preset,
shared by gravity_staff, ice_staff, ice_beam and weapon_chest. Its freeze_shell -- a shell of ice
round the body that melts when the freeze ends -- is dropped: no script in this group ever turns
it on, like gravity_staff's pull and push.)"""

from vfg_engine import Args, Field, Mark, MarkConfig


class Frozen(Mark):
    every = 1.0                      # keep the frost look and particle up while it lasts

    class Config(MarkConfig):
        summary = "Cannot move or attack for a while; blinded too, if asked."
        duration_s = 5
        damage = 0

    class Args(Args):
        blind: bool = Field(False, description="also blinds the bearer while frozen")

    def start(self, ctx):
        self.keep_potion("slowness", 9)
        self.keep_potion("weakness", 9)
        if self.args.blind:
            self.keep_potion("blindness", 3)
        self._chill(ctx)

    def tick(self, ctx):
        self._chill(ctx)

    def _chill(self, ctx):
        if not ctx.user.is_player:
            self.entity_data(ctx.user.key, "{TicksFrozen:140}")
            self.particle("minecraft:snowflake", ctx.user.pos, count=25, delta=(0.4, 0.6, 0.4),
                          speed=0.02, force=True)
