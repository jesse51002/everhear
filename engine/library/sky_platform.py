"""Sky Platform: a platform of a block or mix high in the sky over each player (or one over a
place, for them all), with a rim a block high and glowstone on its corners; each player is put on
it, facing as they were, and their spawn is set there. It ends when built; the platform stays."""

from vfg_engine import Args, BlockMix, Field, Place, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import build_shape

TOP = {"minecraft:overworld": 319, "minecraft:the_nether": 255, "minecraft:the_end": 255}


class SkyPlatform(Scenario):
    class Config(ScenarioConfig):
        summary = ("Puts each player (or all, at a place) on a platform of a block or mix high in the sky, "
                   "with a rim and glowstone corners, their spawn set there.")
        duration_s = 30
        rim: bool = True                                # a rim a block high round its edge, glowstone on its corners
        tp: bool = True                                  # put each player on it, facing as they were

    class Args(Args):
        players: Players = Field("@a", description="who gets one, high over where they stand: @a, @r or names")
        at: Place = Field("", description="over where instead: a saved place, x y z (its y not used), or a "
                                          "player; one platform there for all; empty: over each player")
        y: int = Field(200, ge=-60, le=317, description="how high its floor is, in blocks (kept under the top "
                                                        "of the world)")
        size: int = Field(9, ge=3, le=64, description="how wide it is, a square, in blocks (an even size is "
                                                      "made odd)")
        blocks: BlockMix = Field("obsidian", description='what its floor and rim are made of: a block or a '
                                                         'mix, "70% obsidian 30% crying_obsidian"')
        spawn: bool = Field(True, description="set each player's spawn on it")

    class State(State):
        sites: list = []        # {who, dim, a, b}: the floor each player gets (shared at a place)
        built: list = []        # [player, "x y z"] of each one, for the result

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if not names:
            self.result("no platform: %s" % ("nobody is online" if a.players in ("@a", "@r")
                                              else a.players + " is not online"), players=[])
            self.end()
            return
        if a.at is not None:
            feet = [(n, a.at.dim, a.at.pos) for n in names]
        else:
            feet = [(p.name, p.dim, p.pos) for p in (ctx.world.player(n) for n in names) if p is not None]
        if not feet:
            self.result("no platform: %s is not online" % a.players, players=[])
            self.end()
            return
        sites, floor = [], None
        for who, dim, pos in feet:
            if floor is None or a.at is None:
                at = (pos[0], min(float(a.y), TOP.get(dim, 319) - 3), pos[2])
                floor = build_shape(self, ctx, at, "box", a.size | 1, 1, a.size | 1, a.blocks, facing="south",
                                    dim=dim)
                if self.config.rim:
                    self.rim(dim, floor)
            sites.append({"who": who, "dim": dim, "a": list(floor.a), "b": list(floor.b)})
        self.state.sites = sites

    def rim(self, dim, floor):
        """A rim a block above the floor: its own border, the inside carved back out, glowstone
        on its four corners."""
        a, b = floor.a, floor.b
        y = a[1] + 1
        self.blocks.fill((a[0], y, a[2]), (b[0], y, b[2]), self.args.blocks, dim=dim)
        ia, ib = (a[0] + 1, y, a[2] + 1), (b[0] - 1, y, b[2] - 1)
        if ia[0] <= ib[0] and ia[2] <= ib[2]:
            self.blocks.fill(ia, ib, "minecraft:air", dim=dim)
        for cx in (a[0], b[0]):
            for cz in (a[2], b[2]):
                self.blocks.setblock((cx, y, cz), "minecraft:glowstone", dim=dim)

    def done(self, ctx):                                  # the block queue placed the last of it
        if not self.state.sites or self.state.built:      # nothing queued, or done() already ran
            return
        a, c = self.args, self.config
        how = "put on a sky platform" if c.tp else "has a sky platform"
        for site in self.state.sites:
            box_a, dim = site["a"], site["dim"]
            x, y, z = (site["a"][0] + site["b"][0]) // 2, box_a[1] + 1, (site["a"][2] + site["b"][2]) // 2
            if c.tp and site["who"]:
                self.teleport(site["who"], (x + 0.5, y, z + 0.5), dim=dim)
            if a.spawn and site["who"]:
                self.spawnpoint(site["who"], {"dim": dim, "pos": (x, y, z)})
            where = "%d %d %d" % (x, y, z) + ("" if dim == "minecraft:overworld" else " in " + dim.split(":")[-1])
            self.state.built = self.state.built + [[site["who"], where]]
        summary = "; ".join("%s %s at %s" % (p, how, at) for p, at in self.state.built) + (
            ", spawn set there" if a.spawn else "")
        self.result(summary, players=[p for p, _ in self.state.built], at=[at for _, at in self.state.built],
                   spawn=a.spawn)
        self.end()
