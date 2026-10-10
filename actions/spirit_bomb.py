from actions._types import Action, Field, Live, Players, preset


class SpiritBomb(Action, preset("charge_up"), preset("theme_music"), preset("projectile"), preset("explosion"),
                 preset("crater")):
    """Give a player Goku's Spirit Bomb: hold right-click and a giant ball of light gathers overhead to a choir, then it is thrown where they look, blasts everything for a hundred blocks round and leaves a crater as big."""

    script = "spirit_bomb"
    runs = "until_stopped"
    takes = ["player", "crater_radius", "charge_seconds", "cooldown", "explosion_breaks"]
    defaults = {"charge_seconds": 5, "charge_size": 10, "theme_tune": "spirit_bomb", "projectile_size": 8,
                "projectile_speed": 40, "projectile_particle": "dust{color:[0.9,0.97,1.0],scale:2.0}",
                "explosion_power": 16, "explosion_layout": "rings", "explosion_spread": 32, "explosion_breaks": False,
                "crater_radius": 100}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Spirit Bomb"
    """the name of the item in the hand"""
    charge: Live[float] = Field(1, ge=0, le=10)
    """seconds right-click is held before the ball starts to gather (charge_seconds is how long it then gathers); letting go sooner cancels it"""
    cooldown: Live[float] = Field(0, ge=0, le=120)
    """seconds after a throw before the next can be charged; each player has one bomb under way at a time"""
    reach: Live[int] = Field(100, ge=4, le=300)
    """how far it is thrown at most, where they look, in blocks; it lands sooner on the first block in the way"""
