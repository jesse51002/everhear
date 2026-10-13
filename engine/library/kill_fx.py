"""Kill FX: sounds each player hears only themselves: a sting when they die, a chime per kill that
rises through a streak (a twinkle at three or more), a hit marker when they deal damage, and a
thud when they take it. (The old kill_fx script and action.)"""

from typing import Literal

from vfg_engine import Args, Death, Field, Hit, Hurt, Kill, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED


def matches(players_arg, name):
    """Whether `name` matches a Players arg, by name alone: unlike `players(ctx, arg)`, which lists
    who is alive now (wrong here: the one who just died is never alive in that same event)."""
    s = (players_arg or "@a").strip()
    if s in ("@a", "@r"):
        return True
    return name.lower() in {n.strip().lower() for n in s.split(",") if n.strip()}


class KillFx(Scenario):
    class Config(ScenarioConfig):
        summary = "Sounds only each player hears: a sting on death, a rising kill-streak chime, a hit marker, a hurt thud."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="who hears them: @a, @r, or names")
        on: Literal["death", "kill", "both"] = Field("both", description="death (a sting), kill (a streak "
                                                     "chime), or both")
        streak_s: float = Field(8, ge=1, le=60, description="kills this many seconds apart or closer make a "
                                                            "streak, each chime higher; three or more add a "
                                                            "twinkle")
        hit_markers: bool = Field(True, description="a hit marker sound each time the player deals damage")
        hurt_sound: bool = Field(True, description="a thud each time the player takes damage")
        volume: float = Field(1, ge=0.1, le=4, description="how loud every sound is: 1 normal, 2 twice as loud")

    class State(State):
        streak: dict = {}         # player -> their current kill streak
        streak_at: dict = {}      # player -> ctx.now of their last kill

    def start(self, ctx):
        if self.args.on != "kill":
            self.listen(Death, "on_death")
        if self.args.on != "death":
            self.listen(Kill, "on_kill")
        if self.args.hit_markers:
            self.listen(Hit, "on_hit")
        if self.args.hurt_sound:
            self.listen(Hurt, "on_hurt")

    def on_death(self, ctx, ev):
        if not matches(self.args.players, ev.who):
            return
        self.state.streak = {**self.state.streak, ev.who: 0}
        self.play(ev.who, "minecraft:entity.player.death", 1, 0.7)

    def on_kill(self, ctx, ev):
        if not matches(self.args.players, ev.who):
            return
        last = self.state.streak_at.get(ev.who, 0.0)
        n = self.state.streak.get(ev.who, 0) + 1 if ctx.now - last < self.args.streak_s else 1
        self.state.streak = {**self.state.streak, ev.who: n}
        self.state.streak_at = {**self.state.streak_at, ev.who: ctx.now}
        self.play(ev.who, "minecraft:entity.experience_orb.pickup", 1, min(0.8 + 0.2 * n, 2.0))
        if n >= 3:
            self.play(ev.who, "minecraft:entity.firework_rocket.twinkle", 1, 1)

    def on_hit(self, ctx, ev):
        if matches(self.args.players, ev.who):
            self.play(ev.who, "minecraft:entity.arrow.hit_player", 0.6, 1.6)

    def on_hurt(self, ctx, ev):
        if matches(self.args.players, ev.who):
            self.play(ev.who, "minecraft:entity.player.hurt_sweet_berry_bush", 0.5, 0.6)

    def play(self, p, sound, volume, pitch):
        """A sound only `p` hears, at their own volume setting."""
        self.sound(sound, at=p, volume=volume * self.args.volume, pitch=pitch, to=[p])
