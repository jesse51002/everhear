"""Walking (the kit's live test): three steps give speed, a second chunk gives jump boost."""

from vfg_engine import UNTIL_STOPPED, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import chunks_entered, steps


class KitWalk(Scenario):
    every = 0.1

    class Config(ScenarioConfig):
        summary = "Counts steps and chunks."
        duration_s = UNTIL_STOPPED

    class State(State):
        steps: dict = {}
        chunks: dict = {}

    def tick(self, ctx):
        for name in players(ctx):
            if steps(self, ctx, name, 2.0):
                self.state.steps[name] = self.state.steps.get(name, 0) + 1
                if self.state.steps[name] == 3:
                    self.potion("speed", 10, who=name, particles=False)
            got = chunks_entered(self, ctx, name)
            if got:
                self.state.chunks[name] = self.state.chunks.get(name, 0) + len(got)
                if self.state.chunks[name] == 2:
                    self.potion("jump_boost", 10, who=name, particles=False)
