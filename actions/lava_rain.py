from actions._types import Action, Field, Live, ParticleId, Players, preset


class LavaRain(Action, preset("sky_above")):
    """Make it rain lava over the players: drops of lava and flame fall out of the sky round each, only to look at, no fire, no harm."""

    script = "lava_rain"
    runs = "until_stopped"
    takes = ["player", "drops", "sky_count", "sky_radius", "every"]
    defaults = {"sky_height": 15, "sky_band": 7}

    player: Live[Players] = "@a"
    """who it rains on: a name, several separated by commas, or @a"""
    every: Live[float] = Field(0.4, ge=0.1, le=10)
    """seconds between showers of drops"""
    particles: Live[list[ParticleId]] = ["falling_lava", "falling_lava", "dripping_lava", "lava", "flame"]
    """the particles each puff is made of, one picked at random each time (one named twice comes twice as often)"""
    drops: Live[int] = Field(25, ge=1, le=200)
    """how many drops in each puff, spread 4 blocks wide"""
