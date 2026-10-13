"""Showing things (the kit's live test): a tune, a countdown that calls back at zero, a narrator's
lines in colour."""

from vfg_engine import Args, Field, Scenario, ScenarioConfig
from vfg_engine.kit import countdown, melody, speak


class KitShow(Scenario):
    class Config(ScenarioConfig):
        summary = "A tune, a countdown or a voice, then it ends."
        duration_s = 20

    class Args(Args):
        what: str = Field("tune", description="tune, count or speak")
        player: str = Field("Steve", description="for whom")

    def start(self, ctx):
        w = self.args.what
        if w == "tune":
            lasts = melody(self, ctx, "galick_gun", at=self.args.player)
            self.after(lasts, "finish", "tune %.1f s" % lasts)
        elif w == "count":
            countdown(self, ctx, 3, "Boom", to=self.args.player, then="zero")
        else:
            speak(self, ctx, "You will not leave.")
            speak(self, ctx, "Kneel.", voice="<The Radiant One>", color="gold", to=self.args.player, where="title")
            self.after(0.5, "finish", "spoken")

    def zero(self, ctx):
        self.finish(ctx, "zero")

    def finish(self, ctx, what):
        self.result(what)
        self.end()
