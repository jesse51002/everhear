from actions._types import Action, Field, Literal, Live, Players


class KeepEffects(Action):
    """Keep an effect on players until stopped, such as speed five, night vision or glowing: given again within a second after a death or milk."""

    script = "keep_effects"
    runs = "until_stopped"
    takes = ["player", "effect", "level"]

    player: Live[Players] = "@a"
    """who keeps it: a name, several separated by commas, or @a; someone taken off loses it within a second"""
    effect: Live[Literal["speed", "slowness", "haste", "mining_fatigue", "strength", "jump_boost", "nausea",
                         "regeneration", "resistance", "fire_resistance", "water_breathing", "invisibility",
                         "blindness", "night_vision", "hunger", "weakness", "poison", "wither", "health_boost",
                         "absorption", "saturation", "glowing", "levitation", "luck", "unluck", "slow_falling",
                         "conduit_power", "dolphins_grace", "bad_omen", "hero_of_the_village", "darkness",
                         "trial_omen", "raid_omen", "wind_charged", "weaving", "oozing", "infested",
                         "breath_of_the_nautilus"]] = "speed"
    """the effect, as Minecraft names it; changing it takes the old one off"""
    level: Live[int] = Field(5, ge=1, le=128)
    """its level, 1 for level I (speed five is 5); a lower level takes the higher one off first"""
    particles: Live[bool] = False
    """show the effect's swirling particles round them"""
