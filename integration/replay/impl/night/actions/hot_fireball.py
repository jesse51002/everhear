from actions._types import Action, Field, Live, Players, preset


class HotFireball(Action, preset("charge_up"), preset("theme_music"), preset("projectile"), preset("explosion"), preset("crater")):
    """Hot fireball: launch a fireball where you look; it bursts where it lands."""

    script = "hot_fireball"
    runs = "once"
    takes = ["player", "reach"]
    defaults = {"projectile_kind": "orb", "projectile_size": 1.5, "projectile_speed": 30,
                "projectile_particle": "flame", "explosion_power": 6, "explosion_layout": "one",
                "explosion_spread": 6, "explosion_breaks": True, "crater_radius": 0}

    player: Players = "@a"
    """whose fireball it is: the caster's name"""
    fireball_damage: Live[int] = Field(40, ge=0, le=1000)
    """damage the fireball does to the first mob it hits, in half-hearts (a player has 20)"""
    reach: Live[int] = Field(100, ge=4, le=300)
    """how far the fireball flies at most, where they look, in blocks; it bursts sooner on the first block in the way"""
