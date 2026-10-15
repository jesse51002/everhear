from actions._types import Action, ClassId, Player


class EngineGive(Action):
    """Give a player one of the engine's items, by its name under ENGINE: a stack of one, on the item's own base, that works for whoever holds it."""

    engine = "give"
    takes = ["player", "item"]

    player: Player = ""
    """whose: one player's exact name"""
    item: ClassId = ""
    """the item, by its name under ENGINE, such as fire_staff"""
