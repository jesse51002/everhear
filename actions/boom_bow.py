from actions._types import Action, Live, Players, preset


class BoomBow(Action, preset("projectile_lands"), preset("explosion")):
    """An explosive bow: every arrow a player shoots from his own bow explodes where it lands or hits, from one blast to a ring of them."""

    script = "boom_bow"
    runs = "until_stopped"
    takes = ["player", "explosion_power", "explosion_layout", "explosion_breaks", "cloud"]
    defaults = {"projectile_lands_kind": "arrow", "projectile_lands_remove": True, "explosion_power": 8,
                "explosion_spread": 8}

    player: Players = "@a"
    """whose arrows explode: a name, several separated by commas, or @a"""
    cloud: Live[bool] = False
    """a mushroom cloud of smoke over each blast"""
