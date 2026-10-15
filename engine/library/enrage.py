"""Enrage: a boss grows faster and stronger each time its health falls below a share of what it
had when it was granted (0.5, then 0.25 by default), with a roar and a warning to every player.
Faster is a movement_speed modifier (every mob has one, an ender dragon too, which takes no potion);
stronger is Strength (for whatever takes potions). Passive: granted at spawn to every boss_fight body (horde_king, dragon_head, ice_queen), it watches
its own body alone, so with several bodies (a three-headed dragon) each enrages on its own health,
not the bodies' total (the old boss() tracked one phase for the whole fight; this is simpler, and
each body still roars and buffs itself as it weakens). Its warning names the body by its own live
name (ctx.user.name: WRITING.md), whatever the boss was spawned or overridden as."""

from vfg_engine import UNTIL_STOPPED, Ability, AbilityConfig, Field, State
from vfg_engine.kit import speak


class Enrage(Ability):
    class Config(AbilityConfig):
        summary = "Faster and stronger each time its health falls below a share (0.5, then 0.25), with a roar."
        shares: list[float] = [0.5, 0.25]   # of its starting health, highest first; the old boss_phases
        speed_per_phase: float = Field(0.25, ge=0, le=2)   # its speed, up by this share of its own each phase
        damage = 0
        duration_s = UNTIL_STOPPED          # passive: runs for as long as it is granted
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    every = 1

    class State(State):
        full: float = 0.0     # its health the first tick it could be read (its starting health)
        phase: int = 0        # shares already crossed

    def tick(self, ctx):
        c = self.config
        health = ctx.user.health
        if health is None:
            return
        if not self.state.full:
            # NEEDS: a freshly spawned actor's body has no snapshot yet in the tick its abilities
            # are granted (start(ctx) here saw health None), so the baseline is taken on the first
            # tick it reads instead; a body already hurt within that first tick reads a bit low.
            self.state.full = health
            return
        full = self.state.full
        shares = sorted((s for s in c.shares if 0 < s < 1), reverse=True)
        if self.state.phase >= len(shares) or health >= full * shares[self.state.phase]:
            return
        self.state.phase += 1
        amp = self.state.phase - 1
        # faster: a movement_speed modifier, which every mob has (an ender dragon too: it takes no
        # potion at all, "immune to effects"); stronger: Strength, for whatever takes potions
        self.modifier("movement_speed", self.config.speed_per_phase * self.state.phase, "add_multiplied_base")
        self.keep_potion("strength", amp, seconds=5)
        self.sound("minecraft:entity.ravager.roar", volume=4, pitch=0.7)
        speak(self, ctx, "%s is enraged" % ctx.user.name, voice="", color="red", where="actionbar")
