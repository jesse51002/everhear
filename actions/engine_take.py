from actions._types import Action, ClassId, Player


class EngineTake(Action):
    """Take one of the engine's items away from a player: every stack of it they carry goes."""

    engine = "take"
    takes = ["player", "item"]

    player: Player = ""
    """whose: one player's exact name"""
    item: ClassId = ""
    """the item, by its name under ENGINE, such as fire_staff"""
