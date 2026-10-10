from actions._types import Action, Field, Literal, Live, Players, preset


class SuperSaiyan(Action, preset("sneak"), preset("charge_up"), preset("theme_music")):
    """Make a player Super Saiyan, blue by default: crouch three times (or right-click the star) to power up in an aura, then run fast, hit hard, jump high and fly for a minute; the same again powers down."""

    script = "super_saiyan"
    runs = "until_stopped"
    takes = ["player", "level", "seconds", "flight", "cooldown"]
    defaults = {"sneak_mode": "three_taps", "charge_style": "aura", "charge_seconds": 1.5,
                "charge_particle": "dust{color:[0.2,0.9,1.0],scale:1.4}", "charge_size": 5, "theme_tune": "super_saiyan"}

    player: Players = "@a"
    """who can power up: a name, several separated by commas, or @a"""
    item: str = "Super Saiyan"
    """the name of the nether star that powers up or down on a right-click; empty: no star, crouching only"""
    level: Live[Literal["1", "2", "3", "blue"]] = "blue"
    """the form: 1 (speed, strength and jump boost I), 2 (speed and strength II), 3 (speed and strength III, jump boost II) or blue (speed III, strength II, jump boost II)"""
    seconds: Live[float] = Field(60, ge=5, le=3600)
    """how long the form lasts, in seconds, unless they power down first"""
    cooldown: Live[float] = Field(0, ge=0, le=600)
    """seconds after the form ends before it can be used again"""
    flight: Live[bool] = True
    """hover while in the form: look up to rise, crouch to sink"""
