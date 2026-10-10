from actions._types import Action, Field, Live, ParticleId, Players, preset


class HollowPurple(Action, preset("charge_up"), preset("theme_music"), preset("damage_hits"), preset("crater"),
                   preset("beam_clash"), preset("around")):
    """Give a player Gojo's Hollow Purple: right-click to merge a blue and a red orb into a purple beam that kills and erases everything it passes through, walls too, out to a thousand blocks."""

    script = "hollow_purple"
    runs = "until_stopped"
    takes = ["player", "length", "width", "cooldown", "charge_seconds", "damage_what", "crater_radius"]
    defaults = {"charge_style": "orbs", "charge_seconds": 0.5, "charge_particle": "dust{color:[0.6,0.1,0.9],scale:2.5}",
                "charge_size": 5, "charge_orbs": ["dust{color:[0.2,0.4,1.0],scale:1.6}", "dust{color:[1.0,0.1,0.1],scale:1.6}"],
                "theme_tune": "hollow_purple", "damage_amount": 100, "damage_type": "magic", "damage_kill": True,
                "crater_radius": 7, "crater_tunnel": 2, "around_what": "everything",
                "beam_clash_particle": "dust{color:[0.6,0.1,0.9],scale:2.5}"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Hollow Purple"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(0, ge=0, le=60)
    """seconds after a shot before the next; the orbs still charge before every shot"""
    length: Live[int] = Field(1000, ge=4, le=1000)
    """how far the beam reaches, in blocks"""
    width: Live[float] = Field(6, ge=1, le=32)
    """how wide the beam is, in blocks: everything within half of it from its middle is hit"""
    through_walls: Live[bool] = True
    """whether the beam passes through blocks; off, it stops at the first"""
    beam_particle: Live[ParticleId] = "dust{color:[0.6,0.1,0.9],scale:2.5}"
    """the particle the beam is drawn with; dust{color:[r,g,b],scale:2} for a colour, r, g and b from 0 to 1"""
    around_radius: Live[float] = Field(15, ge=0, le=128)
    """everything this many blocks round where the beam ends is hit too, as around_what says (0: only what the beam passes)"""
