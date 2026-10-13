"""Sniper Nest: a stone tower in front of each player (or one at a place, for them all): a footing
so it never floats, a hollow tower, a walled lookout with slits and windows, a roof, a ladder up
through the floor, a door facing the player, a lantern, a sign over the door ({player} is their
name), and their spawn set on the lookout. It ends when built; the blocks stay."""

from vfg_engine import Args, Field, Place, Players, Scenario, ScenarioConfig, Spot, State, players
from vfg_engine.kit import BACK, build_shape, sign


class SniperNest(Scenario):
    class Config(ScenarioConfig):
        summary = ("Builds a stone tower in front of each player (or one at a place): a ladder up to a walled "
                   "lookout with slits, a sign over the door, their spawn set there.")
        duration_s = 60                                 # the most a build may take
        gap_s: float = Field(0.1, ge=0.1, le=2)         # between queuing one site and the next's
        blocks: str = "minecraft:stone_bricks"           # what the tower, lookout and roof are built of
        width: int = Field(5, ge=3, le=15)              # the tower's width and depth
        footing: int = Field(8, ge=1, le=64)            # how deep its footing goes, so it never floats
        lookout: int = Field(5, ge=3, le=9)             # the lookout's own height, on top of the tower

    class Args(Args):
        players: Players = Field("@a", description="who gets one, in front of them, its door facing them: @a, "
                                                   "@r or names")
        at: Place = Field("", description="where it goes instead: a saved place, x y z, or a player; one nest "
                                          "there, its door facing north (empty: in front of each player)")
        height: int = Field(14, ge=4, le=120, description="how high the lookout's floor is over the ground, in "
                                                          "blocks")
        ahead: int = Field(5, ge=0, le=64, description="how far in front of the player its door is, in blocks; "
                                                       "not used at a place")
        sign: list[str] = Field(["{player}", "Sniper Nest"], max_length=4, description="a sign over the door: "
                                "up to four lines of up to 40 letters, {player} for the player's name; empty: "
                                "none")
        spawn: bool = Field(True, description="set each player's spawn on the lookout")

    class State(State):
        sites: list = []        # where each goes, fixed at the start: {who, dim, pos, yaw, placed}
        left: int = 0           # build calls still to come
        built: list = []        # [player, "x y z"] of each one, for the result

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if a.at is not None:
            sites = [{"who": names[0] if names else "", "dim": a.at.dim, "pos": list(a.at.pos), "yaw": a.at.yaw,
                      "placed": True}]
        else:
            sites = [{"who": p.name, "dim": p.dim, "pos": list(p.pos), "yaw": p.yaw, "placed": False}
                     for p in (ctx.world.player(n) for n in names) if p is not None]
        if not sites:
            self.result("no sniper nest: %s" % ("nobody is online" if a.players in ("@a", "@r")
                                                else a.players + " is not online"), players=[], nests=0)
            self.end()
            return
        self.state.sites = sites
        self.state.left = len(sites)
        for i in range(len(sites)):
            self.after(i * self.config.gap_s, "build_site", i)

    def build_site(self, ctx, i):
        """Queue one nest: its footing, tower, lookout, roof, slits, door, ladder, lantern and
        sign, and set its player's spawn once it is placed."""
        a, c = self.args, self.config
        site = self.state.sites[i]
        spot = Spot(dim=site["dim"], pos=tuple(site["pos"]), yaw=site["yaw"])

        def part(kind, width, depth, height, offset, blocks=c.blocks, hollow=False):
            return build_shape(self, ctx, spot, kind, width=width, height=height, depth=depth, blocks=blocks,
                               facing="look", offset=offset, hollow=hollow)

        h, w = a.height, c.width
        d = (0 if site["placed"] else a.ahead) + w // 2
        part("box", w, w, c.footing, [0, -(c.footing + 1), d])              # the footing
        tower = part("box", w, w, h + 1, [0, -1, d], hollow=True)           # the tower; its top is the floor
        top = part("box", w, w, c.lookout, [0, h - 1, d], hollow=True)      # the lookout's walls, roofed
        part("box", w + 2, w + 2, 1, [0, h + c.lookout - 2, d])             # the roof, one over each side
        back = BACK[tower.facing]
        for y in (h // 2, h + 1):                                          # slits half way up, windows on top
            part("box", 1 if y < h else 3, w, 1, [0, y, d], "minecraft:air")
            part("box", w, 1 if y < h else 3, 1, [0, y, d], "minecraft:air")
        part("box", 1, 1, 2, [0, 0, d - 2], "minecraft:air")                # the door, facing the player
        part("box", 1, 1, h, [0, 0, d + 1], "minecraft:ladder[facing=%s]" % back)  # up the far wall, through the
        part("box", 1, 1, 1, [-1, h, d - 1], "minecraft:lantern")          # floor
        if a.sign:                                                         # over the door, outside
            at = part("box", 1, 1, 1, [0, 2, d - 3], "minecraft:air")
            sign(self, ctx, at.a, [t.replace("{player}", site["who"]) for t in a.sign], facing=back, dim=at.dim)
        x, y, z = (top.a[0] + top.b[0]) // 2, top.a[1] + 1, (top.a[2] + top.b[2]) // 2
        if a.spawn and site["who"]:
            self.spawnpoint(site["who"], Spot(dim=top.dim, pos=(x, y, z)))
        where = "%d %d %d" % (x, y, z) + ("" if top.dim == "minecraft:overworld" else " in " + top.dim.split(":")[-1])
        self.state.built = self.state.built + [[site["who"], where]]
        self.step_done(ctx)

    def step_done(self, ctx):
        self.state.left -= 1
        if not self.state.left and self.blocks.done:     # nothing left to place: done now
            self.done(ctx)

    def done(self, ctx):                                  # the block queue placed the last of it
        if self.state.left:                               # more is still to be queued
            return
        a = self.args
        n = len(self.state.built)
        what = "sniper nest" if n == 1 else "sniper nests"
        at = "; ".join(("%s's at %s" % (w, at)) if w else "at " + at for w, at in self.state.built)
        summary = "built %d %s: %s%s" % (n, what, at, ", spawn set there" if a.spawn else "")
        self.result(summary, players=[w for w, _ in self.state.built], at=[at for _, at in self.state.built],
                   height=a.height, spawn=a.spawn, nests=n)
        self.end()
