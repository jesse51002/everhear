from actions._types import Action, ClassId, Player


class EngineClear(Action):
    """Take one of the engine's marks off a player before it ends by itself: only that mark's effects go."""

    engine = "clear"
    takes = ["player", "mark"]

    player: Player = ""
    """whose: one player's exact name"""
    mark: ClassId = ""
    """the mark, by its name under ENGINE, such as giant"""
