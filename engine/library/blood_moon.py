"""Blood Moon: one of the Demon Lord's rounds, started and owned by it (demon_lord.py). Darkness
and hunger on every player, a mixed wave of undead and arachnids round each of them (glowing, to
help below round 6), midnight and thunder held for as long as it lasts, a warden's heartbeat every
7 seconds, six times, then it ends on its own."""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import mob_nbt, safe_spots, speak

MOBS = ["minecraft:zombie", "minecraft:skeleton", "minecraft:spider", "minecraft:husk", "minecraft:witch"]


class BloodMoon(Scenario):
    class Config(ScenarioConfig):
        summary = ("Darkness and hunger on every player, undead and spiders round each of them (glowing below "
                   "level 6), midnight and thunder held, a warden's heartbeat every 7 s, six times, then it ends.")
        beats: int = Field(6, ge=1, le=20)        # how many heartbeats
        beat_s: float = Field(7, ge=1, le=60)     # seconds between them
        duration_s = 6 * 7 + 3                    # beats * beat_s, plus a little room; see Config.beats/beat_s
        gap_s: float = Field(0.2, ge=0.1, le=2)   # between one player's spawns and the next's (each is a call)
        spread: float = Field(12, ge=4, le=64)
        glow_below: int = Field(6, ge=0, le=100, description="below this level, its mobs glow")

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how fierce it is: more mobs round each player "
                           "(3 + level // 2); below `glow_below` they glow, to help find them")
        players: Players = Field("@a", description="who it happens to: @a, @r, or names")

    class State(State):
        rounds: int = 0        # players whose spawning is still due this start
        beat: int = 0

    def start(self, ctx):
        a = self.args
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no blood moon: %s" % ("nobody is online" if who in ("@a", "@r") else who + " is not online"),
                        players=[], count=0, failed=True)
            self.end()
            return
        self.hold("time", "midnight")
        self.hold("weather", "thunder")
        speak(self, ctx, "BLOOD MOON", voice="", color="dark_red", where="title")
        speak(self, ctx, "the dark is hungry", voice="", color="gray", where="subtitle")
        speak(self, ctx, "BLOOD MOON. You cannot see me, but I see you. Level %d." % a.level)
        for name in names:
            p = ctx.world.player(name)
            self.sound("minecraft:entity.warden.emerge", p.pos, volume=4, pitch=0.7, to=[name], dim=p.dim)
        self.state.rounds = len(names)
        for i, name in enumerate(names):
            self.after(i * self.config.gap_s, "spawn_round", name)
        self.after(self.config.beat_s, "heartbeat")

    def spawn_round(self, ctx, name):
        a, c = self.args, self.config
        p = ctx.world.player(name)
        if p is not None and p.alive:
            self.potion("darkness", 45, 0, who=name)
            self.potion("hunger", 30, 1, who=name)
            n = 3 + a.level // 2
            glow = a.level < c.glow_below
            for spot in safe_spots(ctx, p, n, c.spread):
                self.spawn(random.choice(MOBS), spot, dim=p.dim, nbt=mob_nbt(glowing=glow))
        self.state.rounds -= 1

    def heartbeat(self, ctx):
        self.state.beat += 1
        for name in players(ctx, self.args.players):
            p = ctx.world.player(name)
            if p is not None and p.alive:
                self.sound("minecraft:entity.warden.heartbeat", p.pos, volume=1, pitch=0.5, to=[name], dim=p.dim)
        if self.state.beat < self.config.beats:
            self.after(self.config.beat_s, "heartbeat")
        else:
            count = len(self.spawned)
            self.result("blood moon over: %d mob%s round %s" % (count, "" if count == 1 else "s", self.args.players),
                        players=self.args.players, count=count)
            self.end()
