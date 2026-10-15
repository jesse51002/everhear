"""Lightning Storm: one of the Demon Lord's rounds, started and owned by it (demon_lord.py).
Thunder held for as long as it lasts; every 2 seconds, a bolt falls near each player, a few blocks
off in any direction, `10 + level` times, then it clears and ends."""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import lightning, speak


class LightningStorm(Scenario):
    class Config(ScenarioConfig):
        summary = "Thunder held; every 2 s, a bolt falls near each player, `10 + level` times, then it clears."
        duration_s = 1000 * 2 + 5     # the worst case (level 1000), plus a little room
        strike_every_s: float = Field(2, ge=0.5, le=10)
        spread: float = Field(6, ge=1, le=32)     # how far a bolt may land from a player

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how many strikes: 10 + level")
        players: Players = Field("@a", description="who it strikes near: @a, @r, or names")

    class State(State):
        strikes: int = 0
        total: int = 0

    def start(self, ctx):
        a = self.args
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no lightning storm: %s" % ("nobody is online" if who in ("@a", "@r")
                                                     else who + " is not online"), players=[], count=0)
            self.end()
            return
        self.hold("weather", "thunder")
        speak(self, ctx, "The sky answers to ME. Keep moving or be struck. Level %d." % a.level)
        for name in names:
            p = ctx.world.player(name)
            self.sound("minecraft:entity.wither.spawn", p.pos, volume=0.6, pitch=0.7, to=[name], dim=p.dim)
        self.after(0.1, "strike")

    def strike(self, ctx):
        a, s = self.args, self.config.spread
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is not None and p.alive:
                # lightning()'s "spread" fans several bolts out from the first, struck dead on `at`;
                # one bolt a few blocks off (never on the player) wants the offset in `at` itself.
                near = (p.pos[0] + random.uniform(-s, s), p.pos[1], p.pos[2] + random.uniform(-s, s))
                self.state.total += len(lightning(self, ctx, near, count=1, dim=p.dim))
        self.state.strikes += 1
        if self.state.strikes < 10 + a.level:
            self.after(self.config.strike_every_s, "strike")
        else:
            # the old script forced clear weather at the end, not merely letting go of thunder (which,
            # "time" and "weather" being unreadable in 26.3, would otherwise just leave it storming):
            # release the hold, then say so in words, same as a raw command would.
            self.release("weather")
            self.command("weather clear")
            self.result("the storm struck %d times round %s" % (self.state.total, a.players),
                        players=a.players, strikes=self.state.strikes, hits=self.state.total)
            self.end()
