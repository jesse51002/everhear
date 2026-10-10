from actions.boom_trident import BoomTrident


class SpearBomb(BoomTrident):
    """Give a player a spear bomb: a thrown trident that explodes and blows a crater where it lands."""

    takes = ["player", "explosion_power", "crater_radius", "crater_leave", "give"]
    fixed = {"crater": True, "item": "Spear Bomb"}
