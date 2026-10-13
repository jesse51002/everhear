"""Anti-Camp: a player who stays within a few blocks of one spot too long is warned, in the last
5 s, then TNT falls on them (it breaks blocks) and the Demon Lord mocks them in chat; then it
counts again from where they stand. (The old anti_camp script and action.)"""

import math
import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import speak

SPOTS = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2)]   # a plus and two corners; past that, at random


class AntiCamp(Scenario):
    every = 0.5                      # tick(): the snapshot alone, so a camper is caught within half a second

    class Config(ScenarioConfig):
        summary = "TNT falls on anyone who stays near one spot too long, after a warning; the Demon Lord mocks them."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="who it watches: @a, @r or names")
        seconds: float = Field(15, ge=6, le=3600, description="how long they may stay put before the TNT "
                              "falls (the warning counts down the last 5); then it counts again from where "
                              "they stand")
        radius: float = Field(4, ge=0.5, le=64, description="how far (flat) they may move and still count as "
                             "camping")
        tnt: int = Field(7, ge=1, le=30, description="how many TNT fall round a camper each time")
        height: int = Field(12, ge=2, le=60, description="how high over the camper the TNT appears, in blocks")
        fuse: float = Field(2, ge=0.5, le=10, description="seconds before the TNT blows up")
        mock: bool = Field(True, description="the Demon Lord mocks the camper in chat as the TNT falls")
        warning: str = Field("MOVE. The Demon Lord sees you hiding...", max_length=100,
                            description="shown on their actionbar, with the seconds left, in the last 5; "
                                        "empty: none")

    class State(State):
        anchors: dict = {}   # name -> {"pos": [x, y, z], "dim": str, "since": ctx.now}: where they are camping

    def start(self, ctx):
        a = self.args
        self.result("%d TNT fall on anyone of %s who stays within %g blocks for %g s"
                   % (a.tnt, a.players, a.radius, a.seconds), players=a.players, seconds=a.seconds,
                   radius=a.radius, tnt=a.tnt)

    def tick(self, ctx):
        a = self.args
        anchors = dict(self.state.anchors)
        changed = False
        here = set(players(ctx, a.players))
        for name in list(anchors):
            if name not in here:
                del anchors[name]
                changed = True
        for name in here:
            p = ctx.world.player(name)
            if p is None or not p.alive:
                continue
            x, y, z = p.pos
            anchor = anchors.get(name)
            if (anchor is None or anchor["dim"] != p.dim
                   or math.hypot(x - anchor["pos"][0], z - anchor["pos"][2]) > a.radius):
                anchors[name] = {"pos": [x, y, z], "dim": p.dim, "since": ctx.now}
                changed = True
                continue
            left = anchor["since"] + a.seconds - ctx.now
            if left <= 0:
                anchors[name] = {"pos": [x, y, z], "dim": p.dim, "since": ctx.now}
                changed = True
                self.camped(ctx, name, p)
            elif left <= 5 and a.warning:
                speak(self, ctx, "%s %d" % (a.warning, math.ceil(left)), voice="", color="red", to=[name],
                     where="actionbar")
        if changed:
            self.state.anchors = anchors

    def camped(self, ctx, name, p):
        a = self.args
        if a.mock:
            speak(self, ctx, "%s thinks standing still is safe." % name)   # voice "Demon Lord", color dark_red
        x, y, z = p.pos
        for i in range(a.tnt):
            dx, dz = SPOTS[i] if i < len(SPOTS) else (random.randint(-3, 3), random.randint(-3, 3))
            # keep=True: the TNT is its own thing once it falls, not cleaned up if this scenario is later stopped
            self.spawn("minecraft:tnt", (x + dx, y + a.height, z + dz), dim=p.dim,
                      nbt="{fuse:%ds}" % int(a.fuse * 20), keep=True)
