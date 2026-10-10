from actions._types import Action, Field, Live, Players, preset


class SniperRifle(Action, preset("damage_hits"), preset("sneak")):
    """Give a player a sniper rifle with a zoom scope: right-click for one long shot, double damage on the head; crouch with it to zoom."""

    script = "sniper_rifle"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "damage_what", "zoom", "range"]
    defaults = {"damage_amount": 150, "damage_what": "mobs", "damage_head": 2, "sneak_item": "Super Sniper"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    cooldown: Live[float] = Field(0.5, ge=0, le=60)
    """seconds between shots"""
    range: Live[int] = Field(400, ge=8, le=500)
    """how far a shot reaches, in blocks; it stops at the first solid block"""
    zoom: Live[int] = Field(4, ge=0, le=7)
    """how far the scope zooms in while he crouches with the rifle in hand (0: no zoom)"""
