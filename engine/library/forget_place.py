"""Forget place: remove a saved place by name. New with the engine's own places store (DESIGN2
§2): before, only `vfg place forget` on the box could do this; the brain had no way to make a
place forgotten. The library's `save_place` names a place; this is the other half, through
`self.forget_place`."""

from vfg_engine import Args, Field, Scenario, ScenarioConfig


class ForgetPlace(Scenario):
    class Config(ScenarioConfig):
        summary = "Forgets a saved place by name."
        duration_s = 1

    class Args(Args):
        name: str = Field("", pattern=r"^[a-z0-9_]{0,32}$",
                          description="the saved place's name to forget: castle, home, zoo_entrance")

    def start(self, ctx):
        name = self.args.name
        if not name:
            self.result("which name? give the saved place's name to forget", name="", forgotten=False)
            self.end()
            return
        forgotten = self.forget_place(name)
        summary = ("forgot %s" % name) if forgotten else ("there is no saved place called %s" % name)
        self.result(summary, name=name, forgotten=forgotten)
        self.end()
