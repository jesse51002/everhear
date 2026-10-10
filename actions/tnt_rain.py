from actions._types import Action, Field, Live, Players, preset


class TntRain(Action, preset("sky_above")):
    """Make it rain lit TNT on the players: a volley falls round each every few seconds and blows holes, until stopped."""

    script = "tnt_rain"
    runs = "until_stopped"
    takes = ["player", "every", "sky_count", "sky_radius", "fuse"]
    defaults = {"sky_radius": 7, "sky_height": 25}

    player: Players = "@a"
    """who it rains on: a name, several separated by commas, or @a"""
    every: Live[float] = Field(20, ge=1, le=3600)
    """seconds between volleys; the first falls at once"""
    fuse: Live[float] = Field(4, ge=0.5, le=10)
    """seconds each TNT burns before it blows"""
    sky_count: Live[int] = Field(10, ge=1, le=40)
    """how many TNT fall round each player in a volley"""
