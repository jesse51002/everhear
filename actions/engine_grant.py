from typing import ClassVar

from actions._types import Action, Field


class EngineGrant(Action):
    """Give a player one of the engine's abilities, by its name under ENGINE: it works for them from now on, as the ability says, until revoked.

    The ability must be one the engine has. A grant binds no incantation: once it has
    landed, bind the player's phrase to it with engine_bind, in a later step.
    """

    engine: ClassVar[str] = "grant"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["player", "ability"]

    player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
    """who gets it: one player's exact name"""
    ability: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the ability, by its name under ENGINE, such as fireball"""
