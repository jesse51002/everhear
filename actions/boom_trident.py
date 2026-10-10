from actions._types import Action, Live, Players, preset


class BoomTrident(Action, preset("projectile_lands"), preset("explosion"), preset("crater")):
    """Give a player a boom trident: it explodes like a charged creeper on whatever it hits or where it lands, never on the thrower."""

    script = "boom_trident"
    runs = "until_stopped"
    takes = ["player", "explosion_power", "explosion_layout", "explosion_breaks", "give"]
    defaults = {"projectile_lands_kind": "trident", "explosion_power": 6, "crater_radius": 4}
    fixed = {"crater": False}

    player: Players = "@a"
    """whose thrown tridents explode: a name, several separated by commas, or @a"""
    give: bool = True
    """give each of them the trident (it comes back when thrown, never breaks); off: their own tridents explode"""
    item: str = "Boom Trident"
    """the name of the trident it gives, and takes back when stopped"""
    crater: Live[bool] = False
    """carve a crater where each one lands too, a spear bomb (crater_radius, crater_leave)"""
