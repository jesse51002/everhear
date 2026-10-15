"""Launch Pad: standing on a block (a slime block by default) throws a player through the air -
forward where they face, straight up, or away from or toward a place - landing safely, at most
once per cooldown. (The old launch_pad script and action: its 436-line wind-charge throw is now
the kit's push; its "ride" method is dropped, so there is one way to throw, as Jesse asked.)"""

import math
from typing import Literal

from vfg_engine import Args, BlockId, Field, Place, Players, Scenario, ScenarioConfig, Spot, State, \
    UNTIL_STOPPED, players
from vfg_engine.kit import push


class LaunchPad(Scenario):
    every = 0.1                      # tick(): a live read (the block under each foot), so kept brisk but light

    class Config(ScenarioConfig):
        summary = ("Standing on a block throws a player through the air: forward, up, or away from or "
                   "toward a place, landing safely, at most once per cooldown.")
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="whom the pads launch: @a, @r or names")
        block: BlockId = Field("slime_block", description="the block that launches whoever stands on it")
        way: Literal["forward", "up", "away", "toward"] = Field("forward", description="forward where they "
                                                                "face, straight up, or away from or toward `at`")
        strength: float = Field(12, ge=1, le=64, description="how far it throws them, in blocks (up: that "
                               "high)")
        cooldown: float = Field(2, ge=0, le=60, description="seconds before the same player is launched again")
        at: Place = Field("", description="for away or toward: the place thrown from or toward (a saved "
                         "place, x y z, or a player); empty: the pad itself")
        landing: Literal["safe", "float", "hurt"] = Field("safe", description="safe: no fall damage until "
                                                          "they land; float: slow falling down too; hurt: "
                                                          "the fall as it comes")

    class State(State):
        last: dict = {}     # player name -> ctx.now of their last launch

    def start(self, ctx):
        a = self.args
        self.result("launch pads on for %s: standing on %s throws them %g blocks %s"
                   % (a.players, a.block, a.strength, a.way), players=a.players, block=a.block, way=a.way,
                   strength=a.strength)

    def tick(self, ctx):
        a = self.args
        names = [n for n in players(ctx, a.players) if ctx.now - self.state.last.get(n, -1.0e9) >= a.cooldown]
        if not names:
            return
        by_dim = {}
        for name in names:
            p = ctx.world.player(name)
            if p is not None and p.alive:
                by_dim.setdefault(p.dim, []).append((name, p))
        last = dict(self.state.last)
        for dim, here in by_dim.items():
            feet = [(p.pos[0], p.pos[1] - 0.01, p.pos[2]) for _, p in here]
            on_pad = ctx.world.probe(feet, a.block, dim=dim)
            for (name, p), on in zip(here, on_pad):
                if not on:
                    continue
                frm = a.at if a.at is not None else self.pad_spot(p)
                if push(self, ctx, name, a.way, a.strength, frm=frm if a.way in ("away", "toward") else None,
                       landing=a.landing):
                    last[name] = ctx.now
        self.state.last = last

    @staticmethod
    def pad_spot(p):
        """The pad's own spot: the middle of the block under the feet, for away/toward with no `at`."""
        x, y, z = p.pos
        return Spot(dim=p.dim, pos=(math.floor(x) + 0.5, math.floor(y - 0.01), math.floor(z) + 0.5))
