from typing import ClassVar

from actions._types import Action, Field


class EngineRevoke(Action):
    """Take one of the engine's abilities away from a player, by its name: it stops working for them, and what it was doing ends."""

    engine: ClassVar[str] = "revoke"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["player", "ability"]

    player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
    """whose ability it is: one player's exact name"""
    ability: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the ability, by its name under ENGINE, such as fireball"""
