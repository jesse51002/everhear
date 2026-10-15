"""TNT rain: a volley of lit TNT falls out of the sky round each player every few seconds, until
stopped; it breaks blocks where it lands. A stop takes away whatever is still falling (the old
tnt_rain script)."""

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import sky_spots


class TntRain(Scenario):
    every = 0.1                                  # tick(): check whether the next volley is due

    class Config(ScenarioConfig):
        summary = "A volley of lit TNT falls out of the sky round each player every few seconds, until stopped."
        duration_s = UNTIL_STOPPED
        height: float = Field(25, ge=1, le=300)          # how high over each player the volley falls from
        band: float = 0                                   # give or take, round height
        open_only: bool = True                            # only spots in open air

    class Args(Args):
        players: Players = Field("@a", description="who it rains on: @a, @r or names")
        every_s: float = Field(20, ge=1, le=3600, description="seconds between volleys; the first falls at once")
        count: int = Field(10, ge=1, le=40, description="how many TNT fall round each player in a volley")
        radius: float = Field(7, ge=0, le=128, description="how far out they can fall, in blocks")
        fuse_s: float = Field(4, ge=0.5, le=10, description="seconds each TNT burns before it blows")

    class State(State):
        due: float = 0.0        # ctx.now the next volley is due

    def start(self, ctx):
        a = self.args
        self.result("TNT rain on %s: %d TNT round each every %g s, the first now" % (a.players, a.count, a.every_s),
                    players=a.players, every=a.every_s, count=a.count)
        self.volley(ctx)         # the first falls at once

    def tick(self, ctx):
        if ctx.now >= self.state.due:
            self.volley(ctx)

    def volley(self, ctx):
        a, c = self.args, self.config
        self.state.due = ctx.now + a.every_s
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None:
                continue
            for at in sky_spots(ctx, p, a.count, a.radius, c.height, c.band, c.open_only, dim=p.dim):
                # left to despawn by itself when it blows; a stop removes what is still falling
                self.spawn("minecraft:tnt", at, dim=p.dim, nbt="{fuse:%d}" % round(a.fuse_s * 20))
