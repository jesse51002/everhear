from actions._types import Action, ClassId, Player


class EngineRevoke(Action):
    """Take one of the engine's abilities away from a player: it stops working for them, and what it was doing ends."""

    engine = "revoke"
    takes = ["player", "ability"]

    player: Player = ""
    """whose: one player's exact name"""
    ability: ClassId = ""
    """the ability, by its name under ENGINE, such as fireball"""
