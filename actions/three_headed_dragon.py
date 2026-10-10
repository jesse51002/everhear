from actions._types import Action, Field, Literal, Place, Players, preset


class ThreeHeadedDragon(Action, preset("boss"), preset("theme_music")):
    """Summon a three-headed dragon boss in the sky over us: three ender dragons on one health bar, with boss music until they fall."""

    script = "dragon_boss"
    runs = "once"
    takes = ["player", "boss_bodies", "boss_health", "boss_scale", "boss_music", "boss_reward"]
    defaults = {"boss_name": "Three-Headed Dragon", "boss_mob": "ender_dragon", "boss_bodies": 3, "boss_health": 600,
                "boss_scale": 1, "boss_gear": "none", "boss_minions": 0, "boss_griefing": False,
                "theme_tune": "boss", "theme_to": "everyone", "theme_loop": True}

    player: Players = "@a"
    """who they rise over: the first of them online (a name, several separated by commas, or @a)"""
    at: Place = ""
    """where they rise instead: a saved place, x y z or a player, height blocks over it (empty: over the player)"""
    height: int = Field(30, ge=0, le=200)
    """how many blocks over the player, or over at, the dragons appear (never within 20 of the top of the world)"""
    boss_music: Literal["none", "pigstep", "otherside", "relic", "creator", "precipice"] = "none"
    """a music disc every player hears, over and over, until they die, on top of the boss tune that plays anyway (theme_tune; none: no disc)"""
