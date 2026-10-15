"""Castle: a castle built in front of each player, its gate facing them, or one at a place: walls
with battlements, a round tower with a pyramid roof on each corner, a barred gate, a keep at the
back of the yard, a sign over the gate and a staircase carved down, if asked; any block or mix,
any size. It ends when its last block is placed; the blocks stay. Named, it saves the yard's
middle as a place.

The ice castle (the old ice_castle action) is walls="packed_ice", towers="blue_ice". plan="mansion"
builds KrownJM's mansion instead (made/main's krownjm_mansion): two floors, a dark oak roof, rooms,
a stair, a door, windows and lights.

Where each one goes is fixed when it starts (each player's feet and facing). Then each castle, and
its staircase, is queued in a call of its own, a moment apart: the biggest castle with its 380
steps is over 10 ms of queuing, and several in one call would pass the 50 ms cut."""

import math
from typing import Literal

from vfg_engine import Args, BlockMix, Field, Place, Players, Scenario, ScenarioConfig, Spot, State, players
from vfg_engine.kit import BACK, build_shape, sign

YAW = {"south": 0.0, "west": 90.0, "north": 180.0, "east": -90.0}    # the yaw that faces each way


class Castle(Scenario):
    class Config(ScenarioConfig):
        summary = ("Builds a castle in front of each player (or one at a place): walls, four towers, a gate facing "
                   "them, a keep, a sign, stairs down; plan mansion builds a two-floor mansion instead.")
        duration_s = 300                                # the most a build may take; it ends when the last block is down
        gap_s: float = Field(0.1, ge=0.1, le=2)         # between one queuing call and the next (a castle, its stairs)
        mansion_width: int = Field(31, ge=25, le=41)    # across; the rooms, windows and stair are laid out for 31
        mansion_depth: int = Field(23, ge=19, le=31)    # front to back
        mansion_floor: int = Field(6, ge=5, le=8)       # each floor's height

    class Args(Args):
        players: Players = Field("@a", description="who gets one, in front of them, its gate facing them: @a, @r "
                                                   "or names")
        at: Place = Field("", description="where it goes instead: a saved place, x y z, or a player; one castle there, "
                                          "its gate on that spot (empty: in front of each player)")
        plan: Literal["castle", "mansion"] = Field("castle", description="castle, or mansion: KrownJM's two-floor "
                                                   "mansion (it reads only players, at, ahead, facing and name)")
        size: int = Field(23, ge=11, le=101, description="how wide the castle is, side to side and front to back, in "
                                                         "blocks (an even size is made odd)")
        height: int = Field(7, ge=4, le=40, description="how high its walls are; the towers rise half as high again")
        walls: BlockMix = Field("stone_bricks", description='what the walls, keep and floor are made of: a block or a '
                                                            'mix, "70% stone_bricks 30% cobblestone" (ice castle: '
                                                            'packed_ice)')
        towers: BlockMix = Field("cobblestone", description="what the towers and their roofs are made of (ice castle: "
                                                            "blue_ice)")
        ahead: int = Field(6, ge=0, le=64, description="how far in front of the player its gate is; not used at a "
                                                       "place")
        facing: Literal["look", "north", "east", "south", "west"] = Field(
            "look", description="the way it faces from the gate in: look (the way the player, or a saved place, faces; "
                                "x y z faces south), or a compass way")
        sign: list[str] = Field([], max_length=4, description="a sign over the gate: up to four lines of up to 40 "
                                                              "letters, {player} for the player's name; empty: none")
        stairs: int = Field(0, ge=0, le=380, description="a staircase from the yard this many blocks down, carved "
                                                         "through the ground (0: none)")
        name: str = Field("", max_length=32, pattern=r"^[A-Za-z0-9_ -]*$",
                          description="save the yard's middle as a place by this name (several castles: name_player); "
                                      "empty: not saved")

    class State(State):
        sites: list = []        # where each goes, fixed at the start: {who, dim, pos, yaw, placed}
        left: int = 0           # build calls still to come
        built: list = []        # [player, "x y z"] of each one, for the result
        saved: list = []        # the places it saved

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if a.at is not None:                            # one, at the place: its sign names the first player
            sites = [{"who": names[0] if names else "", "dim": a.at.dim, "pos": list(a.at.pos), "yaw": a.at.yaw,
                      "placed": True}]
        else:
            sites = [{"who": p.name, "dim": p.dim, "pos": list(p.pos), "yaw": p.yaw, "placed": False}
                     for p in (ctx.world.player(n) for n in names) if p is not None]
        if not sites:
            self.result("no %s: %s" % (a.plan, "nobody is online" if a.players in ("@a", "@r")
                                       else a.players + " is not online"), players=[], built=0, failed=True)
            self.end()
            return
        self.state.sites = sites
        steps = []
        for i in range(len(sites)):
            steps.append(("build_site", i))
            if a.stairs and a.plan == "castle":
                steps.append(("build_stairs", i))
        self.state.left = len(steps)
        for k, (method, i) in enumerate(steps):
            self.after(k * self.config.gap_s, method, i)

    def build_site(self, ctx, i):
        """Queue one castle (or mansion), and save its place when named."""
        a = self.args
        site = self.state.sites[i]
        spot = Spot(dim=site["dim"], pos=tuple(site["pos"]), yaw=site["yaw"])
        plan = self.mansion if a.plan == "mansion" else self.castle
        middle, way = plan(ctx, site["who"], spot, site["placed"])
        where = "%d %d %d" % middle + ("" if spot.dim == "minecraft:overworld" else " in " + spot.dim.split(":")[-1])
        self.state.built = self.state.built + [[site["who"], where]]
        if a.name:
            name = a.name if len(self.state.sites) == 1 else ("%s_%s" % (a.name, site["who"]))[:32]
            self.state.saved = self.state.saved + [self.save_place(name, Spot(dim=spot.dim, pos=middle, yaw=YAW[way]))]
        self.step_done(ctx)

    def build_stairs(self, ctx, i):
        """Queue one castle's staircase, carved down from its yard."""
        a = self.args
        site = self.state.sites[i]
        spot = Spot(dim=site["dim"], pos=tuple(site["pos"]), yaw=site["yaw"])
        size, n = a.size | 1, a.stairs
        d = (0 if site["placed"] else a.ahead) + size // 2
        self.part(ctx, spot, "staircase_down", max(3, size // 5), n, n, [0, -n, d - size // 2 + 2 + n // 2], a.walls,
                  hollow=True, load=True)
        self.step_done(ctx)

    def step_done(self, ctx):
        self.state.left -= 1
        if not self.state.left and self.blocks.done:     # nothing left to place: done now
            self.done(ctx)

    def done(self, ctx):                                 # the block queue placed the last of it
        if self.state.left:                              # more is still to be queued
            return
        a = self.args
        n = len(self.state.built)
        what = ("a %s" % a.plan) if n == 1 else ("%d %ss" % (n, a.plan))
        how = ("%d wide, of %s" % (a.size | 1, a.walls)) if a.plan == "castle" else (
            "%d by %d, two floors" % (self.config.mansion_width, self.config.mansion_depth))
        at = "; ".join(("%s's at %s" % (w, at)) if w else "at " + at for w, at in self.state.built)
        summary = "built %s, %s: %s" % (what, how, at)
        if self.state.saved:
            summary += "; saved as %s" % ", ".join(self.state.saved)
        size = a.size | 1 if a.plan == "castle" else [self.config.mansion_width, self.config.mansion_depth]
        self.result(summary, players=[w for w, _ in self.state.built], at=[at for _, at in self.state.built],
                    plan=a.plan, size=size, built=n, places=self.state.saved, failed=not self.state.built)
        self.end()

    # ---- the plans: each part is a kit shape, placed by [right, up, forward] from the spot -----

    def part(self, ctx, spot, kind, width, depth, height, offset, blocks, hollow=False, load=False):
        return build_shape(self, ctx, spot, kind, width=width, height=height, depth=depth, blocks=blocks,
                           facing=self.args.facing, offset=offset, hollow=hollow, load=load)

    def castle(self, ctx, who, spot, placed):
        """The castle round the spot: its front, the gate, `ahead` blocks in front (0 at a place).
        Returns the yard's middle and the way it faces."""
        a = self.args
        size, h = a.size | 1, a.height                  # odd, so it has a middle
        s, d = size // 2, (0 if placed else a.ahead) + size // 2
        tw, th = 5 + 2 * (size // 30), h + max(4, h // 2)            # a tower's width and height
        gw, gh = 5 + 4 * (size // 40), min(h - 2, 4 + size // 30)    # the gate's

        def part(kind, width, depth, height, offset, blocks=a.walls, hollow=False):
            return self.part(ctx, spot, kind, width, depth, height, offset, blocks, hollow)

        room = part("box", size + 4, size + 4, th + 4, [0, 0, d], "minecraft:air")   # room: trees and hills go
        m = tw // 2 + 2                                                         # the towers stand out this far
        self.blocks.forceload((room.a[0] - m, room.a[1], room.a[2] - m), (room.b[0] + m, room.b[1], room.b[2] + m),
                              dim=room.dim)                                     # loaded until it ends
        floor = part("box", size + 4, size + 4, 1, [0, -1, d])
        for right, fwd, w, dp in ((0, -s, size, 1), (0, s, size, 1), (-s, 0, 1, size), (s, 0, 1, size)):
            part("box", w, dp, h, [right, 0, d + fwd])                          # the four walls
        for k in range(-s, s + 1, 2):                                           # battlements, every other block
            for off in ([k, h, d - s], [k, h, d + s], [-s, h, d + k], [s, h, d + k]):
                part("box", 1, 1, 1, off)
        for sx in (-1, 1):                                                      # a round tower on each corner, its
            for sz in (-1, 1):                                                  # roof, and a door from the yard
                part("cylinder", tw, tw, th + 1, [sx * s, -1, d + sz * s], a.towers, hollow=True)
                part("pyramid", tw + 2, tw + 2, tw // 2 + 2, [sx * s, th, d + sz * s], a.towers)
                part("box", 1, 1, 2, [sx * (s - tw // 2), 0, d + sz * (s - 1)], "minecraft:air")
        part("box", gw, 1, gh, [0, 0, d - s], "minecraft:air")                 # the gate, facing the player,
        part("box", gw, 1, 1, [0, gh - 1, d - s], "minecraft:iron_bars")       # bars across its top
        kw, kd = (size // 2) | 1, (size // 4) | 1                               # the keep, at the back of the yard
        part("box", kw, kd, h + 2, [0, -1, d + s - 1 - kd // 2 - 1], hollow=True)
        part("box", 1, 1, 2, [0, 0, d + s - kd - 1], "minecraft:air")
        if a.sign:                                                              # a sign over the gate, outside
            at = part("box", 1, 1, 1, [0, gh, d - s - 1], "minecraft:air")
            sign(self, ctx, at.a, [t.replace("{player}", who) for t in a.sign], facing=BACK[at.facing], dim=at.dim)
        return (math.floor((floor.a[0] + floor.b[0]) / 2), floor.b[1] + 1,
                math.floor((floor.a[2] + floor.b[2]) / 2)), floor.facing

    def mansion(self, ctx, who, spot, placed):
        """KrownJM's mansion: its front `ahead` blocks in front (0 at a place), two hollow floors, a
        stepped roof, two partitions, a stairwell and stair, a door, windows and ceiling lights.
        Returns the middle of its hall and the way it faces."""
        c = self.config
        w, dd, fh = c.mansion_width, c.mansion_depth, c.mansion_floor
        mid = (0 if placed else self.args.ahead) + dd // 2
        front = mid - dd // 2

        def part(kind, width, depth, height, offset, blocks, hollow=False, load=False):
            return self.part(ctx, spot, kind, width, depth, height, offset, blocks, hollow, load)

        room = part("box", w + 2, dd + 2, 2 * fh + 8, [0, 0, mid], "minecraft:air", load=True)
        part("box", w, dd, 1, [0, -1, mid], "minecraft:stone_bricks")                              # foundation
        part("box", w, dd, fh, [0, 0, mid], "70% stone_bricks 30% cobblestone", hollow=True)        # ground floor
        part("box", w, dd, fh, [0, fh, mid], "70% white_concrete 30% quartz_block", hollow=True)    # upper floor
        part("pyramid", w + 2, dd + 2, 8, [0, 2 * fh, mid], "minecraft:dark_oak_planks")            # roof
        for r in (-5, 5):                                                        # rooms either side of a hall
            part("box", 1, dd - 2, fh - 1, [r, 1, mid], "minecraft:spruce_planks")
        part("box", 3, 5, 11, [9, 0, mid + 6], "minecraft:air")                 # a stairwell through both floors,
        part("staircase", 3, 5, fh, [9, 0, mid + 6], "minecraft:spruce_planks")  # and the stairs up
        for r in (-9, -3, 3, 9):                                                 # front windows, both floors
            part("box", 1, 1, 2, [r, 2, front], "minecraft:glass_pane")
            part("box", 1, 1, 2, [r, fh + 2, front], "minecraft:glass_pane")
        for f in (mid - 6, mid + 2):                                             # side windows
            for r in (-(w // 2), w // 2):
                part("box", 1, 2, 2, [r, 2, f], "minecraft:glass_pane")
                part("box", 1, 2, 2, [r, fh + 2, f], "minecraft:glass_pane")
        for r in (-7, 0, 7):                                                     # lights on the ceilings
            part("box", 1, 1, 1, [r, fh - 1, mid + 4], "minecraft:glowstone")
            part("box", 1, 1, 1, [r, 2 * fh - 1, mid + 4], "minecraft:glowstone")
        door = part("box", 1, 1, 2, [0, 1, front], "minecraft:air")              # the doorway in the front wall
        for half, y in (("lower", door.a[1]), ("upper", door.a[1] + 1)):
            self.blocks.setblock((door.a[0], y, door.a[2]), "minecraft:oak_door[facing=%s,half=%s,hinge=left,"
                                 "open=false]" % (door.facing, half), dim=door.dim)
        return (math.floor((room.a[0] + room.b[0]) / 2), room.a[1] + 1,          # on the ground floor, in the hall
                math.floor((room.a[2] + room.b[2]) / 2)), room.facing
