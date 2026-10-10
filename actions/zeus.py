from actions._types import Action, Players, preset


class Zeus(Action, preset("projectile_lands"), preset("lightning")):
    """Zeus's trident: every trident a player throws calls lightning where it lands, in any weather; he throws his own, none is given."""

    script = "zeus"
    runs = "until_stopped"
    takes = ["player", "lightning_count", "lightning_spread"]
    defaults = {"projectile_lands_kind": "trident"}

    player: Players = "@a"
    """whose thrown tridents call lightning: a name, several separated by commas, or @a"""
