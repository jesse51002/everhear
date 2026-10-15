"""Horde: waves of mobs, each picked at random from a list, at safe spots round each player, never
more than a cap of them alive; after the last wave it ends when they are all dead, or with waves 0
it sends waves until stopped. Creepers are defused unless asked, and "Wave N" shows on the action
bar. A stop takes every mob it brought away.

It is three old scripts in one: the horde script (its defaults), the zoo (animals, waves 0,
every_s 1, per_player 20, cap 200, spread 8, announce false) and summon_around (stay true: one
wave, scaled, named or glowing, left in the world; it ends once they are out)."""

import math
import random

from vfg_engine import Args, EntityType, Field, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import mob_nbt, safe_spots, speak


class Horde(Scenario):
    every = 1                                   # tick(): count those alive, send a wave when one is due

    class Config(ScenarioConfig):
        summary = ("Waves of mobs at safe spots round each player, never over a cap alive; it ends when the last "
                   "wave is dead (waves 0: until stopped). A zoo: farm animals, waves 0, every_s 1, per_player 20, "
                   "cap 200, spread 8, announce false. Mobs left in the world: stay true.")
        duration_s = UNTIL_STOPPED              # it ends itself: its last wave dead, or at once with stay
        gap_s: float = Field(0.1, ge=0.1, le=2)         # between one player's spawns and the next's (each is a call)

    class Args(Args):
        players: Players = Field("@a", description="round whom: @a, @r, or names separated by commas")
        mobs: list[EntityType] = Field(["zombie"], min_length=1, max_length=20,
                                       description="the mobs a wave is made of, each one picked at random: zombie, "
                                                   "skeleton, creeper... (a zoo: pig, cow, sheep, chicken, ...)")
        per_player: int = Field(100, ge=1, le=100, description="how many mobs each wave brings round each "
                                                               "player")
        waves: int = Field(1, ge=0, le=1000, description="how many waves; after the last it ends once every mob is "
                                                         "dead (0: until stopped)")
        every_s: float = Field(30, ge=1, le=3600, description="seconds between waves")
        spread: int = Field(60, ge=4, le=100, description="how far round each player they appear, in blocks")
        cap: int = Field(300, ge=1, le=400, description="the most of its mobs alive at once; a wave brings fewer "
                                                        "to stay under it")
        explode: bool = Field(False, description="its creepers blow up; false: their blasts do nothing")
        announce: bool = Field(True, description='show "Wave N" on each player\'s screen at each wave')
        scale: float = Field(1, ge=0.25, le=8, description="their size: 1 is normal, 2 twice as big")
        name: str = Field("", max_length=32, description="a name shown over each one; empty: none")
        glowing: bool = Field(False, description="they glow, seen through walls")
        stay: bool = Field(False, description="leave them in the world: it ends once its waves are out, not when they "
                                              "are dead (a stop still takes them away)")

    class State(State):
        waves: int = 0                          # waves sent
        due: float = 0.0                        # when the next one is due (ctx.now)
        rounds: int = 0                         # players this wave has still to reach
        total: int = 0                          # mobs it summoned, every wave
        out: list = []                          # this wave's rounds: {player, pos, summoned}

    def start(self, ctx):
        self.wave(ctx)

    def tick(self, ctx):
        if self.state.rounds:                   # a wave is still going out
            return
        a = self.args
        if a.waves and self.state.waves >= a.waves:
            if not self.spawned:
                n = self.state.waves
                if self.state.total:
                    self.result("the horde round %s is dead, after %d wave%s" % (a.players, n, "" if n == 1 else "s"),
                                players=a.players, waves=n, mobs=self.kinds())
                    self.end()
                else:
                    self.no_room()
            return
        if ctx.now >= self.state.due:
            self.wave(ctx)

    def wave(self, ctx):
        """Send the next wave: one round per player online, each its own call, a moment apart."""
        names = players(ctx, self.args.players)
        if not names and self.state.waves == 0:
            who = self.args.players
            self.result("no horde: %s" % ("nobody is online" if who in ("@a", "@r") else who + " is not online"),
                        players=[], count=0, failed=True)
            self.end()
            return
        self.state.waves += 1
        self.state.due = ctx.now + self.args.every_s
        self.state.rounds = len(names)
        self.state.out = []
        for i, name in enumerate(names):
            self.after(i * self.config.gap_s, "spawn_round", name)

    def spawn_round(self, ctx, name):
        """This wave's mobs round one player, as many as the cap leaves room for."""
        a = self.args
        p = ctx.world.player(name)
        n = min(a.per_player, a.cap - len(self.spawned))
        summoned = 0
        if p is not None and p.alive and n > 0:
            # safe_spots' two probes are live reads: their wait has its own I/O budget, off the 50 ms cut
            for spot in safe_spots(ctx, p, n, a.spread):     # one wave mixes kinds: spots once, then each spawn
                kind = random.choice(a.mobs)
                self.spawn(kind, spot, dim=p.dim, nbt=self.nbt(kind), keep=a.stay)
                summoned += 1
            if a.announce:
                speak(self, ctx, "Wave %d" % self.state.waves, voice="", color="red", to=[name], where="actionbar")
            self.state.out = self.state.out + [{"player": name, "pos": [math.floor(c) for c in p.pos],
                                                "summoned": summoned}]
        self.state.total += summoned
        self.state.rounds -= 1
        if not self.state.rounds:
            self.wave_out(ctx)

    def wave_out(self, ctx):
        """The wave is out: the first one is reported; with stay, the last one ends it."""
        a = self.args
        last = a.waves and self.state.waves >= a.waves
        if self.state.waves != 1 and not (a.stay and last):
            return
        count = sum(r["summoned"] for r in self.state.out)
        where = ", ".join("%s at %d %d %d" % ((r["player"],) + tuple(r["pos"])) for r in self.state.out) or "nobody"
        summary = "summoned %d mobs (%s) round %s%s" % (count, ", ".join(self.kinds()), where, self.details())
        if a.stay and last and not self.state.total:
            self.no_room()
        elif a.stay and last:
            self.result(summary + "; they stay in the world", count=count, mobs=self.kinds(), rounds=self.state.out,
                        waves=self.state.waves)
            self.end()
        else:
            self.result(summary, count=count, mobs=self.kinds(), rounds=self.state.out, waves=a.waves)

    def no_room(self):
        a = self.args
        self.result("no horde: no safe ground within %d blocks of %s" % (a.spread, a.players), players=a.players,
                    waves=self.state.waves, count=0, failed=True)
        self.end()

    def nbt(self, kind):
        a = self.args
        defused = kind == "minecraft:creeper" and not a.explode
        return mob_nbt(name=a.name or None, scale=a.scale, glowing=a.glowing,
                       extra="ExplosionRadius:0b" if defused else None)

    def kinds(self):
        return [m.removeprefix("minecraft:") for m in self.args.mobs]

    def details(self):
        a = self.args
        d = (["named %s" % a.name] if a.name else []) + (["glowing"] if a.glowing else []) + (
            ["%gx size" % a.scale] if a.scale != 1 else [])
        return " (%s)" % ", ".join(d) if d else ""
