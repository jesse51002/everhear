"""Potion effect: keeps one potion effect at a level on its bearer until cleared, given again
before it runs out so it survives a death or milk. Port of the old keep_effects action/script,
whose own 1-second loop of `effect give`/`effect clear` (keep_effects/main.py) is now
`self.keep_potion`, the engine's own job: a beacon-style potion given again every couple of
seconds while the mark lasts, which the engine never clears itself. Which players carry it, and
for how long, is no longer this class's business either: the brain applies (`engine_apply`) or
clears (`engine_clear`) the mark on whichever bearer it wants, or a scenario does the same with
`self.apply`/`self.state`; reapplying it on the same bearer refreshes it (Config.again, the
default), same as the old loop giving a changed effect or level to someone already in its set."""

from typing import Literal

from vfg_engine import Args, Field, Mark, MarkConfig, UNTIL_STOPPED

# Every potion effect Minecraft 26.3 has, except instant_damage and instant_health: those are
# given once, not kept (the old keep_effects action's own list, keep_effects/main.py:17-20).
EFFECTS = Literal[
    "speed", "slowness", "haste", "mining_fatigue", "strength", "jump_boost", "nausea",
    "regeneration", "resistance", "fire_resistance", "water_breathing", "invisibility",
    "blindness", "night_vision", "hunger", "weakness", "poison", "wither", "health_boost",
    "absorption", "saturation", "glowing", "levitation", "luck", "unluck", "slow_falling",
    "conduit_power", "dolphins_grace", "bad_omen", "hero_of_the_village", "darkness",
    "trial_omen", "raid_omen", "wind_charged", "weaving", "oozing", "infested",
    "breath_of_the_nautilus",
]


class PotionEffect(Mark):
    class Config(MarkConfig):
        summary = "Keeps one potion effect at a level on the bearer until cleared."
        duration_s = UNTIL_STOPPED
        damage = 0
        on_death = "keep"        # given again within a couple of seconds either way (keep_potion)
        on_milk = "keep"
        particles: bool = False  # show the effect's swirling particles round the bearer

    class Args(Args):
        effect: EFFECTS = Field("speed", description="the effect, as Minecraft names it")
        amplifier: int = Field(4, ge=0, le=127,
                               description="its level, 0 for level I (speed five is amplifier 4)")

    def start(self, ctx):
        self.keep_potion(self.args.effect, self.args.amplifier, particles=self.config.particles)
