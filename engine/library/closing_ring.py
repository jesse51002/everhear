"""Closing Ring: one of the Demon Lord's rounds, started and owned by it (demon_lord.py). The
world border centres on the party and shrinks to a final size over a minute, undead and arachnids
arriving with each tightening; then it holds for 20 seconds more before it releases.

# NEEDS: a world border a scenario can hold (centre, size, damage, warning) and put back when it
# ends, same as a gamerule; `self.command` is the escape hatch meanwhile, and `stop` resets it to
# wide open by hand (the old script's own `border_off`, not what it was before this ran)."""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import safe_spots, speak

MOBS = ["minecraft:zombie", "minecraft:skeleton", "minecraft:spider", "minecraft:creeper"]


class ClosingRing(Scenario):
    class Config(ScenarioConfig):
        summary = ("The world border shrinks onto the party over a minute, mobs with every tightening, then "
                   "holds 20 s more before it releases. Needs a raw worldborder command: see the module docstring.")
        duration_s = 5 + 60 + 20 + 3           # settle_s + squeeze_s + hold_s, plus a little room
        open_size: int = Field(80, ge=20, le=1000)
        settle_s: float = Field(5, ge=1, le=30)      # before the squeeze begins
        squeeze_s: float = Field(60, ge=10, le=300)  # how long the border takes to close
        wave_every_s: float = Field(10, ge=2, le=60)
        hold_s: float = Field(20, ge=5, le=120)      # held at its final size before it releases
        spread: float = Field(5, ge=1, le=32)
        warn_distance: int = Field(10, ge=0, le=64)
        damage_amount: float = Field(1, ge=0, le=10)

    class Args(Args):
        level: int = Field(1, ge=1, le=1000, description="how tight the final ring is "
                           "(max(6, 16 - level // 2)) and how many mobs come with each tightening (1 + level // 4)")
        players: Players = Field("@a", description="who it closes on: @a, @r, or names")

    class State(State):
        center: list = []
        dim: str = "minecraft:overworld"
        final: int = 0
        wave: int = 0
        opened: bool = False    # whether it ever touched the border (stop() only puts it back then)

    def start(self, ctx):
        a = self.args
        names = [n for n in players(ctx, a.players) if (p := ctx.world.player(n)) is not None and p.alive]
        if not names:
            who = a.players
            self.result("no closing ring: %s" % ("nobody is online" if who in ("@a", "@r")
                                                 else who + " is not online"), players=[], count=0)
            self.end()
            return
        p0 = ctx.world.player(names[0])
        self.state.center, self.state.dim = list(p0.pos), p0.dim
        self.state.final = max(6, 16 - a.level // 2)
        speak(self, ctx, "THE RING CLOSES", voice="", color="dark_red", where="title")
        speak(self, ctx, "there is nowhere to run", voice="", color="gray", where="subtitle")
        speak(self, ctx, "THE RING CLOSES. Stay inside the border or wither away. Level %d." % a.level)
        self.voice_sound(ctx, names, "minecraft:block.end_portal.spawn")
        c = self.state.center
        self.command("worldborder center %.0f %.0f" % (c[0], c[2]))
        self.command("worldborder damage amount %g" % self.config.damage_amount)
        self.command("worldborder damage buffer 0")
        self.command("worldborder warning distance %d" % self.config.warn_distance)
        self.command("worldborder set %d" % self.config.open_size)
        self.state.opened = True
        for name in names:
            self.spawnpoint(name, c)        # respawn inside it, same as the old spawnpoint @a
        self.after(self.config.settle_s, "begin_squeeze")

    def begin_squeeze(self, ctx):
        self.command("worldborder set %d %g" % (self.state.final, self.config.squeeze_s))
        self.state.wave = 0
        self.after(self.config.wave_every_s, "squeeze_wave")

    def squeeze_wave(self, ctx):
        a, c, i = self.args, self.config, self.state.wave
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None or not p.alive:
                continue
            n = 1 + a.level // 4
            for spot in safe_spots(ctx, p, n, c.spread):
                self.spawn(random.choice(MOBS), spot, dim=p.dim)
            self.sound("minecraft:entity.warden.heartbeat", p.pos, volume=1, pitch=0.6 + i * 0.1, to=[name],
                      dim=p.dim)
        self.state.wave += 1
        waves = int(c.squeeze_s // c.wave_every_s)
        if self.state.wave < waves:
            self.after(c.wave_every_s, "squeeze_wave")
        else:
            self.after(0.1, "holding")

    def holding(self, ctx):
        speak(self, ctx, "The ring holds you... survive %d more seconds." % int(self.config.hold_s))
        self.voice_sound(ctx, players(ctx, self.args.players), "minecraft:entity.warden.roar")
        self.after(self.config.hold_s, "release_ring")

    def release_ring(self, ctx):
        # self.end() below calls stop(ctx, reason), which opens the border back up: no need to do it twice.
        speak(self, ctx, "RELEASED", voice="", color="gold", where="title")
        speak(self, ctx, "for now", voice="", color="gray", where="subtitle")
        speak(self, ctx, "The border breaks. You live... for now.")
        self.voice_sound(ctx, players(ctx, self.args.players), "minecraft:block.beacon.deactivate")
        count = len(self.spawned)
        self.result("the ring closed to %d blocks and broke: %d mob%s round %s"
                    % (self.state.final, count, "" if count == 1 else "s", self.args.players),
                    players=self.args.players, final=self.state.final, count=count)
        self.end()

    def stop(self, ctx, reason):
        if self.state.opened:
            self._open_border()

    def _open_border(self):
        # 59999968, not 60000000: Minecraft 26.3 refuses a border past 5.9999968E7 and silently
        # keeps the old (still shrunk) one when asked for more, leaving it never truly reopened.
        self.command("worldborder set 59999968")
        self.command("worldborder center 0 0")

    def voice_sound(self, ctx, names, sound):
        """The old local `speak()`'s own sound, heard by each player at their own position
        (`execute as @a at @s run playsound <sound> hostile @s ~ ~ ~ 0.6 0.7`)."""
        for name in names:
            p = ctx.world.player(name)
            if p is not None and p.alive:
                self.sound(sound, p.pos, volume=0.6, pitch=0.7, to=[name], dim=p.dim)
