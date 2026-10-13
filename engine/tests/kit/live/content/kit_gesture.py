"""Crouch gestures (the kit's live test): three quick crouches give speed; with mode "held", haste
while crouched."""

from vfg_engine import UNTIL_STOPPED, Args, Field, Scenario, ScenarioConfig, Sneak, Unsneak
from vfg_engine.kit import sneak_gesture


class KitGesture(Scenario):
    class Config(ScenarioConfig):
        summary = "Three quick crouches: speed; held: haste."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        mode: str = Field("three_taps", description="three_taps or held")

    def start(self, ctx):
        self.listen(Sneak, "on_sneak")
        self.listen(Unsneak, "on_sneak")

    def on_sneak(self, ctx, ev):
        if sneak_gesture(self, ctx, ev, self.args.mode) and self.args.mode == "three_taps":
            self.potion("speed", 5, who=ev.who, particles=False)

    def on_sneaking(self, ctx):
        for p in ctx.world.players.values():
            if p.sneaking:
                self.potion("haste", 1, who=p.name, particles=False)
