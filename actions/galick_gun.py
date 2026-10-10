from actions._types import Action, Field, Live, ParticleId, Players, preset


class GalickGun(Action, preset("charge_up"), preset("theme_music"), preset("damage_hits"), preset("crater"),
                preset("explosion"), preset("beam_clash")):
    """Give a player Vegeta's Galick Gun: hold right-click to charge a purple orb, then fire a beam through walls that hurts everything on it and blows a crater at its end; two beams fired at each other clash."""

    script = "galick_gun"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "cooldown", "length", "through_walls", "crater_radius"]
    defaults = {"charge_style": "orbs", "charge_seconds": 1.5, "charge_particle": "dust{color:[0.6,0.1,0.9],scale:2.0}",
                "charge_size": 1, "damage_amount": 60, "crater_radius": 6, "crater_tunnel": 1, "explosion_power": 3,
                "explosion_breaks": False}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Galick Gun"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(0, ge=0, le=60)
    """seconds after a shot before the next; the charge still plays before every shot"""
    length: Live[int] = Field(150, ge=4, le=1000)
    """how far the beam reaches, in blocks"""
    width: Live[float] = Field(3, ge=1, le=16)
    """how wide the beam is, in blocks: everything within half of it from its middle is hit"""
    through_walls: Live[bool] = True
    """whether the beam passes through blocks; off, it stops at the first and blows up there"""
    beam_particle: Live[ParticleId] = "dust{color:[0.6,0.1,0.9],scale:2.0}"
    """the particle the beam is drawn with; dust{color:[r,g,b],scale:2} for a colour, r, g and b from 0 to 1"""
