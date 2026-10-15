"""Defend Gregor: one of the Demon Lord's rounds, started and owned by it (demon_lord.py). A hut
rises near the party with Old Gregor sheltering inside and a chest of patching supplies; a horde
comes for him every 12 seconds. Keep him alive the whole time and everyone is rewarded; let him die
and his killers stay, a punishment left for the party to finish off.

# NEEDS: a world border a scenario can hold (centre, size, damage, warning) and put back when it
# ends (as closing_ring.py's module docstring says); `self.command` is the escape hatch meanwhile,
# and `stop` widens it back out by hand. A spreadplayers-like scatter is not exposed either; the
# column's own ground height (`ctx.world.ground_y`) under the chosen point stands in."""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import chest, mob_nbt, safe_spots, speak

MOBS = ["minecraft:zombie", "minecraft:zombie", "minecraft:vindicator", "minecraft:husk"]


class DefendGregor(Scenario):
    class Config(ScenarioConfig):
        summary = ("A hut and Old Gregor rise near the party, a horde coming for him every 12 s; keep him alive "
                   "the time limit through and everyone is rewarded, or his killers stay as a punishment.")
        duration_s = 15 + 60 + 1000 // 3 + 20     # a generous cap: prep, then the worst-case wait, plus room
        offset: float = Field(6, ge=0, le=32)      # the hut sits this far from the party, right and forward
        border_size: int = Field(44, ge=10, le=200)
        border_damage: float = Field(2, ge=0, le=10)
        prep_s: float = Field(12, ge=1, le=60)     # before the horde starts coming
        min_time_s: float = Field(60, ge=10, le=600)
        per_level_s: float = Field(5, ge=0, le=30)
        wave_every_s: float = Field(12, ge=2, le=60)
        wave_spread: float = Field(14, ge=4, le=64)
        gregor_health: float = Field(30, ge=1, le=100)

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how long Gregor must survive (60 + 5 x level) and "
                           "how many come for him each wave (2 + level // 3)")
        players: Players = Field("@a", description="who defends him: @a, @r, or names")

    class State(State):
        at: list = []
        dim: str = "minecraft:overworld"
        gregor: str = ""
        end_at: float = 0.0

    def start(self, ctx):
        a, c = self.args, self.config
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no defend Gregor: %s" % ("nobody is online" if who in ("@a", "@r")
                                                   else who + " is not online"), players=[], survived=None)
            self.end()
            return
        p0 = ctx.world.player(names[0])
        dim = p0.dim
        x, z = p0.pos[0] + c.offset, p0.pos[2] + c.offset
        y = ctx.world.ground_y(x, z, dim=dim)
        if y is None:
            y = p0.pos[1]
        at = (round(x), y, round(z))
        self.state.at, self.state.dim = list(at), dim
        self.command("worldborder center %d %d" % (at[0], at[2]))
        self.command("worldborder damage amount %g" % c.border_damage)
        self.command("worldborder set %d" % c.border_size)
        self.build_hut(at, dim)
        self.state.gregor = self.spawn("minecraft:villager", (at[0], at[1] + 1, at[2]), dim=dim,
                                       nbt=mob_nbt(name="Old Gregor", health=c.gregor_health, glowing=True))
        chest(self, ctx, (at[0] + 1, at[1], at[2] + 1),
             ["oak_planks 64", "oak_door 4", "arrow 32", "golden_apple 1"], dim=dim)
        t = c.min_time_s + c.per_level_s * a.level
        self.state.end_at = ctx.now + t + c.prep_s
        speak(self, ctx, "DEFEND GREGOR", voice="", color="green", where="title")
        speak(self, ctx, "MINIGAME: Old Gregor shelters in that hut. My horde wants him dead. Keep him alive "
                        "for %d seconds. Patch the doors fast!" % int(t))
        for name in names:
            q = ctx.world.player(name)
            self.sound("minecraft:entity.wither.spawn", q.pos, volume=0.6, pitch=0.7, to=[name], dim=q.dim)
        self.after(c.prep_s, "begin_horde")

    def build_hut(self, at, dim):
        x, y, z = at
        self.blocks.fill((x - 3, y, z - 3), (x + 3, y + 4, z + 3), "minecraft:oak_planks", dim=dim)
        self.blocks.fill((x - 2, y, z - 2), (x + 2, y + 3, z + 2), "minecraft:air", dim=dim)
        self.blocks.fill((x - 3, y - 1, z - 3), (x + 3, y - 1, z + 3), "minecraft:cobblestone", dim=dim)
        self.blocks.fill((x - 3, y, z), (x + 3, y + 1, z), "minecraft:air", dim=dim)
        self.blocks.fill((x, y, z - 3), (x, y + 1, z + 3), "minecraft:air", dim=dim)
        self.blocks.fill((x - 3, y + 2, z - 3), (x + 3, y + 2, z + 3), "minecraft:glass",
                        replace="minecraft:oak_planks", dim=dim)
        self.blocks.setblock((x, y + 3, z), "minecraft:lantern", dim=dim)

    def begin_horde(self, ctx):
        self.hold("time", "midnight")
        self.hold("weather", "rain")
        self.after(0.1, "wave")

    def wave(self, ctx):
        g = ctx.world.get(self.state.gregor)
        if g is None or g.health is None or g.health <= 0:
            self.finish(ctx, survived=False)
            return
        if ctx.now >= self.state.end_at:
            self.finish(ctx, survived=True)
            return
        n = 2 + self.args.level // 3
        for spot in safe_spots(ctx, self.state.at, n, self.config.wave_spread, dim=self.state.dim):
            # kept, not ended with this instance: the old script never killed its horde either way
            # (survived or not), leaving it for the party to finish off, or for demon_lord's own
            # 30 s no-kill recall to clear; see the module NEEDS for what plays that part here.
            self.spawn(random.choice(MOBS), spot, dim=self.state.dim, keep=True)
        self.after(self.config.wave_every_s, "wave")

    def finish(self, ctx, survived: bool):
        a = self.args
        names = players(ctx, a.players)
        if survived:
            for name in names:
                self.command("give %s emerald 8" % name)
                self.command("give %s golden_apple 1" % name)
            speak(self, ctx, "Gregor lives?! Curse you. He rewards his saviors.")
            if self.state.gregor in self.spawned:
                self.remove(self.state.gregor)    # the old script's own "kill the vip" once he is safe
            summary = "Gregor survived: %s rewarded" % (a.players)
        else:
            speak(self, ctx, "Old Gregor is DEAD. His screams were delightful. Your punishment: my children stay.")
            summary = "Gregor is dead: his killers stay"
        self.result(summary, players=a.players, survived=survived)
        self.end()

    def stop(self, ctx, reason):
        if self.state.at:
            # 59999968, not 60000000: Minecraft 26.3 refuses a border past 5.9999968E7 and silently
            # keeps the old (still narrowed) one when asked for more, leaving it never truly widened.
            self.command("worldborder set 59999968")
            self.command("worldborder center 0 0")
