"""Parkour of the Damned: one of the Demon Lord's rounds, started and owned by it
(demon_lord.py). A jumping course rises in the sky between a starting platform and a golden goal;
reach it before the time runs out (falling sends you back to the start) for a prize by place, then
everyone returns to where they stood. Every block placed, and fall damage and PvP, are put back
when this ends (`undo=True`, `self.hold`): the old script's own manual cleanup is the engine's
job now."""

import math
import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import speak

MATS = ["minecraft:red_nether_bricks", "minecraft:magma_block", "minecraft:blackstone",
       "minecraft:nether_wart_block", "minecraft:soul_sand", "minecraft:shroomlight"]
PRIZES = ["diamond 3", "golden_apple 2", "iron_ingot 8"]


class Parkour(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = ("A jumping course of the Demon Lord's own blocks between a start and a golden goal; reach "
                   "it in time (falling sends you back) for a prize by place. Blocks, fall damage and PvP all "
                   "go back to how they were.")
        duration_s = 90 + 5 * 1000 + 10           # the worst case (level 1000), plus a little room
        y: int = Field(230, ge=-60, le=315)        # how high the course sits
        base_steps: int = Field(10, ge=1, le=60)
        extra_step: float = Field(2, ge=0, le=30)  # a farther jump past this level (level > 6)
        web_level: int = Field(5, ge=0, le=1000)   # past this level, some steps get a cobweb above
        web_chance: float = Field(0.3, ge=0, le=1)
        min_time_s: float = Field(90, ge=10, le=600)
        per_level_s: float = Field(5, ge=0, le=60)
        goal_gap: float = Field(3, ge=1, le=20)    # between the last step and the goal platform

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how long it runs (90 + 5 x level) and how "
                           "treacherous (farther jumps past 6, cobwebs over a step past 5)")
        players: Players = Field("@a", description="who it tests: @a, @r, or names")

    class State(State):
        start: list = []
        goal: list = []
        dim: str = "minecraft:overworld"
        home: dict = {}          # name -> [x, y, z]: where to send them back
        winners: list = []       # names, in the order they finished
        end_at: float = 0.0

    def start(self, ctx):
        a, c = self.args, self.config
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no parkour: %s" % ("nobody is online" if who in ("@a", "@r") else who + " is not online"),
                        players=[], winners=[])
            self.end()
            return
        p0 = ctx.world.player(names[0])
        dim = p0.dim
        x, y, z = round(p0.pos[0]), c.y, round(p0.pos[2])
        self.state.dim = dim
        self.state.home = {n: list(ctx.world.player(n).pos) for n in names}
        self.place_platform(x, y, z, "minecraft:crying_obsidian", "minecraft:red_stained_glass",
                           skip=(2, 0), dim=dim)
        cx, cy, cz = x, y, z
        steps = c.base_steps + a.level // 2
        for _ in range(steps):
            cx += random.choice([2, 3, 3, 4] if a.level > 6 else [2, 3, 3])
            cz += random.choice([-1, 0, 1])
            cy += random.choice([0, 0, 1])
            self.blocks.setblock((cx, cy, cz), random.choice(MATS), undo=True, dim=dim)
            if a.level > c.web_level and random.random() < c.web_chance:
                self.blocks.setblock((cx, cy + 3, cz), "minecraft:cobweb", undo=True, dim=dim)
        cx += c.goal_gap
        gx, gy, gz = round(cx), cy, cz
        self.place_platform(gx, gy, gz, "minecraft:gold_block", "minecraft:yellow_stained_glass",
                           skip=(-2, 0), dim=dim)
        self.blocks.setblock((gx, gy + 1, gz), "minecraft:light_weighted_pressure_plate", undo=True, dim=dim)
        self.state.start = [x, y, z]
        self.state.goal = [gx, gy, gz]
        t = c.min_time_s + c.per_level_s * a.level
        self.state.end_at = ctx.now + t
        speak(self, ctx, "PARKOUR OF THE DAMNED", voice="", color="dark_red", where="title")
        speak(self, ctx, "MINIGAME: PARKOUR OF THE DAMNED. Reach the gold in %d seconds. Fall, and you begin "
                        "again. First one there wins my favor." % int(t))
        self.hold("fall_damage", False)
        self.hold("pvp", False)
        for name in names:
            self.teleport(name, (x, y + 1, z), dim=dim, yaw=-90.0, pitch=0.0)

    def tick(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        over = ctx.now >= self.state.end_at or len(self.state.winners) >= len(names)
        for name in names:
            if name in self.state.winners:
                continue
            p = ctx.world.player(name)
            if p is None or not p.alive or p.dim != self.state.dim:
                continue
            gx, gy, gz = self.state.goal
            if math.dist(p.pos, (gx, gy + 1, gz)) <= 2:
                self.state.winners = self.state.winners + [name]
                place = len(self.state.winners)
                speak(self, ctx, "%s conquers the parkour! Place #%d." % (name, place))
                self.command("give %s %s" % (name, PRIZES[min(place - 1, len(PRIZES) - 1)]))
            elif p.pos[1] < self.state.start[1] - 10:
                sx, sy, sz = self.state.start
                self.teleport(name, (sx, sy + 1, sz), dim=self.state.dim, yaw=-90.0, pitch=0.0)
        if over:
            self.finish(ctx, names)

    def finish(self, ctx, names):
        if not self.state.winners:
            speak(self, ctx, "Not one of you reached the gold. Pathetic.")
        for name, home in self.state.home.items():
            if name in names:
                self.teleport(name, home, dim=self.state.dim)
        self.result("the parkour ended: %s" % (", ".join(self.state.winners) or "nobody finished"),
                    players=self.args.players, winners=list(self.state.winners))
        self.end()

    def place_platform(self, x, y, z, floor: str, wall: str, skip, dim: str):
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                self.blocks.setblock((x + dx, y, z + dz), floor, undo=True, dim=dim)
                edge = abs(dx) == 2 or abs(dz) == 2
                if edge and (dx, dz) != skip:
                    self.blocks.setblock((x + dx, y + 1, z + dz), wall, undo=True, dim=dim)
