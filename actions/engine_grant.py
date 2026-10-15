from actions._types import Action, ClassId, Player


class EngineGrant(Action):
    """Give a player one of the engine's abilities, by its name under ENGINE: it works for them from now on, as the ability says, until revoked.

    A grant binds no incantation: once it has landed, bind the player's phrase to it
    with engine_bind, in a later step.
    """

    engine = "grant"
    takes = ["player", "ability"]

    player: Player = ""
    """whose: one player's exact name"""
    ability: ClassId = ""
    """the ability, by its name under ENGINE, such as fireball"""
